package storage

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/jmoiron/sqlx"
	"gorm.io/datatypes"
)

func TestExecutionEvents(t *testing.T) {
	t.Run("SQLite", func(t *testing.T) {
		testExecutionEvents(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	})

	t.Run("PostgreSQL", func(t *testing.T) {
		pgDSN := getPostgreSQLDSN()
		mainDB, err := sqlx.Connect("postgres", pgDSN)
		if err != nil {
			t.Fatalf("PostgreSQL not available: %v", err)
		}
		defer mainDB.Close()

		testDBName := "events_test_db"
		mainDB.Exec("DROP DATABASE IF EXISTS " + testDBName)
		_, err = mainDB.Exec("CREATE DATABASE " + testDBName)
		if err != nil {
			t.Fatalf("Cannot create test database: %v", err)
		}
		defer mainDB.Exec("DROP DATABASE " + testDBName)

		// Extract base DSN and create test DSN
		var testDSN string
		if baseDSN := os.Getenv("DATABASE_URL"); baseDSN != "" {
			// For custom DATABASE_URL, replace database name
			testDSN = baseDSN[:strings.LastIndex(baseDSN, "/")+1] + testDBName
			if strings.Contains(baseDSN, "?") {
				testDSN += "?" + baseDSN[strings.Index(baseDSN, "?")+1:]
			}
		} else {
			testDSN = "postgres://postgres:postgres@127.0.0.1/" + testDBName + "?sslmode=disable"
		}
		testExecutionEvents(t, "postgres", testDSN)
	})
}

func testExecutionEvents(t *testing.T, driver, dsn string) {
	db, err := Open(driver, dsn)
	if err != nil {
		t.Fatalf("Failed to open %s database: %v", driver, err)
	}
	defer db.Close()

	// Create test execution
	execution := &Execution{
		ID:           "test-exec-001",
		WorkflowName: "test-workflow",
		Status:       constants.TaskStateWorking,
		NodeStates:   datatypes.JSON(`{"node1": {"status": "working"}}`),
		A2ATasks:     datatypes.JSON(`{}`),
		ACPSessions:  datatypes.JSON(`{"node1": "session_123"}`),
		Logs:         "Test execution",
	}

	err = db.CreateExecution(execution)
	if err != nil {
		t.Fatalf("Failed to create test execution: %v", err)
	}

	t.Run("CreateAndRetrieve", func(t *testing.T) {
		// Create 5 events
		events := []*ExecutionEvent{
			{
				ExecutionID: execution.ID,
				NodeID:      "node1",
				Type:        EventTypeSubmitted,
				Source:      EventSourceSystem,
				State:       stringPtr(constants.TaskStateSubmitted),
				Message:     "Task submitted",
			},
			{
				ExecutionID: execution.ID,
				NodeID:      "node1",
				Type:        EventTypeStateChange,
				Source:      EventSourceSystem,
				State:       stringPtr(constants.TaskStateWorking),
				PrevState:   stringPtr(constants.TaskStateSubmitted),
				Message:     "Task started",
			},
			{
				ExecutionID: execution.ID,
				NodeID:      "node1",
				Type:        EventTypeOutput,
				Source:      EventSourceA2A,
				Message:     "Processing data",
			},
			{
				ExecutionID: execution.ID,
				NodeID:      "node1",
				Type:        EventTypeProgress,
				Source:      EventSourceA2A,
				Message:     "50% complete",
				Data:        datatypes.JSON(`{"progress": 0.5}`),
			},
			{
				ExecutionID: execution.ID,
				NodeID:      "node1",
				Type:        EventTypeCompleted,
				Source:      EventSourceA2A,
				State:       stringPtr(constants.TaskStateCompleted),
				Message:     "Task completed successfully",
			},
		}

		// Insert events
		for _, event := range events {
			err := db.CreateExecutionEvent(event)
			if err != nil {
				t.Fatalf("Failed to create event: %v", err)
			}
		}

		// Retrieve events
		retrievedEvents, err := db.GetExecutionEvents(execution.ID, 10)
		if err != nil {
			t.Fatalf("Failed to get events: %v", err)
		}

		if len(retrievedEvents) != 5 {
			t.Errorf("Expected 5 events, got %d", len(retrievedEvents))
		}

		// Check that events are ordered by ID (most recent first)
		if len(retrievedEvents) >= 2 {
			if retrievedEvents[0].ID <= retrievedEvents[1].ID {
				t.Error("Events not properly ordered by ID (most recent first)")
			}
		}

		// Check that timestamps are set
		for _, event := range retrievedEvents {
			if event.Timestamp.IsZero() {
				t.Error("Event timestamp not set")
			}
		}
	})

	t.Run("GetEventsAfterID", func(t *testing.T) {
		// Create 10 more events
		for i := 0; i < 10; i++ {
			event := &ExecutionEvent{
				ExecutionID: execution.ID,
				NodeID:      "node2",
				Type:        EventTypeOutput,
				Source:      EventSourceSystem,
				Message:     "Output message",
			}
			err := db.CreateExecutionEvent(event)
			if err != nil {
				t.Fatalf("Failed to create event %d: %v", i, err)
			}
		}

		// Get all events first
		allEvents, err := db.GetExecutionEvents(execution.ID, 100)
		if err != nil {
			t.Fatalf("Failed to get all events: %v", err)
		}

		if len(allEvents) < 10 {
			t.Fatalf("Expected at least 10 events, got %d", len(allEvents))
		}

		// Get events after the 5th most recent
		afterID := allEvents[4].ID
		newEvents, err := db.GetExecutionEventsAfter(execution.ID, afterID, 100)
		if err != nil {
			t.Fatalf("Failed to get events after ID %d: %v", afterID, err)
		}

		// Should get the 4 most recent events
		if len(newEvents) != 4 {
			t.Errorf("Expected 4 events after ID %d, got %d", afterID, len(newEvents))
		}

		// Check ordering
		for i := 1; i < len(newEvents); i++ {
			if newEvents[i-1].ID >= newEvents[i].ID {
				t.Error("Events not properly ordered by ID (ascending)")
			}
		}
	})

	t.Run("GetNodeEvents", func(t *testing.T) {
		// Create events for different nodes
		nodeEvents := map[string]int{
			"node_a": 3,
			"node_b": 5,
			"node_c": 2,
		}

		for nodeID, count := range nodeEvents {
			for i := 0; i < count; i++ {
				event := &ExecutionEvent{
					ExecutionID: execution.ID,
					NodeID:      nodeID,
					Type:        EventTypeOutput,
					Source:      EventSourceSystem,
					Message:     "Node-specific output",
				}
				err := db.CreateExecutionEvent(event)
				if err != nil {
					t.Fatalf("Failed to create event for node %s: %v", nodeID, err)
				}
			}
		}

		// Test getting events for specific node
		nodeAEvents, err := db.GetNodeEvents(execution.ID, "node_a")
		if err != nil {
			t.Fatalf("Failed to get node events: %v", err)
		}

		if len(nodeAEvents) != 3 {
			t.Errorf("Expected 3 events for node_a, got %d", len(nodeAEvents))
		}

		// Verify all events are for the correct node
		for _, event := range nodeAEvents {
			if event.NodeID != "node_a" {
				t.Errorf("Expected node_a, got %s", event.NodeID)
			}
		}
	})

	t.Run("LargeEventVolume", func(t *testing.T) {
		// Create 1000 events
		const eventCount = 1000

		for i := 0; i < eventCount; i++ {
			event := &ExecutionEvent{
				ExecutionID: execution.ID,
				NodeID:      "bulk_node",
				Type:        EventTypeOutput,
				Source:      EventSourceSystem,
				Message:     "Bulk test event",
			}
			err := db.CreateExecutionEvent(event)
			if err != nil {
				t.Fatalf("Failed to create bulk event %d: %v", i, err)
			}
		}

		// Test pagination with large volume
		events, err := db.GetExecutionEvents(execution.ID, 50)
		if err != nil {
			t.Fatalf("Failed to get events with large volume: %v", err)
		}

		if len(events) != 50 {
			t.Errorf("Expected 50 events, got %d", len(events))
		}

		// Test pagination
		lastID := events[len(events)-1].ID
		moreEvents, err := db.GetExecutionEventsAfter(execution.ID, lastID, 100)
		if err != nil {
			t.Fatalf("Failed to paginate events: %v", err)
		}

		if len(moreEvents) == 0 {
			t.Error("Expected more events after pagination")
		}
	})
}

// Helper function to create string pointers
func stringPtr(s string) *string {
	return &s
}
