//go:build e2e

package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol/jsonrpc"
	"github.com/agentmaestro/agentmaestro/core/internal/testutil"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// NOTE: This E2E file now spawns real binaries (agentmaestro + mock-agent) built by `make test-deps`.
// Helper processes are in testutil/proc_helpers_test.go (build-tagged e2e).

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

func TestE2EWorkflowViaA2AProtocol(t *testing.T) {
	if testing.Short() {
		t.Skip("Skipping E2E test in short mode")
	}

	agentPort := testutil.FindAvailablePort(t, 8000, 8999)
	agent := testutil.StartMockAgentBinary(t, agentPort)

	serverPort := testutil.FindAvailablePort(t, 9000, 9999)
	dbFile := filepath.Join(t.TempDir(), "test.db")
	server := testutil.StartAgentMaestroBinary(t, serverPort, dbFile)
	_ = server // kept for symmetry; cleanup via t.Cleanup
	workflowYAML := fmt.Sprintf(`
name: "A2A E2E Test Workflow"
description: "Test workflow using A2A protocol"
config:
  api_keys: "test"
on_error: "stop_all"
nodes:
  - id: task1
    agent_url: "%s/rpc"
    prompt: "Analyze the test scenario"
    timeout: 60
  - id: task2
    agent_url: "%s/rpc"
    depends_on: [task1]
    prompt: "Generate summary of analysis"
    timeout: 60
`, agent.BaseURL, agent.BaseURL)

	contextID := uuid.New().String()
	messages := []protocol.Message{
		{
			Role: "user",
			Parts: []protocol.Part{
				{
					Kind: "text",
					Text: workflowYAML,
				},
			},
		},
	}

	params := map[string]interface{}{
		"contextId": contextID,
		"messages":  messages,
	}

	resp, err := sendA2AE2ERequest(t, fmt.Sprintf("http://localhost:%d", serverPort), "message/send", params)
	require.NoError(t, err)
	require.Nil(t, resp.Error, "A2A request should not return error")
	var task protocol.Task
	taskBytes, err := json.Marshal(resp.Result)
	require.NoError(t, err)
	err = json.Unmarshal(taskBytes, &task)
	require.NoError(t, err)

	assert.Equal(t, contextID, task.ContextID)
	assert.Equal(t, protocol.TaskStateSubmitted, task.Status.State)

	finalTask := pollTaskUntilComplete(t, fmt.Sprintf("http://localhost:%d", serverPort), task.ID, 120*time.Second)
	assert.Equal(t, protocol.TaskStateCompleted, finalTask.Status.State)
	assert.NotEmpty(t, finalTask.Artifacts, "Task should have artifacts")
}

func TestE2EConcurrentA2AWorkflows(t *testing.T) {
	if testing.Short() {
		t.Skip("Skipping E2E test in short mode")
	}

	var agents []*testutil.TestProcess
	for i := 0; i < 3; i++ {
		port := testutil.FindAvailablePort(t, 8000, 8999)
		agents = append(agents, testutil.StartMockAgentBinary(t, port))
	}
	serverPort := testutil.FindAvailablePort(t, 9000, 9999)
	dbFile := filepath.Join(t.TempDir(), "test.db")
	testutil.StartAgentMaestroBinary(t, serverPort, dbFile)
	var wg sync.WaitGroup
	taskIDs := make([]string, 5)

	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func(workflowNum int) {
			defer wg.Done()

			agentIdx := workflowNum % len(agents)
			agent := agents[agentIdx]

			workflowYAML := fmt.Sprintf(`
name: "Concurrent Workflow %d"
description: "Concurrent test workflow %d"
config:
  api_keys: "test"
nodes:
  - id: concurrent_task_%d
    agent_url: "%s/rpc"
    prompt: "Process workflow number %d"
    timeout: 60
`, workflowNum, workflowNum, workflowNum, agent.BaseURL, workflowNum)

			contextID := uuid.New().String()
			messages := []protocol.Message{
				{
					Role: "user",
					Parts: []protocol.Part{
						{
							Kind: "text",
							Text: workflowYAML,
						},
					},
				},
			}

			params := map[string]interface{}{
				"contextId": contextID,
				"messages":  messages,
			}

			resp, err := sendA2AE2ERequest(t, fmt.Sprintf("http://localhost:%d", serverPort), "message/send", params)
			require.NoError(t, err)
			require.Nil(t, resp.Error)

			var task protocol.Task
			taskBytes, err := json.Marshal(resp.Result)
			require.NoError(t, err)
			err = json.Unmarshal(taskBytes, &task)
			require.NoError(t, err)

			taskIDs[workflowNum] = task.ID
		}(i)
	}

	wg.Wait()
	serverURL := fmt.Sprintf("http://localhost:%d", serverPort)
	for i, taskID := range taskIDs {
		finalTask := pollTaskUntilComplete(t, serverURL, taskID, 120*time.Second)
		assert.Equal(t, protocol.TaskStateCompleted, finalTask.Status.State, "Workflow %d should complete", i)
		assert.NotEmpty(t, finalTask.Artifacts, "Workflow %d should have artifacts", i)
	}
}

