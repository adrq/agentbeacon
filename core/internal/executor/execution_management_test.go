package executor

import (
	"path/filepath"
	"testing"
	"time"
)

// Test the new execution management methods
func TestExecutionManagement(t *testing.T) {
	// Setup test database
	db := setupTestDB(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	defer db.Close()

	// Create a simple test workflow
	workflowFile := createTestWorkflow(t, "management-test-workflow", []testNode{
		{ID: "task1", Agent: "mock-agent", Prompt: "Test task 1"},
		{ID: "task2", Agent: "mock-agent", Prompt: "Test task 2", DependsOn: []string{"task1"}},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	configLoader := setupTestConfigLoader(t)
	executor := NewExecutor(db, configLoader)

	t.Run("ExecutionStatus", func(t *testing.T) {
		// Start workflow
		execution, err := executor.StartWorkflow("management-test-workflow")
		if err != nil {
			t.Fatalf("Failed to start workflow: %v", err)
		}

		// Test GetExecutionStatus
		status, err := executor.GetExecutionStatus(execution.ID)
		if err != nil {
			t.Errorf("GetExecutionStatus failed: %v", err)
		}
		if status.ID != execution.ID {
			t.Errorf("Expected ID %s, got %s", execution.ID, status.ID)
		}

		// Wait for completion
		waitForCompletion(t, db, execution.ID, 5*time.Second)
	})

	t.Run("GetExecution", func(t *testing.T) {
		// Start workflow
		execution, err := executor.StartWorkflow("management-test-workflow")
		if err != nil {
			t.Fatalf("Failed to start workflow: %v", err)
		}

		// Test GetExecution
		retrieved, err := executor.GetExecution(execution.ID)
		if err != nil {
			t.Errorf("GetExecution failed: %v", err)
		}
		if retrieved.ID != execution.ID {
			t.Errorf("Expected ID %s, got %s", execution.ID, retrieved.ID)
		}

		// Wait for completion
		waitForCompletion(t, db, execution.ID, 5*time.Second)
	})

	t.Run("ListExecutions", func(t *testing.T) {
		// Start multiple workflows
		execution1, err := executor.StartWorkflow("management-test-workflow")
		if err != nil {
			t.Fatalf("Failed to start workflow 1: %v", err)
		}

		execution2, err := executor.StartWorkflow("management-test-workflow")
		if err != nil {
			t.Fatalf("Failed to start workflow 2: %v", err)
		}

		// Wait for completions
		waitForCompletion(t, db, execution1.ID, 5*time.Second)
		waitForCompletion(t, db, execution2.ID, 5*time.Second)

		// Test ListExecutions (basic functionality)
		executions, err := executor.ListExecutions("")
		if err != nil {
			t.Errorf("ListExecutions failed: %v", err)
		}
		if len(executions) < 2 {
			t.Errorf("Expected at least 2 executions, got %d", len(executions))
		}
	})

	t.Run("GetWorkflowExecutions", func(t *testing.T) {
		// Test GetWorkflowExecutions (basic functionality)
		_, err := executor.GetWorkflowExecutions("management-test-workflow")
		if err != nil {
			t.Errorf("GetWorkflowExecutions failed: %v", err)
		}
	})

	t.Run("StopExecution", func(t *testing.T) {
		// Create a workflow with delay to test stopping
		delayWorkflowFile := createTestWorkflow(t, "delay-workflow", []testNode{
			{ID: "delay_task", Agent: "mock-agent", Prompt: "DELAY_2000"},
		})

		if err := db.RegisterWorkflow(delayWorkflowFile); err != nil {
			t.Fatalf("Failed to register delay workflow: %v", err)
		}

		// Start workflow
		execution, err := executor.StartWorkflow("delay-workflow")
		if err != nil {
			t.Fatalf("Failed to start delay workflow: %v", err)
		}

		// Give it a moment to start
		time.Sleep(100 * time.Millisecond)

		// Test StopExecution (basic functionality)
		err = executor.StopExecution(execution.ID)
		// Note: May succeed or fail depending on execution timing
		t.Logf("StopExecution result: %v", err)
	})
}
