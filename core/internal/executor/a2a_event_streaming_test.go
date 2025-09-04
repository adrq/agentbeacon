package executor

import (
	"context"
	"fmt"
	"os/exec"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/agentmaestro/agentmaestro/core/internal/testutil"
	"github.com/stretchr/testify/require"
)

// High-value TDD tests for A2A event capture (B1)

func TestA2AEventStreaming_Basic(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Start mock A2A server
	port := testutil.FindAvailablePort(t, 9510, 9520)
	cmd := exec.Command("../../../bin/mock-agent", "--mode", "a2a", "--port", strconv.Itoa(port))
	require.NoError(t, cmd.Start())
	t.Cleanup(func() {
		if cmd.Process != nil {
			_ = cmd.Process.Kill()
		}
	})
	time.Sleep(300 * time.Millisecond)

	// DB + executor for event writer
	db := setupTestDB(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	t.Cleanup(func() { _ = db.Close() })
	executor := NewExecutor(db, setupTestConfigLoader(t))
	t.Cleanup(func() { executor.Close() })

	// Agent under test
	agentURL := fmt.Sprintf("http://localhost:%d/rpc", port)
	agent := NewA2AAgent(agentURL)
	t.Cleanup(func() { _ = agent.Close() })

	// Wire events + context
	if s, ok := agent.(EventStreamer); ok {
		s.SetEventChannel(executor.eventChan)
	} else {
		t.Fatalf("A2AAgent should implement EventStreamer")
	}
	if cs, ok := agent.(ContextSetter); ok {
		cs.SetContext("exec-basic-001", "node-basic-001")
	} else {
		t.Fatalf("A2AAgent should implement ContextSetter")
	}

	// Execute normal prompt
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_, err := agent.Execute(ctx, "hello world")
	require.NoError(t, err)

	// Allow event writer to flush
	time.Sleep(200 * time.Millisecond)

	events, err := db.GetExecutionEvents("exec-basic-001", 100)
	require.NoError(t, err)
	if len(events) == 0 {
		t.Fatalf("expected events, got none")
	}

	// Assertions: saw submitted -> working -> completed, and at least one output
	var sawSubmitted, sawWorking, sawCompleted, sawOutput bool
	for _, ev := range events {
		if ev.Type == storage.EventTypeStateChange && ev.State != nil {
			switch *ev.State {
			case constants.TaskStateSubmitted:
				sawSubmitted = true
			case constants.TaskStateWorking:
				sawWorking = true
			case constants.TaskStateCompleted:
				sawCompleted = true
			}
		}
		if ev.Type == storage.EventTypeOutput && ev.Message != "" {
			sawOutput = true
		}
	}

	if !sawSubmitted {
		t.Errorf("missing submitted state_change")
	}
	if !sawWorking {
		t.Errorf("missing working state_change")
	}
	if !sawCompleted {
		t.Errorf("missing completed state_change")
	}
	if !sawOutput {
		t.Errorf("expected at least one output event")
	}
}

func TestA2AEventStreaming_Cancellation(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Start mock A2A server
	port := testutil.FindAvailablePort(t, 9521, 9530)
	cmd := exec.Command("../../../bin/mock-agent", "--mode", "a2a", "--port", strconv.Itoa(port))
	require.NoError(t, cmd.Start())
	t.Cleanup(func() {
		if cmd.Process != nil {
			_ = cmd.Process.Kill()
		}
	})
	time.Sleep(300 * time.Millisecond)

	// DB + executor for event writer
	db := setupTestDB(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	t.Cleanup(func() { _ = db.Close() })
	executor := NewExecutor(db, setupTestConfigLoader(t))
	t.Cleanup(func() { executor.Close() })

	// Agent under test
	agentURL := fmt.Sprintf("http://localhost:%d/rpc", port)
	agent := NewA2AAgent(agentURL)
	t.Cleanup(func() { _ = agent.Close() })

	// Wire events + context
	if s, ok := agent.(EventStreamer); ok {
		s.SetEventChannel(executor.eventChan)
	} else {
		t.Fatalf("A2AAgent should implement EventStreamer")
	}
	if cs, ok := agent.(ContextSetter); ok {
		cs.SetContext("exec-cancel-001", "node-cancel-001")
	} else {
		t.Fatalf("A2AAgent should implement ContextSetter")
	}

	// Context with short timeout; prompt HANG causes long-running task
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	_, err := agent.Execute(ctx, "HANG")
	require.Error(t, err)

	time.Sleep(200 * time.Millisecond)

	events, err := db.GetExecutionEvents("exec-cancel-001", 100)
	require.NoError(t, err)
	var sawWorking, sawCanceled bool
	for _, ev := range events {
		if ev.Type == storage.EventTypeStateChange && ev.State != nil {
			switch *ev.State {
			case constants.TaskStateWorking:
				sawWorking = true
			case constants.TaskStateCanceled:
				sawCanceled = true
			}
		}
	}
	if !sawWorking {
		t.Errorf("expected working state before cancellation")
	}
	if !sawCanceled {
		t.Errorf("expected canceled state_change to be emitted")
	}
}
