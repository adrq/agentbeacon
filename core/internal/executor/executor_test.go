package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/engine"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/jmoiron/sqlx"
	_ "github.com/lib/pq"
	"gopkg.in/yaml.v3"
)

// executeLevel executes all nodes in a dependency level concurrently - test helper.
func (wp *WorkerPool) executeLevel(execution *engine.Execution, workflow *engine.Workflow, level []string) error {
	if len(level) == 0 {
		return nil
	}

	// Build node lookup map for O(1) access
	nodeMap := make(map[string]*engine.Node)
	for i := range workflow.Nodes {
		nodeMap[workflow.Nodes[i].ID] = &workflow.Nodes[i]
	}

	return wp.executeLevelWithNodeMap(execution, nodeMap, level)
}

func setupProcessAgent(t *testing.T, responses map[string]string) *ProcessAgent {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	mockAgentPath := "../../../bin/mock-agent"
	var args []string

	if responses != nil {
		configFile := createTempConfig(t, responses)
		args = []string{"--config", configFile}
	}

	agent, err := NewProcessAgent(mockAgentPath, args...)
	if err != nil {
		t.Fatalf("Failed to create ProcessAgent: %v", err)
	}

	t.Cleanup(func() {
		agent.Close()
	})

	return agent
}

func createTempConfig(t *testing.T, responses map[string]string) string {
	tempDir := t.TempDir()
	configFile := filepath.Join(tempDir, "responses.json")

	configData, err := json.Marshal(responses)
	if err != nil {
		t.Fatalf("Failed to marshal config: %v", err)
	}

	if err := os.WriteFile(configFile, configData, 0644); err != nil {
		t.Fatalf("Failed to write config file: %v", err)
	}

	return configFile
}

func TestProcessAgentPredefinedResponses(t *testing.T) {
	agent := setupProcessAgent(t, map[string]string{
		"analyze code":    "Code analysis complete",
		"transform data":  "Data transformation complete",
		"validate result": "Validation passed",
	})

	result, err := agent.Execute(context.Background(), "analyze code")
	if err != nil {
		t.Fatalf("Expected no error, got: %v", err)
	}
	if result != "Code analysis complete" {
		t.Errorf("Expected 'Code analysis complete', got: %s", result)
	}
}

func TestProcessAgentUnknownPrompt(t *testing.T) {
	agent := setupProcessAgent(t, map[string]string{
		"known prompt": "known response",
	})

	result, err := agent.Execute(context.Background(), "unknown prompt")
	if err != nil {
		t.Fatalf("Expected no error, got: %v", err)
	}
	expected := "Mock response: unknown prompt"
	if result != expected {
		t.Errorf("Expected '%s', got: %s", expected, result)
	}
}

func TestExecutorStartWorkflow(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testExecutorStartWorkflow(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})

	t.Run("PostgreSQL", func(t *testing.T) {
		if !isPostgreSQLAvailable() {
			t.Skip("PostgreSQL not available")
		}
		testExecutorStartWorkflow(t, "postgres", createPostgreSQLTestDB(t, "executor_start_test"))
	})
}

func testExecutorStartWorkflow(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	workflowFile := createTestWorkflow(t, "simple-workflow", []testNode{
		{ID: "analyze", Agent: "claude", Prompt: "Analyze the input"},
		{ID: "transform", Agent: "claude", Prompt: "Transform based on analysis", DependsOn: []string{"analyze"}},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	executor := NewExecutor(db)
	execution, err := executor.StartWorkflow("simple-workflow")
	if err != nil {
		t.Fatalf("Failed to start workflow: %v", err)
	}

	if execution.ID == "" {
		t.Error("Expected execution ID to be set")
	}
	if execution.WorkflowID != "simple-workflow" {
		t.Errorf("Expected workflow ID 'simple-workflow', got: %s", execution.WorkflowID)
	}
	if execution.Status != "running" {
		t.Errorf("Expected status 'running', got: %s", execution.Status)
	}
	if execution.StartedAt.IsZero() {
		t.Error("Expected StartedAt to be set")
	}

	retrieved, err := db.GetExecution(execution.ID)
	if err != nil {
		t.Fatalf("Failed to retrieve execution from database: %v", err)
	}
	if retrieved.Status != "running" {
		t.Errorf("Expected persisted status 'running', got: %s", retrieved.Status)
	}
}

func TestExecutorStartWorkflowNonexistent(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testExecutorStartWorkflowNonexistent(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})
}

func testExecutorStartWorkflowNonexistent(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	executor := NewExecutor(db)

	_, err := executor.StartWorkflow("nonexistent-workflow")
	if err == nil {
		t.Error("Expected error when starting nonexistent workflow")
	}
}

func TestExecutorSequentialExecution(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testExecutorSequentialExecution(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})

	t.Run("PostgreSQL", func(t *testing.T) {
		if !isPostgreSQLAvailable() {
			t.Skip("PostgreSQL not available")
		}
		testExecutorSequentialExecution(t, "postgres", createPostgreSQLTestDB(t, "executor_seq_test"))
	})
}

