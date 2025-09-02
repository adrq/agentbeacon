package protocol

import (
	"testing"
)

// Test ContentBlock union type discrimination logic
func TestContentBlockUnmarshaling(t *testing.T) {
	tests := []struct {
		name         string
		jsonData     string
		expectedType string
		expectError  bool
	}{
		{
			name:         "text content block",
			jsonData:     `{"type": "text", "text": "Hello world"}`,
			expectedType: ContentBlockTypeText,
			expectError:  false,
		},
		{
			name:         "image content block",
			jsonData:     `{"type": "image", "data": "base64data", "mimeType": "image/png"}`,
			expectedType: ContentBlockTypeImage,
			expectError:  false,
		},
		{
			name:         "audio content block",
			jsonData:     `{"type": "audio", "data": "base64audio", "mimeType": "audio/wav"}`,
			expectedType: ContentBlockTypeAudio,
			expectError:  false,
		},
		{
			name:         "resource link content block",
			jsonData:     `{"type": "resource_link", "uri": "file://test.txt", "name": "test.txt"}`,
			expectedType: ContentBlockTypeResourceLink,
			expectError:  false,
		},
		{
			name:         "embedded resource content block",
			jsonData:     `{"type": "resource", "resource": {"uri": "file://test.txt", "text": "content"}}`,
			expectedType: ContentBlockTypeResource,
			expectError:  false,
		},
		{
			name:        "missing type field",
			jsonData:    `{"text": "Hello world"}`,
			expectError: true,
		},
		{
			name:        "invalid type field",
			jsonData:    `{"type": "unknown_type", "text": "Hello world"}`,
			expectError: true,
		},
		{
			name:        "non-string type field",
			jsonData:    `{"type": 123, "text": "Hello world"}`,
			expectError: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cb, err := UnmarshalContentBlock([]byte(tt.jsonData))

			if tt.expectError {
				if err == nil {
					t.Errorf("expected error but got none")
				}
				return
			}

			if err != nil {
				t.Errorf("unexpected error: %v", err)
				return
			}

			if cb.GetType() != tt.expectedType {
				t.Errorf("expected type %s, got %s", tt.expectedType, cb.GetType())
			}
		})
	}
}

// Test SessionUpdate union type discrimination logic
func TestSessionUpdateUnmarshaling(t *testing.T) {
	tests := []struct {
		name         string
		jsonData     string
		expectedType string
		expectError  bool
	}{
		{
			name:         "user message chunk",
			jsonData:     `{"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "Hello"}}`,
			expectedType: SessionUpdateUserMessageChunk,
			expectError:  false,
		},
		{
			name:         "agent message chunk",
			jsonData:     `{"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hi there"}}`,
			expectedType: SessionUpdateAgentMessageChunk,
			expectError:  false,
		},
		{
			name:         "agent thought chunk",
			jsonData:     `{"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "Thinking..."}}`,
			expectedType: SessionUpdateAgentThoughtChunk,
			expectError:  false,
		},
		{
			name:         "tool call",
			jsonData:     `{"sessionUpdate": "tool_call", "toolCallId": "call_123", "title": "Read file"}`,
			expectedType: SessionUpdateToolCall,
			expectError:  false,
		},
		{
			name:         "tool call update",
			jsonData:     `{"sessionUpdate": "tool_call_update", "toolCallId": "call_123"}`,
			expectedType: SessionUpdateToolCallUpdate,
			expectError:  false,
		},
		{
			name:         "plan",
			jsonData:     `{"sessionUpdate": "plan", "entries": [{"content": "Step 1", "priority": "high", "status": "pending"}]}`,
			expectedType: SessionUpdatePlan,
			expectError:  false,
		},
		{
			name:         "available commands update",
			jsonData:     `{"sessionUpdate": "available_commands_update", "availableCommands": [{"name": "test", "description": "test command"}]}`,
			expectedType: SessionUpdateAvailableCommands,
			expectError:  false,
		},
		{
			name:         "current mode update",
			jsonData:     `{"sessionUpdate": "current_mode_update", "currentModeId": "mode123"}`,
			expectedType: SessionUpdateCurrentMode,
			expectError:  false,
		},
		{
			name:        "missing sessionUpdate field",
			jsonData:    `{"content": {"type": "text", "text": "Hello"}}`,
			expectError: true,
		},
		{
			name:        "invalid sessionUpdate field",
			jsonData:    `{"sessionUpdate": "unknown_update", "content": {"type": "text", "text": "Hello"}}`,
			expectError: true,
		},
		{
			name:        "non-string sessionUpdate field",
			jsonData:    `{"sessionUpdate": 123, "content": {"type": "text", "text": "Hello"}}`,
			expectError: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			su, err := UnmarshalSessionUpdate([]byte(tt.jsonData))

			if tt.expectError {
				if err == nil {
					t.Errorf("expected error but got none")
				}
				return
			}

			if err != nil {
				t.Errorf("unexpected error: %v", err)
				return
			}

			if su.GetSessionUpdateType() != tt.expectedType {
				t.Errorf("expected type %s, got %s", tt.expectedType, su.GetSessionUpdateType())
			}
		})
	}
}

