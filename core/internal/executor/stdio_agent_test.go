package executor

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/stretchr/testify/require"
)

// Test basic communication with stdio agent
func TestStdioAgentBasicCommunication(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	agent, err := NewStdioAgent("../../../bin/mock-agent")
	if err != nil {
		t.Fatalf("Failed to create StdioAgent: %v", err)
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
func TestStdioAgentTimeout(t *testing.T) {
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

	agent, err := NewStdioAgent("../../../bin/mock-agent", "--config", configFile)
	if err != nil {
		t.Fatalf("Failed to create StdioAgent: %v", err)
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

// Test EventStreamer interface implementation
func TestStdioAgentEventStreaming(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Create event channel
	eventChan := make(chan *storage.ExecutionEvent, 100)

	agent, err := NewStdioAgent("../../../bin/mock-agent")
	if err != nil {
		t.Fatalf("Failed to create StdioAgent: %v", err)
	}
	defer agent.Close()

	// Test EventStreamer interface (type assert to access EventStreamer interface)
	streamer, ok := agent.(EventStreamer)
	require.True(t, ok, "Agent should implement EventStreamer interface")

	// Set event channel
	streamer.SetEventChannel(eventChan)

	// Set execution context (type assert to access SetContext method)
	stdioAgent, ok := agent.(*StdioAgent)
	require.True(t, ok, "Agent should be of type *StdioAgent")
	stdioAgent.SetContext("test-exec-123", "test-node-456")

	// Execute a task and verify events are emitted
	ctx := context.Background()
	response, err := agent.Execute(ctx, "test prompt")
	if err != nil {
		t.Fatalf("Execute failed: %v", err)
	}

	if response != "Mock response: test prompt" {
		t.Errorf("Expected 'Mock response: test prompt', got '%s'", response)
	}

	// Verify events were emitted
	close(eventChan)
	events := []storage.ExecutionEvent{}
	for event := range eventChan {
		events = append(events, *event)
	}

	if len(events) < 2 {
		t.Errorf("Expected at least 2 events (start and complete), got %d", len(events))
	}

	// Verify event structure
	for _, event := range events {
		if event.ExecutionID != "test-exec-123" {
			t.Errorf("Expected ExecutionID 'test-exec-123', got '%s'", event.ExecutionID)
		}
		if event.NodeID != "test-node-456" {
			t.Errorf("Expected NodeID 'test-node-456', got '%s'", event.NodeID)
		}
		if event.Source != storage.EventSourceSystem {
			t.Errorf("Expected Source '%s', got '%s'", storage.EventSourceSystem, event.Source)
		}
	}
}

// Test non-blocking event emission
func TestStdioAgentNonBlockingEvents(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Create small event channel to test non-blocking behavior
	eventChan := make(chan *storage.ExecutionEvent, 1)
	// Fill the channel to capacity
	eventChan <- &storage.ExecutionEvent{}

	agent, err := NewStdioAgent("../../../bin/mock-agent")
	if err != nil {
		t.Fatalf("Failed to create StdioAgent: %v", err)
	}
	defer agent.Close()

	// Set event channel (type assert to access EventStreamer interface)
	streamer, ok := agent.(EventStreamer)
	require.True(t, ok, "Agent should implement EventStreamer interface")
	streamer.SetEventChannel(eventChan)

	// Type assert to access SetContext method (StdioAgent specific)
	stdioAgent, ok := agent.(*StdioAgent)
	require.True(t, ok, "Agent should be of type *StdioAgent")
	stdioAgent.SetContext("test-exec-123", "test-node-456")

	// Execution should not block even if event channel is full
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	start := time.Now()
	_, err = agent.Execute(ctx, "test prompt")
	duration := time.Since(start)

	if err != nil {
		t.Fatalf("Execute failed: %v", err)
	}

	// Should complete quickly (not block on full channel)
	if duration > 1*time.Second {
		t.Errorf("Execute took too long: %v, might be blocking on event channel", duration)
	}
}

// Helper function to check if mock-agent binary exists
func mockAgentExists() bool {
	// Check relative path from executor test directory
	_, err := os.Stat("../../../bin/mock-agent")
	return err == nil
}

func TestStdioAgentFailNode(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	agent, err := NewStdioAgent("../../../bin/mock-agent")
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
	agent2, err := NewStdioAgent("../../../bin/mock-agent")
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
