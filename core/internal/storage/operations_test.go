package storage

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/jmoiron/sqlx"
	_ "github.com/lib/pq"
	"gorm.io/datatypes"
)

func TestDatabaseSchema(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testDatabaseSchema(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})

	t.Run("PostgreSQL", func(t *testing.T) {
		pgDSN := "postgres://postgres:postgres@127.0.0.1/postgres?sslmode=disable"
		mainDB, err := sqlx.Connect("postgres", pgDSN)
		if err != nil {
			t.Skip("PostgreSQL not available")
		}
		defer mainDB.Close()

		testDBName := "schema_test_db"
		mainDB.Exec("DROP DATABASE IF EXISTS " + testDBName)
		_, err = mainDB.Exec("CREATE DATABASE " + testDBName)
		if err != nil {
			t.Skip("Cannot create test database")
		}
		defer mainDB.Exec("DROP DATABASE " + testDBName)

		testDSN := "postgres://postgres:postgres@127.0.0.1/" + testDBName + "?sslmode=disable"
		testDatabaseSchema(t, "postgres", testDSN)
	})
}

func TestConfigOperations(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testConfigOperations(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})

	t.Run("PostgreSQL", func(t *testing.T) {
		pgDSN := "postgres://postgres:postgres@127.0.0.1/postgres?sslmode=disable"
		mainDB, err := sqlx.Connect("postgres", pgDSN)
		if err != nil {
			t.Skip("PostgreSQL not available")
		}
		defer mainDB.Close()

		testDBName := "config_test_db"
		mainDB.Exec("DROP DATABASE IF EXISTS " + testDBName)
		_, err = mainDB.Exec("CREATE DATABASE " + testDBName)
		if err != nil {
			t.Skip("Cannot create test database")
		}
		defer mainDB.Exec("DROP DATABASE " + testDBName)

		testDSN := "postgres://postgres:postgres@127.0.0.1/" + testDBName + "?sslmode=disable"
		testConfigOperations(t, "postgres", testDSN)
	})
}

func TestWorkflowOperations(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testWorkflowOperations(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})

	t.Run("PostgreSQL", func(t *testing.T) {
		pgDSN := "postgres://postgres:postgres@127.0.0.1/postgres?sslmode=disable"
		mainDB, err := sqlx.Connect("postgres", pgDSN)
		if err != nil {
			t.Skip("PostgreSQL not available")
		}
		defer mainDB.Close()

		testDBName := "workflow_test_db"
		mainDB.Exec("DROP DATABASE IF EXISTS " + testDBName)
		_, err = mainDB.Exec("CREATE DATABASE " + testDBName)
		if err != nil {
			t.Skip("Cannot create test database")
		}
		defer mainDB.Exec("DROP DATABASE " + testDBName)

		testDSN := "postgres://postgres:postgres@127.0.0.1/" + testDBName + "?sslmode=disable"
		testWorkflowOperations(t, "postgres", testDSN)
	})
}

func TestExecutionOperations(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testExecutionOperations(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})

	t.Run("PostgreSQL", func(t *testing.T) {
		pgDSN := "postgres://postgres:postgres@127.0.0.1/postgres?sslmode=disable"
		mainDB, err := sqlx.Connect("postgres", pgDSN)
		if err != nil {
			t.Skip("PostgreSQL not available")
		}
		defer mainDB.Close()

		testDBName := "execution_test_db"
		mainDB.Exec("DROP DATABASE IF EXISTS " + testDBName)
		_, err = mainDB.Exec("CREATE DATABASE " + testDBName)
		if err != nil {
			t.Skip("Cannot create test database")
		}
		defer mainDB.Exec("DROP DATABASE " + testDBName)

		testDSN := "postgres://postgres:postgres@127.0.0.1/" + testDBName + "?sslmode=disable"
		testExecutionOperations(t, "postgres", testDSN)
	})
}

func testConfigOperations(t *testing.T, driver, dsn string) {
	db, err := Open(driver, dsn)
	if err != nil {
		if driver == "postgres" {
			t.Skipf("PostgreSQL not available: %v", err)
		} else {
			t.Fatalf("Failed to open database: %v", err)
		}
	}
	defer db.Close()

	t.Run("UpsertConfig", func(t *testing.T) {
		// Create initial config
		original := &Config{
			ID:            "test-config-1",
			Name:          "primary",
			APIKeys:       datatypes.JSON([]byte(`{"agent-2": "key-1"}`)),
			AgentSettings: datatypes.JSON([]byte(`{"timeout": 60}`)),
		}

		err := db.CreateConfig(original)
		if err != nil {
			t.Fatalf("Failed to create original config: %v", err)
		}

		// Test upsert behavior - should update existing config with same name
		updated := &Config{
			ID:            "test-config-2",
			Name:          "primary", // Same name to trigger upsert
			APIKeys:       datatypes.JSON([]byte(`{"agent-2": "key-2-updated"}`)),
			AgentSettings: datatypes.JSON([]byte(`{"timeout": 120}`)),
		}

		err = db.CreateConfig(updated)
		if err != nil {
			t.Fatalf("Failed to upsert config: %v", err)
		}

		retrieved, err := db.GetConfig("primary")
		if err != nil {
			t.Fatalf("Failed to get updated config: %v", err)
		}

		if string(retrieved.APIKeys) != `{"agent-2": "key-2-updated"}` {
			t.Errorf("Config was not properly upserted, got API keys: %s", string(retrieved.APIKeys))
		}
	})

	t.Run("GetNonexistentConfig", func(t *testing.T) {
		_, err := db.GetConfig("nonexistent")
		if err == nil {
			t.Error("Expected error when getting nonexistent config")
		}
		if !strings.Contains(err.Error(), "config not found") {
			t.Errorf("Expected 'config not found' error, got: %v", err)
		}
	})
}

