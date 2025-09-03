package storage

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jmoiron/sqlx"
	_ "github.com/lib/pq"
	_ "github.com/mattn/go-sqlite3"
)

func TestOpen_CreatesDirectoryIfNotExists(t *testing.T) {
	tempDir := t.TempDir()
	nonExistentDir := filepath.Join(tempDir, "new", "nested", "path")
	dbPath := filepath.Join(nonExistentDir, "test.db")

	db, err := Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Open failed: %v", err)
	}
	defer db.Close()

	if _, err := os.Stat(nonExistentDir); os.IsNotExist(err) {
		t.Errorf("Database directory was not created: %s", nonExistentDir)
	}

	if _, err := os.Stat(dbPath); os.IsNotExist(err) {
		t.Errorf("Database file was not created: %s", dbPath)
	}
}

func TestOpen_RunsMigrations(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	db, err := Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Open failed: %v", err)
	}
	defer db.Close()

	tables := []string{"config", "workflow", "execution"}
	for _, table := range tables {
		var count int
		err := db.Get(&count, `
			SELECT COUNT(*) FROM sqlite_master
			WHERE type='table' AND name=?
		`, table)

		if err != nil {
			t.Errorf("Failed to check if table %s exists: %v", table, err)
		}
		if count != 1 {
			t.Errorf("Required table %s was not created by migration", table)
		}
	}

	var indexCount int
	err = db.Get(&indexCount, `
		SELECT COUNT(*) FROM sqlite_master
		WHERE type='index' AND name='idx_config_name'
	`)
	if err != nil {
		t.Errorf("Failed to check for config name index: %v", err)
	}
	if indexCount != 1 {
		t.Error("Unique index on config.name was not created")
	}
}

func TestOpen_IdempotentMigrations(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	db1, err := Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("First Open failed: %v", err)
	}
	db1.Close()

	db2, err := Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Second Open failed: %v", err)
	}
	defer db2.Close()

	tables := []string{"config", "workflow", "execution"}
	for _, table := range tables {
		var count int
		err := db2.Get(&count, `
			SELECT COUNT(*) FROM sqlite_master
			WHERE type='table' AND name=?
		`, table)

		if err != nil {
			t.Errorf("Table %s check failed after reopening: %v", table, err)
		}
		if count != 1 {
			t.Errorf("Table %s missing after reopening database", table)
		}
	}

	var tableCount int
	err = db2.Get(&tableCount, `SELECT COUNT(*) FROM sqlite_master WHERE type='table'`)
	if err != nil {
		t.Errorf("Failed to count tables: %v", err)
	}
	if tableCount != 4 { // config, workflow, execution, workflow_version
		t.Errorf("Expected exactly 4 tables after idempotent migration, got %d", tableCount)
	}
}

func TestDB_Close(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	db, err := Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Open failed: %v", err)
	}

	err = db.Close()
	if err != nil {
		t.Errorf("Close failed: %v", err)
	}

	err = db.Close()
	if err != nil {
		t.Errorf("Second Close should not error: %v", err)
	}

	_, err = db.Exec("SELECT 1")
	if err == nil {
		t.Error("Expected error when using closed database")
	}
}

func TestDefaultDBPath(t *testing.T) {
	path := DefaultDBPath()
	if path == "" {
		t.Error("DefaultDBPath returned empty string")
	}

	if !filepath.IsAbs(path) {
		t.Error("DefaultDBPath should return absolute path")
	}

	expectedSuffix := filepath.Join(".agentmaestro", "agentmaestro.db")
	if !strings.HasSuffix(path, expectedSuffix) {
		t.Errorf("DefaultDBPath should end with %s, got %s", expectedSuffix, path)
	}

	dir := filepath.Dir(path)
	if !strings.Contains(dir, ".agentmaestro") {
		t.Errorf("Expected .agentmaestro directory in path, got %s", dir)
	}
}

