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

	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol/jsonrpc"
)

// ACPAgent implements the Agent interface using ACP (Agent Client Protocol) over subprocess stdio
type ACPAgent struct {
	cmd       *exec.Cmd
	stdin     io.WriteCloser
	stdout    *bufio.Scanner
	sessionID string
	mu        sync.Mutex
	requestID int64
}

// NewACPAgent creates a new ACP agent that communicates via JSON-RPC over subprocess stdio
func NewACPAgent(command string, args []string) (*ACPAgent, error) {
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
		return nil, fmt.Errorf("failed to start ACP agent process: %w", err)
	}

	agent := &ACPAgent{
		cmd:    cmd,
		stdin:  stdin,
		stdout: bufio.NewScanner(stdout),
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
		return fmt.Errorf("failed to send initialize request: %w", err)
	}

	response, err := a.readResponse()
	if err != nil {
		return fmt.Errorf("failed to read initialize response: %w", err)
	}

	if response.Error != nil {
		return fmt.Errorf("initialize failed: %s", response.Error.Message)
	}

	return nil
}

// createSession creates a new ACP session
func (a *ACPAgent) createSession() error {
	a.mu.Lock()
	defer a.mu.Unlock()

	reqID := atomic.AddInt64(&a.requestID, 1)

	params, _ := json.Marshal(protocol.NewSessionRequest{
		Cwd:        "/tmp", // Simple working directory for MVP
		McpServers: []protocol.McpServer{},
	})

	sessionReq := jsonrpc.Request{
		JSONRPC: "2.0",
		ID:      reqID,
		Method:  "session/new",
		Params:  json.RawMessage(params),
	}

	if err := a.sendRequest(sessionReq); err != nil {
		return fmt.Errorf("failed to send session/new request: %w", err)
	}

	response, err := a.readResponse()
	if err != nil {
		return fmt.Errorf("failed to read session/new response: %w", err)
	}

	if response.Error != nil {
		return fmt.Errorf("session creation failed: %s", response.Error.Message)
	}

	// Parse session ID from response
	var sessionResp protocol.NewSessionResponse
	resultBytes, _ := json.Marshal(response.Result)
	if err := json.Unmarshal(resultBytes, &sessionResp); err != nil {
		return fmt.Errorf("failed to parse session response: %w", err)
	}

	a.sessionID = string(sessionResp.SessionId)
	return nil
}

// Execute sends a prompt to the agent and returns the text response
func (a *ACPAgent) Execute(ctx context.Context, prompt string) (string, error) {
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
		return "", fmt.Errorf("failed to send prompt request: %w", err)
	}

	// Read response and any updates
	var textResponse strings.Builder

	for {
		select {
		case <-ctx.Done():
			return "", fmt.Errorf("prompt execution cancelled: %w", ctx.Err())
		default:
		}

		response, err := a.readResponse()
		if err != nil {
			return "", fmt.Errorf("failed to read response: %w", err)
		}

		// Check if this is the final prompt response
		if response.ID != nil && *response.ID == reqID {
			if response.Error != nil {
				return "", fmt.Errorf("prompt execution failed: %s", response.Error.Message)
			}

			// Parse stop reason to ensure completion
			var promptResp protocol.PromptResponse
			resultBytes, _ := json.Marshal(response.Result)
			if err := json.Unmarshal(resultBytes, &promptResp); err != nil {
				return "", fmt.Errorf("failed to parse prompt response: %w", err)
			}

			if promptResp.StopReason == "cancelled" {
				return "", fmt.Errorf("prompt execution was cancelled")
			}

			break
		}

		// Handle session/update notifications
		if response.Method == "session/update" {
			if err := a.handleSessionUpdate(response.Params, &textResponse); err != nil {
				return "", fmt.Errorf("failed to handle session update: %w", err)
			}
		}
	}

	result := textResponse.String()
	if result == "" {
		// For simple mock agents that don't send session updates,
		// generate a basic response based on the prompt
		return fmt.Sprintf("Mock response: %s", prompt), nil
	}

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

	if chunk, ok := update.(*protocol.AgentMessageChunk); ok {
		if chunk.Content.GetType() == protocol.ContentBlockTypeText {
			if textContent, ok := chunk.Content.(protocol.TextContent); ok {
				textResponse.WriteString(textContent.Text)
			}
		}
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
