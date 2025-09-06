package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol/jsonrpc"
	"github.com/google/uuid"
)

func main() {
	var configFile string
	var mode string
	var port string
	flag.StringVar(&configFile, "config", "", "JSON file with custom responses")
	flag.StringVar(&mode, "mode", "stdio", "Mode to run in: stdio, a2a, or acp")
	flag.StringVar(&port, "port", "9457", "Port for A2A server mode")
	flag.Parse()

	// Load custom responses
	responses := loadCustomResponses(configFile)

	// Run in appropriate mode
	if mode == "a2a" {
		runA2AServer(port, responses)
	} else if mode == "acp" {
		runACPMode(responses)
	} else {
		runStdioMode(responses)
	}
}

// loadCustomResponses loads custom responses from config file
func loadCustomResponses(configFile string) map[string]string {

	responses := make(map[string]string)
	if configFile != "" {
		data, err := os.ReadFile(configFile)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error reading config file: %v\n", err)
			os.Exit(1)
		}

		if err := json.Unmarshal(data, &responses); err != nil {
			fmt.Fprintf(os.Stderr, "Error parsing config JSON: %v\n", err)
			os.Exit(1)
		}
	}
	return responses
}

// runStdioMode runs the agent in stdio mode with A2A-compliant results
func runStdioMode(responses map[string]string) {
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		input := strings.TrimSpace(scanner.Text())

		// Try to parse as JSON task request, fallback to plain prompt
		var taskRequest map[string]interface{}
		var prompt string

		if err := json.Unmarshal([]byte(input), &taskRequest); err == nil {
			// Structured input - extract prompt from request
			if req, ok := taskRequest["request"].(map[string]interface{}); ok {
				if promptVal, exists := req["prompt"]; exists {
					prompt = fmt.Sprintf("%v", promptVal)
				} else if taskVal, exists := req["task"]; exists {
					prompt = fmt.Sprintf("%v", taskVal)
				}
			}
			if prompt == "" {
				prompt = input // fallback to raw input
			}
		} else {
			// Plain text input
			prompt = input
		}

		// Generate A2A-compliant response
		a2aResponse := generateA2AResponse(prompt, responses)
		responseJSON, _ := json.Marshal(a2aResponse)
		fmt.Println(string(responseJSON))
	}

	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "Error reading from stdin: %v\n", err)
		os.Exit(1)
	}
}

// generateA2AResponse creates an A2A-compliant TaskResponse based on prompt processing
func generateA2AResponse(prompt string, responses map[string]string) interface{} {
	// Handle special test commands that cause failures
	if strings.Contains(prompt, "FAIL_NODE") || strings.Contains(prompt, "FAIL_ONCE") {
		// For FAIL_ONCE, randomly succeed or fail
		if strings.Contains(prompt, "FAIL_ONCE") {
			now := time.Now().UnixNano()
			if now%2 != 0 {
				// Success case for FAIL_ONCE
				return createSuccessResponse(fmt.Sprintf("Mock success after retry: %s", prompt))
			}
		}

		// Failure case
		return createFailureResponse(fmt.Sprintf("Mock agent failure: %s", prompt))
	}

	// Handle delay patterns
	if strings.HasPrefix(prompt, "DELAY_") {
		delayStr := strings.TrimPrefix(prompt, "DELAY_")
		if delayStr != "" {
			var delay time.Duration
			switch delayStr {
			case "1":
				delay = 1 * time.Second
			case "2":
				delay = 2 * time.Second
			case "3":
				delay = 3 * time.Second
			case "5":
				delay = 5 * time.Second
			case "1500":
				delay = 1500 * time.Millisecond
			default:
				delay = 1 * time.Second
			}
			time.Sleep(delay)
			return createSuccessResponse(fmt.Sprintf("Mock response after %s delay: %s", delayStr, prompt))
		}
	}

	// Handle HANG command (will block indefinitely)
	if prompt == "HANG" {
		time.Sleep(1 * time.Hour)
		return createSuccessResponse("This should never be reached")
	}

	// Check for custom response
	if response, exists := responses[prompt]; exists {
		if response == "HANG" {
			time.Sleep(1 * time.Hour)
			return createSuccessResponse("This should never be reached")
		}
		return createSuccessResponse(response)
	}

	// Default success response
	return createSuccessResponse(fmt.Sprintf("Mock response: %s", prompt))
}

