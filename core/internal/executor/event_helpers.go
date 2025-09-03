package executor

import (
	"fmt"

	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"gorm.io/datatypes"
)

// CreateStateChangeEvent creates a state transition event
func CreateStateChangeEvent(executionID, nodeID, source string, newState, prevState string) *storage.ExecutionEvent {
	return &storage.ExecutionEvent{
		ExecutionID: executionID,
		NodeID:      nodeID,
		Type:        storage.EventTypeStateChange,
		Source:      source,
		State:       &newState,
		PrevState:   &prevState,
		Message:     fmt.Sprintf("State changed from %s to %s", prevState, newState),
	}
}

// CreateOutputEvent creates an output/progress event
func CreateOutputEvent(executionID, nodeID, source string, output string) *storage.ExecutionEvent {
	return &storage.ExecutionEvent{
		ExecutionID: executionID,
		NodeID:      nodeID,
		Type:        storage.EventTypeOutput,
		Source:      source,
		Message:     output,
	}
}

// CreateProgressEvent creates a progress update event
func CreateProgressEvent(executionID, nodeID, source string, progress float64, message string) *storage.ExecutionEvent {
	return &storage.ExecutionEvent{
		ExecutionID: executionID,
		NodeID:      nodeID,
		Type:        storage.EventTypeProgress,
		Source:      source,
		Message:     message,
		Data:        datatypes.JSON(fmt.Sprintf(`{"progress": %f}`, progress)),
	}
}

// CreateErrorEvent creates an error event
func CreateErrorEvent(executionID, nodeID string, err error) *storage.ExecutionEvent {
	return &storage.ExecutionEvent{
		ExecutionID: executionID,
		NodeID:      nodeID,
		Type:        storage.EventTypeError,
		Source:      storage.EventSourceSystem,
		Message:     err.Error(),
	}
}
