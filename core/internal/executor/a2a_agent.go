package executor

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol/jsonrpc"
)

type A2AAgent struct {
	agentURL   string
	httpClient *http.Client
	taskID     string
	contextID  string
}

func NewA2AAgent(agentURL string) Agent {
	return &A2AAgent{
		agentURL:   agentURL,
		httpClient: &http.Client{Timeout: 30 * time.Second},
		contextID:  fmt.Sprintf("ctx-%d", time.Now().UnixNano()),
	}
}

func (a *A2AAgent) Execute(ctx context.Context, prompt string) (string, error) {
	taskID, err := a.submitTask(prompt)
	if err != nil {
		return "", fmt.Errorf("failed to submit task: %w", err)
	}

	a.taskID = taskID

	for {
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		default:
		}

		task, err := a.pollTaskStatus(taskID)
		if err != nil {
			return "", fmt.Errorf("failed to poll task status: %w", err)
		}

		switch task.Status.State {
		case protocol.TaskStateCompleted:
			return a.extractOutput(task), nil
		case protocol.TaskStateFailed:
			return "", fmt.Errorf("task failed: %s", a.extractError(task))
		case protocol.TaskStateCanceled:
			return "", fmt.Errorf("task was canceled")
		case protocol.TaskStateRejected:
			return "", fmt.Errorf("task was rejected")
		default:
			// Sleep with context cancellation for more responsive timeout handling
			select {
			case <-ctx.Done():
				return "", ctx.Err()
			case <-time.After(1 * time.Second):
				// Continue to next polling iteration
			}
		}
	}
}

func (a *A2AAgent) Close() error {
	if a.taskID != "" && a.httpClient != nil {
		_ = a.cancelTask(a.taskID)
	}
	return nil
}

func (a *A2AAgent) submitTask(prompt string) (string, error) {
	request := jsonrpc.Request{
		JSONRPC: "2.0",
		Method:  "message/send",
		ID:      1,
	}

	params := map[string]interface{}{
		"contextId": a.contextID,
		"messages": []map[string]interface{}{{
			"role": "user",
			"parts": []map[string]interface{}{{
				"kind": "text",
				"text": prompt,
			}},
		}},
	}

	paramsJSON, _ := json.Marshal(params)
	request.Params = json.RawMessage(paramsJSON)

	reqBody, _ := json.Marshal(request)

	resp, err := a.httpClient.Post(
		a.agentURL,
		"application/json",
		bytes.NewBuffer(reqBody),
	)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	var rpcResp jsonrpc.Response
	if err := json.NewDecoder(resp.Body).Decode(&rpcResp); err != nil {
		return "", err
	}

	if rpcResp.Error != nil {
		return "", fmt.Errorf("RPC error: %s", rpcResp.Error.Message)
	}

	resultBytes, err := json.Marshal(rpcResp.Result)
	if err != nil {
		return "", err
	}

	var task protocol.Task
	if err := json.Unmarshal(resultBytes, &task); err != nil {
		return "", err
	}

	return task.ID, nil
}

func (a *A2AAgent) pollTaskStatus(taskID string) (*protocol.Task, error) {
	request := jsonrpc.Request{
		JSONRPC: "2.0",
		Method:  "tasks/get",
		ID:      2,
	}

	params := map[string]interface{}{
		"taskId": taskID,
	}

	paramsJSON, _ := json.Marshal(params)
	request.Params = json.RawMessage(paramsJSON)

	reqBody, _ := json.Marshal(request)

	resp, err := a.httpClient.Post(
		a.agentURL,
		"application/json",
		bytes.NewBuffer(reqBody),
	)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var rpcResp jsonrpc.Response
	if err := json.NewDecoder(resp.Body).Decode(&rpcResp); err != nil {
		return nil, err
	}

	if rpcResp.Error != nil {
		return nil, fmt.Errorf("RPC error: %s", rpcResp.Error.Message)
	}

	resultBytes, err := json.Marshal(rpcResp.Result)
	if err != nil {
		return nil, err
	}

	var task protocol.Task
	if err := json.Unmarshal(resultBytes, &task); err != nil {
		return nil, err
	}

	return &task, nil
}

func (a *A2AAgent) cancelTask(taskID string) error {
	request := jsonrpc.Request{
		JSONRPC: "2.0",
		Method:  "tasks/cancel",
		ID:      3,
	}

	params := map[string]interface{}{
		"taskId": taskID,
	}

	paramsJSON, _ := json.Marshal(params)
	request.Params = json.RawMessage(paramsJSON)

	reqBody, _ := json.Marshal(request)

	resp, err := a.httpClient.Post(
		a.agentURL,
		"application/json",
		bytes.NewBuffer(reqBody),
	)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	return nil
}

func (a *A2AAgent) extractOutput(task *protocol.Task) string {
	if len(task.Artifacts) > 0 {
		for _, artifact := range task.Artifacts {
			if len(artifact.Parts) > 0 {
				for _, part := range artifact.Parts {
					if part.Text != "" {
						return part.Text
					}
				}
			}
		}
	}

	if len(task.History) > 0 {
		lastMessage := task.History[len(task.History)-1]
		if len(lastMessage.Parts) > 0 {
			for _, part := range lastMessage.Parts {
				if part.Text != "" {
					return part.Text
				}
			}
		}
	}

	return "Task completed successfully"
}

func (a *A2AAgent) extractError(task *protocol.Task) string {
	if len(task.History) > 0 {
		lastMessage := task.History[len(task.History)-1]
		if len(lastMessage.Parts) > 0 {
			for _, part := range lastMessage.Parts {
				if part.Text != "" {
					return part.Text
				}
			}
		}
	}

	return "Task failed without error details"
}
