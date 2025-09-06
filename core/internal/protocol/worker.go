package protocol

import (
	"fmt"

	"github.com/agentmaestro/agentmaestro/core/internal/engine"
)

// WorkerResult represents the outcome of a worker executing a task
type WorkerResult struct {
	NodeID string `json:"nodeId"`
	Status string `json:"status"`
	Output string `json:"output,omitempty"`
	Error  string `json:"error,omitempty"`
}

// ToA2AFinalState maps WorkerResult status to A2A final states
func (wr *WorkerResult) ToA2AFinalState() string {
	switch wr.Status {
	case "completed":
		return TaskStateCompleted
	case "failed":
		return TaskStateFailed
	case "canceled":
		return TaskStateCanceled
	case "rejected":
		return TaskStateRejected
	default:
		// Default to failed for unknown or non-final states
		return TaskStateFailed
	}
}

// TaskInput represents input for worker task execution
// Reuses the existing Node struct from engine package
type TaskInput = engine.Node

// NewWorkerResult creates a new WorkerResult with the given parameters
func NewWorkerResult(nodeID, status string) *WorkerResult {
	return &WorkerResult{
		NodeID: nodeID,
		Status: status,
	}
}

// NewCompletedResult creates a WorkerResult for successful completion
func NewCompletedResult(nodeID, output string) *WorkerResult {
	return &WorkerResult{
		NodeID: nodeID,
		Status: "completed",
		Output: output,
	}
}

// NewFailedResult creates a WorkerResult for failed execution
func NewFailedResult(nodeID string, err error) *WorkerResult {
	return &WorkerResult{
		NodeID: nodeID,
		Status: "failed",
		Error:  err.Error(),
	}
}

// NewCanceledResult creates a WorkerResult for canceled execution
func NewCanceledResult(nodeID string) *WorkerResult {
	return &WorkerResult{
		NodeID: nodeID,
		Status: "canceled",
	}
}

// NewRejectedResult creates a WorkerResult for rejected execution
func NewRejectedResult(nodeID, reason string) *WorkerResult {
	return &WorkerResult{
		NodeID: nodeID,
		Status: "rejected",
		Error:  reason,
	}
}

// Validate ensures the WorkerResult has required fields
func (wr *WorkerResult) Validate() error {
	if wr.NodeID == "" {
		return fmt.Errorf("nodeID is required")
	}
	if wr.Status == "" {
		return fmt.Errorf("status is required")
	}
	return nil
}

// TaskRequest represents a work unit submitted to the task queue for external worker polling.
type TaskRequest struct {
	NodeID      string                 `json:"node_id" validate:"required"`
	ExecutionID string                 `json:"execution_id" validate:"required"`
	Agent       string                 `json:"agent" validate:"required"`
	ID          string                 `json:"id" validate:"required"`
	Request     map[string]interface{} `json:"request"`
}

// TaskResponse represents a completed task result submitted by an external worker in A2A-compliant format.
type TaskResponse struct {
	NodeID      string                 `json:"nodeId" validate:"required"`
	ExecutionID string                 `json:"executionId" validate:"required"`
	TaskStatus  A2ATaskStatus          `json:"taskStatus" validate:"required"`
	Artifacts   []A2AArtifact          `json:"artifacts,omitempty"`
	Metadata    map[string]interface{} `json:"metadata,omitempty"`
}

// A2ATaskStatus represents A2A Protocol-compliant task status structure.
type A2ATaskStatus struct {
	State     string   `json:"state" validate:"required,oneof=completed failed canceled rejected"`
	Message   *Message `json:"message,omitempty"`
	Timestamp string   `json:"timestamp,omitempty"`
}

// A2AArtifact represents A2A Protocol-compliant artifact structure for rich outputs.
type A2AArtifact struct {
	ArtifactID  string `json:"artifactId" validate:"required"`
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	Parts       []Part `json:"parts" validate:"required,min=1"`
}
