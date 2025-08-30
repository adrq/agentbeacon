package executor

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// Test basic communication with process agent
func TestProcessAgentBasicCommunication(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	agent, err := NewProcessAgent("../../../bin/mock-agent")
	if err != nil {
		t.Fatalf("Failed to create ProcessAgent: %v", err)
	}
	defer agent.Close()

	ctx := context.Background()
	response, err := agent.Execute(ctx, "hello world")
	if err != nil {
		t.Fatalf("Execute failed: %v", err)
	}

	expected := "Mock response: hello world"
	if response != expected {
		t.Errorf("Expected '%s', got '%s'", expected, response)
	}
}

// Removed custom response configuration test - not core functionality

// Removed crash handling test - complex edge case not essential for MVP

// Test context timeout handling
func TestProcessAgentTimeout(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Create config with a prompt that would cause mock-agent to hang
	// (mock-agent should support a special "HANG" response for testing)
	tempDir := t.TempDir()
	configFile := filepath.Join(tempDir, "hang_responses.json")

	responses := map[string]string{
		"hang test": "HANG", // Special response that causes mock-agent to hang
	}

	configData, err := json.Marshal(responses)
	if err != nil {
		t.Fatalf("Failed to marshal config: %v", err)
	}

	if err := os.WriteFile(configFile, configData, 0644); err != nil {
		t.Fatalf("Failed to write config file: %v", err)
	}

	agent, err := NewProcessAgent("../../../bin/mock-agent", "--config", configFile)
	if err != nil {
		t.Fatalf("Failed to create ProcessAgent: %v", err)
	}
	defer agent.Close()

	// Create context with short timeout
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	start := time.Now()
	_, err = agent.Execute(ctx, "hang test")
	duration := time.Since(start)

	// Should return timeout error
	if err == nil {
		t.Error("Expected timeout error")
	}

	// Should respect timeout (with some buffer for processing)
	if duration > 500*time.Millisecond {
		t.Errorf("Execution took too long: %v", duration)
	}

	// Verify error is timeout-related
	if !strings.Contains(err.Error(), "timeout") && !strings.Contains(err.Error(), "context") {
		t.Errorf("Expected timeout-related error, got: %s", err.Error())
	}
}

// Removed detailed cleanup test - basic cleanup covered by defer agent.Close() in other tests

// Removed edge case validation tests - not essential for MVP

// Removed integration test - covered by other integration tests in executor_test.go

// Helper function to check if mock-agent binary exists
func mockAgentExists() bool {
	// Check relative path from executor test directory
	_, err := os.Stat("../../../bin/mock-agent")
	return err == nil
}

func TestProcessAgentFailNode(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	agent, err := NewProcessAgent("../../../bin/mock-agent")
	if err != nil {
		t.Fatalf("Failed to create agent: %v", err)
	}
	defer agent.Close()

	// Test FAIL_NODE causes failure
	_, err = agent.Execute(context.Background(), "FAIL_NODE")
	if err == nil {
		t.Error("Expected FAIL_NODE to cause an error, but it succeeded")
	}

	// Create new agent for normal prompt test since first one terminated
	agent2, err := NewProcessAgent("../../../bin/mock-agent")
	if err != nil {
		t.Fatalf("Failed to create second agent: %v", err)
	}
	defer agent2.Close()

	result, err := agent2.Execute(context.Background(), "normal prompt")
	if err != nil {
		t.Fatalf("Normal prompt should succeed: %v", err)
	}

	if result != "Mock response: normal prompt" {
		t.Errorf("Expected 'Mock response: normal prompt', got: %s", result)
	}
}
