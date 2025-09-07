package api

import (
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
)

// workerPollHandler handles GET /api/worker/poll requests.
// Returns the next available task or {"task": null} if no tasks are available.
func (api *RestAPI) workerPollHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	// Poll for next available task
	task := api.executor.GetTaskQueue().PollTask()

	w.Header().Set("Content-Type", "application/json")

	if task == nil {
		// No tasks available
		json.NewEncoder(w).Encode(map[string]interface{}{"task": nil})
		return
	}

	// Return the task
	json.NewEncoder(w).Encode(map[string]interface{}{"task": task})
}

// workerResultHandler handles POST /api/worker/result requests.
// Accepts task completion results and validates executionId correlation.
func (api *RestAPI) workerResultHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	var taskResponse protocol.TaskResponse
	if err := json.NewDecoder(r.Body).Decode(&taskResponse); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}

	// Validate required fields
	if taskResponse.NodeID == "" {
		writeError(w, http.StatusBadRequest, "nodeId is required")
		return
	}
	if taskResponse.ExecutionID == "" {
		writeError(w, http.StatusBadRequest, "executionId is required")
		return
	}
	if taskResponse.TaskStatus.State == "" {
		writeError(w, http.StatusBadRequest, "taskStatus.state is required")
		return
	}

	// Validate task status state is a valid A2A final state
	validStates := map[string]bool{
		"completed": true,
		"failed":    true,
		"canceled":  true,
		"rejected":  true,
	}
	if !validStates[taskResponse.TaskStatus.State] {
		writeError(w, http.StatusBadRequest, "taskStatus.state must be one of: completed, failed, canceled, rejected")
		return
	}

	// Validate that the execution exists in the database
	_, err := api.executor.GetExecution(taskResponse.ExecutionID)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid executionId: execution not found")
		return
	}

	// Complete the task in the queue
	if err := api.executor.GetTaskQueue().CompleteTask(taskResponse); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]bool{"accepted": true})
}

// workerSyncHandler handles POST /api/worker/sync requests.
// Implements the bidirectional sync protocol for worker task management.
func (api *RestAPI) workerSyncHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	// Parse sync request
	var syncReq protocol.SyncRequest
	if err := json.NewDecoder(r.Body).Decode(&syncReq); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}

	// Validate sync request
	if err := syncReq.Validate(); err != nil {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("invalid sync request: %v", err))
		return
	}

	// Handle any task result included in the sync request
	if syncReq.TaskResult != nil {
		if err := api.handleTaskResult(syncReq.TaskResult); err != nil {
			writeError(w, http.StatusBadRequest, fmt.Sprintf("failed to handle task result: %v", err))
			return
		}
	}

	// Determine appropriate sync response based on worker status
	var syncResp *protocol.SyncResponse

	switch syncReq.Status {
	case protocol.WorkerStatusIdle:
		// Worker is idle, check for available tasks
		task := api.executor.GetTaskQueue().PollTask()
		if task != nil {
			// Convert internal task to TaskAssignment
			assignment := convertToTaskAssignment(task)
			syncResp = protocol.NewTaskAssignmentResponse(assignment)
		} else {
			// No tasks available
			syncResp = protocol.NewNoActionResponse()
		}

	case protocol.WorkerStatusWorking:
		// Worker is working, no new task assignment
		syncResp = protocol.NewNoActionResponse()

	case protocol.WorkerStatusFailed:
		// Worker reported failure, no new task assignment for now
		syncResp = protocol.NewNoActionResponse()

	default:
		writeError(w, http.StatusBadRequest, fmt.Sprintf("unknown worker status: %s", syncReq.Status))
		return
	}

	// Send sync response
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(syncResp)
}

// handleTaskResult processes task completion results from sync requests
func (api *RestAPI) handleTaskResult(taskResult *protocol.TaskResult) error {
	// Validate that the execution exists in the database
	_, err := api.executor.GetExecution(taskResult.ExecutionID)
	if err != nil {
		return fmt.Errorf("invalid executionId: execution not found")
	}

	// Convert TaskResult to TaskResponse for existing queue interface
	taskResponse := protocol.TaskResponse{
		NodeID:      taskResult.NodeID,
		ExecutionID: taskResult.ExecutionID,
		TaskStatus:  taskResult.TaskStatus,
		Artifacts:   taskResult.Artifacts,
	}

	// Complete the task in the queue
	if err := api.executor.GetTaskQueue().CompleteTask(taskResponse); err != nil {
		return fmt.Errorf("failed to complete task: %w", err)
	}

	return nil
}

// convertToTaskAssignment converts TaskRequest to sync protocol TaskAssignment
func convertToTaskAssignment(task *protocol.TaskRequest) *protocol.TaskAssignment {
	// Extract prompt from request map
	prompt := ""
	if task.Request != nil {
		if promptValue, ok := task.Request["prompt"].(string); ok {
			prompt = promptValue
		}
	}

	return &protocol.TaskAssignment{
		NodeID:      task.NodeID,
		ExecutionID: task.ExecutionID,
		Prompt:      prompt,
		AgentType:   task.Agent,
		Config:      task.Request, // Pass through the full request as config
	}
}
