package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
)

func TestACPAgentLifecycle(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Create ACP agent using mock-agent
	agent, err := NewACPAgent("../../../bin/mock-agent", []string{"--mode", "acp"}, "/tmp")
	require.NoError(t, err, "Failed to create ACP agent")
	defer agent.Close()

	// Verify agent is running and initialized (type assert to access internal fields)
	acpAgent, ok := agent.(*ACPAgent)
	require.True(t, ok, "Agent should be of type *ACPAgent")
	assert.NotNil(t, acpAgent.cmd, "Agent process should be running")
	assert.NotNil(t, acpAgent.stdin, "Agent stdin should be available")
	assert.NotNil(t, acpAgent.stdout, "Agent stdout should be available")
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

	// Verify it's an ACP agent by checking session ID is set (type assert to access internal fields)
	acpAgent, ok := agent.(*ACPAgent)
	require.True(t, ok, "Agent should be of type *ACPAgent")
	assert.NotEmpty(t, acpAgent.sessionID, "Session ID should be set")

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

	// Verify working directory is stored (type assert to access internal fields)
	acpAgent, ok := agent.(*ACPAgent)
	require.True(t, ok, "Agent should be of type *ACPAgent")
	assert.Equal(t, customWorkingDir, acpAgent.workingDir, "Working directory should be set to custom value")

	// Test with empty working directory (should return error)
	_, err = NewACPAgent("../../../bin/mock-agent", []string{"--mode", "acp"}, "")
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "working directory is required")
}

func TestACPAgentEventStreaming(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	agent, err := NewACPAgent("../../../bin/mock-agent", []string{"--mode", "acp"}, "/tmp")
	require.NoError(t, err)
	defer agent.Close()

	// Create event channel
	eventChan := make(chan *storage.ExecutionEvent, 100)

	// Test EventStreamer interface
	eventStreamer, ok := agent.(EventStreamer)
	require.True(t, ok, "ACPAgent should implement EventStreamer interface")
	eventStreamer.SetEventChannel(eventChan)

	// Test ContextSetter interface
	contextSetter, ok := agent.(ContextSetter)
	require.True(t, ok, "ACPAgent should implement ContextSetter interface")
	contextSetter.SetContext("test-execution", "test-node")

	// Execute and collect events
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	go func() {
		_, _ = agent.Execute(ctx, "test prompt")
	}()

	// Collect events with timeout
	var events []*storage.ExecutionEvent
	timeout := time.After(5 * time.Second)

	for {
		select {
		case event := <-eventChan:
			events = append(events, event)
			// Look for completion event
			if event.Type == storage.EventTypeStateChange &&
				event.State != nil &&
				*event.State == constants.TaskStateCompleted {
				goto done
			}
		case <-timeout:
			goto done
		}
	}

done:
	// Verify events were emitted
	assert.Greater(t, len(events), 0, "Should emit at least one event")

	// Verify all events have correct context
	for _, event := range events {
		assert.Equal(t, "test-execution", event.ExecutionID)
		assert.Equal(t, "test-node", event.NodeID)
		assert.Equal(t, storage.EventSourceACP, event.Source)
	}

	// Verify state change events
	var stateEvents []*storage.ExecutionEvent
	for _, event := range events {
		if event.Type == storage.EventTypeStateChange {
			stateEvents = append(stateEvents, event)
		}
	}
	assert.Greater(t, len(stateEvents), 0, "Should emit state change events")
}

func TestACPAgentHandleSessionUpdate(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	agent, err := NewACPAgent("../../../bin/mock-agent", []string{"--mode", "acp"}, "/tmp")
	require.NoError(t, err)
	defer agent.Close()

	// Access internal ACPAgent to test handleSessionUpdate directly
	acpAgent, ok := agent.(*ACPAgent)
	require.True(t, ok)

	// Create event channel and set context
	eventChan := make(chan *storage.ExecutionEvent, 100)
	acpAgent.SetEventChannel(eventChan)
	acpAgent.SetContext("test-execution", "test-node")

	// Test various session update types
	testCases := []struct {
		name         string
		updateJSON   string
		expectedType string
	}{
		{
			name: "AgentMessageChunk",
			updateJSON: `{
				"sessionUpdate": "agent_message_chunk",
				"content": {
					"type": "text",
					"text": "Test output"
				}
			}`,
			expectedType: storage.EventTypeOutput,
		},
		{
			name: "AgentThoughtChunk",
			updateJSON: `{
				"sessionUpdate": "agent_thought_chunk",
				"content": {
					"type": "text",
					"text": "Thinking..."
				}
			}`,
			expectedType: storage.EventTypeProgress,
		},
		{
			name: "Plan",
			updateJSON: `{
				"sessionUpdate": "plan",
				"entries": [
					{"description": "Step 1"},
					{"description": "Step 2"}
				]
			}`,
			expectedType: storage.EventTypePlanUpdate,
		},
		{
			name: "ToolCall",
			updateJSON: `{
				"sessionUpdate": "tool_call",
				"toolCallId": "tc_123",
				"title": "Read file",
				"status": "running"
			}`,
			expectedType: storage.EventTypeProgress,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			// Clear event channel
			for len(eventChan) > 0 {
				<-eventChan
			}

			notificationJSON := fmt.Sprintf(`{"update": %s}`, tc.updateJSON)
			params := json.RawMessage(notificationJSON)

			var textResponse strings.Builder
			err := acpAgent.handleSessionUpdate(params, &textResponse)
			require.NoError(t, err)

			// Check if event was emitted
			select {
			case event := <-eventChan:
				assert.Equal(t, tc.expectedType, event.Type)
				assert.Equal(t, "test-execution", event.ExecutionID)
				assert.Equal(t, "test-node", event.NodeID)
				assert.Equal(t, storage.EventSourceACP, event.Source)
			case <-time.After(1 * time.Second):
				t.Errorf("Expected event of type %s was not emitted", tc.expectedType)
			}
		})
	}
}

func TestACPAgentPermissionRequestDetection(t *testing.T) {
	agent := &ACPAgent{}

	// Create event channel and set context
	eventChan := make(chan *storage.ExecutionEvent, 100)
	agent.SetEventChannel(eventChan)
	agent.SetContext("test-execution", "test-node")

	// Test permission request JSON
	permissionJSON := `{
		"sessionId": "test-session",
		"toolCall": {
			"toolCallId": "file_write_123",
			"status": "pending"
		},
		"options": ["approve", "deny"]
	}`

	// Create a mock response
	response := &JSONRPCMessage{
		Method: "request/permission",
		Params: json.RawMessage(permissionJSON),
	}

	// Simulate permission request handling (we'd need to test this in context of Execute method)
	var permReq protocol.RequestPermissionRequest
	err := json.Unmarshal(response.Params, &permReq)
	require.NoError(t, err)

	// Emit the event manually to test the event structure
	rawJSON, _ := json.Marshal(response.Params)
	agent.emitEvent(&storage.ExecutionEvent{
		Type:    storage.EventTypeInputRequired,
		Message: fmt.Sprintf("Permission required for tool: %s", permReq.ToolCall.ToolCallId),
		Raw:     rawJSON,
	})

	// Verify event was emitted
	select {
	case event := <-eventChan:
		assert.Equal(t, storage.EventTypeInputRequired, event.Type)
		assert.Contains(t, event.Message, "file_write_123")
		assert.Equal(t, "test-execution", event.ExecutionID)
		assert.Equal(t, "test-node", event.NodeID)
		assert.Equal(t, storage.EventSourceACP, event.Source)
	case <-time.After(1 * time.Second):
		t.Error("Expected input_required event was not emitted")
	}
}
