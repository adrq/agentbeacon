package executor

import (
	"errors"
	"testing"

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
)

func TestEventHelpers(t *testing.T) {
	executionID := "test-exec-123"
	nodeID := "test-node-456"

	t.Run("CreateStateChangeEvent", func(t *testing.T) {
		event := CreateStateChangeEvent(
			executionID,
			nodeID,
			storage.EventSourceSystem,
			constants.TaskStateWorking,
			constants.TaskStateSubmitted,
		)

		if event.ExecutionID != executionID {
			t.Errorf("Expected ExecutionID %s, got %s", executionID, event.ExecutionID)
		}

		if event.NodeID != nodeID {
			t.Errorf("Expected NodeID %s, got %s", nodeID, event.NodeID)
		}

		if event.Type != storage.EventTypeStateChange {
			t.Errorf("Expected Type %s, got %s", storage.EventTypeStateChange, event.Type)
		}

		if event.Source != storage.EventSourceSystem {
			t.Errorf("Expected Source %s, got %s", storage.EventSourceSystem, event.Source)
		}

		if event.State == nil || *event.State != constants.TaskStateWorking {
			t.Errorf("Expected State %s, got %v", constants.TaskStateWorking, event.State)
		}

		if event.PrevState == nil || *event.PrevState != constants.TaskStateSubmitted {
			t.Errorf("Expected PrevState %s, got %v", constants.TaskStateSubmitted, event.PrevState)
		}

		expectedMessage := "State changed from submitted to working"
		if event.Message != expectedMessage {
			t.Errorf("Expected Message %s, got %s", expectedMessage, event.Message)
		}
	})

	t.Run("CreateOutputEvent", func(t *testing.T) {
		output := "Processing data successfully"
		event := CreateOutputEvent(executionID, nodeID, storage.EventSourceA2A, output)

		if event.ExecutionID != executionID {
			t.Errorf("Expected ExecutionID %s, got %s", executionID, event.ExecutionID)
		}

		if event.NodeID != nodeID {
			t.Errorf("Expected NodeID %s, got %s", nodeID, event.NodeID)
		}

		if event.Type != storage.EventTypeOutput {
			t.Errorf("Expected Type %s, got %s", storage.EventTypeOutput, event.Type)
		}

		if event.Source != storage.EventSourceA2A {
			t.Errorf("Expected Source %s, got %s", storage.EventSourceA2A, event.Source)
		}

		if event.Message != output {
			t.Errorf("Expected Message %s, got %s", output, event.Message)
		}

		if event.State != nil {
			t.Errorf("Expected State to be nil for output event, got %v", event.State)
		}
	})

	t.Run("CreateProgressEvent", func(t *testing.T) {
		progress := 0.75
		message := "75% complete"
		event := CreateProgressEvent(executionID, nodeID, storage.EventSourceACP, progress, message)

		if event.ExecutionID != executionID {
			t.Errorf("Expected ExecutionID %s, got %s", executionID, event.ExecutionID)
		}

		if event.NodeID != nodeID {
			t.Errorf("Expected NodeID %s, got %s", nodeID, event.NodeID)
		}

		if event.Type != storage.EventTypeProgress {
			t.Errorf("Expected Type %s, got %s", storage.EventTypeProgress, event.Type)
		}

		if event.Source != storage.EventSourceACP {
			t.Errorf("Expected Source %s, got %s", storage.EventSourceACP, event.Source)
		}

		if event.Message != message {
			t.Errorf("Expected Message %s, got %s", message, event.Message)
		}

		expectedData := `{"progress": 0.750000}`
		if string(event.Data) != expectedData {
			t.Errorf("Expected Data %s, got %s", expectedData, string(event.Data))
		}
	})

	t.Run("CreateErrorEvent", func(t *testing.T) {
		testError := errors.New("test error occurred")
		event := CreateErrorEvent(executionID, nodeID, testError)

		if event.ExecutionID != executionID {
			t.Errorf("Expected ExecutionID %s, got %s", executionID, event.ExecutionID)
		}

		if event.NodeID != nodeID {
			t.Errorf("Expected NodeID %s, got %s", nodeID, event.NodeID)
		}

		if event.Type != storage.EventTypeError {
			t.Errorf("Expected Type %s, got %s", storage.EventTypeError, event.Type)
		}

		if event.Source != storage.EventSourceSystem {
			t.Errorf("Expected Source %s, got %s", storage.EventSourceSystem, event.Source)
		}

		if event.Message != testError.Error() {
			t.Errorf("Expected Message %s, got %s", testError.Error(), event.Message)
		}
	})
}
