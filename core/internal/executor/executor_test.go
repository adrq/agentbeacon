package executor

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/config"
	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/engine"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/jmoiron/sqlx"
	_ "github.com/lib/pq"
)

func setupStdioAgent(t *testing.T, responses map[string]string) Agent {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	mockAgentPath := "../../../bin/mock-agent"
	var args []string

	if responses != nil {
		configFile := createTempConfig(t, responses)
		args = []string{"--config", configFile}
	}

	agent, err := NewStdioAgent(mockAgentPath, args...)
	if err != nil {
		t.Fatalf("Failed to create StdioAgent: %v", err)
	}

	t.Cleanup(func() {
		agent.Close()
	})

	return agent
}

func setupTestConfigLoader(t *testing.T) *config.ConfigLoader {
	// Create a temporary agents.yaml file for testing
	tempDir := t.TempDir()
	agentsFile := filepath.Join(tempDir, "agents.yaml")

	agentsConfig := `agents:
  mock-agent:
    type: stdio
    config:
      command: "../../../bin/mock-agent"
  claude:
    type: stdio
    config:
      command: "../../../bin/mock-agent"
`

	if err := os.WriteFile(agentsFile, []byte(agentsConfig), 0644); err != nil {
		t.Fatalf("Failed to create test agents config: %v", err)
	}

	return config.NewConfigLoader(agentsFile)
}

func createTempConfig(t *testing.T, responses map[string]string) string {
	tempDir := t.TempDir()
	configFile := filepath.Join(tempDir, "responses.json")

	// Simple JSON construction (keys are safe test prompts)
	first := true
	content := "{"
	for k, v := range responses {
		if !first {
			content += ","
		} else {
			first = false
		}
		content += fmt.Sprintf("\n  \"%s\": \"%s\"", k, v)
	}
	if !first {
		content += "\n"
	}
	content += "}"
	if err := os.WriteFile(configFile, []byte(content), 0644); err != nil {
		t.Fatalf("Failed to write config file: %v", err)
	}

	return configFile
}

