package protocol

import (
	"fmt"
	"time"
)

// SyncRequest represents the worker's request to the sync endpoint
type SyncRequest struct {
	Status      WorkerStatus `json:"status" validate:"required"`
	Timestamp   string       `json:"timestamp" validate:"required"`
	CurrentTask *CurrentTask `json:"currentTask,omitempty"`
	TaskResult  *TaskResult  `json:"taskResult,omitempty"`
}

// SyncResponse represents the orchestrator's response to a sync request
type SyncResponse struct {
	Type      SyncResponseType `json:"type" validate:"required"`
	Timestamp string           `json:"timestamp" validate:"required"`
	Task      *TaskAssignment  `json:"task,omitempty"`
	Command   *WorkerCommand   `json:"command,omitempty"`
}

// WorkerStatus represents the current state of a worker
type WorkerStatus string

const (
	WorkerStatusIdle    WorkerStatus = "idle"
	WorkerStatusWorking WorkerStatus = "working"
	WorkerStatusFailed  WorkerStatus = "failed"
)

// SyncResponseType indicates the type of response from the orchestrator
type SyncResponseType string

const (
	SyncResponseNoAction     SyncResponseType = "no_action"
	SyncResponseTaskAssigned SyncResponseType = "task_assigned"
	SyncResponseCommand      SyncResponseType = "command"
)

// CurrentTask contains details about the task currently being executed
type CurrentTask struct {
	NodeID      string `json:"nodeId" validate:"required"`
	ExecutionID string `json:"executionId" validate:"required"`
	StartTime   string `json:"startTime,omitempty"`
}

// TaskResult contains the result of a completed task
type TaskResult struct {
	NodeID      string        `json:"nodeId" validate:"required"`
	ExecutionID string        `json:"executionId" validate:"required"`
	TaskStatus  A2ATaskStatus `json:"taskStatus" validate:"required"`
	Artifacts   []A2AArtifact `json:"artifacts,omitempty"`
}

// TaskAssignment contains details of a task to be assigned to a worker
type TaskAssignment struct {
	NodeID      string                 `json:"nodeId" validate:"required"`
	ExecutionID string                 `json:"executionId" validate:"required"`
	Prompt      string                 `json:"prompt" validate:"required"`
	AgentType   string                 `json:"agentType" validate:"required"`
	Config      map[string]interface{} `json:"config,omitempty"`
}

// WorkerCommand represents a control command for a worker
type WorkerCommand struct {
	Action      CommandAction `json:"action" validate:"required"`
	NodeID      string        `json:"nodeId,omitempty"`
	ExecutionID string        `json:"executionId,omitempty"`
	Reason      string        `json:"reason,omitempty"`
}

// CommandAction represents the type of command being sent to a worker
type CommandAction string

const (
	CommandActionCancel CommandAction = "cancel"
	CommandActionFail   CommandAction = "fail"
)

// Validation methods

// Validate ensures the SyncRequest has required fields and valid combinations
func (sr *SyncRequest) Validate() error {
	if sr.Status == "" {
		return fmt.Errorf("status is required")
	}

	if sr.Timestamp == "" {
		return fmt.Errorf("timestamp is required")
	}

	// Validate status-specific requirements
	switch sr.Status {
	case WorkerStatusWorking:
		if sr.CurrentTask == nil {
			return fmt.Errorf("currentTask is required when status is working")
		}
		if err := sr.CurrentTask.Validate(); err != nil {
			return fmt.Errorf("invalid currentTask: %w", err)
		}
	case WorkerStatusIdle, WorkerStatusFailed:
		// These statuses don't require currentTask
	default:
		return fmt.Errorf("invalid status: %s", sr.Status)
	}

	// Validate task result if present
	if sr.TaskResult != nil {
		if err := sr.TaskResult.Validate(); err != nil {
			return fmt.Errorf("invalid taskResult: %w", err)
		}
	}

	return nil
}

// Validate ensures the CurrentTask has required fields
func (ct *CurrentTask) Validate() error {
	if ct.NodeID == "" {
		return fmt.Errorf("nodeId is required")
	}
	if ct.ExecutionID == "" {
		return fmt.Errorf("executionId is required")
	}
	return nil
}

// Validate ensures the TaskResult has required fields
func (tr *TaskResult) Validate() error {
	if tr.NodeID == "" {
		return fmt.Errorf("nodeId is required")
	}
	if tr.ExecutionID == "" {
		return fmt.Errorf("executionId is required")
	}
	if tr.TaskStatus.State == "" {
		return fmt.Errorf("taskStatus.state is required")
	}
	return nil
}

// Validate ensures the TaskAssignment has required fields
func (ta *TaskAssignment) Validate() error {
	if ta.NodeID == "" {
		return fmt.Errorf("nodeId is required")
	}
	if ta.ExecutionID == "" {
		return fmt.Errorf("executionId is required")
	}
	if ta.Prompt == "" {
		return fmt.Errorf("prompt is required")
	}
	if ta.AgentType == "" {
		return fmt.Errorf("agentType is required")
	}
	return nil
}

// Validate ensures the WorkerCommand has required fields
func (wc *WorkerCommand) Validate() error {
	if wc.Action == "" {
		return fmt.Errorf("action is required")
	}

	switch wc.Action {
	case CommandActionCancel, CommandActionFail:
		// Valid actions
	default:
		return fmt.Errorf("invalid action: %s", wc.Action)
	}

	return nil
}

// Helper functions for creating sync structures

// NewSyncRequest creates a new SyncRequest with current timestamp
func NewSyncRequest(status WorkerStatus) *SyncRequest {
	return &SyncRequest{
		Status:    status,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}
}

// NewSyncResponse creates a new SyncResponse with current timestamp
func NewSyncResponse(responseType SyncResponseType) *SyncResponse {
	return &SyncResponse{
		Type:      responseType,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}
}

// NewNoActionResponse creates a sync response indicating no action needed
func NewNoActionResponse() *SyncResponse {
	return NewSyncResponse(SyncResponseNoAction)
}

// NewTaskAssignmentResponse creates a sync response with a task assignment
func NewTaskAssignmentResponse(task *TaskAssignment) *SyncResponse {
	resp := NewSyncResponse(SyncResponseTaskAssigned)
	resp.Task = task
	return resp
}

// NewCommandResponse creates a sync response with a worker command
func NewCommandResponse(command *WorkerCommand) *SyncResponse {
	resp := NewSyncResponse(SyncResponseCommand)
	resp.Command = command
	return resp
}

// WithCurrentTask adds current task details to a sync request
func (sr *SyncRequest) WithCurrentTask(nodeID, executionID string) *SyncRequest {
	sr.CurrentTask = &CurrentTask{
		NodeID:      nodeID,
		ExecutionID: executionID,
		StartTime:   time.Now().UTC().Format(time.RFC3339),
	}
	return sr
}

// WithTaskResult adds task result to a sync request
func (sr *SyncRequest) WithTaskResult(nodeID, executionID string, status A2ATaskStatus, artifacts []A2AArtifact) *SyncRequest {
	sr.TaskResult = &TaskResult{
		NodeID:      nodeID,
		ExecutionID: executionID,
		TaskStatus:  status,
		Artifacts:   artifacts,
	}
	return sr
}
