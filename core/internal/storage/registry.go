package storage

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"regexp"
	"time"

	"github.com/google/uuid"
)

var (
	// ErrDuplicateContent is returned when attempting to register identical content to current latest.
	ErrDuplicateContent = errors.New("identical content already latest")
	// validation pattern per spec
	identPattern = regexp.MustCompile(`^[a-z0-9._-]{1,64}$`)
)

// hashContent returns sha256 hex digest of the provided yaml snapshot.
func hashContent(yaml string) string {
	sum := sha256.Sum256([]byte(yaml))
	return hex.EncodeToString(sum[:])
}

// validateIdentity enforces namespace/name rules. Reserved namespace 'team' rejected.
func validateIdentity(namespace, name string) error {
	if namespace == "team" { // reserved for future governance
		return fmt.Errorf("namespace 'team' is reserved")
	}
	if !identPattern.MatchString(namespace) {
		return fmt.Errorf("invalid namespace")
	}
	if !identPattern.MatchString(name) {
		return fmt.Errorf("invalid name")
	}
	return nil
}

// RegisterInlineWorkflow registers a new inline workflow version (UUID version) unless identical
// to current latest. Returns the inserted *WorkflowVersion on success.
// This is a low-level storage primitive; higher layers perform YAML parsing & description extraction.
func (db *DB) RegisterInlineWorkflow(namespace, name, description, yamlSnapshot string) (*WorkflowVersion, error) {
	if err := validateIdentity(namespace, name); err != nil {
		return nil, err
	}

	newHash := hashContent(yamlSnapshot)

	// Check current latest
	var latest WorkflowVersion
	latestQuery := db.placeholder("SELECT * FROM workflow_version WHERE namespace=? AND name=? AND is_latest=TRUE")
	err := db.Get(&latest, latestQuery, namespace, name)
	if err == nil {
		if latest.ContentHash == newHash { // duplicate latest
			return nil, ErrDuplicateContent
		}
	}
	// else ignore not found error

	version := uuid.New().String()

	// Transactional latest flip + insert
	tx, err := db.Beginx()
	if err != nil {
		return nil, fmt.Errorf("begin tx: %w", err)
	}
	defer func() {
		if err != nil {
			tx.Rollback()
		}
	}()

	// Clear previous latest if any (best effort; trigger also handles if skipped)
	updateLatest := db.placeholder("UPDATE workflow_version SET is_latest=FALSE WHERE namespace=? AND name=? AND is_latest=TRUE")
	if _, uerr := tx.Exec(updateLatest, namespace, name); uerr != nil {
		err = fmt.Errorf("unset previous latest: %w", uerr)
		return nil, err
	}

	now := time.Now()
	insert := `INSERT INTO workflow_version (namespace,name,version,is_latest,description,content_hash,yaml_snapshot,created_at)
               VALUES (?,?,?,?,?,?,?,?)`
	if db.driver == "postgres" { // adapt placeholders
		insert = db.placeholder(insert)
	}

	_, err = tx.Exec(insert, namespace, name, version, true, description, newHash, yamlSnapshot, now)
	if err != nil {
		err = fmt.Errorf("insert workflow_version: %w", err)
		return nil, err
	}

	if cerr := tx.Commit(); cerr != nil {
		return nil, fmt.Errorf("commit: %w", cerr)
	}

	return &WorkflowVersion{
		Namespace:    namespace,
		Name:         name,
		Version:      version,
		IsLatest:     true,
		Description:  description,
		ContentHash:  newHash,
		YAMLSnapshot: yamlSnapshot,
		CreatedAt:    now,
	}, nil
}

// GetLatestWorkflowVersion returns the latest version row for a workflow.
func (db *DB) GetLatestWorkflowVersion(namespace, name string) (*WorkflowVersion, error) {
	query := db.placeholder("SELECT * FROM workflow_version WHERE namespace=? AND name=? AND is_latest=TRUE")
	var wf WorkflowVersion
	if err := db.Get(&wf, query, namespace, name); err != nil {
		return nil, fmt.Errorf("latest workflow version not found: %w", err)
	}
	return &wf, nil
}

// GetWorkflowVersion fetches a specific version.
func (db *DB) GetWorkflowVersion(namespace, name, version string) (*WorkflowVersion, error) {
	query := db.placeholder("SELECT * FROM workflow_version WHERE namespace=? AND name=? AND version=?")
	var wf WorkflowVersion
	if err := db.Get(&wf, query, namespace, name, version); err != nil {
		return nil, fmt.Errorf("workflow version not found: %w", err)
	}
	return &wf, nil
}

// ListWorkflowVersions returns versions newest->oldest (latest first). Uses created_at desc with is_latest prioritization.
func (db *DB) ListWorkflowVersions(namespace, name string) ([]WorkflowVersion, error) {
	// Order: latest first, then by created_at desc
	query := db.placeholder("SELECT * FROM workflow_version WHERE namespace=? AND name=? ORDER BY is_latest DESC, created_at DESC")
	var rows []WorkflowVersion
	if err := db.Select(&rows, query, namespace, name); err != nil {
		return nil, err
	}
	return rows, nil
}

// ListLatestWorkflowVersions returns one latest row per (namespace,name) ordered deterministically.
func (db *DB) ListLatestWorkflowVersions() ([]WorkflowVersion, error) {
	query := db.placeholder("SELECT * FROM workflow_version WHERE is_latest=TRUE ORDER BY namespace, name")
	var rows []WorkflowVersion
	if err := db.Select(&rows, query); err != nil {
		return nil, err
	}
	return rows, nil
}