func TestDatabaseConstraints(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testDatabaseConstraints(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})

	t.Run("PostgreSQL", func(t *testing.T) {
		pgDSN := "postgres://postgres:postgres@127.0.0.1/postgres?sslmode=disable"
		testDB, err := sqlx.Connect("postgres", pgDSN)
		if err != nil {
			t.Skipf("PostgreSQL not available: %v", err)
		}
		defer testDB.Close()

		schemaName := "test_constraints_" + strings.ReplaceAll(strings.ToLower(t.Name()), "/", "_")
		testDB.Exec("DROP SCHEMA IF EXISTS " + schemaName + " CASCADE")
		_, err = testDB.Exec("CREATE SCHEMA " + schemaName)
		if err != nil {
			t.Skipf("Cannot create test schema: %v", err)
		}
		defer testDB.Exec("DROP SCHEMA IF EXISTS " + schemaName + " CASCADE")

		testDSN := pgDSN + "&search_path=" + schemaName
		testDatabaseConstraints(t, "postgres", testDSN)
	})
}

func TestWorkflowsDirectory(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	db, err := Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Open failed: %v", err)
	}
	defer db.Close()

	workflowsDir := db.getWorkflowsDir()
	if workflowsDir == "" {
		t.Error("Workflows directory should not be empty")
	}

	if _, err := os.Stat(workflowsDir); os.IsNotExist(err) {
		t.Errorf("Workflows directory was not created: %s", workflowsDir)
	}

	expectedDir := filepath.Join(tempDir, "workflows")
	if workflowsDir != expectedDir {
		t.Errorf("Expected workflows dir %s, got %s", expectedDir, workflowsDir)
	}
}

func testDatabaseConstraints(t *testing.T, driver, dsn string) {
	db, err := Open(driver, dsn)
	if err != nil {
		if driver == "postgres" {
			t.Skipf("PostgreSQL not available: %v", err)
		} else {
			t.Fatalf("Failed to open database: %v", err)
		}
	}
	defer db.Close()

	tables := []string{"config", "workflow", "execution"}
	for _, table := range tables {
		var count int
		var query string

		if db.driver == "postgres" {
			query = "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = $1"
		} else {
			query = "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?"
		}

		err := db.Get(&count, query, table)
		if err != nil {
			t.Errorf("Failed to check if table %s exists: %v", table, err)
		}
		if count != 1 {
			t.Errorf("Table %s does not exist", table)
		}
	}

	t.Run("ConfigNameUniqueness", func(t *testing.T) {
		config1 := map[string]interface{}{
			"id":             "config-1",
			"name":           "unique-config",
			"api_keys":       `{"test": "key1"}`,
			"agent_settings": `{"timeout": 30}`,
			"created_at":     time.Now(),
			"updated_at":     time.Now(),
		}

		query := db.placeholder("INSERT INTO config (id, name, api_keys, agent_settings, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)")
		_, err := db.Exec(query, config1["id"], config1["name"], config1["api_keys"], config1["agent_settings"], config1["created_at"], config1["updated_at"])
		if err != nil {
			t.Fatalf("Failed to insert first config: %v", err)
		}

		config2 := map[string]interface{}{
			"id":             "config-2",
			"name":           "unique-config",
			"api_keys":       `{"test": "key2"}`,
			"agent_settings": `{"timeout": 60}`,
			"created_at":     time.Now(),
			"updated_at":     time.Now(),
		}

		query = db.placeholder("INSERT INTO config (id, name, api_keys, agent_settings, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)")
		_, err = db.Exec(query, config2["id"], config2["name"], config2["api_keys"], config2["agent_settings"], config2["created_at"], config2["updated_at"])
		if err == nil {
			t.Error("Expected unique constraint violation when inserting duplicate config name")
		}
		if !strings.Contains(err.Error(), "UNIQUE") && !strings.Contains(err.Error(), "duplicate") {
			t.Errorf("Expected uniqueness constraint error, got: %v", err)
		}
	})
}
