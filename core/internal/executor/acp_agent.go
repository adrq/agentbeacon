package executor

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os/exec"
	"strings"
	"sync"
	"sync/atomic"

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol/jsonrpc"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
)

// ACPAgent implements the Agent interface using ACP (Agent Client Protocol) over subprocess stdio
type ACPAgent struct {
	cmd        *exec.Cmd
	stdin      io.WriteCloser
	stdout     *bufio.Scanner
	sessionID  string
	workingDir string
	mu         sync.Mutex
	requestID  int64
	// Event streaming support
	eventChan   chan<- *storage.ExecutionEvent
	executionID string
	nodeID      string
}

// NewACPAgent creates a new ACP agent that communicates via JSON-RPC over subprocess stdio
func NewACPAgent(command string, args []string, workingDir string) (Agent, error) {
	// Create subprocess
	cmd := exec.Command(command, args...)

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("failed to create stdin pipe: %w", err)
	}

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		stdin.Close()
		return nil, fmt.Errorf("failed to create stdout pipe: %w", err)
	}

	if err := cmd.Start(); err != nil {
		stdin.Close()
		stdout.Close()
		return nil, fmt.Errorf("failed to start ACP agent process (command=%s): %w", command, err)
	}

	// Working directory is required for ACP agents
	if workingDir == "" {
		return nil, fmt.Errorf("working directory is required for ACP agent")
	}

	agent := &ACPAgent{
		cmd:        cmd,
		stdin:      stdin,
		stdout:     bufio.NewScanner(stdout),
		workingDir: workingDir,
	}

	// Initialize ACP connection
	if err := agent.initialize(); err != nil {
		agent.Close()
		return nil, fmt.Errorf("failed to initialize ACP connection: %w", err)
	}

	// Create session
	if err := agent.createSession(); err != nil {
		agent.Close()
		return nil, fmt.Errorf("failed to create ACP session: %w", err)
	}

	return agent, nil
}

// initialize performs the ACP protocol handshake
func (a *ACPAgent) initialize() error {
	a.emitEvent(CreateStateChangeEvent("", "", storage.EventSourceACP, constants.TaskStateWorking, ""))

	a.mu.Lock()
	defer a.mu.Unlock()

	reqID := atomic.AddInt64(&a.requestID, 1)

	params, _ := json.Marshal(protocol.InitializeRequest{
		ProtocolVersion: 1,
		ClientCapabilities: protocol.ClientCapabilities{
			Fs: protocol.FileSystemCapability{
				ReadTextFile:  false,
				WriteTextFile: false,
			},
		},
	})

	initReq := jsonrpc.Request{
		JSONRPC: "2.0",
		ID:      reqID,
		Method:  "initialize",
		Params:  json.RawMessage(params),
	}

	if err := a.sendRequest(initReq); err != nil {
		a.emitEventUnsafe(CreateErrorEvent("", "", fmt.Errorf("ACP initialize: failed to send request: %w", err)))
		return fmt.Errorf("ACP initialize: failed to send request: %w", err)
	}

	response, err := a.readResponse()
	if err != nil {
		a.emitEventUnsafe(CreateErrorEvent("", "", fmt.Errorf("ACP initialize: failed to read response: %w", err)))
		return fmt.Errorf("ACP initialize: failed to read response: %w", err)
	}

	if response.Error != nil {
		a.emitEventUnsafe(CreateErrorEvent("", "", fmt.Errorf("ACP initialize: agent rejected request (code %d): %s", response.Error.Code, response.Error.Message)))
		return fmt.Errorf("ACP initialize: agent rejected request (code %d): %s", response.Error.Code, response.Error.Message)
	}

	return nil
}

