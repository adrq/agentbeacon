package storage

import (
	"testing"
)

// TestWorkflowVersionsMigration verifies schema presence and latest flip behavior at DB level.
func TestWorkflowVersionsMigration(t *testing.T) {
	db, err := Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	// Insert first version manually (simulate storage layer later)
	_, err = db.Exec(`INSERT INTO workflow_version (namespace,name,version,is_latest,description,content_hash,yaml_snapshot,created_at)
        VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)`, "alice", "demo", "v1", true, "desc", repeatHash('a'), "yaml: v1")
	if err != nil {
		t.Fatalf("insert v1: %v", err)
	}

	// Insert second version as latest
	_, err = db.Exec(`INSERT INTO workflow_version (namespace,name,version,is_latest,description,content_hash,yaml_snapshot,created_at)
        VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)`, "alice", "demo", "v2", true, "desc2", repeatHash('b'), "yaml: v2")
	if err != nil {
		t.Fatalf("insert v2: %v", err)
	}

	// Check counts and latest flags
	type row struct {
		Version  string `db:"version"`
		IsLatest bool   `db:"is_latest"`
	}
	rows := []row{}
	if err := db.Select(&rows, `SELECT version, is_latest FROM workflow_version WHERE namespace=? AND name=? ORDER BY version`, "alice", "demo"); err != nil {
		t.Fatalf("select rows: %v", err)
	}
	if len(rows) != 2 {
		t.Fatalf("expected 2 versions, got %d", len(rows))
	}
	if rows[0].Version != "v1" || rows[0].IsLatest {
		t.Errorf("v1 should not be latest after v2 insert")
	}
	if rows[1].Version != "v2" || !rows[1].IsLatest {
		t.Errorf("v2 should be latest")
	}
}

// repeatHash creates a 64-char pseudo hash from a single rune for testing.
func repeatHash(ch rune) string {
	b := make([]rune, 64)
	for i := range b {
		b[i] = ch
	}
	return string(b)
}
