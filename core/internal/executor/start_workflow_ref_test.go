package executor

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/agentmaestro/agentmaestro/core/internal/config"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
)

// minimalConfigLoader returns a config loader pointing at a temp agents file with mock-agent.
func minimalConfigLoader(t *testing.T) *config.ConfigLoader {
	tempDir := t.TempDir()
	agentsFile := filepath.Join(tempDir, "agents.yaml")
	data := []byte("agents:\n  mock-agent:\n    type: stdio\n    config:\n      command: ../../../bin/mock-agent\n")
	if err := os.WriteFile(agentsFile, data, 0644); err != nil {
		t.Fatalf("write agents file: %v", err)
	}
	return config.NewConfigLoader(agentsFile)
}

// registerSimpleVersion registers a one-node workflow and returns concrete version.
func registerSimpleVersion(t *testing.T, db *storage.DB, ns, name, prompt string) string {
	yaml := fmt.Sprintf("namespace: %s\nname: %s\nnodes:\n  - id: n1\n    agent: mock-agent\n    request:\n      prompt: \"%s\"\n", ns, name, prompt)
	wf, err := db.RegisterInlineWorkflow(ns, name, "", yaml)
	if err != nil {
		t.Fatalf("register inline: %v", err)
	}
	return wf.Version
}

func TestStartWorkflowRef_HappyPathPersistsVersion(t *testing.T) {
	db := setupTestDB(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	defer db.Close()
	if _, err := os.Stat("../../../bin/mock-agent"); err != nil {
		t.Skip("mock-agent binary not found; run make build")
	}
	version := registerSimpleVersion(t, db, "alice", "demo", "hi")
	exec := NewExecutor(db, minimalConfigLoader(t))
	info, err := exec.StartWorkflowRef("alice/demo:latest")
	if err != nil {
		t.Fatalf("StartWorkflowRef failed: %v", err)
	}
	if info.WorkflowID != fmt.Sprintf("alice/demo:%s", version) {
		t.Fatalf("expected canonical id with version %s got %s", version, info.WorkflowID)
	}
	row, err := db.GetExecution(info.ID)
	if err != nil {
		t.Fatalf("fetch execution: %v", err)
	}
	if row.WorkflowNamespace == nil || *row.WorkflowNamespace != "alice" {
		t.Fatalf("expected namespace alice got %v", row.WorkflowNamespace)
	}
	if row.WorkflowVersion == nil || *row.WorkflowVersion != version {
		t.Fatalf("expected version %s got %v", version, row.WorkflowVersion)
	}
}

func TestStartWorkflowRef_ExplicitVersionUnaffectedByLaterLatest(t *testing.T) {
	db := setupTestDB(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	defer db.Close()
	if _, err := os.Stat("../../../bin/mock-agent"); err != nil {
		t.Skip("mock-agent binary not found; run make build")
	}
	v1 := registerSimpleVersion(t, db, "alice", "immut", "first")
	exec := NewExecutor(db, minimalConfigLoader(t))
	info, err := exec.StartWorkflowRef("alice/immut:" + v1)
	if err != nil {
		t.Fatalf("start explicit v1: %v", err)
	}
	v2 := registerSimpleVersion(t, db, "alice", "immut", "second")
	if v1 == v2 {
		t.Fatalf("expected differing versions")
	}
	row, _ := db.GetExecution(info.ID)
	if row.WorkflowVersion == nil || *row.WorkflowVersion != v1 {
		t.Fatalf("execution mutated version expected %s got %v", v1, row.WorkflowVersion)
	}
	// Confirm latest now resolves to v2
	ref, wf, err := db.ResolveWorkflowRef("alice/immut:latest")
	if err != nil {
		t.Fatalf("resolve latest: %v", err)
	}
	if wf.Version != v2 || ref.Version != v2 {
		t.Fatalf("latest should be v2 got %s", wf.Version)
	}
}

func TestStartWorkflowRef_MalformedRef(t *testing.T) {
	db := setupTestDB(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	defer db.Close()
	exec := NewExecutor(db, minimalConfigLoader(t))
	if _, err := exec.StartWorkflowRef("notvalidref"); err == nil {
		t.Fatal("expected error for malformed ref")
	}
}

func TestStartWorkflowRef_NonexistentVersion(t *testing.T) {
	db := setupTestDB(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	defer db.Close()
	if _, err := os.Stat("../../../bin/mock-agent"); err != nil {
		t.Skip("mock-agent binary not found; run make build")
	}
	registerSimpleVersion(t, db, "alice", "demo", "hi")
	exec := NewExecutor(db, minimalConfigLoader(t))
	if _, err := exec.StartWorkflowRef("alice/demo:deadbeef"); err == nil {
		t.Fatal("expected error for missing version")
	}
}