// createSuccessResponse creates a successful A2A-compliant TaskResponse
func createSuccessResponse(output string) interface{} {
	return map[string]interface{}{
		"taskStatus": map[string]interface{}{
			"state":     "completed",
			"timestamp": time.Now().Format(time.RFC3339),
		},
		"artifacts": []map[string]interface{}{
			{
				"artifactId":  uuid.New().String(),
				"name":        "agent-output",
				"description": "Output from mock agent execution",
				"parts": []map[string]interface{}{
					{
						"text": output,
					},
				},
			},
		},
	}
}

// createFailureResponse creates a failed A2A-compliant TaskResponse
func createFailureResponse(errorMsg string) interface{} {
	return map[string]interface{}{
		"taskStatus": map[string]interface{}{
			"state": "failed",
			"message": map[string]interface{}{
				"role": "assistant",
				"content": []map[string]interface{}{
					{
						"text": errorMsg,
					},
				},
			},
			"timestamp": time.Now().Format(time.RFC3339),
		},
	}
}

// processPrompt processes a prompt and returns the appropriate response
func processPrompt(prompt string, responses map[string]string) string {
	// Check for custom response
	if response, exists := responses[prompt]; exists {
		// Handle special test responses
		if response == "HANG" {
			time.Sleep(1 * time.Hour)
			return ""
		}
		return response
	}

	// Handle built-in test commands
	if prompt == "HANG" {
		time.Sleep(1 * time.Hour)
		return ""
	} else if strings.HasPrefix(prompt, "DELAY_") {
		delayStr := strings.TrimPrefix(prompt, "DELAY_")
		if delayStr != "" {
			var delay time.Duration
			switch delayStr {
			case "1":
				delay = 1 * time.Second
			case "2":
				delay = 2 * time.Second
			case "3":
				delay = 3 * time.Second
			case "5":
				delay = 5 * time.Second
			case "1500":
				delay = 1500 * time.Millisecond
			default:
				delay = 1 * time.Second
			}
			time.Sleep(delay)
			return fmt.Sprintf("Mock response after %s delay: %s", delayStr, prompt)
		}
	} else if strings.Contains(prompt, "FAIL_NODE") {
		os.Exit(1)
	} else if strings.Contains(prompt, "FAIL_ONCE") {
		now := time.Now().UnixNano()
		if now%2 == 0 {
			fmt.Printf("Mock failure: %s\n", prompt)
			os.Exit(1)
		} else {
			return fmt.Sprintf("Mock success after retry: %s", prompt)
		}
	}

	return fmt.Sprintf("Mock response: %s", prompt)
}

// runACPMode runs the agent in ACP mode (JSON-RPC over stdio)
func runACPMode(responses map[string]string) {
	server := &MockACPServer{
		sessions:  make(map[string]*ACPSession),
		responses: responses,
	}

	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}

		var req jsonrpc.Request
		if err := json.Unmarshal([]byte(line), &req); err != nil {
			server.writeError(&jsonrpc.Error{
				Code:    jsonrpc.ErrorCodeParseError,
				Message: "Parse error",
			}, nil)
			continue
		}

		server.handleRequest(req)
	}

	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "Error reading from stdin: %v\n", err)
		os.Exit(1)
	}
}

// MockACPServer implements ACP protocol for testing
type MockACPServer struct {
	sessions  map[string]*ACPSession
	responses map[string]string
	mu        sync.RWMutex
}

// ACPSession represents an ACP session state
type ACPSession struct {
	id      string
	cwd     string
	history []protocol.ContentBlock
}

// handleRequest routes ACP requests to appropriate handlers
func (s *MockACPServer) handleRequest(req jsonrpc.Request) {
	var result interface{}
	var err error

	switch req.Method {
	case "initialize":
		result, err = s.handleInitialize(req.Params)
	case "session/new":
		result, err = s.handleSessionNew(req.Params)
	case "session/load":
		result, err = s.handleSessionLoad(req.Params)
	case "session/prompt":
		result, err = s.handleSessionPrompt(req.Params)
	default:
		s.writeError(&jsonrpc.Error{
			Code:    jsonrpc.ErrorCodeMethodNotFound,
			Message: "Method not found",
		}, req.ID)
		return
	}

	if err != nil {
		s.writeError(&jsonrpc.Error{
			Code:    jsonrpc.ErrorCodeInternalError,
			Message: "Internal error",
			Data:    err.Error(),
		}, req.ID)
		return
	}

	response := jsonrpc.Response{
		JSONRPC: "2.0",
		Result:  result,
		ID:      req.ID,
	}

	data, _ := json.Marshal(response)
	fmt.Println(string(data))
}