func TestE2EMixedAgentTypes(t *testing.T) {
	if testing.Short() {
		t.Skip("Skipping E2E test in short mode")
	}

	a2aPort := testutil.FindAvailablePort(t, 8000, 8999)
	a2aAgent := testutil.StartMockAgentBinary(t, a2aPort)
	serverPort := testutil.FindAvailablePort(t, 9000, 9999)
	dbFile := filepath.Join(t.TempDir(), "test.db")
	testutil.StartAgentMaestroBinary(t, serverPort, dbFile)
	workflowYAML := fmt.Sprintf(`
name: "Mixed Agent Types Test"
description: "Workflow with both stdio and A2A agents"
config:
  api_keys: "test"
nodes:
  - id: stdio_task
    agent: demo-agent
    prompt: "This task uses stdio agent"
    timeout: 60
  - id: a2a_task
    agent_url: "%s/rpc"
    depends_on: [stdio_task]
    prompt: "This task uses A2A agent"
    timeout: 60
  - id: another_stdio_task
    agent: demo-agent
    depends_on: [a2a_task]
    prompt: "Another stdio task"
    timeout: 60
`, a2aAgent.BaseURL)

	contextID := uuid.New().String()
	messages := []protocol.Message{
		{
			Role: "user",
			Parts: []protocol.Part{
				{
					Kind: "text",
					Text: workflowYAML,
				},
			},
		},
	}

	params := map[string]interface{}{
		"contextId": contextID,
		"messages":  messages,
	}

	resp, err := sendA2AE2ERequest(t, fmt.Sprintf("http://localhost:%d", serverPort), "message/send", params)
	require.NoError(t, err)
	require.Nil(t, resp.Error)

	var task protocol.Task
	taskBytes, err := json.Marshal(resp.Result)
	require.NoError(t, err)
	err = json.Unmarshal(taskBytes, &task)
	require.NoError(t, err)

	finalTask := pollTaskUntilComplete(t, fmt.Sprintf("http://localhost:%d", serverPort), task.ID, 180*time.Second)
	assert.Equal(t, protocol.TaskStateCompleted, finalTask.Status.State)
	assert.NotEmpty(t, finalTask.Artifacts)
}

func TestE2EAgentFailureRecovery(t *testing.T) {
	if testing.Short() {
		t.Skip("Skipping E2E test in short mode")
	}

	agentPort := testutil.FindAvailablePort(t, 8000, 8999)
	agent := testutil.StartMockAgentBinary(t, agentPort)
	serverPort := testutil.FindAvailablePort(t, 9000, 9999)
	dbFile := filepath.Join(t.TempDir(), "test.db")
	testutil.StartAgentMaestroBinary(t, serverPort, dbFile)
	workflowYAML := fmt.Sprintf(`
name: "Agent Failure Recovery Test"
description: "Test workflow that will experience agent failure"
config:
  api_keys: "test"
nodes:
  - id: task_before_failure
    agent_url: "%s/rpc"
    prompt: "Task that should complete before failure"
    timeout: 30
  - id: task_during_failure
    agent_url: "%s/rpc"
    depends_on: [task_before_failure]
    prompt: "Task that will fail when agent is killed"
    timeout: 120
`, agent.BaseURL, agent.BaseURL)

	contextID := uuid.New().String()
	messages := []protocol.Message{
		{
			Role: "user",
			Parts: []protocol.Part{
				{
					Kind: "text",
					Text: workflowYAML,
				},
			},
		},
	}

	params := map[string]interface{}{
		"contextId": contextID,
		"messages":  messages,
	}

	resp, err := sendA2AE2ERequest(t, fmt.Sprintf("http://localhost:%d", serverPort), "message/send", params)
	require.NoError(t, err)
	require.Nil(t, resp.Error)

	var task protocol.Task
	taskBytes, err := json.Marshal(resp.Result)
	require.NoError(t, err)
	err = json.Unmarshal(taskBytes, &task)
	require.NoError(t, err)

	time.Sleep(2 * time.Second)

	agent.Stop() // kill external agent mid-execution

	finalTask := pollTaskUntilComplete(t, fmt.Sprintf("http://localhost:%d", serverPort), task.ID, 180*time.Second)
	assert.Equal(t, protocol.TaskStateFailed, finalTask.Status.State)
	assert.NotEmpty(t, finalTask.History, "Task should have failure history")

	hasErrorInfo := false
	for _, msg := range finalTask.History {
		for _, part := range msg.Parts {
			if part.Kind == "text" && (strings.Contains(part.Text, "error") || strings.Contains(part.Text, "failed")) {
				hasErrorInfo = true
				break
			}
		}
		if hasErrorInfo {
			break
		}
	}
	assert.True(t, hasErrorInfo, "Task history should contain error information")
}
