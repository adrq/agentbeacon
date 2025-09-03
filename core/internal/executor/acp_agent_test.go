package executor

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestACPAgentLifecycle(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Create ACP agent using mock-agent
	agent, err := NewACPAgent("../../../bin/mock-agent", []string{"--mode", "acp"}, "/tmp")
	require.NoError(t, err, "Failed to create ACP agent")
	defer agent.Close()

	// Verify agent is running and initialized
	assert.NotNil(t, agent.cmd, "Agent process should be running")
	assert.NotNil(t, agent.stdin, "Agent stdin should be available")
	assert.NotNil(t, agent.stdout, "Agent stdout should be available")
}

func TestACPAgentPromptExecution(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	agent, err := NewACPAgent("../../../bin/mock-agent", []string{"--mode", "acp"}, "/tmp")
	require.NoError(t, err)
	defer agent.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	// Test basic prompt execution
	response, err := agent.Execute(ctx, "test prompt for acp agent")
	require.NoError(t, err)
	assert.Contains(t, response, "Mock response: test prompt for acp agent")
}

func TestACPAgentErrorScenarios(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Test process that doesn't exist (should fail at creation)
	_, err := NewACPAgent("/nonexistent/command", []string{"--mode", "acp"}, "")
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "failed to start ACP agent process")
}

func TestACPAgentIntegrationWithExecutor(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Test direct agent creation and execution
	agent, err := NewACPAgent("../../../bin/mock-agent", []string{"--mode", "acp"}, "/tmp")
	require.NoError(t, err)
	defer agent.Close()

	// Verify it's an ACP agent by checking session ID is set
	assert.NotEmpty(t, agent.sessionID, "Session ID should be set")

	// Test execution
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	response, err := agent.Execute(ctx, "test integration prompt")
	require.NoError(t, err)
	assert.Contains(t, response, "Mock response: test integration prompt")
}

func TestACPAgentWorkingDirectory(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Test with custom working directory
	customWorkingDir := "/home/user/project"
	agent, err := NewACPAgent("../../../bin/mock-agent", []string{"--mode", "acp"}, customWorkingDir)
	require.NoError(t, err)
	defer agent.Close()

	// Verify working directory is stored
	assert.Equal(t, customWorkingDir, agent.workingDir, "Working directory should be set to custom value")

	// Test with empty working directory (should return error)
	_, err = NewACPAgent("../../../bin/mock-agent", []string{"--mode", "acp"}, "")
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "working directory is required")
}