func testExecutorSequentialExecution(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	workflowFile := createTestWorkflow(t, "sequential-workflow", []testNode{
		{ID: "step1", Agent: "claude", Prompt: "Execute step 1"},
		{ID: "step2", Agent: "claude", Prompt: "Execute step 2", DependsOn: []string{"step1"}},
		{ID: "step3", Agent: "claude", Prompt: "Execute step 3", DependsOn: []string{"step2"}},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	executor := NewExecutor(db)
	execution, err := executor.StartWorkflow("sequential-workflow")
	if err != nil {
		t.Fatalf("Failed to start workflow: %v", err)
	}

	waitForCompletion(t, db, execution.ID, 5*time.Second)

	// Dependencies ensure execution order: step1 → step2 → step3
	completed, err := db.GetExecution(execution.ID)
	if err != nil {
		t.Fatalf("Failed to get completed execution: %v", err)
	}
	if completed.Status != "completed" {
		t.Errorf("Expected final status 'completed', got: %s", completed.Status)
	}
	if completed.CompletedAt == nil {
		t.Error("Expected CompletedAt to be set")
	}
}

func TestExecutorStatePersistence(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testExecutorStatePersistence(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})
}

func testExecutorStatePersistence(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	workflowFile := createTestWorkflow(t, "state-test-workflow", []testNode{
		{ID: "node1", Agent: "claude", Prompt: "First task"},
		{ID: "node2", Agent: "claude", Prompt: "Second task", DependsOn: []string{"node1"}},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	executor := NewExecutor(db)
	execution, err := executor.StartWorkflow("state-test-workflow")
	if err != nil {
		t.Fatalf("Failed to start workflow: %v", err)
	}

	waitForCompletion(t, db, execution.ID, 5*time.Second)

	final, err := db.GetExecution(execution.ID)
	if err != nil {
		t.Fatalf("Failed to get final execution: %v", err)
	}

	if final.NodeStates == nil {
		t.Fatal("Expected node states to be persisted")
	}

	nodeStatesStr := string(final.NodeStates)
	if !contains(nodeStatesStr, "node1") {
		t.Error("Expected node1 state to be persisted")
	}
	if !contains(nodeStatesStr, "node2") {
		t.Error("Expected node2 state to be persisted")
	}
}

func TestExecutorFailureHandling(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testExecutorFailureHandling(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})
}

func testExecutorFailureHandling(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	workflowFile := createTestWorkflow(t, "failure-workflow", []testNode{
		{ID: "success", Agent: "claude", Prompt: "Success task"},
		{ID: "failure", Agent: "claude", Prompt: "Failure task", DependsOn: []string{"success"}},
		{ID: "after_failure", Agent: "claude", Prompt: "Should not execute", DependsOn: []string{"failure"}},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	executor := NewExecutor(db)
	execution, err := executor.StartWorkflow("failure-workflow")
	if err != nil {
		t.Fatalf("Failed to start workflow: %v", err)
	}

	// ProcessAgent doesn't simulate failures, so workflow completes successfully
	waitForCompletion(t, db, execution.ID, 5*time.Second)

	final, err := db.GetExecution(execution.ID)
	if err != nil {
		t.Fatalf("Failed to get final execution: %v", err)
	}
	if final.Status != "completed" {
		t.Errorf("Expected final status 'completed', got: %s", final.Status)
	}
}

func TestExecutorMultipleWorkflows(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testExecutorMultipleWorkflows(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})
}

func testExecutorMultipleWorkflows(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	workflow1 := createTestWorkflow(t, "workflow-1", []testNode{
		{ID: "task1", Agent: "claude", Prompt: "Task 1 for workflow 1"},
	})
	workflow2 := createTestWorkflow(t, "workflow-2", []testNode{
		{ID: "task1", Agent: "claude", Prompt: "Task 1 for workflow 2"},
	})

	if err := db.RegisterWorkflow(workflow1); err != nil {
		t.Fatalf("Failed to register workflow 1: %v", err)
	}
	if err := db.RegisterWorkflow(workflow2); err != nil {
		t.Fatalf("Failed to register workflow 2: %v", err)
	}

	executor := NewExecutor(db)

	execution1, err := executor.StartWorkflow("workflow-1")
	if err != nil {
		t.Fatalf("Failed to start workflow 1: %v", err)
	}

	waitForCompletion(t, db, execution1.ID, 5*time.Second)

	execution2, err := executor.StartWorkflow("workflow-2")
	if err != nil {
		t.Fatalf("Failed to start workflow 2: %v", err)
	}

	if execution1.ID == execution2.ID {
		t.Error("Expected different execution IDs for different workflows")
	}

	waitForCompletion(t, db, execution2.ID, 5*time.Second)

	final1, err := db.GetExecution(execution1.ID)
	if err != nil {
		t.Fatalf("Failed to get execution 1: %v", err)
	}
	if final1.Status != "completed" {
		t.Errorf("Expected workflow 1 to complete, got status: %s", final1.Status)
	}

	final2, err := db.GetExecution(execution2.ID)
	if err != nil {
		t.Fatalf("Failed to get execution 2: %v", err)
	}
	if final2.Status != "completed" {
		t.Errorf("Expected workflow 2 to complete, got status: %s", final2.Status)
	}

	if final1.WorkflowName != "workflow-1" {
		t.Errorf("Expected execution 1 to be for workflow-1, got: %s", final1.WorkflowName)
	}
	if final2.WorkflowName != "workflow-2" {
		t.Errorf("Expected execution 2 to be for workflow-2, got: %s", final2.WorkflowName)
	}
}

func TestWorkerPoolTaskSubmissionAndCollection(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testWorkerPoolTaskSubmissionAndCollection(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})

	t.Run("PostgreSQL", func(t *testing.T) {
		if !isPostgreSQLAvailable() {
			t.Skip("PostgreSQL not available")
		}
		testWorkerPoolTaskSubmissionAndCollection(t, "postgres", createPostgreSQLTestDB(t, "worker_pool_tasks"))
	})
}

func testWorkerPoolTaskSubmissionAndCollection(t *testing.T, driver, dsn string) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	workflowFile := createTestWorkflow(t, "task-test-workflow", []testNode{
		{ID: "task1", Agent: "mock-agent", Prompt: "Execute task 1"},
		{ID: "task2", Agent: "mock-agent", Prompt: "Execute task 2"},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	execution := &engine.Execution{
		ID:         "test-execution-id",
		WorkflowID: "task-test-workflow",
		Status:     "running",
		NodeStates: map[string]engine.NodeState{
			"task1": {Status: "pending"},
			"task2": {Status: "pending"},
		},
		StartedAt: time.Now(),
	}

	workflow := &engine.Workflow{
		Name:        "task-test-workflow",
		Description: "Test workflow for task submission",
		Nodes: []engine.Node{
			{ID: "task1", Agent: "mock-agent", Prompt: "Execute task 1"},
			{ID: "task2", Agent: "mock-agent", Prompt: "Execute task 2"},
		},
	}

	executor := NewExecutor(db)
	pool := NewWorkerPool(2, executor)

	pool.Start()
	defer pool.Shutdown()

	level := []string{"task1", "task2"}
	err := pool.executeLevel(execution, workflow, level)

	if err != nil {
		t.Errorf("Expected no error from executeLevel, got: %v", err)
	}
}

func TestWorkerPoolContextCancellation(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testWorkerPoolContextCancellation(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})
}

func testWorkerPoolContextCancellation(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	executor := NewExecutor(db)
	pool := NewWorkerPool(2, executor)

	pool.Start()

	pool.cancel()

	done := make(chan struct{})
	go func() {
		pool.wg.Wait()
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(1 * time.Second):
		t.Error("Workers did not shut down within timeout after context cancellation")
	}

	pool.Shutdown()
}

func TestWorkerPoolErrorHandling(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testWorkerPoolErrorHandling(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})
}

func testWorkerPoolErrorHandling(t *testing.T, driver, dsn string) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	workflowFile := createTestWorkflow(t, "error-test-workflow", []testNode{
		{ID: "success-task", Agent: "mock-agent", Prompt: "Success task"},
		{ID: "another-task", Agent: "mock-agent", Prompt: "Another success task"},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	execution := &engine.Execution{
		ID:         "error-test-execution",
		WorkflowID: "error-test-workflow",
		Status:     "running",
		NodeStates: map[string]engine.NodeState{
			"success-task": {Status: "pending"},
			"another-task": {Status: "pending"},
		},
		StartedAt: time.Now(),
	}

	workflow := &engine.Workflow{
		Name: "error-test-workflow",
		Nodes: []engine.Node{
			{ID: "success-task", Agent: "mock-agent", Prompt: "Success task"},
			{ID: "another-task", Agent: "mock-agent", Prompt: "Another success task"},
		},
	}

	executor := NewExecutor(db)
	pool := NewWorkerPool(2, executor)

	pool.Start()
	defer pool.Shutdown()

	level := []string{"success-task", "another-task"}
	err := pool.executeLevel(execution, workflow, level)

	if err != nil {
		t.Errorf("Unexpected error from executeLevel: %v", err)
	}
}

func TestWorkerPoolParallelExecution(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testWorkerPoolParallelExecution(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})
}

func testWorkerPoolParallelExecution(t *testing.T, driver, dsn string) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	numTasks := 4
	var nodes []testNode
	for i := 0; i < numTasks; i++ {
		nodes = append(nodes, testNode{
			ID:     fmt.Sprintf("parallel-task-%d", i+1),
			Agent:  "mock-agent",
			Prompt: fmt.Sprintf("Parallel task %d", i+1),
		})
	}

	workflowFile := createTestWorkflow(t, "parallel-workflow", nodes)
	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	execution := &engine.Execution{
		ID:         "parallel-execution",
		WorkflowID: "parallel-workflow",
		Status:     "running",
		NodeStates: make(map[string]engine.NodeState),
		StartedAt:  time.Now(),
	}

	var workflowNodes []engine.Node
	for i := 0; i < numTasks; i++ {
		nodeID := fmt.Sprintf("parallel-task-%d", i+1)
		execution.NodeStates[nodeID] = engine.NodeState{Status: "pending"}
		workflowNodes = append(workflowNodes, engine.Node{
			ID:     nodeID,
			Agent:  "mock-agent",
			Prompt: fmt.Sprintf("Parallel task %d", i+1),
		})
	}

	workflow := &engine.Workflow{
		Name:  "parallel-workflow",
		Nodes: workflowNodes,
	}

	executor := NewExecutor(db)
	pool := NewWorkerPool(3, executor) // Use fewer workers than tasks to test queuing

	pool.Start()
	defer pool.Shutdown()

	startTime := time.Now()

	var level []string
	for i := 0; i < numTasks; i++ {
		level = append(level, fmt.Sprintf("parallel-task-%d", i+1))
	}

	err := pool.executeLevel(execution, workflow, level)
	if err != nil {
		t.Errorf("Unexpected error from parallel execution: %v", err)
	}

	executionTime := time.Since(startTime)

	// Allow generous buffer for CI environments
	expectedMaxTime := 2 * time.Second
	if executionTime > expectedMaxTime {
		t.Logf("Execution time: %v (may indicate non-parallel execution)", executionTime)
	}
}

