package executor

import (
	"context"
	"sync"

	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
)

// TaskQueue manages pending tasks awaiting worker assignment with FIFO distribution.
type TaskQueue struct {
	submittedTasks chan protocol.TaskRequest        // FIFO queue of available tasks
	workingTasks   map[string]protocol.TaskRequest  // Tasks currently assigned to workers
	completedTasks map[string]protocol.TaskResponse // Completed task results
	mu             sync.RWMutex                     // Protects working and completed maps
	ctx            context.Context                  // Context for cancellation
	cancel         context.CancelFunc               // Cancel function
}

// NewTaskQueue creates a new TaskQueue instance with specified buffer size.
func NewTaskQueue(bufferSize int) *TaskQueue {
	ctx, cancel := context.WithCancel(context.Background())
	return &TaskQueue{
		submittedTasks: make(chan protocol.TaskRequest, bufferSize),
		workingTasks:   make(map[string]protocol.TaskRequest),
		completedTasks: make(map[string]protocol.TaskResponse),
		ctx:            ctx,
		cancel:         cancel,
	}
}

// SubmitTask adds a task to the submitted queue for worker polling.
func (tq *TaskQueue) SubmitTask(req protocol.TaskRequest) error {
	select {
	case tq.submittedTasks <- req:
		return nil
	case <-tq.ctx.Done():
		return tq.ctx.Err()
	}
}

// PollTask returns the next available task and moves it to working state.
// Returns nil if no tasks are available or context is cancelled.
func (tq *TaskQueue) PollTask() *protocol.TaskRequest {
	select {
	case task := <-tq.submittedTasks:
		// Move task to working state
		tq.mu.Lock()
		tq.workingTasks[task.ID] = task
		tq.mu.Unlock()
		return &task
	case <-tq.ctx.Done():
		return nil
	default:
		// No tasks available
		return nil
	}
}

// CompleteTask marks a task as complete and removes it from working state.
func (tq *TaskQueue) CompleteTask(resp protocol.TaskResponse) error {
	tq.mu.Lock()
	defer tq.mu.Unlock()

	// Find the corresponding task by matching node ID and execution ID
	var taskID string
	for id, task := range tq.workingTasks {
		if task.NodeID == resp.NodeID && task.ExecutionID == resp.ExecutionID {
			taskID = id
			break
		}
	}

	if taskID == "" {
		return &TaskNotFoundError{
			NodeID:      resp.NodeID,
			ExecutionID: resp.ExecutionID,
		}
	}

	// Move task from working to completed
	delete(tq.workingTasks, taskID)
	tq.completedTasks[taskID] = resp

	return nil
}

// GetWorkingTask returns a working task by task ID.
func (tq *TaskQueue) GetWorkingTask(taskID string) (*protocol.TaskRequest, bool) {
	tq.mu.RLock()
	defer tq.mu.RUnlock()
	task, exists := tq.workingTasks[taskID]
	return &task, exists
}

// GetCompletedTask returns a completed task result by task ID.
func (tq *TaskQueue) GetCompletedTask(taskID string) (*protocol.TaskResponse, bool) {
	tq.mu.RLock()
	defer tq.mu.RUnlock()
	result, exists := tq.completedTasks[taskID]
	return &result, exists
}

// GetStats returns current queue statistics.
func (tq *TaskQueue) GetStats() TaskQueueStats {
	tq.mu.RLock()
	defer tq.mu.RUnlock()

	return TaskQueueStats{
		SubmittedCount: len(tq.submittedTasks),
		WorkingCount:   len(tq.workingTasks),
		CompletedCount: len(tq.completedTasks),
	}
}

// Close shuts down the task queue and cancels all pending operations.
func (tq *TaskQueue) Close() {
	tq.cancel()
	close(tq.submittedTasks)
}

// TaskQueueStats represents current queue state for monitoring.
type TaskQueueStats struct {
	SubmittedCount int `json:"submitted_count"`
	WorkingCount   int `json:"working_count"`
	CompletedCount int `json:"completed_count"`
}

// TaskNotFoundError is returned when a task cannot be found for completion.
type TaskNotFoundError struct {
	NodeID      string
	ExecutionID string
}

func (e *TaskNotFoundError) Error() string {
	return "task not found for nodeId: " + e.NodeID + ", executionId: " + e.ExecutionID
}