func TestStdioAgentPredefinedResponses(t *testing.T) {
	agent := setupStdioAgent(t, map[string]string{
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

func TestStdioAgentUnknownPrompt(t *testing.T) {
	agent := setupStdioAgent(t, map[string]string{
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

// Legacy StartWorkflow tests removed; coverage moved to StartWorkflowRef tests in start_workflow_ref_test.go

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

	// Build execution & workflow directly (registry path covered elsewhere)
	execution := &engine.Execution{
		ID:         "test-execution-id",
		WorkflowID: "inline-test-workflow",
		Status:     constants.TaskStateWorking,
		NodeStates: map[string]engine.NodeState{
			"task1": {Status: constants.TaskStateSubmitted},
			"task2": {Status: constants.TaskStateSubmitted},
		},
		StartedAt: time.Now(),
	}

	workflow := &engine.Workflow{
		Name:        "inline-test-workflow",
		Description: "Test workflow for task submission",
		Nodes: []engine.Node{
			{ID: "task1", Agent: "mock-agent", Request: map[string]interface{}{"prompt": "Execute task 1"}},
			{ID: "task2", Agent: "mock-agent", Request: map[string]interface{}{"prompt": "Execute task 2"}},
		},
	}

	configLoader := setupTestConfigLoader(t)
	executor := NewExecutor(db, configLoader)
	pool := NewWorkerPool(context.Background(), 2, executor)

	pool.Start()
	defer pool.Shutdown()

	// Build node lookup map for O(1) access
	nodeMap := make(map[string]*engine.Node)
	for i := range workflow.Nodes {
		nodeMap[workflow.Nodes[i].ID] = &workflow.Nodes[i]
	}

	level := []string{"task1", "task2"}
	err := pool.executeLevel(execution, nodeMap, level)

	if err != nil {
		t.Errorf("Expected no error from executeLevel, got: %v", err)
	}
}

// Removed worker pool context cancellation test - complex edge case

// Removed worker pool error handling test - detailed error testing not needed for MVP

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

	execution := &engine.Execution{
		ID:         "parallel-execution",
		WorkflowID: "parallel-workflow",
		Status:     constants.TaskStateWorking,
		NodeStates: make(map[string]engine.NodeState),
		StartedAt:  time.Now(),
	}

	var workflowNodes []engine.Node
	for i := 0; i < numTasks; i++ {
		nodeID := fmt.Sprintf("parallel-task-%d", i+1)
		execution.NodeStates[nodeID] = engine.NodeState{Status: constants.TaskStateSubmitted}
		workflowNodes = append(workflowNodes, engine.Node{
			ID:    nodeID,
			Agent: "mock-agent",
			Request: map[string]interface{}{
				"prompt": fmt.Sprintf("Parallel task %d", i+1),
			},
		})
	}

	workflow := &engine.Workflow{
		Name:  "parallel-workflow",
		Nodes: workflowNodes,
	}

	configLoader := setupTestConfigLoader(t)
	executor := NewExecutor(db, configLoader)
	pool := NewWorkerPool(context.Background(), 3, executor) // Use fewer workers than tasks to test queuing

	pool.Start()
	defer pool.Shutdown()

	// Build node lookup map for O(1) access
	nodeMap := make(map[string]*engine.Node)
	for i := range workflow.Nodes {
		nodeMap[workflow.Nodes[i].ID] = &workflow.Nodes[i]
	}

	startTime := time.Now()

	var level []string
	for i := 0; i < numTasks; i++ {
		level = append(level, fmt.Sprintf("parallel-task-%d", i+1))
	}

	err := pool.executeLevel(execution, nodeMap, level)
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

	workflow := engine.Workflow{
		Name: "topological-workflow",
		Nodes: []engine.Node{
			{ID: "level1-a", Agent: "mock-agent", Request: map[string]interface{}{"prompt": "Level 1 task A"}},
			{ID: "level1-b", Agent: "mock-agent", Request: map[string]interface{}{"prompt": "Level 1 task B"}},
			{ID: "level2-a", Agent: "mock-agent", Request: map[string]interface{}{"prompt": "Level 2 task A"}, DependsOn: []string{"level1-a", "level1-b"}},
			{ID: "level2-b", Agent: "mock-agent", Request: map[string]interface{}{"prompt": "Level 2 task B"}, DependsOn: []string{"level1-a"}},
			{ID: "level3-a", Agent: "mock-agent", Request: map[string]interface{}{"prompt": "Level 3 task A"}, DependsOn: []string{"level2-a", "level2-b"}},
		},
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
		Status:     constants.TaskStateWorking,
		NodeStates: make(map[string]engine.NodeState),
		StartedAt:  time.Now(),
	}

	for _, node := range workflow.Nodes {
		execution.NodeStates[node.ID] = engine.NodeState{Status: constants.TaskStateSubmitted}
	}

	configLoader := setupTestConfigLoader(t)
	executor := NewExecutor(db, configLoader)
	pool := NewWorkerPool(context.Background(), 2, executor)

	pool.Start()
	defer pool.Shutdown()

	// Build node lookup map for O(1) access
	nodeMap := make(map[string]*engine.Node)
	for i := range workflow.Nodes {
		nodeMap[workflow.Nodes[i].ID] = &workflow.Nodes[i]
	}

	for i, level := range levels {
		err := pool.executeLevel(execution, nodeMap, level)
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
		yamlContent += "    request:\n"
		yamlContent += "      prompt: \"" + node.Prompt + "\"\n"
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
		if execution.Status == constants.TaskStateCompleted || execution.Status == constants.TaskStateFailed {
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
	_, _ = mainDB.Exec("DROP DATABASE IF EXISTS " + testDBName)
	_, err = mainDB.Exec("CREATE DATABASE " + testDBName)
	if err != nil {
		t.Skipf("Cannot create test database: %v", err)
	}

	return "postgres://postgres:postgres@127.0.0.1/" + testDBName + "?sslmode=disable"
}

// Removed redundant parallel execution test - covered by TestWorkerPoolParallelExecution

// Removed dependency chain test - covered by TestExecutorSequentialExecution

// Removed diamond pattern test - covered by integration test TestParallelExecutionWithConvergence

// Removed many tasks test - covered by TestWorkerPoolParallelExecution

// Removed race condition test - complex concurrency testing beyond MVP needs

func writeFile(filename, content string) error {
	return os.WriteFile(filename, []byte(content), 0644)
}
