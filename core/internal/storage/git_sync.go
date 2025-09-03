package storage

// Git repository sync logic for workflow registry
// Scans a repository for *.yaml files, determines last commit touching each file,
// parses workflow metadata (name, namespace, description), and registers a version
// with version = commit hash. Skips if (namespace,name,commit) already stored or
// if content identical to current latest.

import (
	"bytes"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// GitSyncResult provides counters for a sync operation.
type GitSyncResult struct {
	Scanned                int `json:"scanned"`
	Inserted               int `json:"inserted"`
	SkippedExistingVersion int `json:"skipped_existing_version"`
	SkippedSameAsLatest    int `json:"skipped_same_as_latest"`
}

// RegisterGitWorkflowVersion inserts a workflow_version row sourced from git metadata.
// Follows duplicate policy: only compare hash against current latest; identical -> skip (no insert).
func (db *DB) RegisterGitWorkflowVersion(namespace, name, description, yamlSnapshot, repo, path, commit, branch string) (inserted bool, skippedSame bool, err error) {
	if err = validateIdentity(namespace, name); err != nil {
		return false, false, err
	}
	newHash := hashContent(yamlSnapshot)

	// Check existing exact version
	var exists int
	checkQuery := db.placeholder("SELECT 1 FROM workflow_version WHERE namespace=? AND name=? AND version=?")
	if qerr := db.Get(&exists, checkQuery, namespace, name, commit); qerr == nil {
		return false, false, nil // existing version row, caller increments skipped_existing_version
	}
	// Compare against latest
	var latest WorkflowVersion
	latestQuery := db.placeholder("SELECT * FROM workflow_version WHERE namespace=? AND name=? AND is_latest=TRUE")
	latestErr := db.Get(&latest, latestQuery, namespace, name)
	if latestErr == nil && latest.ContentHash == newHash { // identical to current latest
		return false, true, nil
	}

	tx, txErr := db.Beginx()
	if txErr != nil {
		return false, false, fmt.Errorf("begin tx: %w", txErr)
	}
	defer func() {
		if err != nil {
			tx.Rollback()
		}
	}()

	unset := db.placeholder("UPDATE workflow_version SET is_latest=FALSE WHERE namespace=? AND name=? AND is_latest=TRUE")
	if _, uerr := tx.Exec(unset, namespace, name); uerr != nil {
		return false, false, fmt.Errorf("unset previous latest: %w", uerr)
	}

	now := time.Now()
	insert := `INSERT INTO workflow_version (namespace,name,version,is_latest,description,content_hash,yaml_snapshot,git_repo,git_path,git_commit,git_branch,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`
	if db.driver == "postgres" {
		insert = db.placeholder(insert)
	}
	if _, ierr := tx.Exec(insert, namespace, name, commit, true, description, newHash, yamlSnapshot, repo, path, commit, branch, now); ierr != nil {
		return false, false, fmt.Errorf("insert workflow_version: %w", ierr)
	}
	if cerr := tx.Commit(); cerr != nil {
		return false, false, fmt.Errorf("commit: %w", cerr)
	}
	return true, false, nil
}

// GitSync scans the repository at repoPath for YAML workflow files and registers versions.
// Simple heuristic: any *.yaml whose YAML root contains name + namespace fields.
func (db *DB) GitSync(repoPath string) (GitSyncResult, error) {
	var res GitSyncResult
	// Determine branch
	branch, _ := gitSimple(repoPath, "rev-parse", "--abbrev-ref", "HEAD")
	branch = strings.TrimSpace(branch)
	if branch == "" {
		branch = "unknown"
	}

	// Collect files
	var files []string
	filepath.WalkDir(repoPath, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			return nil
		}
		if strings.HasSuffix(d.Name(), ".yaml") || strings.HasSuffix(d.Name(), ".yml") {
			files = append(files, path)
		}
		return nil
	})

	type wfYAML struct {
		Name        string `yaml:"name"`
		Namespace   string `yaml:"namespace"`
		Description string `yaml:"description"`
	}

	for _, f := range files {
		// Ensure tracked (git ls-files --error-unmatch path)
		if _, err := gitCmd(repoPath, "ls-files", "--error-unmatch", f); err != nil {
			continue // untracked
		}
		res.Scanned++
		contentBytes, err := os.ReadFile(f)
		if err != nil {
			continue
		}
		var parsed wfYAML
		if yerr := yaml.Unmarshal(contentBytes, &parsed); yerr != nil {
			continue
		}
		if parsed.Name == "" || parsed.Namespace == "" {
			continue
		}
		// Last commit touching file
		commit, err := gitCmd(repoPath, "log", "-n1", "--pretty=format:%H", "--", f)
		if err != nil {
			continue
		}
		commit = strings.TrimSpace(commit)
		if commit == "" {
			continue
		}

		inserted, skippedSame, rerr := db.RegisterGitWorkflowVersion(parsed.Namespace, parsed.Name, parsed.Description, string(contentBytes), repoPath, relPath(repoPath, f), commit, branch)
		if rerr != nil {
			continue
		}
		if inserted {
			res.Inserted++
			continue
		}
		if skippedSame {
			res.SkippedSameAsLatest++
			continue
		}
		// neither inserted nor skippedSame indicates existing version
		res.SkippedExistingVersion++
	}
	return res, nil
}

func relPath(root, p string) string {
	rp, err := filepath.Rel(root, p)
	if err != nil {
		return p
	}
	return rp
}

// gitCmd executes git and returns stdout string.
func gitCmd(dir string, args ...string) (string, error) { return gitSimple(dir, args...) }

func gitSimple(dir string, args ...string) (string, error) {
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	var out bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("git %v failed: %v: %s", args, err, stderr.String())
	}
	return out.String(), nil
}