// Test nested ContentBlock unmarshaling within SessionUpdate
func TestNestedContentBlockUnmarshaling(t *testing.T) {
	jsonData := `{
		"sessionUpdate": "agent_message_chunk",
		"content": {
			"type": "text",
			"text": "This is a nested content block"
		}
	}`

	su, err := UnmarshalSessionUpdate([]byte(jsonData))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Verify we got an AgentMessageChunk
	agentChunk, ok := su.(AgentMessageChunk)
	if !ok {
		t.Fatalf("expected AgentMessageChunk, got %T", su)
	}

	// Verify the nested content block was properly parsed
	textContent, ok := agentChunk.Content.(TextContent)
	if !ok {
		t.Fatalf("expected TextContent, got %T", agentChunk.Content)
	}

	if textContent.Text != "This is a nested content block" {
		t.Errorf("expected text content, got %s", textContent.Text)
	}
}

// Test ToolCallContent union type discrimination logic
func TestToolCallContentUnmarshaling(t *testing.T) {
	tests := []struct {
		name         string
		jsonData     string
		expectedType string
		expectError  bool
	}{
		{
			name:         "standard tool call content",
			jsonData:     `{"type": "content", "content": {"type": "text", "text": "Hello world"}}`,
			expectedType: ToolCallContentTypeStandard,
			expectError:  false,
		},
		{
			name:         "diff tool call content",
			jsonData:     `{"type": "diff", "path": "/test.txt", "oldText": "old", "newText": "new"}`,
			expectedType: ToolCallContentTypeDiff,
			expectError:  false,
		},
		{
			name:         "terminal tool call content",
			jsonData:     `{"type": "terminal", "terminalId": "term123"}`,
			expectedType: ToolCallContentTypeTerminal,
			expectError:  false,
		},
		{
			name:        "missing type field",
			jsonData:    `{"path": "/test.txt", "newText": "new"}`,
			expectError: true,
		},
		{
			name:        "invalid type field",
			jsonData:    `{"type": "unknown_type", "path": "/test.txt"}`,
			expectError: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tc, err := UnmarshalToolCallContent([]byte(tt.jsonData))

			if tt.expectError {
				if err == nil {
					t.Errorf("expected error but got none")
				}
				return
			}

			if err != nil {
				t.Errorf("unexpected error: %v", err)
				return
			}

			if tc.GetContentType() != tt.expectedType {
				t.Errorf("expected type %s, got %s", tt.expectedType, tc.GetContentType())
			}
		})
	}
}