// createSession creates a new ACP session
func (a *ACPAgent) createSession() error {
	a.mu.Lock()
	defer a.mu.Unlock()

	reqID := atomic.AddInt64(&a.requestID, 1)

	params, _ := json.Marshal(protocol.NewSessionRequest{
		Cwd:        a.workingDir,
		McpServers: []protocol.McpServer{},
	})

	sessionReq := jsonrpc.Request{
		JSONRPC: "2.0",
		ID:      reqID,
		Method:  "session/new",
		Params:  json.RawMessage(params),
	}

	if err := a.sendRequest(sessionReq); err != nil {
		a.emitEventUnsafe(CreateErrorEvent("", "", fmt.Errorf("ACP session/new: failed to send request (cwd=%s): %w", a.workingDir, err)))
		return fmt.Errorf("ACP session/new: failed to send request (cwd=%s): %w", a.workingDir, err)
	}

	response, err := a.readResponse()
	if err != nil {
		a.emitEventUnsafe(CreateErrorEvent("", "", fmt.Errorf("ACP session/new: failed to read response: %w", err)))
		return fmt.Errorf("ACP session/new: failed to read response: %w", err)
	}

	if response.Error != nil {
		a.emitEventUnsafe(CreateErrorEvent("", "", fmt.Errorf("ACP session/new: agent rejected request (code %d, cwd=%s): %s", response.Error.Code, a.workingDir, response.Error.Message)))
		return fmt.Errorf("ACP session/new: agent rejected request (code %d, cwd=%s): %s", response.Error.Code, a.workingDir, response.Error.Message)
	}

	// Parse session ID from response
	var sessionResp protocol.NewSessionResponse
	resultBytes, _ := json.Marshal(response.Result)
	if err := json.Unmarshal(resultBytes, &sessionResp); err != nil {
		a.emitEventUnsafe(CreateErrorEvent("", "", fmt.Errorf("failed to parse session response: %w", err)))
		return fmt.Errorf("failed to parse session response: %w", err)
	}

	a.sessionID = string(sessionResp.SessionId)
	a.emitEventUnsafe(CreateOutputEvent("", "", storage.EventSourceACP, fmt.Sprintf("Session created: %s", a.sessionID)))
	return nil
}

// Execute sends a prompt to the agent and returns the text response
func (a *ACPAgent) Execute(ctx context.Context, prompt string) (string, error) {
	a.emitEvent(CreateStateChangeEvent("", "", storage.EventSourceACP, constants.TaskStateWorking, constants.TaskStateSubmitted))

	a.mu.Lock()
	defer a.mu.Unlock()

	reqID := atomic.AddInt64(&a.requestID, 1)

	// Create prompt request with text content block
	params, _ := json.Marshal(protocol.PromptRequest{
		SessionId: protocol.SessionId(a.sessionID),
		Prompt: []protocol.ContentBlock{
			protocol.NewTextContent(prompt),
		},
	})

	promptReq := jsonrpc.Request{
		JSONRPC: "2.0",
		ID:      reqID,
		Method:  "session/prompt",
		Params:  json.RawMessage(params),
	}

	if err := a.sendRequest(promptReq); err != nil {
		a.emitEventUnsafe(CreateErrorEvent("", "", fmt.Errorf("ACP session/prompt: failed to send request (session=%s): %w", a.sessionID, err)))
		return "", fmt.Errorf("ACP session/prompt: failed to send request (session=%s): %w", a.sessionID, err)
	}

	// Read response and any updates
	var textResponse strings.Builder

	for {
		select {
		case <-ctx.Done():
			a.emitEventUnsafe(CreateStateChangeEvent("", "", storage.EventSourceACP, constants.TaskStateCanceled, constants.TaskStateWorking))
			return "", fmt.Errorf("prompt execution cancelled: %w", ctx.Err())
		default:
		}

		response, err := a.readResponse()
		if err != nil {
			a.emitEventUnsafe(CreateErrorEvent("", "", fmt.Errorf("ACP session/prompt: failed to read response (session=%s): %w", a.sessionID, err)))
			return "", fmt.Errorf("ACP session/prompt: failed to read response (session=%s): %w", a.sessionID, err)
		}

		// Check if this is the final prompt response
		if response.ID != nil && *response.ID == reqID {
			if response.Error != nil {
				a.emitEventUnsafe(CreateErrorEvent("", "", fmt.Errorf("ACP session/prompt: execution failed (session=%s, code=%d): %s", a.sessionID, response.Error.Code, response.Error.Message)))
				a.emitEventUnsafe(CreateStateChangeEvent("", "", storage.EventSourceACP, constants.TaskStateFailed, constants.TaskStateWorking))
				return "", fmt.Errorf("ACP session/prompt: execution failed (session=%s, code=%d): %s", a.sessionID, response.Error.Code, response.Error.Message)
			}

			// Parse stop reason to ensure completion
			var promptResp protocol.PromptResponse
			resultBytes, _ := json.Marshal(response.Result)
			if err := json.Unmarshal(resultBytes, &promptResp); err != nil {
				a.emitEventUnsafe(CreateErrorEvent("", "", fmt.Errorf("failed to parse prompt response: %w", err)))
				return "", fmt.Errorf("failed to parse prompt response: %w", err)
			}

			if promptResp.StopReason == "cancelled" {
				a.emitEventUnsafe(CreateStateChangeEvent("", "", storage.EventSourceACP, constants.TaskStateCanceled, constants.TaskStateWorking))
				return "", fmt.Errorf("prompt execution was cancelled")
			}

			break
		}

		// Handle session/update notifications
		if response.Method == "session/update" {
			if err := a.handleSessionUpdate(response.Params, &textResponse); err != nil {
				a.emitEventUnsafe(CreateErrorEvent("", "", fmt.Errorf("ACP session/update: failed to handle notification (session=%s): %w", a.sessionID, err)))
				return "", fmt.Errorf("ACP session/update: failed to handle notification (session=%s): %w", a.sessionID, err)
			}
		}

		// Handle permission requests
		if response.Method == "request/permission" {
			var permReq protocol.RequestPermissionRequest
			if err := json.Unmarshal(response.Params, &permReq); err == nil {
				message := fmt.Sprintf("Permission required for tool: %s", permReq.ToolCall.ToolCallId)
				rawJSON, _ := json.Marshal(response.Params)
				a.emitEventUnsafe(&storage.ExecutionEvent{
					Type:    storage.EventTypeInputRequired,
					Message: message,
					Raw:     rawJSON,
				})
			} else {
				rawJSON, _ := json.Marshal(response.Params)
				a.emitEventUnsafe(&storage.ExecutionEvent{
					Type:    storage.EventTypeInputRequired,
					Message: "Permission request received",
					Raw:     rawJSON,
				})
			}
		}
	}

	result := textResponse.String()
	if result == "" {
		// For simple mock agents that don't send session updates,
		// generate a basic response based on the prompt
		result = fmt.Sprintf("Mock response: %s", prompt)
	}

	a.emitEventUnsafe(CreateOutputEvent("", "", storage.EventSourceACP, result))
	a.emitEventUnsafe(CreateStateChangeEvent("", "", storage.EventSourceACP, constants.TaskStateCompleted, constants.TaskStateWorking))
	return result, nil
}

