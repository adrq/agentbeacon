package executor

import (
	"context"
	"fmt"
	"os/exec"
	"strconv"
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/engine"
	"github.com/agentmaestro/agentmaestro/core/internal/testutil"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestA2AAgentBasicCommunication(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Find available port for mock A2A agent
	port := testutil.FindAvailablePort(t, 9460, 9470)

	// Start mock A2A agent in server mode
	cmd := exec.Command("../../../bin/mock-agent", "--mode", "a2a", "--port", strconv.Itoa(port))
	require.NoError(t, cmd.Start())

	// Cleanup
	defer func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
		}
	}()

	// Wait for agent to start
	time.Sleep(500 * time.Millisecond)

	agentURL := fmt.Sprintf("http://localhost:%d/rpc", port)
	agent := NewA2AAgent(agentURL)
	defer agent.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	response, err := agent.Execute(ctx, "test prompt")
	require.NoError(t, err)
	assert.Contains(t, response, "Mock response: test prompt")
}

func TestA2AAgentErrorHandling(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Find available port for mock A2A agent
	port := testutil.FindAvailablePort(t, 9471, 9480)

	// Start mock A2A agent
	cmd := exec.Command("../../../bin/mock-agent", "--mode", "a2a", "--port", strconv.Itoa(port))
	require.NoError(t, cmd.Start())

	defer func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
		}
	}()

	// Wait for agent to start
	time.Sleep(500 * time.Millisecond)

	agentURL := fmt.Sprintf("http://localhost:%d/rpc", port)
	agent := NewA2AAgent(agentURL)
	defer agent.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	// Test task failure
	_, err := agent.Execute(ctx, "FAIL_NODE")
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "task failed")
}

func TestA2AAgentTimeout(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Find available port for mock A2A agent
	port := testutil.FindAvailablePort(t, 9481, 9490)

	// Start mock A2A agent
	cmd := exec.Command("../../../bin/mock-agent", "--mode", "a2a", "--port", strconv.Itoa(port))
	require.NoError(t, cmd.Start())

	defer func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
		}
	}()

	// Wait for agent to start
	time.Sleep(500 * time.Millisecond)

	agentURL := fmt.Sprintf("http://localhost:%d/rpc", port)
	agent := NewA2AAgent(agentURL)
	defer agent.Close()

	// Test context timeout
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	start := time.Now()
	_, err := agent.Execute(ctx, "HANG")
	duration := time.Since(start)

	assert.Error(t, err)
	assert.True(t, duration < 1*time.Second, "Should timeout quickly")
}

func TestA2AAgentIntegrationWithExecutor(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Find available port for mock A2A agent
	port := testutil.FindAvailablePort(t, 9491, 9500)

	// Start mock A2A agent
	cmd := exec.Command("../../../bin/mock-agent", "--mode", "a2a", "--port", strconv.Itoa(port))
	require.NoError(t, cmd.Start())

	defer func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
		}
	}()

	// Wait for agent to start
	time.Sleep(500 * time.Millisecond)

	// Create executor
	executor := &Executor{}

	// Create test node with AgentURL
	node := &engine.Node{
		ID:       "test-a2a-node",
		AgentURL: fmt.Sprintf("http://localhost:%d/rpc", port),
		Prompt:   "test integration prompt",
	}

	// Test agent creation
	agent, err := executor.createAgentForNode(node)
	require.NoError(t, err)
	defer agent.Close()

	// Verify it's an A2A agent by checking type
	a2aAgent, ok := agent.(*A2AAgent)
	require.True(t, ok, "Expected A2AAgent, got %T", agent)
	assert.Equal(t, node.AgentURL, a2aAgent.agentURL)

	// Test execution
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	response, err := agent.Execute(ctx, node.Prompt)
	require.NoError(t, err)
	assert.Contains(t, response, "Mock response: test integration prompt")
}

func TestA2AAgentFallbackToStdio(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Create executor
	executor := &Executor{}

	// Create test node WITHOUT AgentURL
	node := &engine.Node{
		ID:     "test-stdio-node",
		Prompt: "test stdio prompt",
	}

	// Test agent creation falls back to stdio
	agent, err := executor.createAgentForNode(node)
	require.NoError(t, err)
	defer agent.Close()

	// Verify it's a StdioAgent
	_, ok := agent.(*StdioAgent)
	require.True(t, ok, "Expected StdioAgent, got %T", agent)
}