// Test helper functions create correct types
func TestHelperFunctions(t *testing.T) {
	t.Run("NewTextContent", func(t *testing.T) {
		content := NewTextContent("hello")
		if content.GetType() != ContentBlockTypeText {
			t.Errorf("expected type %s, got %s", ContentBlockTypeText, content.GetType())
		}
		if content.Text != "hello" {
			t.Errorf("expected text 'hello', got %s", content.Text)
		}
	})

	t.Run("NewAgentMessageChunk", func(t *testing.T) {
		textContent := NewTextContent("response")
		chunk := NewAgentMessageChunk(textContent)
		if chunk.GetSessionUpdateType() != SessionUpdateAgentMessageChunk {
			t.Errorf("expected type %s, got %s", SessionUpdateAgentMessageChunk, chunk.GetSessionUpdateType())
		}
	})

	t.Run("NewPlanEntry", func(t *testing.T) {
		entry := NewPlanEntry("Do something", PlanEntryPriorityHigh, PlanEntryStatusPending)
		if entry.Content != "Do something" {
			t.Errorf("expected content 'Do something', got %s", entry.Content)
		}
		if entry.Priority != PlanEntryPriorityHigh {
			t.Errorf("expected priority %s, got %s", PlanEntryPriorityHigh, entry.Priority)
		}
		if entry.Status != PlanEntryStatusPending {
			t.Errorf("expected status %s, got %s", PlanEntryStatusPending, entry.Status)
		}
	})

	t.Run("NewAvailableCommandsUpdate", func(t *testing.T) {
		commands := []AvailableCommand{{Name: "test", Description: "test command"}}
		update := NewAvailableCommandsUpdate(commands)
		if update.GetSessionUpdateType() != SessionUpdateAvailableCommands {
			t.Errorf("expected type %s, got %s", SessionUpdateAvailableCommands, update.GetSessionUpdateType())
		}
		if len(update.AvailableCommands) != 1 {
			t.Errorf("expected 1 command, got %d", len(update.AvailableCommands))
		}
	})

	t.Run("NewCurrentModeUpdate", func(t *testing.T) {
		modeId := SessionModeId("mode123")
		update := NewCurrentModeUpdate(modeId)
		if update.GetSessionUpdateType() != SessionUpdateCurrentMode {
			t.Errorf("expected type %s, got %s", SessionUpdateCurrentMode, update.GetSessionUpdateType())
		}
		if update.CurrentModeId != modeId {
			t.Errorf("expected mode ID %s, got %s", modeId, update.CurrentModeId)
		}
	})

	t.Run("NewToolCallContentStandard", func(t *testing.T) {
		textContent := NewTextContent("test")
		content := NewToolCallContentStandard(textContent)
		if content.GetContentType() != ToolCallContentTypeStandard {
			t.Errorf("expected type %s, got %s", ToolCallContentTypeStandard, content.GetContentType())
		}
	})

	t.Run("NewToolCallContentDiff", func(t *testing.T) {
		oldText := "old content"
		content := NewToolCallContentDiff("/test.txt", &oldText, "new content")
		if content.GetContentType() != ToolCallContentTypeDiff {
			t.Errorf("expected type %s, got %s", ToolCallContentTypeDiff, content.GetContentType())
		}
		if content.Path != "/test.txt" {
			t.Errorf("expected path '/test.txt', got %s", content.Path)
		}
		if *content.OldText != "old content" {
			t.Errorf("expected old text 'old content', got %s", *content.OldText)
		}
		if content.NewText != "new content" {
			t.Errorf("expected new text 'new content', got %s", content.NewText)
		}
	})

	t.Run("NewToolCallContentTerminal", func(t *testing.T) {
		content := NewToolCallContentTerminal("term123")
		if content.GetContentType() != ToolCallContentTypeTerminal {
			t.Errorf("expected type %s, got %s", ToolCallContentTypeTerminal, content.GetContentType())
		}
		if content.TerminalId != "term123" {
			t.Errorf("expected terminal ID 'term123', got %s", content.TerminalId)
		}
	})
}