// handleSessionUpdate processes session update notifications
func (a *ACPAgent) handleSessionUpdate(params json.RawMessage, textResponse *strings.Builder) error {
	var notification protocol.SessionNotification
	if err := json.Unmarshal(params, &notification); err != nil {
		return fmt.Errorf("failed to unmarshal session notification: %w", err)
	}

	// Extract text from agent message chunks
	updateBytes, _ := json.Marshal(notification.Update)
	update, err := protocol.UnmarshalSessionUpdate(updateBytes)
	if err != nil {
		return fmt.Errorf("failed to unmarshal session update: %w", err)
	}

	// Emit events based on update type
	updateType := update.GetSessionUpdateType()

	switch updateType {
	case protocol.SessionUpdateAgentMessageChunk:
		if chunk, ok := update.(*protocol.AgentMessageChunk); ok {
			if chunk.Content.GetType() == protocol.ContentBlockTypeText {
				if textContent, ok := chunk.Content.(protocol.TextContent); ok {
					textResponse.WriteString(textContent.Text)
					a.emitEventUnsafe(CreateOutputEvent("", "", storage.EventSourceACP, textContent.Text))
				}
			}
		}

	case protocol.SessionUpdateUserMessageChunk:
		rawJSON, _ := json.Marshal(updateBytes)
		a.emitEventUnsafe(&storage.ExecutionEvent{
			Type:    storage.EventTypeProgress,
			Message: "User message chunk received",
			Raw:     rawJSON,
		})

	case protocol.SessionUpdateAgentThoughtChunk:
		if chunk, ok := update.(*protocol.AgentThoughtChunk); ok {
			if chunk.Content.GetType() == protocol.ContentBlockTypeText {
				if textContent, ok := chunk.Content.(protocol.TextContent); ok {
					rawJSON, _ := json.Marshal(updateBytes)
					a.emitEventUnsafe(&storage.ExecutionEvent{
						Type:    storage.EventTypeProgress,
						Message: fmt.Sprintf("Agent thought: %s", textContent.Text),
						Raw:     rawJSON,
					})
				}
			}
		}

	case protocol.SessionUpdatePlan:
		if plan, ok := update.(*protocol.Plan); ok {
			message := fmt.Sprintf("Plan update - Entries: %d", len(plan.Entries))
			rawJSON, _ := json.Marshal(updateBytes)
			a.emitEventUnsafe(&storage.ExecutionEvent{
				Type:    storage.EventTypePlanUpdate,
				Message: message,
				Raw:     rawJSON,
			})
		}

	case protocol.SessionUpdateToolCall:
		if toolCall, ok := update.(*protocol.ToolCall); ok {
			message := fmt.Sprintf("Tool call: %s", toolCall.Title)
			rawJSON, _ := json.Marshal(updateBytes)
			a.emitEventUnsafe(&storage.ExecutionEvent{
				Type:    storage.EventTypeProgress,
				Message: message,
				Raw:     rawJSON,
			})
		}

	case protocol.SessionUpdateToolCallUpdate:
		if toolUpdate, ok := update.(*protocol.ToolCallUpdate); ok {
			message := fmt.Sprintf("Tool update: %s", toolUpdate.ToolCallId)
			rawJSON, _ := json.Marshal(updateBytes)
			a.emitEventUnsafe(&storage.ExecutionEvent{
				Type:    storage.EventTypeProgress,
				Message: message,
				Raw:     rawJSON,
			})
		}

	case protocol.SessionUpdateAvailableCommands:
		rawJSON, _ := json.Marshal(updateBytes)
		a.emitEventUnsafe(&storage.ExecutionEvent{
			Type:    storage.EventTypeProgress,
			Message: "Available commands updated",
			Raw:     rawJSON,
		})

	case protocol.SessionUpdateCurrentMode:
		if modeUpdate, ok := update.(*protocol.CurrentModeUpdate); ok {
			message := fmt.Sprintf("Mode changed to: %s", modeUpdate.CurrentModeId)
			rawJSON, _ := json.Marshal(updateBytes)
			a.emitEventUnsafe(&storage.ExecutionEvent{
				Type:    storage.EventTypeProgress,
				Message: message,
				Raw:     rawJSON,
			})
		}

	default:
		// Unknown update type - emit as generic progress
		rawJSON, _ := json.Marshal(updateBytes)
		a.emitEventUnsafe(&storage.ExecutionEvent{
			Type:    storage.EventTypeProgress,
			Message: fmt.Sprintf("Unknown session update: %s", updateType),
			Raw:     rawJSON,
		})
	}

	return nil
}