func testWorkflowOperations(t *testing.T, driver, dsn string) {
	db, err := Open(driver, dsn)
	if err != nil {
		if driver == "postgres" {
			t.Skipf("PostgreSQL not available: %v", err)
		} else {
			t.Fatalf("Failed to open database: %v", err)
		}
	}
	defer db.Close()

	tempDir := t.TempDir()

	// Set up test workflow for subsequent tests
	workflowFile := filepath.Join(tempDir, "simple-workflow.yaml")
	yamlContent := `name: simple-workflow
description: A basic workflow for testing
nodes:
  - name: analyze
    type: code
    prompt: "Analyze the input"
  - name: transform
    type: code
    depends_on: [analyze]
    prompt: "Transform based on analysis"`

	if err := os.WriteFile(workflowFile, []byte(yamlContent), 0644); err != nil {
		t.Fatalf("Failed to write workflow file: %v", err)
	}

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	t.Run("UpdateWorkflowFile", func(t *testing.T) {
		updatedYAML := `name: simple-workflow
description: Updated workflow with more complexity
nodes:
  - name: analyze
    type: code
    prompt: "Deep analysis of input"
  - name: transform
    type: code
    depends_on: [analyze]
    prompt: "Advanced transformation"
  - name: validate
    type: code
    depends_on: [transform]
    prompt: "Validate results"`

		err := db.UpdateWorkflowFile("simple-workflow", []byte(updatedYAML))
		if err != nil {
			t.Fatalf("Failed to update workflow file: %v", err)
		}

		meta, err := db.GetWorkflowMetadata("simple-workflow")
		if err != nil {
			t.Fatalf("Failed to get updated workflow metadata: %v", err)
		}

		if meta.Description != "Updated workflow with more complexity" {
			t.Errorf("Expected updated description, got %s", meta.Description)
		}
		if meta.Version != 2 {
			t.Errorf("Expected version to increment to 2, got %d", meta.Version)
		}

		loadedYAML, err := db.LoadWorkflowYAML("simple-workflow")
		if err != nil {
			t.Fatalf("Failed to load updated YAML: %v", err)
		}

		if string(loadedYAML) != updatedYAML {
			t.Error("File content was not properly updated")
		}
	})

	t.Run("InvalidYAMLHandling", func(t *testing.T) {
		invalidFile := filepath.Join(tempDir, "invalid.yaml")
		err := os.WriteFile(invalidFile, []byte("invalid: yaml: content: [["), 0644)
		if err != nil {
			t.Fatalf("Failed to write invalid YAML file: %v", err)
		}

		err = db.RegisterWorkflow(invalidFile)
		if err == nil {
			t.Error("Expected error when registering invalid YAML")
		}
		if !strings.Contains(err.Error(), "failed to parse workflow YAML") {
			t.Errorf("Expected YAML parse error, got: %v", err)
		}
	})

	t.Run("MissingNameHandling", func(t *testing.T) {
		noNameFile := filepath.Join(tempDir, "no-name.yaml")
		err := os.WriteFile(noNameFile, []byte("description: Missing name field"), 0644)
		if err != nil {
			t.Fatalf("Failed to write no-name YAML file: %v", err)
		}

		err = db.RegisterWorkflow(noNameFile)
		if err == nil {
			t.Error("Expected error when workflow name is missing")
		}
		if !strings.Contains(err.Error(), "workflow name is required") {
			t.Errorf("Expected 'workflow name is required' error, got: %v", err)
		}
	})

	t.Run("NameMismatchOnUpdate", func(t *testing.T) {
		mismatchYAML := `name: different-name
description: Wrong name for update`

		err := db.UpdateWorkflowFile("simple-workflow", []byte(mismatchYAML))
		if err == nil {
			t.Error("Expected error when updating with mismatched name")
		}
		if !strings.Contains(err.Error(), "workflow name mismatch") {
			t.Errorf("Expected name mismatch error, got: %v", err)
		}
	})

}