// writeError writes a JSON-RPC error to stdout
func (s *MockACPServer) writeError(err *jsonrpc.Error, id interface{}) {
	response := jsonrpc.Response{
		JSONRPC: "2.0",
		Error:   err,
		ID:      id,
	}
	data, _ := json.Marshal(response)
	fmt.Println(string(data))
}

// handleInitialize handles initialize requests
func (s *MockACPServer) handleInitialize(params json.RawMessage) (*protocol.InitializeResponse, error) {
	var req protocol.InitializeRequest
	if err := json.Unmarshal(params, &req); err != nil {
		return nil, fmt.Errorf("invalid parameters: %w", err)
	}

	// Return mock capabilities
	response := &protocol.InitializeResponse{
		ProtocolVersion: 1,
		AgentCapabilities: protocol.ACPAgentCapabilities{
			LoadSession: false, // Keep it simple for MVP
			PromptCapabilities: protocol.PromptCapabilities{
				Image:           false,
				Audio:           false,
				EmbeddedContext: true,
			},
			McpCapabilities: protocol.McpCapabilities{
				Http: false,
				Sse:  false,
			},
		},
		AuthMethods: []protocol.AuthMethod{},
	}

	return response, nil
}

// handleSessionNew handles session/new requests
func (s *MockACPServer) handleSessionNew(params json.RawMessage) (*protocol.NewSessionResponse, error) {
	var req protocol.NewSessionRequest
	if err := json.Unmarshal(params, &req); err != nil {
		return nil, fmt.Errorf("invalid parameters: %w", err)
	}

	sessionID := uuid.New().String()
	session := &ACPSession{
		id:      sessionID,
		cwd:     req.Cwd,
		history: []protocol.ContentBlock{},
	}

	s.mu.Lock()
	s.sessions[sessionID] = session
	s.mu.Unlock()

	response := &protocol.NewSessionResponse{
		SessionId: protocol.SessionId(sessionID),
		Modes:     nil, // UNSTABLE field - keep nil
	}

	return response, nil
}

// handleSessionLoad handles session/load requests
func (s *MockACPServer) handleSessionLoad(params json.RawMessage) (*protocol.NewSessionResponse, error) {
	// For MVP, we don't support loading sessions
	return nil, fmt.Errorf("session loading not supported in mock agent")
}

// handleSessionPrompt handles session/prompt requests
func (s *MockACPServer) handleSessionPrompt(params json.RawMessage) (*protocol.PromptResponse, error) {
	// First unmarshal into a raw struct to handle ContentBlock manually
	var rawReq struct {
		SessionId protocol.SessionId `json:"sessionId"`
		Prompt    []json.RawMessage  `json:"prompt"`
	}
	if err := json.Unmarshal(params, &rawReq); err != nil {
		return nil, fmt.Errorf("invalid parameters: %w", err)
	}

	// Unmarshal ContentBlocks manually
	var contentBlocks []protocol.ContentBlock
	for _, rawBlock := range rawReq.Prompt {
		block, err := protocol.UnmarshalContentBlock(rawBlock)
		if err != nil {
			return nil, fmt.Errorf("invalid content block: %w", err)
		}
		contentBlocks = append(contentBlocks, block)
	}

	req := protocol.PromptRequest{
		SessionId: rawReq.SessionId,
		Prompt:    contentBlocks,
	}

	s.mu.Lock()
	_, exists := s.sessions[string(req.SessionId)]
	s.mu.Unlock()

	if !exists {
		return nil, fmt.Errorf("session not found: %s", req.SessionId)
	}

	// Extract prompt text from ContentBlocks
	var prompt string
	for _, block := range req.Prompt {
		if textContent, ok := block.(protocol.TextContent); ok {
			prompt += textContent.Text + " "
		}
	}
	prompt = strings.TrimSpace(prompt)

	// Send session/update notification for agent message
	go s.sendSessionUpdate(string(req.SessionId), prompt)

	// Return completion
	response := &protocol.PromptResponse{
		StopReason: protocol.StopReasonEndTurn,
	}

	return response, nil
}

