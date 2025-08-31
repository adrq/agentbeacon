package executor

import (
	"context"
	"fmt"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/engine"
)

type RetryableExecutor struct {
	executor *Executor
}

func NewRetryableExecutor(executor *Executor) *RetryableExecutor {
	return &RetryableExecutor{
		executor: executor,
	}
}

func (re *RetryableExecutor) executeNodeWithRetry(ctx context.Context, execution *engine.Execution, node *engine.Node) error {
	re.executor.mutex.RLock()
	nodeState := execution.NodeStates[node.ID]
	status := nodeState.Status
	re.executor.mutex.RUnlock()

	if status != constants.TaskStateSubmitted {
		return nil
	}

	maxAttempts := 3
	if node.Retry != nil && node.Retry.Attempts > 0 && node.Retry.Attempts < 3 {
		maxAttempts = node.Retry.Attempts
	}

	var lastError error
	var errorHistory []string

	for attempt := 1; attempt <= maxAttempts; attempt++ {
		re.updateNodeStateForAttempt(execution, node.ID, attempt, errorHistory)

		if attempt == 1 {
			re.executor.updateExecutionInDB(execution, fmt.Sprintf("Started executing node: %s", node.ID))
		}

		err := re.executeNodeAttempt(ctx, execution, node)
		lastError = err

		if err == nil {
			re.finalizeSuccessfulNode(execution, node.ID, attempt, errorHistory)
			return nil
		}

		if ctx.Err() != nil {
			re.finalizeCancelledNode(execution, node.ID, attempt, errorHistory, ctx.Err())
			return fmt.Errorf("retry cancelled after attempt: %w", ctx.Err())
		}

		if len(errorHistory) < 10 {
			errorHistory = append(errorHistory, err.Error())
		}

		if attempt >= maxAttempts {
			re.finalizeFailedNode(execution, node.ID, attempt, errorHistory, lastError)
			return fmt.Errorf("node %s execution failed after %d attempts: %w", node.ID, attempt, err)
		}

		delay := time.Second

		re.executor.updateExecutionInDB(execution, fmt.Sprintf("Node %s attempt %d failed: %v (retrying in %v)", node.ID, attempt, err, delay))

		select {
		case <-time.After(delay):
		case <-ctx.Done():
			re.finalizeCancelledNode(execution, node.ID, attempt, errorHistory, ctx.Err())
			return fmt.Errorf("retry cancelled during backoff: %w", ctx.Err())
		}
	}

	return lastError
}

func (re *RetryableExecutor) executeNodeAttempt(ctx context.Context, execution *engine.Execution, node *engine.Node) error {
	var cancel context.CancelFunc
	var nodeCtx context.Context
	if node.Timeout > 0 {
		nodeCtx, cancel = context.WithTimeout(ctx, time.Duration(node.Timeout)*time.Second)
	} else {
		nodeCtx, cancel = context.WithTimeout(ctx, 300*time.Second)
	}
	defer cancel()

	agent, err := re.executor.createAgentForNode(node)
	if err != nil {
		return fmt.Errorf("failed to create agent for node %s: %w", node.ID, err)
	}
	defer agent.Close()

	result, err := agent.Execute(nodeCtx, node.Prompt)
	if err != nil {
		return err
	}

	re.executor.mutex.Lock()
	nodeState := execution.NodeStates[node.ID]
	nodeState.Output = result
	execution.NodeStates[node.ID] = nodeState
	re.executor.mutex.Unlock()

	return nil
}

func (re *RetryableExecutor) updateNodeStateForAttempt(execution *engine.Execution, nodeID string, attempt int, errorHistory []string) {
	re.executor.mutex.Lock()
	defer re.executor.mutex.Unlock()

	nodeState := execution.NodeStates[nodeID]

	if nodeState.Status == constants.TaskStateCanceled || nodeState.Status == constants.TaskStateFailed || nodeState.Status == constants.TaskStateCompleted {
		return
	}

	nodeState.AttemptCount = attempt
	nodeState.ErrorHistory = make([]string, len(errorHistory))
	copy(nodeState.ErrorHistory, errorHistory)

	if attempt == 1 {
		nodeState.StartedAt = time.Now()
		nodeState.Status = constants.TaskStateWorking
	}

	execution.NodeStates[nodeID] = nodeState
}

func (re *RetryableExecutor) finalizeSuccessfulNode(execution *engine.Execution, nodeID string, attempt int, errorHistory []string) {
	re.executor.mutex.Lock()
	nodeState := execution.NodeStates[nodeID]

	if execution.Status == constants.TaskStateCanceled {
		nodeState.Status = constants.TaskStateCanceled
		nodeState.Error = "execution was cancelled"
	} else {
		nodeState.Status = constants.TaskStateCompleted
	}

	nodeState.AttemptCount = attempt
	nodeState.ErrorHistory = make([]string, len(errorHistory))
	copy(nodeState.ErrorHistory, errorHistory)
	endedAt := time.Now()
	nodeState.EndedAt = &endedAt
	execution.NodeStates[nodeID] = nodeState
	re.executor.mutex.Unlock()

	re.executor.updateExecutionInDB(execution, fmt.Sprintf("Node %s completed successfully", nodeID))
}

func (re *RetryableExecutor) finalizeFailedNode(execution *engine.Execution, nodeID string, attempt int, errorHistory []string, finalError error) {
	re.executor.mutex.Lock()
	nodeState := execution.NodeStates[nodeID]
	nodeState.AttemptCount = attempt
	nodeState.ErrorHistory = make([]string, len(errorHistory))
	copy(nodeState.ErrorHistory, errorHistory)

	if execution.Status == constants.TaskStateCanceled {
		nodeState.Status = constants.TaskStateCanceled
		nodeState.Error = "execution was cancelled"
	} else {
		nodeState.Status = constants.TaskStateFailed
		nodeState.Error = finalError.Error()
	}
	endedAt := time.Now()
	nodeState.EndedAt = &endedAt
	execution.NodeStates[nodeID] = nodeState
	re.executor.mutex.Unlock()

	re.executor.updateExecutionInDB(execution, fmt.Sprintf("Node %s %s: %v", nodeID, nodeState.Status, finalError))
}

func (re *RetryableExecutor) finalizeCancelledNode(execution *engine.Execution, nodeID string, attempt int, errorHistory []string, finalError error) {
	re.executor.mutex.Lock()
	nodeState := execution.NodeStates[nodeID]
	nodeState.AttemptCount = attempt
	nodeState.ErrorHistory = make([]string, len(errorHistory))
	copy(nodeState.ErrorHistory, errorHistory)
	nodeState.Status = constants.TaskStateCanceled
	nodeState.Error = finalError.Error()
	endedAt := time.Now()
	nodeState.EndedAt = &endedAt
	execution.NodeStates[nodeID] = nodeState
	re.executor.mutex.Unlock()

	re.executor.updateExecutionInDB(execution, fmt.Sprintf("Node %s cancelled: %v", nodeID, finalError))
}