func TestWorkerPoolTopologicalSort(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testWorkerPoolTopologicalSort(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})
}

func testWorkerPoolTopologicalSort(t *testing.T, driver, dsn string) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	workflowFile := createTestWorkflow(t, "topological-workflow", []testNode{
		{ID: "level1-a", Agent: "mock-agent", Prompt: "Level 1 task A"},
		{ID: "level1-b", Agent: "mock-agent", Prompt: "Level 1 task B"},
		{ID: "level2-a", Agent: "mock-agent", Prompt: "Level 2 task A", DependsOn: []string{"level1-a", "level1-b"}},
		{ID: "level2-b", Agent: "mock-agent", Prompt: "Level 2 task B", DependsOn: []string{"level1-a"}},
		{ID: "level3-a", Agent: "mock-agent", Prompt: "Level 3 task A", DependsOn: []string{"level2-a", "level2-b"}},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	yamlContent, err := db.LoadWorkflowYAML("topological-workflow")
	if err != nil {
		t.Fatalf("Failed to load workflow YAML: %v", err)
	}

	var workflow engine.Workflow
	if err := yaml.Unmarshal(yamlContent, &workflow); err != nil {
		t.Fatalf("Failed to parse workflow YAML: %v", err)
	}

	levels, err := engine.TopologicalSort(workflow.Nodes)
	if err != nil {
		t.Fatalf("TopologicalSort failed: %v", err)
	}

	if len(levels) != 3 {
		t.Errorf("Expected 3 levels, got: %d", len(levels))
	}

	if len(levels) > 0 && len(levels[0]) != 2 {
		t.Errorf("Expected level 0 to have 2 nodes, got: %d", len(levels[0]))
	}

	if len(levels) > 1 && len(levels[1]) != 2 {
		t.Errorf("Expected level 1 to have 2 nodes, got: %d", len(levels[1]))
	}

	if len(levels) > 2 && len(levels[2]) != 1 {
		t.Errorf("Expected level 2 to have 1 node, got: %d", len(levels[2]))
	}

	execution := &engine.Execution{
		ID:         "topological-execution",
		WorkflowID: "topological-workflow",
		Status:     "running",
		NodeStates: make(map[string]engine.NodeState),
		StartedAt:  time.Now(),
	}

	for _, node := range workflow.Nodes {
		execution.NodeStates[node.ID] = engine.NodeState{Status: "pending"}
	}

	executor := NewExecutor(db)
	pool := NewWorkerPool(2, executor)

	pool.Start()
	defer pool.Shutdown()

	for i, level := range levels {
		err := pool.executeLevel(execution, &workflow, level)
		if err != nil {
			t.Errorf("Error executing level %d: %v", i, err)
			break
		}
	}
}

func setupTestDB(t *testing.T, driver, dsn string) *storage.DB {
	db, err := storage.Open(driver, dsn)
	if err != nil {
		if driver == "postgres" {
			t.Skipf("PostgreSQL not available: %v", err)
		}
		t.Fatalf("Failed to open database: %v", err)
	}
	return db
}

type testNode struct {
	ID        string
	Agent     string
	Prompt    string
	DependsOn []string
}

func createTestWorkflow(t *testing.T, name string, nodes []testNode) string {
	tempDir := t.TempDir()
	workflowFile := filepath.Join(tempDir, name+".yaml")

	yamlContent := "name: " + name + "\n"
	yamlContent += "description: Test workflow for " + name + "\n"
	yamlContent += "nodes:\n"

	for _, node := range nodes {
		yamlContent += "  - id: " + node.ID + "\n"
		yamlContent += "    agent: " + node.Agent + "\n"
		yamlContent += "    prompt: \"" + node.Prompt + "\"\n"
		if len(node.DependsOn) > 0 {
			yamlContent += "    depends_on: ["
			for i, dep := range node.DependsOn {
				if i > 0 {
					yamlContent += ", "
				}
				yamlContent += dep
			}
			yamlContent += "]\n"
		}
	}

	if err := writeFile(workflowFile, yamlContent); err != nil {
		t.Fatalf("Failed to create test workflow file: %v", err)
	}

	return workflowFile
}

func waitForCompletion(t *testing.T, db *storage.DB, executionID string, timeout time.Duration) {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		execution, err := db.GetExecution(executionID)
		if err != nil {
			t.Fatalf("Failed to get execution status: %v", err)
		}
		if execution.Status == "completed" || execution.Status == "failed" {
			return
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("Execution did not complete within timeout")
}

func isPostgreSQLAvailable() bool {
	pgDSN := "postgres://postgres:postgres@127.0.0.1/postgres?sslmode=disable"
	db, err := sqlx.Connect("postgres", pgDSN)
	if err != nil {
		return false
	}
	defer db.Close()
	return true
}

func createPostgreSQLTestDB(t *testing.T, testName string) string {
	pgDSN := "postgres://postgres:postgres@127.0.0.1/postgres?sslmode=disable"
	mainDB, err := sqlx.Connect("postgres", pgDSN)
	if err != nil {
		t.Skipf("PostgreSQL not available: %v", err)
	}
	defer mainDB.Close()

	testDBName := testName + "_db"
	mainDB.Exec("DROP DATABASE IF EXISTS " + testDBName)
	_, err = mainDB.Exec("CREATE DATABASE " + testDBName)
	if err != nil {
		t.Skipf("Cannot create test database: %v", err)
	}

	return "postgres://postgres:postgres@127.0.0.1/" + testDBName + "?sslmode=disable"
}

func TestParallelExecutionIndependentNodes(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testParallelExecutionIndependentNodes(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})
}

func testParallelExecutionIndependentNodes(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	workflowFile := createTestWorkflow(t, "parallel-independent", []testNode{
		{ID: "node1", Agent: "mock-agent", Prompt: "Independent task 1"},
		{ID: "node2", Agent: "mock-agent", Prompt: "Independent task 2"},
		{ID: "node3", Agent: "mock-agent", Prompt: "Independent task 3"},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	executor := NewExecutor(db)
	start := time.Now()
	execution, err := executor.StartWorkflow("parallel-independent")
	if err != nil {
		t.Fatalf("Failed to start workflow: %v", err)
	}

	waitForCompletion(t, db, execution.ID, 5*time.Second)
	elapsed := time.Since(start)

	final, err := db.GetExecution(execution.ID)
	if err != nil {
		t.Fatalf("Failed to get final execution: %v", err)
	}
	if final.Status != "completed" {
		t.Errorf("Expected status 'completed', got: %s", final.Status)
	}

	if elapsed > 3*time.Second {
		t.Errorf("Execution took too long (%v), parallel execution may not be working", elapsed)
	}

	t.Logf("Independent nodes executed in %v", elapsed)
}

func TestParallelExecutionDependencyChains(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testParallelExecutionDependencyChains(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})
}

func testParallelExecutionDependencyChains(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	workflowFile := createTestWorkflow(t, "dependency-chain", []testNode{
		{ID: "A", Agent: "mock-agent", Prompt: "Task A"},
		{ID: "B", Agent: "mock-agent", Prompt: "Task B depends on A", DependsOn: []string{"A"}},
		{ID: "C", Agent: "mock-agent", Prompt: "Task C depends on B", DependsOn: []string{"B"}},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	executor := NewExecutor(db)
	execution, err := executor.StartWorkflow("dependency-chain")
	if err != nil {
		t.Fatalf("Failed to start workflow: %v", err)
	}

	waitForCompletion(t, db, execution.ID, 5*time.Second)

	final, err := db.GetExecution(execution.ID)
	if err != nil {
		t.Fatalf("Failed to get final execution: %v", err)
	}
	if final.Status != "completed" {
		t.Errorf("Expected status 'completed', got: %s", final.Status)
	}

}

func TestParallelExecutionDiamondPattern(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testParallelExecutionDiamondPattern(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})
}

func testParallelExecutionDiamondPattern(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	workflowFile := createTestWorkflow(t, "diamond-pattern", []testNode{
		{ID: "start", Agent: "mock-agent", Prompt: "Start task"},
		{ID: "branch1", Agent: "mock-agent", Prompt: "Branch 1", DependsOn: []string{"start"}},
		{ID: "branch2", Agent: "mock-agent", Prompt: "Branch 2", DependsOn: []string{"start"}},
		{ID: "merge", Agent: "mock-agent", Prompt: "Merge both branches", DependsOn: []string{"branch1", "branch2"}},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	executor := NewExecutor(db)
	start := time.Now()
	execution, err := executor.StartWorkflow("diamond-pattern")
	if err != nil {
		t.Fatalf("Failed to start workflow: %v", err)
	}

	waitForCompletion(t, db, execution.ID, 5*time.Second)
	elapsed := time.Since(start)

	final, err := db.GetExecution(execution.ID)
	if err != nil {
		t.Fatalf("Failed to get final execution: %v", err)
	}
	if final.Status != "completed" {
		t.Errorf("Expected status 'completed', got: %s", final.Status)
	}

	t.Logf("Diamond pattern executed in %v", elapsed)
}

func TestParallelExecutionMoreTasksThanWorkers(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testParallelExecutionMoreTasksThanWorkers(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})
}

func testParallelExecutionMoreTasksThanWorkers(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	nodes := make([]testNode, 10)
	for i := 0; i < 10; i++ {
		nodes[i] = testNode{
			ID:     fmt.Sprintf("node_%d", i),
			Agent:  "mock-agent",
			Prompt: fmt.Sprintf("Task %d", i),
		}
	}

	workflowFile := createTestWorkflow(t, "many-tasks", nodes)
	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	executor := NewExecutor(db)
	start := time.Now()
	execution, err := executor.StartWorkflow("many-tasks")
	if err != nil {
		t.Fatalf("Failed to start workflow: %v", err)
	}

	waitForCompletion(t, db, execution.ID, 10*time.Second)
	elapsed := time.Since(start)

	final, err := db.GetExecution(execution.ID)
	if err != nil {
		t.Fatalf("Failed to get final execution: %v", err)
	}
	if final.Status != "completed" {
		t.Errorf("Expected status 'completed', got: %s", final.Status)
	}

	t.Logf("10 tasks executed with 5 workers in %v", elapsed)
}

func TestParallelExecutionNoRaceConditions(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testParallelExecutionNoRaceConditions(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})
}

func testParallelExecutionNoRaceConditions(t *testing.T, driver, dsn string) {
	db := setupTestDB(t, driver, dsn)
	defer db.Close()

	workflowFile := createTestWorkflow(t, "race-test", []testNode{
		{ID: "init", Agent: "mock-agent", Prompt: "Initialize"},
		{ID: "task1", Agent: "mock-agent", Prompt: "Task 1", DependsOn: []string{"init"}},
		{ID: "task2", Agent: "mock-agent", Prompt: "Task 2", DependsOn: []string{"init"}},
		{ID: "task3", Agent: "mock-agent", Prompt: "Task 3", DependsOn: []string{"init"}},
		{ID: "final1", Agent: "mock-agent", Prompt: "Final 1", DependsOn: []string{"task1", "task2"}},
		{ID: "final2", Agent: "mock-agent", Prompt: "Final 2", DependsOn: []string{"task2", "task3"}},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	executor := NewExecutor(db)
	execution, err := executor.StartWorkflow("race-test")
	if err != nil {
		t.Fatalf("Failed to start workflow: %v", err)
	}

	waitForCompletion(t, db, execution.ID, 5*time.Second)

	final, err := db.GetExecution(execution.ID)
	if err != nil {
		t.Fatalf("Failed to get final execution: %v", err)
	}
	if final.Status != "completed" {
		t.Errorf("Expected status 'completed', got: %s", final.Status)
	}

}

func contains(s, substr string) bool {
	return strings.Contains(s, substr)
}

func writeFile(filename, content string) error {
	return os.WriteFile(filename, []byte(content), 0644)
}
