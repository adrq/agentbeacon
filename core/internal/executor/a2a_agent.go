package executor

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol/jsonrpc"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
)

type A2AAgent struct {
	agentURL   string
	httpClient *http.Client
	taskID     string
	contextID  string
	// Event streaming
	events      chan<- *storage.ExecutionEvent
	executionID string
	nodeID      string
	// Minimal diff tracking
	lastState       string
	lastHistoryLen  int
	lastArtifactLen int
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

	// Emit submitted immediately
	a.emitStateChange(constants.TaskStateSubmitted, constants.TaskStateUnknown)

	for {
		select {
		case <-ctx.Done():
			// Best-effort cancel remote task and emit canceled state
			_ = a.cancelTask(taskID)
			a.emitStateChange(constants.TaskStateCanceled, a.lastStateOrUnknown())
			return "", ctx.Err()
		default:
		}

		task, err := a.pollTaskStatus(taskID)
		if err != nil {
			a.emitError(fmt.Errorf("failed to poll task status: %w", err))
			return "", fmt.Errorf("failed to poll task status: %w", err)
		}

		switch task.Status.State {
		case protocol.TaskStateCompleted:
			a.processTaskUpdate(task)
			return a.extractOutput(task), nil
		case protocol.TaskStateFailed:
			a.processTaskUpdate(task)
			return "", fmt.Errorf("task failed: %s", a.extractError(task))
		case protocol.TaskStateCanceled:
			a.processTaskUpdate(task)
			return "", fmt.Errorf("task was canceled")
		case protocol.TaskStateRejected:
			a.processTaskUpdate(task)
			return "", fmt.Errorf("task was rejected")
		default:
			a.processTaskUpdate(task)
			// Sleep with context cancellation for more responsive timeout handling
			select {
			case <-ctx.Done():
				_ = a.cancelTask(taskID)
				a.emitStateChange(constants.TaskStateCanceled, a.lastStateOrUnknown())
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

// GetProtocolID implements ProtocolTracker interface
func (a *A2AAgent) GetProtocolID() (string, string) {
	return "a2a", a.taskID
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

// --- Event streaming Helpers ---

func (a *A2AAgent) SetEventChannel(events chan<- *storage.ExecutionEvent) {
	a.events = events
}

func (a *A2AAgent) SetContext(executionID, nodeID string) {
	a.executionID = executionID
	a.nodeID = nodeID
}

func (a *A2AAgent) emit(ev *storage.ExecutionEvent) {
	if a.events == nil || a.executionID == "" || a.nodeID == "" || ev == nil {
		return
	}
	ev.ExecutionID = a.executionID
	ev.NodeID = a.nodeID
	select {
	case a.events <- ev:
	default:
		// drop to avoid blocking
	}
}

func (a *A2AAgent) lastStateOrUnknown() string {
	if a.lastState == "" {
		return constants.TaskStateUnknown
	}
	return a.lastState
}

func (a *A2AAgent) emitStateChange(newState, prevState string) {
	if newState == a.lastState {
		return
	}
	a.emit(CreateStateChangeEvent("", "", storage.EventSourceA2A, newState, prevState))
	a.lastState = newState
}

func (a *A2AAgent) emitOutput(text string) {
	if text == "" {
		return
	}
	const maxChunk = 8 * 1024
	if len(text) > maxChunk {
		text = text[:maxChunk] + "\n... [TRUNCATED]"
	}
	a.emit(CreateOutputEvent("", "", storage.EventSourceA2A, text))
}

func (a *A2AAgent) emitError(err error) {
	if err == nil {
		return
	}
	a.emit(CreateErrorEvent("", "", err))
}

func (a *A2AAgent) processTaskUpdate(task *protocol.Task) {
	// State change
	state := task.Status.State
	prev := a.lastStateOrUnknown()
	if state != a.lastState {
		a.emitStateChange(state, prev)
		// If input required, also emit a dedicated event for easier filtering
		if state == protocol.TaskStateInputRequired {
			ev := &storage.ExecutionEvent{Type: storage.EventTypeInputRequired, Source: storage.EventSourceA2A, Message: "Input required"}
			a.emit(ev)
		}
	}

	// Stream new history messages as output
	if l := len(task.History); l > a.lastHistoryLen {
		for i := a.lastHistoryLen; i < l; i++ {
			msg := task.History[i]
			for _, part := range msg.Parts {
				if part.Text != "" {
					a.emitOutput(part.Text)
				}
			}
		}
		a.lastHistoryLen = l
	}

	// Stream new artifacts as output (text parts only for MVP)
	if l := len(task.Artifacts); l > a.lastArtifactLen {
		for i := a.lastArtifactLen; i < l; i++ {
			art := task.Artifacts[i]
			for _, part := range art.Parts {
				if part.Text != "" {
					a.emitOutput(part.Text)
				}
			}
		}
		a.lastArtifactLen = l
	}
}
