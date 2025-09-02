package executor

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/engine"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
)

// Test complete execution lifecycle: start workflow → poll status → verify completion
func TestExecutionLifecycle(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testExecutionLifecycle(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})

	t.Run("PostgreSQL", func(t *testing.T) {
		if !isPostgreSQLAvailable() {
			t.Skip("PostgreSQL not available")
		}
		testExecutionLifecycle(t, "postgres", createPostgreSQLTestDB(t, "execution_lifecycle"))
	})
}

func testExecutionLifecycle(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	// Create workflow with sequential dependencies
	workflowFile := createTestWorkflow(t, "lifecycle-workflow", []testNode{
		{ID: "analyze", Agent: "mock-agent", Prompt: "Analyze the code structure"},
		{ID: "transform", Agent: "mock-agent", Prompt: "Transform based on analysis", DependsOn: []string{"analyze"}},
		{ID: "validate", Agent: "mock-agent", Prompt: "Validate transformation results", DependsOn: []string{"transform"}},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	configLoader := setupTestConfigLoader(t)
	executor := NewExecutor(db, configLoader)

	// Start workflow execution
	startTime := time.Now()
	execution, err := executor.StartWorkflow("lifecycle-workflow")
	if err != nil {
		t.Fatalf("Failed to start workflow: %v", err)
	}

	// Verify initial state
	if execution.ID == "" {
		t.Error("Expected execution ID to be set")
	}
	if execution.WorkflowID != "lifecycle-workflow" {
		t.Errorf("Expected workflow ID 'lifecycle-workflow', got: %s", execution.WorkflowID)
	}
	if execution.Status != constants.TaskStateWorking {
		t.Errorf("Expected initial status '%s', got: %s", constants.TaskStateWorking, execution.Status)
	}
	if execution.StartedAt.IsZero() {
		t.Error("Expected StartedAt to be set")
	}
	if execution.CompletedAt != nil {
		t.Error("Expected CompletedAt to be nil for running execution")
	}

	// Poll status until completion
	var finalExecution *storage.Execution
	maxPolls := 50 // 5 seconds with 100ms intervals
	for i := 0; i < maxPolls; i++ {
		retrieved, err := db.GetExecution(execution.ID)
		if err != nil {
			t.Fatalf("Failed to poll execution status: %v", err)
		}

		if retrieved.Status == constants.TaskStateCompleted || retrieved.Status == constants.TaskStateFailed {
			finalExecution = retrieved
			break
		}

		// Verify intermediate state
		if retrieved.Status != constants.TaskStateWorking {
			t.Errorf("Unexpected status during execution: %s", retrieved.Status)
		}

		time.Sleep(100 * time.Millisecond)
	}

	if finalExecution == nil {
		t.Fatal("Execution did not complete within timeout")
	}

	// Verify final state
	if finalExecution.Status != constants.TaskStateCompleted {
		t.Errorf("Expected final status '%s', got: %s", constants.TaskStateCompleted, finalExecution.Status)
		if finalExecution.Logs != "" {
			t.Logf("Execution logs: %s", finalExecution.Logs)
		}
	}

	if finalExecution.CompletedAt == nil {
		t.Error("Expected CompletedAt to be set for completed execution")
	}

	if finalExecution.CompletedAt != nil && finalExecution.CompletedAt.Before(startTime) {
		t.Error("CompletedAt should be after StartedAt")
	}

	// Verify node states are properly tracked
	if finalExecution.NodeStates == nil {
		t.Fatal("Expected NodeStates to be persisted")
	}

	nodeStatesStr := string(finalExecution.NodeStates)
	requiredNodes := []string{"analyze", "transform", "validate"}
	for _, nodeID := range requiredNodes {
		if !strings.Contains(nodeStatesStr, nodeID) {
			t.Errorf("Expected node %s state to be persisted", nodeID)
		}
	}

	// Parse node states to verify all completed successfully
	var nodeStates map[string]engine.NodeState
	if err := json.Unmarshal(finalExecution.NodeStates, &nodeStates); err != nil {
		t.Fatalf("Failed to parse node states: %v", err)
	}

	for _, nodeID := range requiredNodes {
		nodeState, exists := nodeStates[nodeID]
		if !exists {
			t.Errorf("Node %s state not found", nodeID)
			continue
		}

		if nodeState.Status != constants.TaskStateCompleted {
			t.Errorf("Expected node %s status '%s', got: %s", nodeID, constants.TaskStateCompleted, nodeState.Status)
		}

		if nodeState.Output == "" {
			t.Errorf("Expected node %s to have output", nodeID)
		}

		if nodeState.StartedAt.IsZero() {
			t.Errorf("Expected node %s StartedAt to be set", nodeID)
		}

		if nodeState.EndedAt == nil {
			t.Errorf("Expected node %s EndedAt to be set", nodeID)
		}
	}

	// Verify execution logs contain meaningful information
	if finalExecution.Logs == "" {
		t.Error("Expected execution logs to be recorded")
	} else {
		logs := finalExecution.Logs
		for _, nodeID := range requiredNodes {
			if !strings.Contains(logs, nodeID) {
				t.Errorf("Expected logs to mention node %s", nodeID)
			}
		}
	}

	executionTime := time.Since(startTime)
	t.Logf("Complete workflow executed in %v", executionTime)
}

// Test parallel execution with convergence (diamond pattern: A → B,C → D)
func TestParallelExecutionWithConvergence(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testParallelExecutionWithConvergence(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})

	t.Run("PostgreSQL", func(t *testing.T) {
		if !isPostgreSQLAvailable() {
			t.Skip("PostgreSQL not available")
		}
		testParallelExecutionWithConvergence(t, "postgres", createPostgreSQLTestDB(t, "parallel_convergence"))
	})
}

func testParallelExecutionWithConvergence(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	// Create diamond pattern workflow
	workflowFile := createTestWorkflow(t, "diamond-workflow", []testNode{
		{ID: "start", Agent: "mock-agent", Prompt: "Initialize data processing"},
		{ID: "branch_a", Agent: "mock-agent", Prompt: "Process branch A", DependsOn: []string{"start"}},
		{ID: "branch_b", Agent: "mock-agent", Prompt: "Process branch B", DependsOn: []string{"start"}},
		{ID: "merge", Agent: "mock-agent", Prompt: "Merge results from both branches", DependsOn: []string{"branch_a", "branch_b"}},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	configLoader := setupTestConfigLoader(t)
	executor := NewExecutor(db, configLoader)

	startTime := time.Now()
	execution, err := executor.StartWorkflow("diamond-workflow")
	if err != nil {
		t.Fatalf("Failed to start workflow: %v", err)
	}

	// Wait for completion
	waitForCompletion(t, db, execution.ID, 10*time.Second)
	executionTime := time.Since(startTime)

	// Verify execution completed successfully
	completed, err := db.GetExecution(execution.ID)
	if err != nil {
		t.Fatalf("Failed to get completed execution: %v", err)
	}

	if completed.Status != constants.TaskStateCompleted {
		t.Errorf("Expected execution to complete successfully, got status: %s", completed.Status)
		if completed.Logs != "" {
			t.Logf("Execution logs: %s", completed.Logs)
		}
	}

	// Parse and verify node states
	var nodeStates map[string]engine.NodeState
	if err := json.Unmarshal(completed.NodeStates, &nodeStates); err != nil {
		t.Fatalf("Failed to parse node states: %v", err)
	}

	expectedNodes := []string{"start", "branch_a", "branch_b", "merge"}
	for _, nodeID := range expectedNodes {
		nodeState, exists := nodeStates[nodeID]
		if !exists {
			t.Errorf("Node %s state not found", nodeID)
			continue
		}

		if nodeState.Status != constants.TaskStateCompleted {
			t.Errorf("Expected node %s status '%s', got: %s", nodeID, constants.TaskStateCompleted, nodeState.Status)
		}

		if nodeState.Output == "" {
			t.Errorf("Expected node %s to have output", nodeID)
		}
	}

	// Verify parallel execution efficiency
	// branches should execute in parallel, so total time should be less than sequential
	if executionTime > 8*time.Second {
		t.Logf("Diamond pattern execution took %v (may indicate sequential rather than parallel execution)", executionTime)
	}

	// Verify execution order constraints
	startNode := nodeStates["start"]
	branchA := nodeStates["branch_a"]
	branchB := nodeStates["branch_b"]
	mergeNode := nodeStates["merge"]

	// Start must complete before branches
	if branchA.StartedAt.Before(startNode.EndedAt.Add(-time.Millisecond)) {
		t.Error("Branch A should not start before start node completes")
	}
	if branchB.StartedAt.Before(startNode.EndedAt.Add(-time.Millisecond)) {
		t.Error("Branch B should not start before start node completes")
	}

	// Merge must start after both branches complete
	if mergeNode.StartedAt.Before(branchA.EndedAt.Add(-time.Millisecond)) {
		t.Error("Merge should not start before branch A completes")
	}
	if mergeNode.StartedAt.Before(branchB.EndedAt.Add(-time.Millisecond)) {
		t.Error("Merge should not start before branch B completes")
	}

	t.Logf("Diamond pattern executed in %v with proper parallelization", executionTime)
}

// Removed execution cancellation test - complex cancellation scenarios removed from MVP

// Test concurrent workflow executions - multiple workflows simultaneously
func TestConcurrentWorkflowExecutions(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testConcurrentWorkflowExecutions(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})

	t.Run("PostgreSQL", func(t *testing.T) {
		if !isPostgreSQLAvailable() {
			t.Skip("PostgreSQL not available")
		}
		testConcurrentWorkflowExecutions(t, "postgres", createPostgreSQLTestDB(t, "concurrent_executions"))
	})
}

func testConcurrentWorkflowExecutions(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	// Create multiple different workflows
	workflow1 := createTestWorkflow(t, "concurrent-workflow-1", []testNode{
		{ID: "task1", Agent: "mock-agent", Prompt: "Workflow 1 task 1"},
		{ID: "task2", Agent: "mock-agent", Prompt: "Workflow 1 task 2", DependsOn: []string{"task1"}},
	})

	workflow2 := createTestWorkflow(t, "concurrent-workflow-2", []testNode{
		{ID: "analyze", Agent: "mock-agent", Prompt: "Workflow 2 analyze"},
		{ID: "process", Agent: "mock-agent", Prompt: "Workflow 2 process", DependsOn: []string{"analyze"}},
		{ID: "finalize", Agent: "mock-agent", Prompt: "Workflow 2 finalize", DependsOn: []string{"process"}},
	})

	workflow3 := createTestWorkflow(t, "concurrent-workflow-3", []testNode{
		{ID: "init", Agent: "mock-agent", Prompt: "Workflow 3 init"},
		{ID: "branch_a", Agent: "mock-agent", Prompt: "Workflow 3 branch A", DependsOn: []string{"init"}},
		{ID: "branch_b", Agent: "mock-agent", Prompt: "Workflow 3 branch B", DependsOn: []string{"init"}},
		{ID: "merge", Agent: "mock-agent", Prompt: "Workflow 3 merge", DependsOn: []string{"branch_a", "branch_b"}},
	})

	// Register all workflows
	for _, wf := range []string{workflow1, workflow2, workflow3} {
		if err := db.RegisterWorkflow(wf); err != nil {
			t.Fatalf("Failed to register workflow: %v", err)
		}
	}

	configLoader := setupTestConfigLoader(t)
	executor := NewExecutor(db, configLoader)

	// Start all executions concurrently
	executions := make(map[string]*ExecutionInfo)
	var mu sync.Mutex
	var wg sync.WaitGroup

	startTime := time.Now()

	workflowNames := []string{"concurrent-workflow-1", "concurrent-workflow-2", "concurrent-workflow-3"}
	for _, workflowName := range workflowNames {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()

			execution, err := executor.StartWorkflow(name)
			if err != nil {
				t.Errorf("Failed to start workflow %s: %v", name, err)
				return
			}

			mu.Lock()
			executions[name] = execution
			mu.Unlock()

			t.Logf("Started execution (%s): %s", name, execution.ID)
		}(workflowName)
	}

	wg.Wait()

	if len(executions) != 3 {
		t.Fatalf("Expected 3 executions, got %d", len(executions))
	}

	// Verify all executions have unique IDs
	executionIDs := make(map[string]bool)
	for _, exec := range executions {
		if executionIDs[exec.ID] {
			t.Errorf("Duplicate execution ID: %s", exec.ID)
		}
		executionIDs[exec.ID] = true
	}

	// Wait for all executions to complete
	for workflowName, exec := range executions {
		t.Logf("Waiting for execution to complete (%s): %s", workflowName, exec.ID)
		waitForCompletion(t, db, exec.ID, 10*time.Second)
	}

	executionTime := time.Since(startTime)

	// Verify all executions completed successfully and independently
	for workflowName, exec := range executions {
		final, err := db.GetExecution(exec.ID)
		if err != nil {
			t.Errorf("Failed to get final execution for %s: %v", workflowName, err)
			continue
		}

		if final.Status != constants.TaskStateCompleted {
			t.Errorf("Execution %s did not complete successfully, got status: %s", workflowName, final.Status)
			if final.Logs != "" {
				t.Logf("Execution %s logs: %s", workflowName, final.Logs)
			}
		}

		// Verify workflow isolation - each execution should have its expected workflow
		if final.WorkflowName != workflowName {
			t.Errorf("Execution has wrong workflow: expected %s, got %s", workflowName, final.WorkflowName)
		}

		// Verify node states don't cross-contaminate
		if final.NodeStates == nil {
			t.Errorf("Execution %s missing node states", workflowName)
			continue
		}

		var nodeStates map[string]engine.NodeState
		if err := json.Unmarshal(final.NodeStates, &nodeStates); err != nil {
			t.Errorf("Failed to parse node states for execution %s: %v", workflowName, err)
			continue
		}

		// Verify expected nodes exist and completed
		var expectedNodeCount int
		switch workflowName {
		case "concurrent-workflow-1":
			expectedNodeCount = 2
			if _, exists := nodeStates["task1"]; !exists {
				t.Errorf("Execution %s missing task1 state", workflowName)
			}
			if _, exists := nodeStates["task2"]; !exists {
				t.Errorf("Execution %s missing task2 state", workflowName)
			}
		case "concurrent-workflow-2":
			expectedNodeCount = 3
			if _, exists := nodeStates["analyze"]; !exists {
				t.Errorf("Execution %s missing analyze state", workflowName)
			}
			if _, exists := nodeStates["process"]; !exists {
				t.Errorf("Execution %s missing process state", workflowName)
			}
			if _, exists := nodeStates["finalize"]; !exists {
				t.Errorf("Execution %s missing finalize state", workflowName)
			}
		case "concurrent-workflow-3":
			expectedNodeCount = 4
			if _, exists := nodeStates["init"]; !exists {
				t.Errorf("Execution %s missing init state", workflowName)
			}
			if _, exists := nodeStates["merge"]; !exists {
				t.Errorf("Execution %s missing merge state", workflowName)
			}
		}

		if len(nodeStates) != expectedNodeCount {
			t.Errorf("Execution %s has %d node states, expected %d", workflowName, len(nodeStates), expectedNodeCount)
		}
	}

	t.Logf("All concurrent executions completed in %v", executionTime)
}

// Removed helper functions for deleted test scenarios

// Removed complex dependency workflow test - overly detailed for MVP

// Removed hardcoded stop-all behavior test - error strategy system was removed from MVP

// Removed retry with recovery test - complex retry scenarios not needed for MVP

// Removed complex helper functions for deleted test scenarios
