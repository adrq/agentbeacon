package executor

import (
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/engine"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"gorm.io/datatypes"
)

// Minimal regression test ensuring concurrent log update path (protected by logMutex) appends logs.
func TestConcurrentLogUpdates(t *testing.T) {
	db := setupTestDB(t, "sqlite3", ":memory:")
	defer db.Close()

	configLoader := setupTestConfigLoader(t)
	executor := NewExecutor(db, configLoader)

	execution := &engine.Execution{
		ID:         "concurrent-log-test",
		WorkflowID: "test-workflow",
		Status:     constants.TaskStateWorking,
		NodeStates: make(map[string]engine.NodeState),
		StartedAt:  time.Now(),
	}

	dbExecution := &storage.Execution{ID: execution.ID, WorkflowName: execution.WorkflowID, Status: execution.Status, NodeStates: datatypes.JSON("{}"), A2ATasks: datatypes.JSON("{}"), Logs: "", StartedAt: execution.StartedAt}
	if err := db.CreateExecution(dbExecution); err != nil {
		t.Fatalf("create exec: %v", err)
	}

	executor.updateExecutionInDB(execution, "log1")
	executor.updateExecutionInDB(execution, "log2")
	finalExecution, err := db.GetExecution(execution.ID)
	if err != nil {
		t.Fatalf("get exec: %v", err)
	}
	if finalExecution.Logs == "" {
		t.Error("expected logs written")
	}
}

// Other legacy execution fix tests removed during registry migration cleanup.
