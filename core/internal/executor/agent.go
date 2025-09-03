package executor

import (
	"context"

	"github.com/agentmaestro/agentmaestro/core/internal/storage"
)

// EventStreamer is an optional interface for agents that support event streaming
type EventStreamer interface {
	// SetEventChannel provides the agent with the channel to send events to
	SetEventChannel(events chan<- *storage.ExecutionEvent)
}

// Agent defines the interface for workflow execution agents
type Agent interface {
	Execute(ctx context.Context, prompt string) (string, error)
	Close() error
}

// ContextSetter is an optional interface for agents that need execution context for event streaming
type ContextSetter interface {
	SetContext(executionID, nodeID string)
}
