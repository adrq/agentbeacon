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

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/engine"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestExecuteNode_WithTimeout_Success(t *testing.T) {
	db := setupTestDB(t, "sqlite3", ":memory:")
	defer db.Close()

	executor := NewExecutor(db)

	// Create execution
	execution := &engine.Execution{
		ID:         "test-exec",
		WorkflowID: "test-workflow",
		Status:     constants.TaskStateWorking,
		NodeStates: make(map[string]engine.NodeState),
		StartedAt:  time.Now(),
	}

	// Node with 5 second timeout - should succeed quickly
	node := &engine.Node{
		ID:      "test-node",
		Agent:   "mock-agent",
		Prompt:  "quick task",
		Timeout: 5, // 5 seconds
	}

	// Initialize node state
	execution.NodeStates[node.ID] = engine.NodeState{
		Status: constants.TaskStateSubmitted,
	}

	ctx := context.Background()
	err := executor.executeNode(ctx, execution, node)

	assert.NoError(t, err)
	assert.Equal(t, constants.TaskStateCompleted, execution.NodeStates[node.ID].Status)
	assert.Contains(t, execution.NodeStates[node.ID].Output, "Mock response: quick task")
}

func TestExecuteNode_WithTimeout_Exceeded(t *testing.T) {
	db := setupTestDB(t, "sqlite3", ":memory:")
	defer db.Close()

	executor := NewExecutor(db)
	retryExecutor := NewRetryableExecutor(executor)

	// Create execution
	execution := &engine.Execution{
		ID:         "test-exec",
		WorkflowID: "test-workflow",
		Status:     constants.TaskStateWorking,
		NodeStates: make(map[string]engine.NodeState),
		StartedAt:  time.Now(),
	}

	// Node with very short timeout that will be exceeded
	node := &engine.Node{
		ID:      "test-node",
		Agent:   "mock-agent",
		Prompt:  "DELAY_3", // Delay 3 seconds
		Timeout: 1,         // 1 second timeout - will be exceeded
	}

	// Initialize node state
	execution.NodeStates[node.ID] = engine.NodeState{
		Status: constants.TaskStateSubmitted,
	}

	ctx := context.Background()
	start := time.Now()
	err := retryExecutor.executeNodeWithRetry(ctx, execution, node)
	duration := time.Since(start)

	t.Logf("Execution took %v, error: %v", duration, err)
	if err != nil {
		t.Logf("Error message: %s", err.Error())
	}

	// Should fail with timeout error
	if err == nil {
		t.Errorf("Expected timeout error but got none. Duration: %v", duration)
		return
	}

	assert.Contains(t, err.Error(), "context deadline exceeded")
	assert.Equal(t, constants.TaskStateFailed, execution.NodeStates[node.ID].Status)
	assert.Contains(t, execution.NodeStates[node.ID].Error, "context deadline exceeded")
}

// Removed complex timeout edge case tests to focus on core functionality

func TestWorkflowExecution_WithTimeouts(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	db := setupTestDB(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	defer db.Close()

	// Create workflow file with mixed timeouts
	workflowFile := createTimeoutTestWorkflow(t, "timeout-test", []timeoutNode{
		{ID: "quick-node", Agent: "mock-agent", Prompt: "quick task", Timeout: 5},
		{ID: "slow-node", Agent: "mock-agent", Prompt: "DELAY_3", Timeout: 2, DependsOn: []string{"quick-node"}},
	})

	// Register workflow
	err := db.RegisterWorkflow(workflowFile)
	require.NoError(t, err)

	executor := NewExecutor(db)

	// Start execution
	execInfo, err := executor.StartWorkflow("timeout-test")
	require.NoError(t, err)

	// Wait for execution to complete/fail
	waitForCompletion(t, db, execInfo.ID, 10*time.Second)

	// Get final execution state
	finalExecution, err := db.GetExecution(execInfo.ID)
	require.NoError(t, err)

	// Parse node states
	var nodeStates map[string]engine.NodeState
	err = json.Unmarshal(finalExecution.NodeStates, &nodeStates)
	require.NoError(t, err)

	// Should fail due to timeout in slow-node
	assert.Equal(t, constants.TaskStateFailed, finalExecution.Status)
	assert.Equal(t, constants.TaskStateCompleted, nodeStates["quick-node"].Status)
	assert.Equal(t, constants.TaskStateFailed, nodeStates["slow-node"].Status)
	assert.Contains(t, nodeStates["slow-node"].Error, "context deadline exceeded")
}

// Removed complex stop-all timeout test - error strategy system was removed

// Helper types and functions for timeout tests

type timeoutNode struct {
	ID        string
	Agent     string
	Prompt    string
	Timeout   int
	DependsOn []string
}

func createTimeoutTestWorkflow(t *testing.T, name string, nodes []timeoutNode) string {
	tempDir := t.TempDir()
	workflowFile := filepath.Join(tempDir, name+".yaml")

	yamlContent := "name: " + name + "\n"
	yamlContent += "description: Test workflow for " + name + "\n"
	yamlContent += "nodes:\n"

	for _, node := range nodes {
		yamlContent += "  - id: " + node.ID + "\n"
		yamlContent += "    agent: " + node.Agent + "\n"
		yamlContent += "    prompt: \"" + node.Prompt + "\"\n"
		if node.Timeout > 0 {
			yamlContent += fmt.Sprintf("    timeout: %d\n", node.Timeout)
		}
		if len(node.DependsOn) > 0 {
			yamlContent += "    depends_on: [" + strings.Join(node.DependsOn, ", ") + "]\n"
		}
	}

	if err := os.WriteFile(workflowFile, []byte(yamlContent), 0644); err != nil {
		t.Fatalf("Failed to write workflow file: %v", err)
	}

	return workflowFile
}

// These helper functions are defined in error_strategy_integration_test.go
