package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/executor"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol/jsonrpc"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/google/uuid"
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
		h.writeError(w, &jsonrpc.Error{
			Code:    jsonrpc.ErrorCodeMethodNotFound,
			Message: "Method not allowed",
		}, nil)
		return
	}

	var req jsonrpc.Request
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		h.writeError(w, &jsonrpc.Error{
			Code:    jsonrpc.ErrorCodeParseError,
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
		h.writeError(w, &jsonrpc.Error{
			Code:    jsonrpc.ErrorCodeMethodNotFound,
			Message: "Method not found",
		}, req.ID)
		return
	}

	if err != nil {
		h.writeError(w, &jsonrpc.Error{
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
func (h *A2AHandler) writeError(w http.ResponseWriter, err *jsonrpc.Error, id interface{}) {
	response := jsonrpc.Response{
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
	ContextID string             `json:"contextId"`
	Messages  []protocol.Message `json:"messages"`
}

// handleMessageSend implements the message/send A2A method
func (h *A2AHandler) handleMessageSend(params json.RawMessage) (*protocol.Task, error) {
	var p A2AMessageSendParams
	if err := json.Unmarshal(params, &p); err != nil {
		return nil, fmt.Errorf("invalid parameters: %w", err)
	}
	// Enforce workflowRef only (reject inline YAML)
	ref, err := h.extractWorkflowRef(p.Messages)
	if err != nil {
		return nil, fmt.Errorf("invalid workflow reference: %w", err)
	}
	execution, err := h.executor.StartWorkflowRef(ref)
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
	task := &protocol.Task{
		ID:        taskID,
		ContextID: p.ContextID,
		Status:    protocol.TaskStatus{State: protocol.TaskStateSubmitted},
		History:   []protocol.Message{},
		Artifacts: []protocol.Artifact{},
	}

	return task, nil
}

// TasksGetParams represents parameters for tasks/get method
type TasksGetParams struct {
	TaskID string `json:"taskId"`
}

// handleTasksGet implements the tasks/get A2A method
func (h *A2AHandler) handleTasksGet(params json.RawMessage) (*protocol.Task, error) {
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
	task := &protocol.Task{
		ID:        p.TaskID,
		ContextID: contextID,
		Status:    protocol.TaskStatus{State: execution.Status},
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
func (h *A2AHandler) handleTasksCancel(params json.RawMessage) (*protocol.Task, error) {
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
// Registry-only extraction: returns workflowRef or error if inline YAML supplied.
func (h *A2AHandler) extractWorkflowRef(messages []protocol.Message) (string, error) {
	if len(messages) != 1 {
		return "", fmt.Errorf("exactly one message required (got %d)", len(messages))
	}
	m := messages[0]
	var ref string
	for _, part := range m.Parts {
		switch part.Kind {
		case "data":
			if part.Data != nil {
				data := part.Data.Data
				if inline, ok := data["workflowYaml"].(string); ok && inline != "" {
					return "", fmt.Errorf("inline workflow YAML disabled; supply workflowRef")
				}
				if r, ok := data["workflowRef"].(string); ok && r != "" {
					if ref != "" && ref != r {
						return "", fmt.Errorf("multiple workflowRef values in single message not allowed")
					}
					ref = r
				}
			}
		case "text":
			if strings.Contains(part.Text, "name:") && strings.Contains(part.Text, "nodes:") {
				return "", fmt.Errorf("inline workflow YAML disabled; supply workflowRef")
			}
		}
	}
	if ref == "" {
		return "", fmt.Errorf("workflowRef is required")
	}
	return ref, nil
}

// loadWorkflowByRef loads a workflow by reference
// resolveAndShim resolves reference and registers a temporary legacy workflow for executor use.
// resolveAndShim removed – A2A now invokes executor.StartWorkflowRef directly.

// buildTaskHistory creates A2A task history from execution logs
func (h *A2AHandler) buildTaskHistory(execution *storage.Execution) []protocol.Message {
	history := []protocol.Message{}

	if execution.Logs != "" {
		// Convert logs to A2A message format
		history = append(history, protocol.Message{
			Role: "assistant",
			Parts: []protocol.Part{{
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
func (h *A2AHandler) buildTaskArtifacts(execution *storage.Execution) []protocol.Artifact {
	artifacts := []protocol.Artifact{}

	// For MVP, create a simple status artifact
	artifacts = append(artifacts, protocol.Artifact{
		ArtifactID:  "execution-status",
		Name:        "Execution Status",
		Description: "Current status of workflow execution",
		Parts: []protocol.Part{{
			Kind: "text",
			Text: fmt.Sprintf("Status: %s\nStarted: %s", execution.Status, execution.StartedAt.Format("2006-01-02 15:04:05")),
		}},
	})

	return artifacts
}

// GetAgentCard returns the A2A Agent Card for AgentMaestro
func GetAgentCard() *protocol.AgentCard {
	return &protocol.AgentCard{
		ProtocolVersion: "0.3.0",
		Name:            "AgentMaestro Orchestrator",
		Description:     "AI agent workflow orchestrator",
		URL:             "http://localhost:9456/rpc",
		Version:         "1.0.0",
		Capabilities: protocol.AgentCapabilities{
			Streaming:         false,
			PushNotifications: false,
		},
		DefaultInputModes:  []string{"application/json"},
		DefaultOutputModes: []string{"application/json"},
		PreferredTransport: "JSONRPC",
		Skills: []protocol.AgentSkill{{
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