// sendSessionUpdate sends session update notifications
func (s *MockACPServer) sendSessionUpdate(sessionID, prompt string) {
	// Simulate processing delay for test patterns
	if strings.HasPrefix(prompt, "DELAY_") {
		delayStr := strings.TrimPrefix(prompt, "DELAY_")
		var delay time.Duration
		switch delayStr {
		case "1":
			delay = 1 * time.Second
		case "2":
			delay = 2 * time.Second
		case "3":
			delay = 3 * time.Second
		default:
			delay = 1 * time.Second
		}
		time.Sleep(delay)
	}

	response := processPrompt(prompt, s.responses)

	// Send agent message chunk update
	update := protocol.AgentMessageChunk{
		SessionUpdate: "agent_message_chunk",
		Content: protocol.TextContent{
			Type: "text",
			Text: response,
		},
	}

	notification := struct {
		JSONRPC string                       `json:"jsonrpc"`
		Method  string                       `json:"method"`
		Params  protocol.SessionNotification `json:"params"`
	}{
		JSONRPC: "2.0",
		Method:  "session/update",
		Params: protocol.SessionNotification{
			SessionId: protocol.SessionId(sessionID),
			Update:    update,
		},
	}

	data, _ := json.Marshal(notification)
	fmt.Println(string(data))
}

// MockA2AServer implements A2A protocol server for testing
type MockA2AServer struct {
	tasks     map[string]*protocol.Task
	responses map[string]string
	mu        sync.RWMutex
}

// runA2AServer starts the mock A2A server
func runA2AServer(port string, responses map[string]string) {
	server := &MockA2AServer{
		tasks:     make(map[string]*protocol.Task),
		responses: responses,
	}

	http.HandleFunc("/rpc", server.handleRPC)
	http.HandleFunc("/.well-known/agent-card.json", server.handleAgentCard)

	fmt.Printf("Mock A2A Agent listening on :%s\n", port)
	log.Fatal(http.ListenAndServe(":"+port, nil))
}

