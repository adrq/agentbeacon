package executor

import (
	"context"
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/stretchr/testify/require"
)

// TestEventStreamingIntegration tests the complete event streaming flow from agent to database
func TestEventStreamingIntegration(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Create in-memory database
	db := setupTestDB(t, "sqlite3", ":memory:")
	defer db.Close()

	// Create config loader
	configLoader := setupTestConfigLoader(t)

	// Create executor with event streaming
	executor := NewExecutor(db, configLoader)
	defer executor.Close()

	// Create agent directly and set event channel
	agent, err := NewStdioAgent("../../../bin/mock-agent")
	if err != nil {
		t.Fatalf("Failed to create agent: %v", err)
	}
	defer agent.Close()

	// Set event channel and execution context (type assert to access EventStreamer interface)
	streamer, ok := agent.(EventStreamer)
	require.True(t, ok, "Agent should implement EventStreamer interface")
	streamer.SetEventChannel(executor.eventChan)

	// Type assert to access SetContext method (StdioAgent specific)
	stdioAgent, ok := agent.(*StdioAgent)
	require.True(t, ok, "Agent should be of type *StdioAgent")
	stdioAgent.SetContext("test-exec-123", "test-node-456")

	// Execute a task
	ctx := context.Background()
	response, err := agent.Execute(ctx, "test event streaming")
	if err != nil {
		t.Fatalf("Execute failed: %v", err)
	}

	if response != "Mock response: test event streaming" {
		t.Errorf("Expected 'Mock response: test event streaming', got '%s'", response)
	}

	// Give some time for events to be written to database
	time.Sleep(100 * time.Millisecond)

	// Query events from database
	events, err := db.GetExecutionEvents("test-exec-123", 100)
	if err != nil {
		t.Fatalf("Failed to get events: %v", err)
	}

	if len(events) == 0 {
		t.Fatal("Expected events to be stored in database, but none found")
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
		if event.Timestamp.IsZero() {
			t.Error("Expected timestamp to be set")
		}
	}

	// Verify we have expected event types
	foundStart := false
	foundComplete := false
	foundOutput := false

	for _, event := range events {
		switch event.Type {
		case storage.EventTypeStateChange:
			if event.State != nil {
				if *event.State == constants.TaskStateWorking {
					foundStart = true
				}
				if *event.State == constants.TaskStateCompleted {
					foundComplete = true
				}
			}
		case storage.EventTypeOutput:
			foundOutput = true
		}
	}

	if !foundStart {
		t.Error("Expected to find start event (state change to working)")
	}
	if !foundComplete {
		t.Error("Expected to find completion event (state change to completed)")
	}
	if !foundOutput {
		t.Error("Expected to find output event")
	}

	t.Logf("Successfully verified %d events stored in database", len(events))
}

// TestEventStreamingCleanup tests that the event writer properly shuts down
func TestEventStreamingCleanup(t *testing.T) {
	// Create in-memory database
	db := setupTestDB(t, "sqlite3", ":memory:")
	defer db.Close()

	// Create config loader
	configLoader := setupTestConfigLoader(t)

	// Create executor
	executor := NewExecutor(db, configLoader)

	// Verify executor can be closed without hanging
	done := make(chan struct{})
	go func() {
		executor.Close()
		close(done)
	}()

	// Wait for cleanup with timeout
	select {
	case <-done:
		// Successfully cleaned up
	case <-time.After(2 * time.Second):
		t.Fatal("Executor cleanup timed out")
	}
}

// TestACPEventStreamingIntegration tests the complete ACP event streaming flow from agent to database
func TestACPEventStreamingIntegration(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Create in-memory database
	db := setupTestDB(t, "sqlite3", ":memory:")
	defer db.Close()

	// Create config loader
	configLoader := setupTestConfigLoader(t)

	// Create executor with event streaming
	executor := NewExecutor(db, configLoader)
	defer executor.Close()

	// Create ACP agent directly and set event channel
	agent, err := NewACPAgent("../../../bin/mock-agent", []string{"--mode", "acp"}, "/tmp")
	if err != nil {
		t.Fatalf("Failed to create ACP agent: %v", err)
	}
	defer agent.Close()

	// Set event channel and execution context
	streamer, ok := agent.(EventStreamer)
	require.True(t, ok, "ACPAgent should implement EventStreamer interface")
	streamer.SetEventChannel(executor.eventChan)

	contextSetter, ok := agent.(ContextSetter)
	require.True(t, ok, "ACPAgent should implement ContextSetter interface")
	contextSetter.SetContext("test-acp-exec-123", "test-acp-node-456")

	// Execute a task
	ctx := context.Background()
	response, err := agent.Execute(ctx, "test ACP event streaming")
	if err != nil {
		t.Fatalf("Execute failed: %v", err)
	}

	if response != "Mock response: test ACP event streaming" {
		t.Errorf("Expected 'Mock response: test ACP event streaming', got '%s'", response)
	}

	// Give some time for events to be written to database
	time.Sleep(100 * time.Millisecond)

	// Query events from database
	events, err := db.GetExecutionEvents("test-acp-exec-123", 100)
	if err != nil {
		t.Fatalf("Failed to get events: %v", err)
	}

	if len(events) == 0 {
		t.Fatal("Expected events to be stored in database, but none found")
	}

	// Verify event structure
	for _, event := range events {
		if event.ExecutionID != "test-acp-exec-123" {
			t.Errorf("Expected ExecutionID 'test-acp-exec-123', got '%s'", event.ExecutionID)
		}
		if event.NodeID != "test-acp-node-456" {
			t.Errorf("Expected NodeID 'test-acp-node-456', got '%s'", event.NodeID)
		}
		if event.Source != storage.EventSourceACP {
			t.Errorf("Expected Source '%s', got '%s'", storage.EventSourceACP, event.Source)
		}
		if event.Timestamp.IsZero() {
			t.Error("Expected timestamp to be set")
		}
	}

	// Verify we have expected event types for ACP
	foundStart := false
	foundComplete := false
	foundOutput := false

	for _, event := range events {
		switch event.Type {
		case storage.EventTypeStateChange:
			if event.State != nil {
				if *event.State == constants.TaskStateWorking {
					foundStart = true
				}
				if *event.State == constants.TaskStateCompleted {
					foundComplete = true
				}
			}
		case storage.EventTypeOutput:
			foundOutput = true
		}
	}

	if !foundStart {
		t.Error("Expected to find start event (state change to working)")
	}
	if !foundComplete {
		t.Error("Expected to find completion event (state change to completed)")
	}
	if !foundOutput {
		t.Error("Expected to find output event")
	}
	// Session creation output is optional; some ACP agents may not emit it
	// Keep counting it if present, but do not fail if missing

	t.Logf("Successfully verified %d ACP events stored in database", len(events))
}