func testExecutionOperations(t *testing.T, driver, dsn string) {
	db, err := Open(driver, dsn)
	if err != nil {
		if driver == "postgres" {
			t.Skipf("PostgreSQL not available: %v", err)
		} else {
			t.Fatalf("Failed to open database: %v", err)
		}
	}
	defer db.Close()

	tempDir := t.TempDir()
	workflowFile := filepath.Join(tempDir, "test-workflow.yaml")
	yamlContent := `name: test-workflow
description: Test workflow for execution tests`

	err = os.WriteFile(workflowFile, []byte(yamlContent), 0644)
	if err != nil {
		t.Fatalf("Failed to write test workflow file: %v", err)
	}

	err = db.RegisterWorkflow(workflowFile)
	if err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	t.Run("CreateAndUpdateExecution", func(t *testing.T) {
		execution := &Execution{
			ID:           "exec-001",
			WorkflowName: "test-workflow",
			Status:       constants.TaskStateWorking,
			NodeStates:   datatypes.JSON([]byte(`{"node1": "` + constants.TaskStateCompleted + `", "node2": "` + constants.TaskStateWorking + `"}`)),
			A2ATasks:     datatypes.JSON("{}"),
			Logs:         "Starting execution\nNode1 completed",
		}

		err := db.CreateExecution(execution)
		if err != nil {
			t.Fatalf("Failed to create execution: %v", err)
		}

		retrieved, err := db.GetExecution("exec-001")
		if err != nil {
			t.Fatalf("Failed to get execution: %v", err)
		}

		if retrieved.Status != constants.TaskStateWorking {
			t.Errorf("Expected status '%s', got %s", constants.TaskStateWorking, retrieved.Status)
		}
		if retrieved.WorkflowName != "test-workflow" {
			t.Errorf("Expected workflow name 'test-workflow', got %s", retrieved.WorkflowName)
		}
		if retrieved.CompletedAt != nil {
			t.Error("Expected CompletedAt to be nil for running execution")
		}

		execution.Status = constants.TaskStateCompleted
		execution.Logs += "\nNode2 completed\nExecution finished successfully"
		execution.NodeStates = datatypes.JSON([]byte(`{"node1": "` + constants.TaskStateCompleted + `", "node2": "` + constants.TaskStateCompleted + `"}`))
		completedAt := time.Now()
		execution.CompletedAt = &completedAt

		err = db.UpdateExecution(execution)
		if err != nil {
			t.Fatalf("Failed to update execution: %v", err)
		}

		updated, err := db.GetExecution("exec-001")
		if err != nil {
			t.Fatalf("Failed to get updated execution: %v", err)
		}

		if updated.Status != constants.TaskStateCompleted {
			t.Errorf("Expected status '%s', got %s", constants.TaskStateCompleted, updated.Status)
		}
		if updated.CompletedAt == nil {
			t.Error("Expected CompletedAt to be set after completion")
		}
		if !strings.Contains(updated.Logs, "finished successfully") {
			t.Error("Expected logs to contain completion message")
		}
	})

	t.Run("ListExecutionsForNonexistentWorkflow", func(t *testing.T) {
		executions, err := db.ListExecutions("nonexistent-workflow")
		if err != nil {
			t.Fatalf("Listing executions should not error for nonexistent workflow: %v", err)
		}
		if len(executions) != 0 {
			t.Errorf("Expected 0 executions for nonexistent workflow, got %d", len(executions))
		}
	})
}

func testDatabaseSchema(t *testing.T, driver, dsn string) {
	db, err := Open(driver, dsn)
	if err != nil {
		if driver == "postgres" {
			t.Skipf("PostgreSQL not available: %v", err)
		} else {
			t.Fatalf("Failed to open database: %v", err)
		}
	}
	defer db.Close()

	// Test that all expected tables exist with correct structure
	tables := []string{"config", "workflow", "execution"}

	for _, table := range tables {
		var count int
		var query string

		if driver == "postgres" {
			query = "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = $1"
		} else {
			query = "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name = ?"
		}

		err = db.Get(&count, query, table)
		if err != nil {
			t.Fatalf("Failed to check table %s existence: %v", table, err)
		}

		if count != 1 {
			t.Errorf("Expected table %s to exist, found %d instances", table, count)
		}
	}

	// Test basic constraint - config name should be unique
	config1 := &Config{
		ID:            "test-1",
		Name:          "test-constraint",
		APIKeys:       datatypes.JSON([]byte(`{}`)),
		AgentSettings: datatypes.JSON([]byte(`{}`)),
	}

	err = db.CreateConfig(config1)
	if err != nil {
		t.Fatalf("Failed to create first config: %v", err)
	}

	// This should work due to upsert behavior
	config2 := &Config{
		ID:            "test-2",
		Name:          "test-constraint", // Same name
		APIKeys:       datatypes.JSON([]byte(`{"updated": true}`)),
		AgentSettings: datatypes.JSON([]byte(`{}`)),
	}

	err = db.CreateConfig(config2)
	if err != nil {
		t.Fatalf("Upsert should not fail: %v", err)
	}
}
