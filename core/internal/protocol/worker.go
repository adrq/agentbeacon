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