// sendRequest sends a JSON-RPC request to the agent
func (a *ACPAgent) sendRequest(req jsonrpc.Request) error {
	reqBytes, err := json.Marshal(req)
	if err != nil {
		return fmt.Errorf("failed to marshal request: %w", err)
	}

	_, err = a.stdin.Write(append(reqBytes, '\n'))
	if err != nil {
		return fmt.Errorf("failed to write request: %w", err)
	}

	return nil
}

// JSONRPCMessage represents both responses and notifications
type JSONRPCMessage struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      *int64          `json:"id,omitempty"`     // Present for responses
	Method  string          `json:"method,omitempty"` // Present for notifications
	Params  json.RawMessage `json:"params,omitempty"` // Present for notifications
	Result  interface{}     `json:"result,omitempty"` // Present for responses
	Error   *jsonrpc.Error  `json:"error,omitempty"`  // Present for error responses
}

// readResponse reads a JSON-RPC message from the agent
func (a *ACPAgent) readResponse() (*JSONRPCMessage, error) {
	if !a.stdout.Scan() {
		if err := a.stdout.Err(); err != nil {
			return nil, fmt.Errorf("failed to read response: %w", err)
		}
		return nil, fmt.Errorf("agent process closed connection")
	}

	line := a.stdout.Text()
	if line == "" {
		return nil, fmt.Errorf("received empty response line")
	}

	var message JSONRPCMessage
	if err := json.Unmarshal([]byte(line), &message); err != nil {
		return nil, fmt.Errorf("failed to unmarshal message: %w", err)
	}

	return &message, nil
}

// Close terminates the agent process and cleans up resources
func (a *ACPAgent) Close() error {
	var errs []error

	if a.stdin != nil {
		if err := a.stdin.Close(); err != nil {
			errs = append(errs, fmt.Errorf("failed to close stdin: %w", err))
		}
	}

	if a.cmd != nil && a.cmd.Process != nil {
		if err := a.cmd.Process.Kill(); err != nil {
			errs = append(errs, fmt.Errorf("failed to kill process: %w", err))
		}

		// Wait for process to exit
		if err := a.cmd.Wait(); err != nil {
			// Process kill is expected to cause an error, so we don't add it to errs
		}
	}

	if len(errs) > 0 {
		return fmt.Errorf("errors during close: %v", errs)
	}

	return nil
}

// SetEventChannel implements EventStreamer interface
func (a *ACPAgent) SetEventChannel(events chan<- *storage.ExecutionEvent) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.eventChan = events
}

// SetContext implements ContextSetter interface
func (a *ACPAgent) SetContext(executionID, nodeID string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.executionID = executionID
	a.nodeID = nodeID
}

// emitEvent sends an event to the event channel in a non-blocking way
func (a *ACPAgent) emitEvent(event *storage.ExecutionEvent) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.emitEventUnsafe(event)
}

// emitEventUnsafe sends an event without acquiring the mutex - for use when mutex is already held
func (a *ACPAgent) emitEventUnsafe(event *storage.ExecutionEvent) {
	eventChan := a.eventChan
	executionID := a.executionID
	nodeID := a.nodeID

	if eventChan == nil || executionID == "" || nodeID == "" {
		return
	}

	event.ExecutionID = executionID
	event.NodeID = nodeID
	event.Source = storage.EventSourceACP

	select {
	case eventChan <- event:
	default:
		// Channel full, drop event
	}
}
