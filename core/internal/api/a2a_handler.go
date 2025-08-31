package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/executor"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/google/uuid"
	"gopkg.in/yaml.v3"
	"gorm.io/datatypes"
)

// A2AHandler implements the A2A Protocol v0.3.0 server
type A2AHandler struct {
	db         *storage.DB
	executor   *executor.Executor
	taskMap    map[string]string // A2A task ID → execution ID
	contextMap map[string]string // context ID → A2A task ID
	mu         sync.RWMutex
}

// NewA2AHandler creates a new A2A handler instance
func NewA2AHandler(db *storage.DB, executor *executor.Executor) *A2AHandler {
	return &A2AHandler{
		db:         db,
		executor:   executor,
		taskMap:    make(map[string]string),
		contextMap: make(map[string]string),
	}
}

// ServeHTTP handles A2A JSON-RPC requests
func (h *A2AHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		h.writeError(w, &JSONRPCError{
			Code:    -32601,
			Message: "Method not allowed",
		}, nil)
		return
	}

	var req JSONRPCRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		h.writeError(w, &JSONRPCError{
			Code:    -32700,
			Message: "Parse error",
		}, req.ID)
		return
	}

	var result interface{}
	var err error

	switch req.Method {
	case "message/send":
		result, err = h.handleMessageSend(req.Params)
	case "tasks/get":
		result, err = h.handleTasksGet(req.Params)
	case "tasks/cancel":
		result, err = h.handleTasksCancel(req.Params)
	default:
		h.writeError(w, &JSONRPCError{
			Code:    -32601,
			Message: "Method not found",
		}, req.ID)
		return
	}

	if err != nil {
		h.writeError(w, &JSONRPCError{
			Code:    -32603,
			Message: "Internal error",
			Data:    err.Error(),
		}, req.ID)
		return
	}

	response := JSONRPCResponse{
		JSONRPC: "2.0",
		Result:  result,
		ID:      req.ID,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// writeError writes a JSON-RPC error response
func (h *A2AHandler) writeError(w http.ResponseWriter, err *JSONRPCError, id interface{}) {
	response := JSONRPCResponse{
		JSONRPC: "2.0",
		Error:   err,
		ID:      id,
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK) // JSON-RPC errors still return 200
	json.NewEncoder(w).Encode(response)
}

// A2AMessageSendParams represents parameters for message/send method (renamed to avoid conflict)
type A2AMessageSendParams struct {
	ContextID string    `json:"contextId"`
	Messages  []Message `json:"messages"`
}

// handleMessageSend implements the message/send A2A method
func (h *A2AHandler) handleMessageSend(params json.RawMessage) (*Task, error) {
	var p A2AMessageSendParams
	if err := json.Unmarshal(params, &p); err != nil {
		return nil, fmt.Errorf("invalid parameters: %w", err)
	}

	// Extract workflow from message content
	workflowData, err := h.extractWorkflowFromMessages(p.Messages)
	if err != nil {
		return nil, fmt.Errorf("failed to extract workflow: %w", err)
	}

	// Start execution using existing executor
	execution, err := h.startWorkflowExecution(workflowData)
	if err != nil {
		return nil, fmt.Errorf("failed to start execution: %w", err)
	}

	// Generate A2A task ID and store mapping
	taskID := uuid.New().String()

	// Store A2A task metadata in execution record
	a2aData := map[string]interface{}{
		"task_id":    taskID,
		"context_id": p.ContextID,
	}
	a2aJSON, _ := json.Marshal(a2aData)

	// Update execution with A2A task data
	query := h.db.Placeholder("UPDATE execution SET a2_a_tasks = ? WHERE id = ?")
	_, err = h.db.Exec(query, datatypes.JSON(a2aJSON), execution.ID)
	if err != nil {
		return nil, fmt.Errorf("failed to store A2A task mapping: %w", err)
	}

	// Store in-memory mappings for quick lookup
	h.mu.Lock()
	h.taskMap[taskID] = execution.ID
	h.contextMap[p.ContextID] = taskID
	h.mu.Unlock()

	// Return A2A Task
	task := &Task{
		ID:        taskID,
		ContextID: p.ContextID,
		Status:    TaskStatus{State: TaskStateSubmitted},
		History:   []Message{},
		Artifacts: []Artifact{},
	}

	return task, nil
}

// TasksGetParams represents parameters for tasks/get method
type TasksGetParams struct {
	TaskID string `json:"taskId"`
}

// handleTasksGet implements the tasks/get A2A method
func (h *A2AHandler) handleTasksGet(params json.RawMessage) (*Task, error) {
	var p TasksGetParams
	if err := json.Unmarshal(params, &p); err != nil {
		return nil, fmt.Errorf("invalid parameters: %w", err)
	}

	// Find execution ID for this A2A task
	h.mu.RLock()
	executionID, exists := h.taskMap[p.TaskID]
	h.mu.RUnlock()

	if !exists {
		return nil, fmt.Errorf("task not found: %s", p.TaskID)
	}

	// Get execution from database
	execution, err := h.db.GetExecution(executionID)
	if err != nil {
		return nil, fmt.Errorf("failed to get execution: %w", err)
	}

	// Get A2A task data
	var a2aData map[string]interface{}
	if execution.A2ATasks != nil {
		json.Unmarshal(execution.A2ATasks, &a2aData)
	}

	contextID := ""
	if ctxID, ok := a2aData["context_id"].(string); ok {
		contextID = ctxID
	}

	// Convert execution status to A2A task
	task := &Task{
		ID:        p.TaskID,
		ContextID: contextID,
		Status:    TaskStatus{State: execution.Status},
		History:   h.buildTaskHistory(execution),
		Artifacts: h.buildTaskArtifacts(execution),
	}

	return task, nil
}

// TasksCancelParams represents parameters for tasks/cancel method
type TasksCancelParams struct {
	TaskID string `json:"taskId"`
}

// handleTasksCancel implements the tasks/cancel A2A method
func (h *A2AHandler) handleTasksCancel(params json.RawMessage) (*Task, error) {
	var p TasksCancelParams
	if err := json.Unmarshal(params, &p); err != nil {
		return nil, fmt.Errorf("invalid parameters: %w", err)
	}

	// Find execution ID for this A2A task
	h.mu.RLock()
	executionID, exists := h.taskMap[p.TaskID]
	h.mu.RUnlock()

	if !exists {
		return nil, fmt.Errorf("task not found: %s", p.TaskID)
	}

	// Cancel the execution
	err := h.executor.StopExecution(executionID)
	if err != nil {
		return nil, fmt.Errorf("failed to cancel execution: %w", err)
	}

	// Return updated task status
	return h.handleTasksGet(json.RawMessage(`{"taskId":"` + p.TaskID + `"}`))
}

// extractWorkflowFromMessages extracts workflow content from A2A messages
func (h *A2AHandler) extractWorkflowFromMessages(messages []Message) (string, error) {
	for _, message := range messages {
		for _, part := range message.Parts {
			switch part.Kind {
			case "data":
				// Check for JSON data with workflowRef or workflowYaml
				var data map[string]interface{}
				if part.Data != nil {
					data = part.Data.Data
				}

				if workflowRef, ok := data["workflowRef"].(string); ok {
					// Load workflow by reference
					return h.loadWorkflowByRef(workflowRef)
				}

				if workflowYaml, ok := data["workflowYaml"].(string); ok {
					// Return inline workflow YAML
					return workflowYaml, nil
				}

			case "text":
				// Check if text content is YAML
				content := part.Text

				// Try to parse as YAML to validate
				var workflow map[string]interface{}
				if err := yaml.Unmarshal([]byte(content), &workflow); err == nil {
					if _, hasName := workflow["name"]; hasName {
						return content, nil
					}
				}
			}
		}
	}

	return "", fmt.Errorf("no workflow found in messages")
}

// loadWorkflowByRef loads a workflow by reference
func (h *A2AHandler) loadWorkflowByRef(workflowRef string) (string, error) {
	// For MVP, just extract workflow name (ignore version for now)
	workflowName := workflowRef
	if colonIdx := len(workflowRef) - 1; colonIdx >= 0 {
		for i := colonIdx; i >= 0; i-- {
			if workflowRef[i] == ':' {
				workflowName = workflowRef[:i]
				break
			}
		}
	}

	// Load workflow YAML from database
	yamlData, err := h.db.LoadWorkflowYAML(workflowName)
	if err != nil {
		return "", fmt.Errorf("workflow not found: %s", workflowName)
	}

	return string(yamlData), nil
}

// buildTaskHistory creates A2A task history from execution logs
func (h *A2AHandler) buildTaskHistory(execution *storage.Execution) []Message {
	history := []Message{}

	if execution.Logs != "" {
		// Convert logs to A2A message format
		history = append(history, Message{
			Role: "assistant",
			Parts: []Part{{
				Kind: "text",
				Text: execution.Logs,
			}},
			MessageID: "execution-log",
			Kind:      "message",
		})
	}

	return history
}

// buildTaskArtifacts creates A2A task artifacts from execution results
func (h *A2AHandler) buildTaskArtifacts(execution *storage.Execution) []Artifact {
	artifacts := []Artifact{}

	// For MVP, create a simple status artifact
	artifacts = append(artifacts, Artifact{
		ArtifactID:  "execution-status",
		Name:        "Execution Status",
		Description: "Current status of workflow execution",
		Parts: []Part{{
			Kind: "text",
			Text: fmt.Sprintf("Status: %s\nStarted: %s", execution.Status, execution.StartedAt.Format("2006-01-02 15:04:05")),
		}},
	})

	return artifacts
}

// GetAgentCard returns the A2A Agent Card for AgentMaestro
func GetAgentCard() *AgentCard {
	return &AgentCard{
		ProtocolVersion: "0.3.0",
		Name:            "AgentMaestro Orchestrator",
		Description:     "AI agent workflow orchestrator",
		URL:             "http://localhost:9456/rpc",
		Version:         "1.0.0",
		Capabilities: AgentCapabilities{
			Streaming:         false,
			PushNotifications: false,
		},
		DefaultInputModes:  []string{"application/json"},
		DefaultOutputModes: []string{"application/json"},
		PreferredTransport: "JSONRPC",
		Skills: []AgentSkill{{
			ID:          "execute-workflow",
			Name:        "Execute Workflow",
			Description: "Execute a predefined workflow or inline workflow definition",
			InputModes:  []string{"application/json"},
			OutputModes: []string{"application/json"},
			Examples: []string{
				"Execute workflow: team/refactor-auth:latest",
				"Run inline workflow with YAML definition",
			},
		}},
	}
}

// ExecutionInfo represents execution information returned by startWorkflowExecution
type ExecutionInfo struct {
	ID          string
	WorkflowID  string
	Status      string
	StartedAt   time.Time
	CompletedAt *time.Time
}

// startWorkflowExecution starts execution with inline YAML or workflow reference
func (h *A2AHandler) startWorkflowExecution(workflowData string) (*ExecutionInfo, error) {
	// For inline YAML, we need to parse the workflow name and register it temporarily
	var workflow map[string]interface{}
	if err := yaml.Unmarshal([]byte(workflowData), &workflow); err != nil {
		return nil, fmt.Errorf("invalid workflow YAML: %w", err)
	}

	workflowName, ok := workflow["name"].(string)
	if !ok {
		return nil, fmt.Errorf("workflow must have a name field")
	}

	// Check if workflow is already registered
	_, err := h.db.GetWorkflowMetadata(workflowName)
	if err != nil {
		// Workflow doesn't exist, create it temporarily
		// For MVP, we'll create a temporary YAML file
		tempFile := fmt.Sprintf("/tmp/a2a-workflow-%s.yaml", uuid.New().String())
		if err := os.WriteFile(tempFile, []byte(workflowData), 0644); err != nil {
			return nil, fmt.Errorf("failed to create temporary workflow file: %w", err)
		}

		// Register the workflow
		if err := h.db.RegisterWorkflow(tempFile); err != nil {
			os.Remove(tempFile) // Cleanup on error
			return nil, fmt.Errorf("failed to register workflow: %w", err)
		}

		// Note: Don't remove temp file yet - executor might need it
		// TODO: Implement proper workflow storage that doesn't require temp files
	}

	// Start the workflow execution
	execInfo, err := h.executor.StartWorkflow(workflowName)
	if err != nil {
		return nil, fmt.Errorf("failed to start workflow: %w", err)
	}

	return &ExecutionInfo{
		ID:          execInfo.ID,
		WorkflowID:  execInfo.WorkflowID,
		Status:      execInfo.Status,
		StartedAt:   execInfo.StartedAt,
		CompletedAt: execInfo.CompletedAt,
	}, nil
}
