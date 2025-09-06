package api

import (
	"encoding/json"
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
