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
	"github.com/google/uuid"
)

func main() {
	var configFile string
	var mode string
	var port string
	flag.StringVar(&configFile, "config", "", "JSON file with custom responses")
	flag.StringVar(&mode, "mode", "stdio", "Mode to run in: stdio or a2a")
	flag.StringVar(&port, "port", "9457", "Port for A2A server mode")
	flag.Parse()

	// Load custom responses
	responses := loadCustomResponses(configFile)

	// Run in appropriate mode
	if mode == "a2a" {
		runA2AServer(port, responses)
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

// runStdioMode runs the agent in stdio mode (original functionality)
func runStdioMode(responses map[string]string) {

	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		prompt := strings.TrimSpace(scanner.Text())
		response := processPrompt(prompt, responses)
		fmt.Println(response)
	}

	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "Error reading from stdin: %v\n", err)
		os.Exit(1)
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

	var req protocol.JSONRPCRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		s.writeError(w, &protocol.JSONRPCError{
			Code:    -32700,
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
		s.writeError(w, &protocol.JSONRPCError{
			Code:    -32601,
			Message: "Method not found",
		}, req.ID)
		return
	}

	if err != nil {
		s.writeError(w, &protocol.JSONRPCError{
			Code:    -32603,
			Message: "Internal error",
			Data:    err.Error(),
		}, req.ID)
		return
	}

	response := protocol.JSONRPCResponse{
		JSONRPC: "2.0",
		Result:  result,
		ID:      req.ID,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// writeError writes a JSON-RPC error response
func (s *MockA2AServer) writeError(w http.ResponseWriter, err *protocol.JSONRPCError, id interface{}) {
	response := protocol.JSONRPCResponse{
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
