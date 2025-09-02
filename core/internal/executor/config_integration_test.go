package executor

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/config"
	"github.com/agentmaestro/agentmaestro/core/internal/engine"
)

// TestConfigIntegration validates the complete config loader integration
func TestConfigIntegration(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Create test config
	tempDir := t.TempDir()
	agentsFile := filepath.Join(tempDir, "agents.yaml")

	agentsConfig := `agents:
  test-stdio:
    type: stdio
    config:
      command: "../../../bin/mock-agent"
  test-a2a:
    type: a2a
    config:
      url: "http://localhost:9999/rpc"
`

	if err := os.WriteFile(agentsFile, []byte(agentsConfig), 0644); err != nil {
		t.Fatalf("Failed to create test agents config: %v", err)
	}

	configLoader := config.NewConfigLoader(agentsFile)

	// Valid stdio agent configuration
	stdioConfig, err := configLoader.GetAgentConfig("test-stdio")
	if err != nil {
		t.Fatalf("Failed to get stdio agent config: %v", err)
	}
	if stdioConfig.Type != "stdio" {
		t.Errorf("Expected stdio type, got: %s", stdioConfig.Type)
	}

	// Valid a2a agent configuration
	a2aConfig, err := configLoader.GetAgentConfig("test-a2a")
	if err != nil {
		t.Fatalf("Failed to get a2a agent config: %v", err)
	}
	if a2aConfig.Type != "a2a" {
		t.Errorf("Expected a2a type, got: %s", a2aConfig.Type)
	}

	// Unknown agent configuration
	_, err = configLoader.GetAgentConfig("unknown-agent")
	if err == nil {
		t.Error("Expected error for unknown agent")
	}

	// Create executor with config loader
	db := setupTestDB(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	defer db.Close()

	executor := NewExecutor(db, configLoader)
	if executor.configLoader != configLoader {
		t.Error("ConfigLoader not properly set in executor")
	}

	// Agent creation with stdio type
	stdioNode := &engine.Node{
		ID:    "test-node",
		Agent: "test-stdio",
		Request: map[string]interface{}{
			"prompt": "test prompt",
		},
	}

	agent, err := executor.createAgentForNode(stdioNode)
	if err != nil {
		t.Fatalf("Failed to create stdio agent: %v", err)
	}
	defer agent.Close()

	// Request validation for stdio agent
	err = executor.validateNodeRequest(stdioNode, stdioConfig)
	if err != nil {
		t.Errorf("Stdio request validation failed: %v", err)
	}

	// Request validation failure (missing prompt)
	invalidNode := &engine.Node{
		ID:    "invalid-node",
		Agent: "test-stdio",
		Request: map[string]interface{}{
			"task": "no prompt field",
		},
	}

	err = executor.validateNodeRequest(invalidNode, stdioConfig)
	if err == nil {
		t.Error("Expected validation error for missing prompt")
	}

	// Agent execution with valid request
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	result, err := agent.Execute(ctx, "test execution prompt")
	if err != nil {
		t.Errorf("Agent execution failed: %v", err)
	}
	if result == "" {
		t.Error("Expected non-empty result from agent execution")
	}

	// A2A agent creation should work but not execute (no server)
	a2aNode := &engine.Node{
		ID:    "a2a-test-node",
		Agent: "test-a2a",
		Request: map[string]interface{}{
			"task": "test a2a task",
		},
	}

	a2aAgent, err := executor.createAgentForNode(a2aNode)
	if err != nil {
		t.Fatalf("Failed to create a2a agent: %v", err)
	}
	defer a2aAgent.Close()

	// A2A request validation
	err = executor.validateNodeRequest(a2aNode, a2aConfig)
	if err != nil {
		t.Errorf("A2A request validation failed: %v", err)
	}
}
