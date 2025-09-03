package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol/jsonrpc"
	"github.com/agentmaestro/agentmaestro/core/internal/testutil"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
	_ "github.com/lib/pq"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// NOTE: This E2E file spawns the real agentmaestro binary built by `make test-deps` and exercises the public HTTP API.
// Helper processes are in testutil/proc_helpers_e2e.go and are available in normal test runs.

func sendA2AE2ERequest(t *testing.T, serverURL, method string, params interface{}) (*jsonrpc.Response, error) {
	request := jsonrpc.NewRequest(method, params, uuid.New().String())

	body, err := json.Marshal(request)
	require.NoError(t, err)

	resp, err := http.Post(serverURL+"/rpc", "application/json", bytes.NewBuffer(body))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	responseBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var rpcResponse jsonrpc.Response
	err = json.Unmarshal(responseBody, &rpcResponse)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal response: %w, body: %s", err, string(responseBody))
	}

	return &rpcResponse, nil
}

func pollTaskUntilComplete(t *testing.T, serverURL, taskID string, timeout time.Duration) *protocol.Task {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			t.Fatalf("Task %s did not complete within timeout", taskID)
		case <-ticker.C:
			params := map[string]string{"taskId": taskID}
			resp, err := sendA2AE2ERequest(t, serverURL, "tasks/get", params)
			require.NoError(t, err)

			if resp.Error != nil {
				t.Fatalf("Error getting task status: %+v", resp.Error)
			}

			var task protocol.Task
			taskBytes, err := json.Marshal(resp.Result)
			require.NoError(t, err)
			err = json.Unmarshal(taskBytes, &task)
			require.NoError(t, err)

			switch task.Status.State {
			case protocol.TaskStateCompleted, protocol.TaskStateFailed, protocol.TaskStateCanceled:
				return &task
			}
		}
	}
}

// Happy path on SQLite backend using registry + workflowRef
func TestE2E_A2A_HappyPath_WorkflowRef_SQLite(t *testing.T) {
	if testing.Short() {
		t.Skip("short mode")
	}

	serverPort := testutil.FindAvailablePort(t, 9000, 9999)
	dbFile := filepath.Join(t.TempDir(), "test.db")
	_ = testutil.StartAgentMaestroBinary(t, serverPort, dbFile)

	base := fmt.Sprintf("http://localhost:%d", serverPort)

	// Register a simple two-node workflow referencing the stdio mock-agent
	// The orchestrator will spawn ./bin/mock-agent for each node based on examples/agents.yaml
	workflowYAML := "name: e2e-ref\nnamespace: demo\ndescription: 'e2e happy path'\n" +
		"nodes:\n" +
		"  - id: t1\n    agent: mock-agent\n    request:\n      prompt: 'a'\n" +
		"  - id: t2\n    agent: mock-agent\n    depends_on: [t1]\n    request:\n      prompt: 'b'\n"

	regBody := map[string]string{"workflow_yaml": workflowYAML}
	body, _ := json.Marshal(regBody)
	r, err := http.Post(base+"/api/workflows/register", "application/json", bytes.NewBuffer(body))
	require.NoError(t, err)
	r.Body.Close()

	// Submit A2A message with workflowRef
	contextID := uuid.New().String()
	msg := map[string]interface{}{
		"role": "user",
		"parts": []map[string]interface{}{{
			"kind": "data",
			"data": map[string]interface{}{"data": map[string]interface{}{"workflowRef": "demo/e2e-ref:latest"}},
		}},
		"messageId": uuid.New().String(),
		"kind":      "message",
	}
	params := map[string]interface{}{"contextId": contextID, "messages": []interface{}{msg}}
	rpcResp, err := sendA2AE2ERequest(t, base, "message/send", params)
	require.NoError(t, err)
	require.Nil(t, rpcResp.Error)

	var task protocol.Task
	taskBytes, _ := json.Marshal(rpcResp.Result)
	_ = json.Unmarshal(taskBytes, &task)
	assert.NotEmpty(t, task.ID)
	assert.Equal(t, contextID, task.ContextID)

	final := pollTaskUntilComplete(t, base, task.ID, 120*time.Second)
	assert.Equal(t, protocol.TaskStateCompleted, final.Status.State)
	assert.NotEmpty(t, final.Artifacts)
}

// Optional Postgres variant: uses DATABASE_URL environment variable if provided
func TestE2E_A2A_HappyPath_WorkflowRef_Postgres(t *testing.T) {
	if testing.Short() {
		t.Skip("short mode")
	}
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		dsn = "postgres://postgres:postgres@127.0.0.1/postgres?sslmode=disable"
	}
	// Fail fast if PostgreSQL isn't reachable instead of silently skipping
	if _, err := sqlx.Connect("postgres", dsn); err != nil {
		t.Fatalf("PostgreSQL not available at %s: %v. Set DATABASE_URL to override.", dsn, err)
	}
	serverPort := testutil.FindAvailablePort(t, 9000, 9999)
	_ = testutil.StartAgentMaestroBinaryWith(t, serverPort, "postgres", dsn)

	base := fmt.Sprintf("http://localhost:%d", serverPort)
	workflowYAML := "name: e2e-ref-pg\nnamespace: demo\ndescription: 'e2e happy path pg'\n" +
		"nodes:\n" +
		"  - id: t1\n    agent: mock-agent\n    request:\n      prompt: 'a'\n"
	regBody := map[string]string{"workflow_yaml": workflowYAML}
	body, _ := json.Marshal(regBody)
	r, err := http.Post(base+"/api/workflows/register", "application/json", bytes.NewBuffer(body))
	require.NoError(t, err)
	r.Body.Close()

	contextID := uuid.New().String()
	msg := map[string]interface{}{
		"role": "user",
		"parts": []map[string]interface{}{{
			"kind": "data",
			"data": map[string]interface{}{"data": map[string]interface{}{"workflowRef": "demo/e2e-ref-pg:latest"}},
		}},
		"messageId": uuid.New().String(),
		"kind":      "message",
	}
	params := map[string]interface{}{"contextId": contextID, "messages": []interface{}{msg}}
	rpcResp, err := sendA2AE2ERequest(t, base, "message/send", params)
	require.NoError(t, err)
	require.Nil(t, rpcResp.Error)
}

// Other complex E2E scenarios have been moved to BACKLOG.md to keep this suite lean and fast.
