package storage

import (
	"database/sql"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jmoiron/sqlx"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// (repeatHash helper already defined in migration test; reusing that file's helper in same package.)

// TestRegistryStorageOperations covers SQLite and PostgreSQL variants ensuring constraints work on both.
func TestRegistryStorageOperations(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		db, err := Open("sqlite3", ":memory:")
		if err != nil {
			t.Fatalf("open db: %v", err)
		}
		defer db.Close()
		runRegistryScenario(t, db)
	})

	t.Run("PostgreSQL", func(t *testing.T) {
		baseDSN := "postgres://postgres:postgres@127.0.0.1/postgres?sslmode=disable"
		baseConn, err := sqlx.Connect("postgres", baseDSN)
		if err != nil {
			t.Skipf("PostgreSQL not available: %v", err)
		}
		defer baseConn.Close()

		schemaName := "test_registry_" + strings.ReplaceAll(strings.ToLower(t.Name()), "/", "_")
		baseConn.Exec("DROP SCHEMA IF EXISTS " + schemaName + " CASCADE")
		if _, err := baseConn.Exec("CREATE SCHEMA " + schemaName); err != nil {
			t.Skipf("cannot create schema: %v", err)
		}
		defer baseConn.Exec("DROP SCHEMA IF EXISTS " + schemaName + " CASCADE")

		dsn := baseDSN + "&search_path=" + schemaName
		db, err := Open("postgres", dsn)
		if err != nil {
			t.Fatalf("open postgres db: %v", err)
		}
		defer db.Close()

		runRegistryScenario(t, db)

		// Constraint validation: attempt two inserts with is_latest=true directly should violate partial unique index
		// First manual insert
		_, err = db.Exec(`INSERT INTO workflow_version (namespace,name,version,is_latest,description,content_hash,yaml_snapshot,created_at) VALUES ($1,$2,$3,true,'d',$4,'y',NOW())`, "alice", "uniq", "v1", repeatHash('a'))
		if err != nil {
			t.Fatalf("manual insert v1 failed: %v", err)
		}

		// Second conflicting insert (same namespace/name, also is_latest=true) expected to fail
		_, err = db.Exec(`INSERT INTO workflow_version (namespace,name,version,is_latest,description,content_hash,yaml_snapshot,created_at) VALUES ($1,$2,$3,true,'d',$4,'y',NOW())`, "alice", "uniq", "v2", repeatHash('b'))
		if err == nil {
			t.Fatalf("expected unique latest constraint violation, got nil error")
		}
		// Basic assertion it is a duplicate key error
		if !isUniqueViolation(err) {
			t.Fatalf("expected unique violation error, got: %v", err)
		}
	})
}

func TestRegistryLifecycleIntegration(t *testing.T) {
	db, err := Open("sqlite3", filepath.Join(t.TempDir(), "lifecycle.db"))
	require.NoError(t, err)
	defer db.Close()

	// Register two versions inline
	yamlV1 := "name: demo\nnamespace: alice\ndescription: first\nnodes: []\n"
	v1, err := db.RegisterInlineWorkflow("alice", "demo", "first", yamlV1)
	require.NoError(t, err)
	assert.True(t, v1.IsLatest)

	yamlV2 := "name: demo\nnamespace: alice\ndescription: second\nnodes: []\n"
	v2, err := db.RegisterInlineWorkflow("alice", "demo", "second", yamlV2)
	require.NoError(t, err)
	assert.True(t, v2.IsLatest)

	latest, err := db.GetLatestWorkflowVersion("alice", "demo")
	require.NoError(t, err)
	assert.Equal(t, v2.Version, latest.Version)

	versions, err := db.ListWorkflowVersions("alice", "demo")
	require.NoError(t, err)
	require.Len(t, versions, 2)
	assert.Equal(t, v2.Version, versions[0].Version)
	assert.Equal(t, v1.Version, versions[1].Version)

	// Resolve latest ref and explicit version
	refLatest, _, err := db.ResolveWorkflowRef("alice/demo")
	require.NoError(t, err)
	assert.Equal(t, v2.Version, refLatest.Version)

	refExplicit, _, err := db.ResolveWorkflowRef(fmt.Sprintf("alice/demo:%s", v1.Version))
	require.NoError(t, err)
	assert.Equal(t, v1.Version, refExplicit.Version)
}

// Common scenario executed for each driver.
func runRegistryScenario(t *testing.T, db *DB) {
	v1, err := db.RegisterInlineWorkflow("alice", "demo", "first version", "name: demo\nnamespace: alice\nnodes: []\n")
	if err != nil {
		t.Fatalf("register v1: %v", err)
	}
	if !v1.IsLatest {
		t.Errorf("v1 should be latest")
	}

	latest, err := db.GetLatestWorkflowVersion("alice", "demo")
	if err != nil {
		t.Fatalf("get latest after v1: %v", err)
	}
	if latest.Version != v1.Version {
		t.Errorf("expected latest version %s got %s", v1.Version, latest.Version)
	}

	v2, err := db.RegisterInlineWorkflow("alice", "demo", "second version", "name: demo\nnamespace: alice\ndescription: second\nnodes: []\n")
	if err != nil {
		t.Fatalf("register v2: %v", err)
	}
	if v2.Version == v1.Version {
		t.Errorf("expected different version IDs")
	}
	if !v2.IsLatest {
		t.Errorf("v2 should be latest")
	}

	rows, err := db.ListWorkflowVersions("alice", "demo")
	if err != nil {
		t.Fatalf("list versions: %v", err)
	}
	if len(rows) != 2 {
		t.Fatalf("expected 2 versions got %d", len(rows))
	}
	if !rows[0].IsLatest || rows[0].Version != v2.Version {
		t.Errorf("expected first row to be v2 latest")
	}
	if rows[1].IsLatest || rows[1].Version != v1.Version {
		t.Errorf("expected second row to be v1 non-latest")
	}

	if _, err := db.RegisterInlineWorkflow("alice", "demo", "dup attempt", "name: demo\nnamespace: alice\ndescription: second\nnodes: []\n"); err == nil {
		t.Fatalf("expected duplicate error, got nil")
	} else if err != ErrDuplicateContent {
		t.Fatalf("expected ErrDuplicateContent, got %v", err)
	}

	rows, err = db.ListWorkflowVersions("alice", "demo")
	if err != nil {
		t.Fatalf("list after dup: %v", err)
	}
	if len(rows) != 2 {
		t.Fatalf("expected 2 versions after duplicate attempt got %d", len(rows))
	}

	gotV1, err := db.GetWorkflowVersion("alice", "demo", v1.Version)
	if err != nil {
		t.Fatalf("get v1: %v", err)
	}
	gotV2, err := db.GetWorkflowVersion("alice", "demo", v2.Version)
	if err != nil {
		t.Fatalf("get v2: %v", err)
	}
	if gotV1.ContentHash == gotV2.ContentHash {
		t.Errorf("expected different content hashes for distinct versions")
	}

	latest, err = db.GetLatestWorkflowVersion("alice", "demo")
	if err != nil {
		t.Fatalf("get latest after v2: %v", err)
	}
	if latest.Version != v2.Version {
		t.Errorf("expected latest to be v2, got %s", latest.Version)
	}
}

// isUniqueViolation performs a minimal check for Postgres unique constraint error without importing lib/pq error types.
func isUniqueViolation(err error) bool {
	if err == nil {
		return false
	}
	// lib/pq includes phrase "duplicate key value" for unique violations
	if strings.Contains(err.Error(), "duplicate key value") {
		return true
	}
	return false
}

// Ensure unused imports not triggered when postgres skipped
var _ *sql.DB
