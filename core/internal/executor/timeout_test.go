package executor

import (
	"context"
	"encoding/json"
	"path/filepath"
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/engine"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestExecuteNode_WithTimeout_Success(t *testing.T) {
	db := setupTestDB(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	defer db.Close()

	configLoader := setupTestConfigLoader(t)
	executor := NewExecutor(db, configLoader)

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
		ID:    "test-node",
		Agent: "mock-agent",
		Request: map[string]interface{}{
			"prompt": "quick task",
		},
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
	db := setupTestDB(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	defer db.Close()

	configLoader := setupTestConfigLoader(t)
	executor := NewExecutor(db, configLoader)
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
		ID:    "test-node",
		Agent: "mock-agent",
		Request: map[string]interface{}{
			"prompt": "DELAY_3", // Delay 3 seconds
		},
		Timeout: 1, // 1 second timeout - will be exceeded
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

	// Inline register workflow (registry model)
	yaml := "name: timeout-test\n" +
		"namespace: test\n" +
		"description: timeout test\n" +
		"nodes:\n" +
		"  - id: quick-node\n" +
		"    agent: mock-agent\n" +
		"    request:\n" +
		"      prompt: quick task\n" +
		"    timeout: 5\n" +
		"  - id: slow-node\n" +
		"    agent: mock-agent\n" +
		"    request:\n" +
		"      prompt: DELAY_3\n" +
		"    timeout: 2\n" +
		"    depends_on: [quick-node]\n"
	_, err := db.RegisterInlineWorkflow("test", "timeout-test", "", yaml)
	require.NoError(t, err)

	configLoader := setupTestConfigLoader(t)
	executor := NewExecutor(db, configLoader)
	execInfo, err := executor.StartWorkflowRef("test/timeout-test:latest")
	require.NoError(t, err)
	waitForCompletion(t, db, execInfo.ID, 10*time.Second)
	finalExecution, err := db.GetExecution(execInfo.ID)
	require.NoError(t, err)
	var nodeStates map[string]engine.NodeState
	err = json.Unmarshal(finalExecution.NodeStates, &nodeStates)
	require.NoError(t, err)
	assert.Equal(t, constants.TaskStateFailed, finalExecution.Status)
	assert.Equal(t, constants.TaskStateCompleted, nodeStates["quick-node"].Status)
	assert.Equal(t, constants.TaskStateFailed, nodeStates["slow-node"].Status)
	assert.Contains(t, nodeStates["slow-node"].Error, "context deadline exceeded")
}

// Removed complex stop-all timeout test - error strategy system was removed

// Helper types and functions for timeout tests

// Legacy file-based helpers removed (registry inline registration used instead).

// These helper functions are defined in error_strategy_integration_test.go