// handleRPC handles JSON-RPC requests
func (s *MockA2AServer) handleRPC(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req jsonrpc.Request
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		s.writeError(w, &jsonrpc.Error{
			Code:    jsonrpc.ErrorCodeParseError,
			Message: "Parse error",
		}, req.ID)
		return
	}

	var result interface{}
	var err error

	switch req.Method {
	case "message/send":
		result, err = s.handleMessageSend(req.Params)
	case "tasks/get":
		result, err = s.handleTasksGet(req.Params)
	case "tasks/cancel":
		result, err = s.handleTasksCancel(req.Params)
	default:
		s.writeError(w, &jsonrpc.Error{
			Code:    jsonrpc.ErrorCodeMethodNotFound,
			Message: "Method not found",
		}, req.ID)
		return
	}

	if err != nil {
		s.writeError(w, &jsonrpc.Error{
			Code:    jsonrpc.ErrorCodeInternalError,
			Message: "Internal error",
			Data:    err.Error(),
		}, req.ID)
		return
	}

	response := jsonrpc.Response{
		JSONRPC: "2.0",
		Result:  result,
		ID:      req.ID,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// writeError writes a JSON-RPC error response
func (s *MockA2AServer) writeError(w http.ResponseWriter, err *jsonrpc.Error, id interface{}) {
	response := jsonrpc.Response{
		JSONRPC: "2.0",
		Error:   err,
		ID:      id,
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// handleMessageSend handles message/send requests
func (s *MockA2AServer) handleMessageSend(params json.RawMessage) (*protocol.Task, error) {
	var p struct {
		ContextID string             `json:"contextId"`
		Messages  []protocol.Message `json:"messages"`
	}

	if err := json.Unmarshal(params, &p); err != nil {
		return nil, fmt.Errorf("invalid parameters: %w", err)
	}

	// Extract prompt from messages for processing
	var prompt string
	if len(p.Messages) > 0 && len(p.Messages[0].Parts) > 0 {
		part := p.Messages[0].Parts[0]
		if part.Text != "" {
			prompt = part.Text
		}
	}

	taskID := uuid.New().String()
	task := &protocol.Task{
		ID:        taskID,
		ContextID: p.ContextID,
		Status:    protocol.TaskStatus{State: protocol.TaskStateSubmitted},
		History:   p.Messages,
		Artifacts: []protocol.Artifact{},
	}

	s.mu.Lock()
	s.tasks[taskID] = task
	s.mu.Unlock()

	// Simulate async execution
	go s.executeTask(taskID, prompt)

	return task, nil
}

// executeTask simulates task execution with test pattern support
func (s *MockA2AServer) executeTask(taskID, prompt string) {
	s.mu.Lock()
	task := s.tasks[taskID]
	if task == nil {
		s.mu.Unlock()
		return
	}
	task.Status.State = protocol.TaskStateWorking
	s.mu.Unlock()

	// Handle special test patterns
	if prompt == "HANG" {
		time.Sleep(1 * time.Hour)
		return
	}

	if strings.Contains(prompt, "FAIL_NODE") {
		s.mu.Lock()
		task.Status.State = protocol.TaskStateFailed
		task.History = append(task.History, protocol.Message{
			Role: "assistant",
			Parts: []protocol.Part{
				{
					Kind: "text",
					Text: "Mock agent failure: " + prompt,
				},
			},
		})
		s.mu.Unlock()
		return
	}

	// Handle delay patterns
	delay := 1 * time.Second
	if strings.HasPrefix(prompt, "DELAY_") {
		delayStr := strings.TrimPrefix(prompt, "DELAY_")
		switch delayStr {
		case "1":
			delay = 1 * time.Second
		case "2":
			delay = 2 * time.Second
		case "3":
			delay = 3 * time.Second
		case "5":
			delay = 5 * time.Second
		case "1500":
			delay = 1500 * time.Millisecond
		}
	}

	time.Sleep(delay)

	// Complete the task
	response := processPrompt(prompt, s.responses)

	s.mu.Lock()
	task.Status.State = protocol.TaskStateCompleted
	task.History = append(task.History, protocol.Message{
		Role: "assistant",
		Parts: []protocol.Part{
			{
				Kind: "text",
				Text: response,
			},
		},
	})
	task.Artifacts = append(task.Artifacts, protocol.Artifact{
		ArtifactID: uuid.New().String(),
		Name:       "Mock Response",
		Parts: []protocol.Part{
			{
				Kind: "text",
				Text: response,
			},
		},
	})
	s.mu.Unlock()
}

// handleTasksGet handles tasks/get requests
func (s *MockA2AServer) handleTasksGet(params json.RawMessage) (*protocol.Task, error) {
	var p struct {
		TaskID string `json:"taskId"`
	}

	if err := json.Unmarshal(params, &p); err != nil {
		return nil, fmt.Errorf("invalid parameters: %w", err)
	}

	s.mu.RLock()
	task, exists := s.tasks[p.TaskID]
	s.mu.RUnlock()

	if !exists {
		return nil, fmt.Errorf("task not found: %s", p.TaskID)
	}

	return task, nil
}

// handleTasksCancel handles tasks/cancel requests
func (s *MockA2AServer) handleTasksCancel(params json.RawMessage) (*protocol.Task, error) {
	var p struct {
		TaskID string `json:"taskId"`
	}

	if err := json.Unmarshal(params, &p); err != nil {
		return nil, fmt.Errorf("invalid parameters: %w", err)
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	task, exists := s.tasks[p.TaskID]
	if !exists {
		return nil, fmt.Errorf("task not found: %s", p.TaskID)
	}

	if task.Status.State == protocol.TaskStateSubmitted || task.Status.State == protocol.TaskStateWorking {
		task.Status.State = protocol.TaskStateCanceled
	}

	return task, nil
}

// handleAgentCard serves the agent card
func (s *MockA2AServer) handleAgentCard(w http.ResponseWriter, r *http.Request) {
	card := &protocol.AgentCard{
		ProtocolVersion: "0.3.0",
		Name:            "Mock A2A Agent",
		Description:     "Mock A2A agent for testing",
		URL:             "http://localhost:9457/rpc",
		Version:         "1.0.0",
		Capabilities: protocol.AgentCapabilities{
			Streaming:         false,
			PushNotifications: false,
		},
		DefaultInputModes:  []string{"application/json"},
		DefaultOutputModes: []string{"application/json"},
		PreferredTransport: "JSONRPC",
		Skills: []protocol.AgentSkill{
			{
				ID:          "execute-workflow",
				Name:        "Execute Workflow",
				Description: "Mock workflow execution for testing",
				InputModes:  []string{"application/json"},
				OutputModes: []string{"application/json"},
				Examples: []string{
					"Execute test workflow",
					"Test delay patterns with DELAY_X",
				},
			},
		},
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(card)
}
