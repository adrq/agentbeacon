package executor

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/config"
	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/engine"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/google/uuid"
	"gopkg.in/yaml.v3"
	"gorm.io/datatypes"
)

const (
	maxOutputBytes = 10 * 1024 * 1024 // 10MB output limit to prevent memory issues
)

type Executor struct {
	db               *storage.DB
	configLoader     *config.ConfigLoader
	mutex            sync.RWMutex
	activeExecutions map[string]context.CancelFunc
	dbMutex          sync.Mutex // Serialize database updates
	// Task queue for external worker integration
	taskQueue *TaskQueue
	// Event streaming
	eventChan       chan *storage.ExecutionEvent
	eventWriterDone chan struct{}
	eventWriterWG   sync.WaitGroup
}

func NewExecutor(db *storage.DB, configLoader *config.ConfigLoader) *Executor {
	e := &Executor{
		db:               db,
		configLoader:     configLoader,
		activeExecutions: make(map[string]context.CancelFunc),
		taskQueue:        NewTaskQueue(100), // Buffer size of 100 tasks
		eventChan:        make(chan *storage.ExecutionEvent, 1000),
		eventWriterDone:  make(chan struct{}),
	}

	// Start event writer goroutine
	e.eventWriterWG.Add(1)
	go e.eventWriter()

	return e
}

func (e *Executor) Close() {
	// Close task queue
	if e.taskQueue != nil {
		e.taskQueue.Close()
	}
	// Signal event writer to stop
	close(e.eventWriterDone)
	// Wait for event writer to finish
	e.eventWriterWG.Wait()
	// Close event channel
	close(e.eventChan)
}

// GetTaskQueue returns the executor's task queue for external worker integration.
func (e *Executor) GetTaskQueue() *TaskQueue {
	return e.taskQueue
}

type ExecutionInfo struct {
	ID          string
	WorkflowID  string
	Status      string
	StartedAt   time.Time
	CompletedAt *time.Time
}

// StartWorkflowRef starts execution of a versioned workflow referenced via the registry
// using canonical form namespace/name[:version|latest]. It resolves the reference to a
// concrete stored version, parses its YAML snapshot directly (no legacy file lookup),
// and launches execution identical to StartWorkflow. The execution row records
// workflow_namespace & workflow_version for downstream introspection.
func (e *Executor) StartWorkflowRef(rawRef string) (*ExecutionInfo, error) {
	ref, wf, err := e.db.ResolveWorkflowRef(rawRef)
	if err != nil {
		return nil, fmt.Errorf("failed to resolve workflow ref: %w", err)
	}

	var workflow engine.Workflow
	if err := yaml.Unmarshal([]byte(wf.YAMLSnapshot), &workflow); err != nil {
		return nil, fmt.Errorf("failed to parse stored workflow YAML: %w", err)
	}

	nodeIDMap := make(map[string]bool)
	for _, node := range workflow.Nodes {
		if nodeIDMap[node.ID] {
			return nil, fmt.Errorf("duplicate node ID found: %s", node.ID)
		}
		nodeIDMap[node.ID] = true
	}

	executionID := uuid.New().String()
	nodeStates := make(map[string]engine.NodeState)
	for _, node := range workflow.Nodes {
		nodeStates[node.ID] = engine.NodeState{Status: constants.TaskStateSubmitted}
	}
	nodeStatesJSON, err := json.Marshal(nodeStates)
	if err != nil {
		return nil, fmt.Errorf("failed to serialize node states: %w", err)
	}

	execution := &engine.Execution{
		ID:         executionID,
		WorkflowID: ref.Canonical,
		Status:     constants.TaskStateWorking,
		NodeStates: nodeStates,
		StartedAt:  time.Now(),
	}

	// Store namespace & version linkage.
	ns := wf.Namespace
	ver := wf.Version

	dbExecution := &storage.Execution{
		ID:                executionID,
		WorkflowName:      ref.Canonical,
		Status:            constants.TaskStateWorking,
		NodeStates:        datatypes.JSON(nodeStatesJSON),
		A2ATasks:          datatypes.JSON("{}"),
		Logs:              fmt.Sprintf("Started workflow %s at %s\n", ref.Canonical, time.Now().Format(time.RFC3339)),
		StartedAt:         execution.StartedAt,
		WorkflowNamespace: &ns,
		WorkflowVersion:   &ver,
	}
	if err := e.db.CreateExecution(dbExecution); err != nil {
		return nil, fmt.Errorf("failed to create execution record: %w", err)
	}

	// Perform workflow validation and submit first level tasks synchronously
	levels, err := engine.TopologicalSort(workflow.Nodes)
	if err != nil {
		e.mutex.Lock()
		execution.Status = constants.TaskStateFailed
		completedAt := time.Now()
		execution.CompletedAt = &completedAt
		e.mutex.Unlock()
		e.updateExecutionInDB(execution, fmt.Sprintf("Workflow validation failed: %v", err))
		return nil, fmt.Errorf("workflow validation failed: %w", err)
	}

	nodeMap := make(map[string]*engine.Node)
	for i := range workflow.Nodes {
		nodeMap[workflow.Nodes[i].ID] = &workflow.Nodes[i]
	}

	ctx, cancel := context.WithCancel(context.Background())
	e.mutex.Lock()
	e.activeExecutions[executionID] = cancel
	e.mutex.Unlock()

	// Start async execution for all levels (including first level)
	go func() {
		defer func() {
			if r := recover(); r != nil {
				log.Printf("Workflow execution %s panicked: %v", executionID, r)
				e.mutex.Lock()
				execution.Status = constants.TaskStateFailed
				if execution.CompletedAt == nil {
					completedAt := time.Now()
					execution.CompletedAt = &completedAt
				}
				e.mutex.Unlock()
				e.updateExecutionInDB(execution, fmt.Sprintf("Workflow execution panicked: %v", r))
			}
			e.mutex.Lock()
			delete(e.activeExecutions, executionID)
			e.mutex.Unlock()
		}()

		if err := e.executeWorkflowFromLevel(ctx, execution, &workflow, levels, nodeMap, 0); err != nil {
			log.Printf("Workflow execution %s failed: %v", executionID, err)
		}
	}()

	e.mutex.RLock()
	info := &ExecutionInfo{ID: execution.ID, WorkflowID: execution.WorkflowID, Status: execution.Status, StartedAt: execution.StartedAt}
	e.mutex.RUnlock()
	return info, nil
}

// submitLevel submits tasks for a level without waiting for completion (used for synchronous first level submission)
func (e *Executor) submitLevel(ctx context.Context, execution *engine.Execution, nodeMap map[string]*engine.Node, level []string) error {
	if len(level) == 0 {
		return nil
	}

	// Submit all valid tasks to TaskQueue for external workers
	for _, nodeID := range level {
		node := nodeMap[nodeID]
		if node == nil {
			continue
		}

		if !e.shouldExecuteNode(execution, node) {
			continue
		}

		// Create TaskRequest for external worker
		taskRequest := protocol.TaskRequest{
			NodeID:      nodeID,
			ExecutionID: execution.ID,
			Agent:       node.Agent,
			ID:          uuid.New().String(),
			Request:     node.Request,
		}

		// Submit task to queue
		if err := e.taskQueue.SubmitTask(taskRequest); err != nil {
			return fmt.Errorf("failed to submit task for node %s: %w", nodeID, err)
		}

		// Track external task in database
		if err := e.trackExternalTask(execution.ID, nodeID, taskRequest.ID); err != nil {
			log.Printf("Failed to track external task %s for node %s: %v", taskRequest.ID, nodeID, err)
		}

		// Update node state to working when submitted
		e.mutex.Lock()
		nodeState := execution.NodeStates[nodeID]
		nodeState.Status = constants.TaskStateWorking
		nodeState.StartedAt = time.Now()
		execution.NodeStates[nodeID] = nodeState
		e.mutex.Unlock()

		e.updateExecutionInDB(execution, fmt.Sprintf("Submitted external task %s for node: %s", taskRequest.ID, nodeID))
	}

	return nil
}

// executeWorkflowFromLevel runs workflow execution starting from a specific level
func (e *Executor) executeWorkflowFromLevel(ctx context.Context, execution *engine.Execution, workflow *engine.Workflow, levels [][]string, nodeMap map[string]*engine.Node, startLevel int) error {
	defer func() {
		if r := recover(); r != nil {
			log.Printf("Workflow execution panic recovered for execution %s: %v", execution.ID, r)

			// Mark execution as failed and send terminal update
			e.mutex.Lock()
			execution.Status = constants.TaskStateFailed
			if execution.CompletedAt == nil {
				completedAt := time.Now()
				execution.CompletedAt = &completedAt
			}

			// Mark all non-terminal nodes as failed
			now := time.Now()
			for nodeID, nodeState := range execution.NodeStates {
				if nodeState.Status == constants.TaskStateSubmitted || nodeState.Status == constants.TaskStateWorking {
					nodeState.Status = constants.TaskStateFailed
					nodeState.Error = fmt.Sprintf("Workflow execution panicked: %v", r)
					if nodeState.EndedAt == nil {
						nodeState.EndedAt = &now
					}
					execution.NodeStates[nodeID] = nodeState
				}
			}
			e.mutex.Unlock()

			// Force terminal update regardless of channel state
			e.updateExecutionInDB(execution, fmt.Sprintf("Workflow execution panicked: %v", r))
		}
	}()

	for levelIndex := startLevel; levelIndex < len(levels); levelIndex++ {
		level := levels[levelIndex]

		// Check for cancellation before processing level
		select {
		case <-ctx.Done():
			e.mutex.Lock()
			execution.Status = constants.TaskStateCanceled
			if execution.CompletedAt == nil {
				completedAt := time.Now()
				execution.CompletedAt = &completedAt
			}
			// Update only pending nodes to cancelled - let running nodes converge naturally
			for nodeID, nodeState := range execution.NodeStates {
				if nodeState.Status == constants.TaskStateSubmitted {
					nodeState.Status = constants.TaskStateCanceled
					if nodeState.EndedAt == nil {
						endedAt := time.Now()
						nodeState.EndedAt = &endedAt
					}
					execution.NodeStates[nodeID] = nodeState
				}
			}
			e.mutex.Unlock()
			e.updateExecutionInDB(execution, "Execution cancelled by user")
			return fmt.Errorf("execution cancelled: %w", ctx.Err())
		default:
		}

		if err := e.executeLevel(ctx, execution, nodeMap, level); err != nil {
			e.mutex.Lock()
			if execution.CompletedAt == nil {
				completedAt := time.Now()
				execution.CompletedAt = &completedAt
			}
			// Set proper status based on the type of error
			if errors.Is(err, context.Canceled) {
				execution.Status = constants.TaskStateCanceled
				// Mark all remaining pending and running nodes as cancelled when execution is cancelled
				for nodeID, nodeState := range execution.NodeStates {
					if nodeState.Status == constants.TaskStateSubmitted || nodeState.Status == constants.TaskStateWorking {
						nodeState.Status = constants.TaskStateCanceled
						nodeState.Error = "execution was cancelled"
						if nodeState.EndedAt == nil {
							endedAt := time.Now()
							nodeState.EndedAt = &endedAt
						}
						execution.NodeStates[nodeID] = nodeState
					}
				}
			} else {
				execution.Status = constants.TaskStateFailed
				// Mark all remaining pending nodes as cancelled for StopAll behavior
				now := time.Now()
				for nodeID, nodeState := range execution.NodeStates {
					if nodeState.Status == constants.TaskStateSubmitted {
						nodeState.Status = constants.TaskStateCanceled
						nodeState.Error = "execution failed - node cancelled"
						if nodeState.EndedAt == nil {
							nodeState.EndedAt = &now
						}
						execution.NodeStates[nodeID] = nodeState
					}
				}
			}
			e.mutex.Unlock()
			e.updateExecutionInDB(execution, fmt.Sprintf("Execution failed at level %d: %v", levelIndex, err))
			return fmt.Errorf("execution failed at level %d: %w", levelIndex, err)
		}

		e.mutex.RLock()
		status := execution.Status
		e.mutex.RUnlock()

		if status != constants.TaskStateWorking {
			e.mutex.Lock()
			if execution.CompletedAt == nil {
				completedAt := time.Now()
				execution.CompletedAt = &completedAt
			}
			e.mutex.Unlock()
			break
		}

		// Additional check for context cancellation after level completion
		select {
		case <-ctx.Done():
			e.mutex.Lock()
			execution.Status = constants.TaskStateCanceled
			if execution.CompletedAt == nil {
				completedAt := time.Now()
				execution.CompletedAt = &completedAt
			}
			e.mutex.Unlock()
			e.updateExecutionInDB(execution, "Execution cancelled after level completion")
			return fmt.Errorf("execution cancelled: %w", ctx.Err())
		default:
		}
	}

	e.mutex.Lock()

	// Final integrity check: ensure no nodes remain in pending or running state
	now := time.Now()
	hasOrphanNodes := false
	for nodeID, nodeState := range execution.NodeStates {
		if nodeState.Status == constants.TaskStateSubmitted || nodeState.Status == constants.TaskStateWorking {
			hasOrphanNodes = true
			nodeState.Status = constants.TaskStateCanceled
			nodeState.Error = "execution completed - orphaned node cancelled"
			if nodeState.EndedAt == nil {
				nodeState.EndedAt = &now
			}
			execution.NodeStates[nodeID] = nodeState
		}
	}
	if hasOrphanNodes {
		log.Printf("Warning: Found orphaned nodes in execution %s, marked as cancelled", execution.ID)
	}

	e.mutex.Unlock()

	// Determine final status with hardcoded stop-all behavior (after orphan nodes are cancelled)
	finalStatus := e.determineExecutionStatus(execution)

	e.mutex.Lock()
	execution.Status = finalStatus

	if execution.CompletedAt == nil {
		completedAt := time.Now()
		execution.CompletedAt = &completedAt
	}
	e.mutex.Unlock()

	e.updateExecutionInDB(execution, fmt.Sprintf("Workflow execution %s", finalStatus))
	return nil
}

// executeWorkflow runs complete workflow execution using dependency levels and external task queue.
func (e *Executor) executeWorkflow(ctx context.Context, execution *engine.Execution, workflow *engine.Workflow) error {
	defer func() {
		if r := recover(); r != nil {
			log.Printf("Workflow execution panic recovered for execution %s: %v", execution.ID, r)

			// Mark execution as failed and send terminal update
			e.mutex.Lock()
			execution.Status = constants.TaskStateFailed
			if execution.CompletedAt == nil {
				completedAt := time.Now()
				execution.CompletedAt = &completedAt
			}

			// Mark all non-terminal nodes as failed
			now := time.Now()
			for nodeID, nodeState := range execution.NodeStates {
				if nodeState.Status == constants.TaskStateSubmitted || nodeState.Status == constants.TaskStateWorking {
					nodeState.Status = constants.TaskStateFailed
					nodeState.Error = fmt.Sprintf("Workflow execution panicked: %v", r)
					if nodeState.EndedAt == nil {
						nodeState.EndedAt = &now
					}
					execution.NodeStates[nodeID] = nodeState
				}
			}
			e.mutex.Unlock()

			// Force terminal update regardless of channel state
			e.updateExecutionInDB(execution, fmt.Sprintf("Workflow execution panicked: %v", r))
		}
	}()

	levels, err := engine.TopologicalSort(workflow.Nodes)
	if err != nil {
		e.mutex.Lock()
		execution.Status = constants.TaskStateFailed
		completedAt := time.Now()
		execution.CompletedAt = &completedAt
		e.mutex.Unlock()
		e.updateExecutionInDB(execution, fmt.Sprintf("Workflow validation failed: %v", err))
		return fmt.Errorf("workflow validation failed: %w", err)
	}

	nodeMap := make(map[string]*engine.Node)
	for i := range workflow.Nodes {
		nodeMap[workflow.Nodes[i].ID] = &workflow.Nodes[i]
	}

	for levelIndex, level := range levels {
		// Check for cancellation before processing level
		select {
		case <-ctx.Done():
			e.mutex.Lock()
			execution.Status = constants.TaskStateCanceled
			if execution.CompletedAt == nil {
				completedAt := time.Now()
				execution.CompletedAt = &completedAt
			}
			// Update only pending nodes to cancelled - let running nodes converge naturally
			for nodeID, nodeState := range execution.NodeStates {
				if nodeState.Status == constants.TaskStateSubmitted {
					nodeState.Status = constants.TaskStateCanceled
					if nodeState.EndedAt == nil {
						endedAt := time.Now()
						nodeState.EndedAt = &endedAt
					}
					execution.NodeStates[nodeID] = nodeState
				}
			}
			e.mutex.Unlock()
			e.updateExecutionInDB(execution, "Execution cancelled by user")
			return fmt.Errorf("execution cancelled: %w", ctx.Err())
		default:
		}

		if err := e.executeLevel(ctx, execution, nodeMap, level); err != nil {
			e.mutex.Lock()
			if execution.CompletedAt == nil {
				completedAt := time.Now()
				execution.CompletedAt = &completedAt
			}
			// Set proper status based on the type of error
			if errors.Is(err, context.Canceled) {
				execution.Status = constants.TaskStateCanceled
				// Mark all remaining pending and running nodes as cancelled when execution is cancelled
				for nodeID, nodeState := range execution.NodeStates {
					if nodeState.Status == constants.TaskStateSubmitted || nodeState.Status == constants.TaskStateWorking {
						nodeState.Status = constants.TaskStateCanceled
						nodeState.Error = "execution was cancelled"
						if nodeState.EndedAt == nil {
							endedAt := time.Now()
							nodeState.EndedAt = &endedAt
						}
						execution.NodeStates[nodeID] = nodeState
					}
				}
			} else {
				execution.Status = constants.TaskStateFailed
				// Mark all remaining pending nodes as cancelled for StopAll behavior
				now := time.Now()
				for nodeID, nodeState := range execution.NodeStates {
					if nodeState.Status == constants.TaskStateSubmitted {
						nodeState.Status = constants.TaskStateCanceled
						nodeState.Error = "execution failed - node cancelled"
						if nodeState.EndedAt == nil {
							nodeState.EndedAt = &now
						}
						execution.NodeStates[nodeID] = nodeState
					}
				}
			}
			e.mutex.Unlock()
			e.updateExecutionInDB(execution, fmt.Sprintf("Execution failed at level %d: %v", levelIndex, err))
			return fmt.Errorf("execution failed at level %d: %w", levelIndex, err)
		}

		e.mutex.RLock()
		status := execution.Status
		e.mutex.RUnlock()

		if status != constants.TaskStateWorking {
			e.mutex.Lock()
			if execution.CompletedAt == nil {
				completedAt := time.Now()
				execution.CompletedAt = &completedAt
			}
			e.mutex.Unlock()
			break
		}

		// Additional check for context cancellation after level completion
		select {
		case <-ctx.Done():
			e.mutex.Lock()
			execution.Status = constants.TaskStateCanceled
			if execution.CompletedAt == nil {
				completedAt := time.Now()
				execution.CompletedAt = &completedAt
			}
			e.mutex.Unlock()
			e.updateExecutionInDB(execution, "Execution cancelled after level completion")
			return fmt.Errorf("execution cancelled: %w", ctx.Err())
		default:
		}
	}

	e.mutex.Lock()

	// Final integrity check: ensure no nodes remain in pending or running state
	now := time.Now()
	hasOrphanNodes := false
	for nodeID, nodeState := range execution.NodeStates {
		if nodeState.Status == constants.TaskStateSubmitted || nodeState.Status == constants.TaskStateWorking {
			hasOrphanNodes = true
			nodeState.Status = constants.TaskStateCanceled
			nodeState.Error = "execution completed - orphaned node cancelled"
			if nodeState.EndedAt == nil {
				nodeState.EndedAt = &now
			}
			execution.NodeStates[nodeID] = nodeState
		}
	}
	if hasOrphanNodes {
		log.Printf("Warning: Found orphaned nodes in execution %s, marked as cancelled", execution.ID)
	}

	e.mutex.Unlock()

	// Determine final status with hardcoded stop-all behavior (after orphan nodes are cancelled)
	finalStatus := e.determineExecutionStatus(execution)

	e.mutex.Lock()
	execution.Status = finalStatus

	if execution.CompletedAt == nil {
		completedAt := time.Now()
		execution.CompletedAt = &completedAt
	}
	e.mutex.Unlock()

	e.updateExecutionInDB(execution, fmt.Sprintf("Workflow execution %s", finalStatus))
	return nil
}

// executeLevel executes a level using external task queue with hardcoded stop-all behavior on node failures
func (e *Executor) executeLevel(ctx context.Context, execution *engine.Execution, nodeMap map[string]*engine.Node, level []string) error {
	if len(level) == 0 {
		return nil
	}

	// Submit all valid tasks to TaskQueue for external workers
	var submittedTaskIDs []string
	for _, nodeID := range level {
		node := nodeMap[nodeID]
		if node == nil {
			continue
		}

		if !e.shouldExecuteNode(execution, node) {
			continue
		}

		// Create TaskRequest for external worker
		taskRequest := protocol.TaskRequest{
			NodeID:      nodeID,
			ExecutionID: execution.ID,
			Agent:       node.Agent,
			ID:          uuid.New().String(),
			Request:     node.Request,
		}

		// Submit task to queue
		if err := e.taskQueue.SubmitTask(taskRequest); err != nil {
			return fmt.Errorf("failed to submit task for node %s: %w", nodeID, err)
		}

		submittedTaskIDs = append(submittedTaskIDs, taskRequest.ID)

		// Track external task in database
		if err := e.trackExternalTask(execution.ID, nodeID, taskRequest.ID); err != nil {
			log.Printf("Failed to track external task %s for node %s: %v", taskRequest.ID, nodeID, err)
		}

		// Update node state to working when submitted
		e.mutex.Lock()
		nodeState := execution.NodeStates[nodeID]
		nodeState.Status = constants.TaskStateWorking
		nodeState.StartedAt = time.Now()
		execution.NodeStates[nodeID] = nodeState
		e.mutex.Unlock()

		e.updateExecutionInDB(execution, fmt.Sprintf("Submitted external task %s for node: %s", taskRequest.ID, nodeID))
	}

	// Wait for all submitted tasks to complete
	completedCount := 0
	for completedCount < len(submittedTaskIDs) {
		select {
		case <-ctx.Done():
			// Mark any remaining nodes as cancelled when context is cancelled
			e.markRemainingNodesAsCancelled(execution, submittedTaskIDs, completedCount)
			return fmt.Errorf("task completion waiting cancelled: %w", ctx.Err())
		default:
		}

		// Check for completed tasks (poll every 100ms)
		time.Sleep(100 * time.Millisecond)

		for i := completedCount; i < len(submittedTaskIDs); i++ {
			taskID := submittedTaskIDs[i]
			if taskResponse, found := e.taskQueue.GetCompletedTask(taskID); found {
				// Process completed task
				if err := e.handleTaskCompletion(execution, nodeMap, *taskResponse); err != nil {
					// Task failed - implement stop-all behavior
					e.markRemainingNodesAsCancelled(execution, submittedTaskIDs, i+1)
					return err
				}
				completedCount++
			}
		}
	}

	return nil
}

// shouldExecuteNode determines if a node should be executed
func (e *Executor) shouldExecuteNode(execution *engine.Execution, node *engine.Node) bool {
	e.mutex.RLock()
	defer e.mutex.RUnlock()

	if execution.Status != constants.TaskStateWorking {
		return false
	}

	nodeState, exists := execution.NodeStates[node.ID]
	if !exists {
		return false
	}

	return nodeState.Status == constants.TaskStateSubmitted
}

// handleTaskCompletion processes a completed task response from external worker
func (e *Executor) handleTaskCompletion(execution *engine.Execution, nodeMap map[string]*engine.Node, taskResponse protocol.TaskResponse) error {
	node := nodeMap[taskResponse.NodeID]
	if node == nil {
		return fmt.Errorf("received task completion for unknown node: %s", taskResponse.NodeID)
	}

	endedAt := time.Now()
	e.mutex.Lock()
	nodeState := execution.NodeStates[taskResponse.NodeID]
	nodeState.EndedAt = &endedAt

	// Process task result based on A2A state
	switch taskResponse.TaskStatus.State {
	case "completed":
		nodeState.Status = constants.TaskStateCompleted
		// Extract output from artifacts if available
		if len(taskResponse.Artifacts) > 0 && len(taskResponse.Artifacts[0].Parts) > 0 {
			// Simple extraction for MVP - use first artifact's first part as output
			if taskResponse.Artifacts[0].Parts[0].Text != "" {
				nodeState.Output = taskResponse.Artifacts[0].Parts[0].Text
			}
		}
	case "failed":
		nodeState.Status = constants.TaskStateFailed
		if taskResponse.TaskStatus.Message != nil && len(taskResponse.TaskStatus.Message.Parts) > 0 {
			if taskResponse.TaskStatus.Message.Parts[0].Text != "" {
				nodeState.Error = taskResponse.TaskStatus.Message.Parts[0].Text
			}
		}
		if nodeState.Error == "" {
			nodeState.Error = "Task failed"
		}
	case "canceled":
		nodeState.Status = constants.TaskStateCanceled
		nodeState.Error = "Task was cancelled"
	case "rejected":
		nodeState.Status = constants.TaskStateRejected
		nodeState.Error = "Task was rejected"
	default:
		nodeState.Status = constants.TaskStateFailed
		nodeState.Error = fmt.Sprintf("Unknown task state: %s", taskResponse.TaskStatus.State)
	}

	execution.NodeStates[taskResponse.NodeID] = nodeState
	e.mutex.Unlock()

	// Track external worker result in database
	if err := e.trackExternalResult(execution.ID, taskResponse); err != nil {
		log.Printf("Failed to track external result for node %s: %v", taskResponse.NodeID, err)
	}

	// Check if node failed and implement stop-all behavior
	if nodeState.Status == constants.TaskStateFailed {
		e.handleNodeFailure(execution, node, fmt.Errorf("external worker task failed: %s", nodeState.Error))
		return fmt.Errorf("node %s failed: %s", taskResponse.NodeID, nodeState.Error)
	}

	e.updateExecutionInDB(execution, fmt.Sprintf("Node %s completed with state: %s (external worker)", taskResponse.NodeID, taskResponse.TaskStatus.State))
	return nil
}

// markRemainingNodesAsCancelled marks any remaining nodes in the current level as cancelled
func (e *Executor) markRemainingNodesAsCancelled(execution *engine.Execution, submittedTaskIDs []string, completedCount int) {
	// This method would ideally track which nodes correspond to which task IDs
	// For MVP simplicity, we'll let the main execution logic handle node cancellation
}

// handleNodeFailure handles node failure with hardcoded stop-all behavior
func (e *Executor) handleNodeFailure(execution *engine.Execution, failedNode *engine.Node, nodeErr error) error {
	e.mutex.Lock()
	execution.Status = constants.TaskStateFailed
	if execution.CompletedAt == nil {
		completedAt := time.Now()
		execution.CompletedAt = &completedAt
	}

	now := time.Now()
	for nodeID, nodeState := range execution.NodeStates {
		// Only cancel pending nodes, let running nodes complete naturally
		if nodeState.Status == constants.TaskStateSubmitted {
			nodeState.Status = constants.TaskStateCanceled
			nodeState.EndedAt = &now
			execution.NodeStates[nodeID] = nodeState
		}
	}
	e.mutex.Unlock()

	e.updateExecutionInDB(execution, "Execution failed - cancelling all pending nodes")

	return nil
}

// StopExecution cancels a running execution
func (e *Executor) StopExecution(executionID string) error {
	e.mutex.Lock()
	cancel, exists := e.activeExecutions[executionID]
	e.mutex.Unlock()

	if exists {
		// Just cancel the context, let executeWorkflow goroutine handle all DB updates
		cancel()
		return nil
	}

	// Check database for execution status to make this idempotent
	execution, err := e.db.GetExecution(executionID)
	if err != nil {
		return fmt.Errorf("execution %s not found", executionID)
	}

	// If already in terminal state, return success (idempotent)
	if execution.Status == constants.TaskStateCompleted || execution.Status == constants.TaskStateFailed || execution.Status == constants.TaskStateCanceled {
		return nil
	}

	// If still running but not in activeExecutions, wait briefly and re-check DB
	// This handles the race where execution finished but DB update hasn't flushed yet
	time.Sleep(100 * time.Millisecond)
	execution, err = e.db.GetExecution(executionID)
	if err != nil {
		return fmt.Errorf("execution %s not found", executionID)
	}

	// Check again if now in terminal state
	if execution.Status == constants.TaskStateCompleted || execution.Status == constants.TaskStateFailed || execution.Status == constants.TaskStateCanceled {
		return nil
	}

	return fmt.Errorf("execution %s is in inconsistent state", executionID)
}

// ExecutionStatus represents lightweight execution status information
type ExecutionStatus struct {
	ID          string     `json:"id"`
	WorkflowID  string     `json:"workflow_id"`
	Status      string     `json:"status"`
	Progress    float64    `json:"progress"` // completed nodes / total nodes (excludes failed/cancelled)
	StartedAt   time.Time  `json:"started_at"`
	CompletedAt *time.Time `json:"completed_at,omitempty"`
}

// GetExecutionStatus returns lightweight status information for an execution
func (e *Executor) GetExecutionStatus(executionID string) (*ExecutionStatus, error) {
	execution, err := e.db.GetExecution(executionID)
	if err != nil {
		return nil, fmt.Errorf("failed to get execution: %w", err)
	}

	// Parse node states to calculate progress
	var nodeStates map[string]engine.NodeState
	totalNodes := 0
	completedNodes := 0

	if execution.NodeStates != nil {
		if err := json.Unmarshal(execution.NodeStates, &nodeStates); err != nil {
			return nil, fmt.Errorf("failed to unmarshal node states: %w", err)
		}
		totalNodes = len(nodeStates)
		for _, state := range nodeStates {
			// Count all terminal states for progress (completed, failed, cancelled, skipped)
			if state.Status == constants.TaskStateCompleted || state.Status == constants.TaskStateFailed || state.Status == constants.TaskStateCanceled || state.Status == constants.TaskStateRejected {
				completedNodes++
			}
		}
	}

	var progress float64
	if totalNodes > 0 {
		progress = float64(completedNodes) / float64(totalNodes)
	}

	return &ExecutionStatus{
		ID:          execution.ID,
		WorkflowID:  execution.WorkflowName,
		Status:      execution.Status,
		Progress:    progress,
		StartedAt:   execution.StartedAt,
		CompletedAt: execution.CompletedAt,
	}, nil
}

// GetExecution returns full execution details from the database
func (e *Executor) GetExecution(executionID string) (*storage.Execution, error) {
	execution, err := e.db.GetExecution(executionID)
	if err != nil {
		return nil, fmt.Errorf("failed to get execution: %w", err)
	}
	return execution, nil
}

// ListExecutions returns all executions, optionally filtered by status
func (e *Executor) ListExecutions(statusFilter string) ([]storage.Execution, error) {
	// Get all executions across all workflows
	allExecutions, err := e.db.ListAllExecutions()
	if err != nil {
		return nil, fmt.Errorf("failed to list executions: %w", err)
	}

	if statusFilter == "" {
		// Return all executions
		return allExecutions, nil
	}

	// Filter by status
	var filteredExecutions []storage.Execution
	for _, exec := range allExecutions {
		if exec.Status == statusFilter {
			filteredExecutions = append(filteredExecutions, exec)
		}
	}

	return filteredExecutions, nil
}

// GetWorkflowExecutions returns executions for a specific workflow
func (e *Executor) GetWorkflowExecutions(workflowName string) ([]storage.Execution, error) {
	executions, err := e.db.ListExecutions(workflowName)
	if err != nil {
		return nil, fmt.Errorf("failed to get workflow executions: %w", err)
	}
	return executions, nil
}

// executeNode runs a single workflow node with dedicated agent process.
func (e *Executor) executeNode(ctx context.Context, execution *engine.Execution, node *engine.Node) error {
	var cancel context.CancelFunc
	var nodeCtx context.Context
	if node.Timeout > 0 {
		nodeCtx, cancel = context.WithTimeout(ctx, time.Duration(node.Timeout)*time.Second)
	} else {
		nodeCtx, cancel = context.WithTimeout(ctx, 300*time.Second)
	}
	defer cancel()

	agent, err := e.createAgentForNode(node)
	if err != nil {
		return fmt.Errorf("failed to create agent for node %s: %w", node.ID, err)
	}
	defer agent.Close()

	// Set execution context for agents that support it
	if contextSetter, ok := agent.(ContextSetter); ok {
		contextSetter.SetContext(execution.ID, node.ID)
	}

	// Capture protocol ID for agents that support protocol tracking
	if tracker, ok := agent.(ProtocolTracker); ok {
		go func() {
			// Wait briefly for agent to establish connection and get protocol ID
			time.Sleep(100 * time.Millisecond)

			protocolType, protocolID := tracker.GetProtocolID()
			if protocolType != "" && protocolID != "" {
				// Store protocol mapping in execution record
				e.storeProtocolMapping(execution.ID, node.ID, protocolType, protocolID)
			}
		}()
	}

	e.mutex.Lock()
	nodeState := execution.NodeStates[node.ID]
	nodeState.Status = constants.TaskStateWorking
	nodeState.StartedAt = time.Now()
	execution.NodeStates[node.ID] = nodeState
	e.mutex.Unlock()

	e.updateExecutionInDB(execution, fmt.Sprintf("Started executing node: %s", node.ID))

	// Extract prompt from request based on agent type
	var prompt string
	if promptValue, exists := node.Request["prompt"]; exists {
		prompt = promptValue.(string)
	} else if taskValue, exists := node.Request["task"]; exists {
		prompt = taskValue.(string)
	} else {
		return fmt.Errorf("node %s missing prompt or task in request", node.ID)
	}

	result, err := agent.Execute(nodeCtx, prompt)

	endedAt := time.Now()
	e.mutex.Lock()
	nodeState = execution.NodeStates[node.ID]
	nodeState.EndedAt = &endedAt

	if err != nil {
		// Check if execution is cancelled or failed before setting failed state
		if execution.Status == constants.TaskStateCanceled {
			nodeState.Status = constants.TaskStateCanceled
			nodeState.Error = "execution was cancelled"
		} else if execution.Status == constants.TaskStateFailed {
			nodeState.Status = constants.TaskStateCanceled
			nodeState.Error = "execution failed - node cancelled"
		} else {
			nodeState.Status = constants.TaskStateFailed
			nodeState.Error = err.Error()
		}
		execution.NodeStates[node.ID] = nodeState
		e.mutex.Unlock()

		e.updateExecutionInDB(execution, fmt.Sprintf("Node %s failed: %v", node.ID, err))
		return fmt.Errorf("node %s execution failed: %w", node.ID, err)
	}

	// Check if execution is cancelled or failed before setting completed state
	if execution.Status == constants.TaskStateCanceled {
		nodeState.Status = constants.TaskStateCanceled
		nodeState.Error = "execution was cancelled"
	} else if execution.Status == constants.TaskStateFailed {
		nodeState.Status = constants.TaskStateCanceled
		nodeState.Error = "execution failed - node cancelled"
	} else {
		nodeState.Status = constants.TaskStateCompleted
		nodeState.Output = result
	}
	execution.NodeStates[node.ID] = nodeState
	e.mutex.Unlock()

	e.updateExecutionInDB(execution, fmt.Sprintf("Node %s completed successfully", node.ID))
	return nil
}

// updateExecutionInDB writes execution state directly to database synchronously
func (e *Executor) updateExecutionInDB(execution *engine.Execution, logMessage string) {
	// Make a deep copy to avoid race conditions
	e.mutex.RLock()
	executionCopy := &engine.Execution{
		ID:          execution.ID,
		WorkflowID:  execution.WorkflowID,
		Status:      execution.Status,
		NodeStates:  make(map[string]engine.NodeState),
		StartedAt:   execution.StartedAt,
		CompletedAt: execution.CompletedAt,
	}
	for k, v := range execution.NodeStates {
		// Copy node state with output size limit to prevent memory amplification
		nodeStateCopy := v
		if len(v.Output) > maxOutputBytes {
			nodeStateCopy.Output = v.Output[:maxOutputBytes] + fmt.Sprintf("\n... [TRUNCATED: output was %d bytes, showing first %d bytes]", len(v.Output), maxOutputBytes)
			log.Printf("Warning: Truncated large output for node %s (execution %s): %d bytes -> %d bytes", k, execution.ID, len(v.Output), len(nodeStateCopy.Output))
		}
		executionCopy.NodeStates[k] = nodeStateCopy
	}
	e.mutex.RUnlock()

	// Serialize database updates to prevent concurrent update race conditions
	e.dbMutex.Lock()
	defer e.dbMutex.Unlock()

	// Perform synchronous database update
	nodeStatesJSON, err := json.Marshal(executionCopy.NodeStates)
	if err != nil {
		return
	}

	currentExecution, err := e.db.GetExecution(executionCopy.ID)
	if err != nil {
		return
	}

	dbExecution := &storage.Execution{
		ID:           executionCopy.ID,
		WorkflowName: executionCopy.WorkflowID,
		Status:       executionCopy.Status,
		NodeStates:   datatypes.JSON(nodeStatesJSON),
		A2ATasks:     currentExecution.A2ATasks, // Preserve A2A task mapping
		Logs:         currentExecution.Logs + logMessage + "\n",
		StartedAt:    executionCopy.StartedAt,
		CompletedAt:  executionCopy.CompletedAt,
	}

	_ = e.db.UpdateExecution(dbExecution) // DB errors don't fail workflow execution
}

// eventWriter processes events from the event channel and writes them to database
func (e *Executor) eventWriter() {
	defer e.eventWriterWG.Done()

	for {
		select {
		case event, ok := <-e.eventChan:
			if !ok {
				// Channel closed, exit
				return
			}

			// Set timestamp if not already set
			if event.Timestamp.IsZero() {
				event.Timestamp = time.Now()
			}

			// Handle state synchronization for critical events
			e.handleEventStateSync(event)

			// Write to database (ignore errors to avoid blocking)
			_ = e.db.CreateExecutionEvent(event)

		case <-e.eventWriterDone:
			// Drain remaining events before exiting
			for {
				select {
				case event := <-e.eventChan:
					if event.Timestamp.IsZero() {
						event.Timestamp = time.Now()
					}
					_ = e.db.CreateExecutionEvent(event)
				default:
					return
				}
			}
		}
	}
}

// trackExternalTask stores external worker task information in the database
func (e *Executor) trackExternalTask(executionID, nodeID, taskID string) error {
	e.dbMutex.Lock()
	defer e.dbMutex.Unlock()

	// Get current execution from database
	currentExecution, err := e.db.GetExecution(executionID)
	if err != nil {
		return fmt.Errorf("failed to get execution %s for external task tracking: %w", executionID, err)
	}

	// Parse existing external task mappings from A2ATasks field
	var externalTasks map[string]string
	if currentExecution.A2ATasks != nil {
		if err := json.Unmarshal(currentExecution.A2ATasks, &externalTasks); err != nil {
			log.Printf("Failed to unmarshal existing A2ATasks, creating new map: %v", err)
			externalTasks = make(map[string]string)
		}
	} else {
		externalTasks = make(map[string]string)
	}

	// Store external task mapping: nodeID -> external taskID
	externalTasks[nodeID] = taskID

	// Marshal back to JSON
	externalTasksJSON, err := json.Marshal(externalTasks)
	if err != nil {
		return fmt.Errorf("failed to marshal external tasks: %w", err)
	}

	// Update execution with new external task mapping
	currentExecution.A2ATasks = datatypes.JSON(externalTasksJSON)
	currentExecution.Logs += fmt.Sprintf("Tracked external task %s for node %s at %s\n", taskID, nodeID, time.Now().Format(time.RFC3339))

	if err := e.db.UpdateExecution(currentExecution); err != nil {
		return fmt.Errorf("failed to update execution %s with external task tracking: %w", executionID, err)
	}

	log.Printf("Tracked external task: node %s -> task %s in execution %s", nodeID, taskID, executionID)
	return nil
}

// trackExternalResult stores external worker result information in the database
func (e *Executor) trackExternalResult(executionID string, taskResponse protocol.TaskResponse) error {
	// Create execution event for external worker result
	event := &storage.ExecutionEvent{
		ExecutionID: executionID,
		NodeID:      taskResponse.NodeID,
		Timestamp:   time.Now(),
		Type:        "external_worker_result",
		Source:      "external_worker",
		State:       &taskResponse.TaskStatus.State,
		Message:     fmt.Sprintf("External worker completed task with state: %s", taskResponse.TaskStatus.State),
	}

	// Store structured result data
	resultData := map[string]interface{}{
		"taskStatus": taskResponse.TaskStatus,
		"artifacts":  taskResponse.Artifacts,
		"metadata":   taskResponse.Metadata,
	}

	resultJSON, err := json.Marshal(resultData)
	if err != nil {
		log.Printf("Failed to marshal external result data: %v", err)
	} else {
		event.Data = datatypes.JSON(resultJSON)
	}

	// Store raw TaskResponse for complete audit trail
	rawJSON, err := json.Marshal(taskResponse)
	if err != nil {
		log.Printf("Failed to marshal raw TaskResponse: %v", err)
	} else {
		event.Raw = datatypes.JSON(rawJSON)
	}

	// Store event in database
	if err := e.db.CreateExecutionEvent(event); err != nil {
		return fmt.Errorf("failed to create execution event for external result: %w", err)
	}

	log.Printf("Tracked external worker result for node %s in execution %s", taskResponse.NodeID, executionID)
	return nil
}

// storeProtocolMapping stores protocol ID mapping in execution record
func (e *Executor) storeProtocolMapping(executionID, nodeID, protocolType, protocolID string) {
	e.dbMutex.Lock()
	defer e.dbMutex.Unlock()

	// Get current execution from database
	currentExecution, err := e.db.GetExecution(executionID)
	if err != nil {
		log.Printf("Failed to get execution %s for protocol mapping: %v", executionID, err)
		return
	}

	// Parse existing mappings
	var a2aTasks map[string]string
	var acpSessions map[string]string

	if currentExecution.A2ATasks != nil {
		if err := json.Unmarshal(currentExecution.A2ATasks, &a2aTasks); err != nil {
			a2aTasks = make(map[string]string)
		}
	} else {
		a2aTasks = make(map[string]string)
	}

	if currentExecution.ACPSessions != nil {
		if err := json.Unmarshal(currentExecution.ACPSessions, &acpSessions); err != nil {
			acpSessions = make(map[string]string)
		}
	} else {
		acpSessions = make(map[string]string)
	}

	// Store mapping based on protocol type
	switch protocolType {
	case "a2a":
		a2aTasks[nodeID] = protocolID
	case "acp":
		acpSessions[nodeID] = protocolID
	default:
		log.Printf("Unknown protocol type: %s", protocolType)
		return
	}

	// Marshal mappings back to JSON
	a2aTasksJSON, err := json.Marshal(a2aTasks)
	if err != nil {
		log.Printf("Failed to marshal A2A tasks: %v", err)
		return
	}

	acpSessionsJSON, err := json.Marshal(acpSessions)
	if err != nil {
		log.Printf("Failed to marshal ACP sessions: %v", err)
		return
	}

	// Update execution with new mappings
	currentExecution.A2ATasks = datatypes.JSON(a2aTasksJSON)
	currentExecution.ACPSessions = datatypes.JSON(acpSessionsJSON)

	if err := e.db.UpdateExecution(currentExecution); err != nil {
		log.Printf("Failed to update execution %s with protocol mapping: %v", executionID, err)
	} else {
		log.Printf("Stored %s protocol mapping: node %s -> %s", protocolType, nodeID, protocolID)
	}
}

// handleEventStateSync processes events that require state synchronization
func (e *Executor) handleEventStateSync(event *storage.ExecutionEvent) {
	// Handle input required events separately
	if event.Type == storage.EventTypeInputRequired {
		e.handleInputRequiredEvent(event)
		return
	}

	// Handle state change events by syncing to node state
	stateChangeEvents := map[string]bool{
		storage.EventTypeStateChange: true,
		storage.EventTypeSubmitted:   true,
		storage.EventTypeWorking:     true,
		storage.EventTypeCompleted:   true,
		storage.EventTypeFailed:      true,
		storage.EventTypeCanceled:    true,
	}

	if stateChangeEvents[event.Type] {
		e.syncNodeStateFromEvent(event)
	}
}

// syncNodeStateFromEvent updates execution node state based on event
func (e *Executor) syncNodeStateFromEvent(event *storage.ExecutionEvent) {
	if event.State == nil {
		return // No state to sync
	}

	// Check if this is an active execution
	e.mutex.RLock()
	_, isActive := e.activeExecutions[event.ExecutionID]
	e.mutex.RUnlock()

	if !isActive {
		// Not an active execution, skip sync to avoid complexity
		return
	}

	// Get current execution state from database
	currentExecution, err := e.db.GetExecution(event.ExecutionID)
	if err != nil {
		log.Printf("Failed to get execution %s for state sync: %v", event.ExecutionID, err)
		return
	}

	// Parse current node states
	var nodeStates map[string]*engine.NodeState
	if currentExecution.NodeStates != nil {
		if err := json.Unmarshal(currentExecution.NodeStates, &nodeStates); err != nil {
			log.Printf("Failed to unmarshal node states for execution %s: %v", event.ExecutionID, err)
			return
		}
	} else {
		nodeStates = make(map[string]*engine.NodeState)
	}

	// Get or create node state
	nodeState, exists := nodeStates[event.NodeID]
	if !exists {
		nodeState = &engine.NodeState{
			Status: constants.TaskStateSubmitted,
		}
	}

	// Update state based on event
	nodeState.Status = *event.State

	// Set timestamps based on state transitions
	now := time.Now()
	if *event.State == constants.TaskStateWorking && nodeState.StartedAt.IsZero() {
		nodeState.StartedAt = now
	}
	if (*event.State == constants.TaskStateCompleted || *event.State == constants.TaskStateFailed ||
		*event.State == constants.TaskStateCanceled) && nodeState.EndedAt == nil {
		nodeState.EndedAt = &now
	}

	// Store updated state
	nodeStates[event.NodeID] = nodeState

	// Update database with new state
	nodeStatesJSON, err := json.Marshal(nodeStates)
	if err != nil {
		log.Printf("Failed to marshal node states for execution %s: %v", event.ExecutionID, err)
		return
	}

	// Update execution in database
	currentExecution.NodeStates = datatypes.JSON(nodeStatesJSON)
	currentExecution.Logs += fmt.Sprintf("Node %s state updated to %s via event at %s\n", event.NodeID, *event.State, time.Now().Format(time.RFC3339))

	if err := e.db.UpdateExecution(currentExecution); err != nil {
		log.Printf("Failed to update execution %s with state sync: %v", event.ExecutionID, err)
	}
}

// handleInputRequiredEvent processes input_required events to pause execution
func (e *Executor) handleInputRequiredEvent(event *storage.ExecutionEvent) {
	log.Printf("Input required for execution %s, node %s: %s",
		event.ExecutionID, event.NodeID, event.Message)

	// Check if this is an active execution
	e.mutex.RLock()
	_, isActive := e.activeExecutions[event.ExecutionID]
	e.mutex.RUnlock()

	if !isActive {
		// Not an active execution, skip
		return
	}

	// Get current execution state from database
	currentExecution, err := e.db.GetExecution(event.ExecutionID)
	if err != nil {
		log.Printf("Failed to get execution %s for input required handling: %v", event.ExecutionID, err)
		return
	}

	// Parse current node states
	var nodeStates map[string]*engine.NodeState
	if currentExecution.NodeStates != nil {
		if err := json.Unmarshal(currentExecution.NodeStates, &nodeStates); err != nil {
			log.Printf("Failed to unmarshal node states for execution %s: %v", event.ExecutionID, err)
			return
		}
	} else {
		nodeStates = make(map[string]*engine.NodeState)
	}

	// Get or create node state
	nodeState, exists := nodeStates[event.NodeID]
	if !exists {
		nodeState = &engine.NodeState{
			Status: constants.TaskStateSubmitted,
		}
	}

	// Mark node as requiring input
	nodeState.Status = constants.TaskStateInputRequired
	if nodeState.StartedAt.IsZero() {
		nodeState.StartedAt = time.Now()
	}

	// Store input request details in the error field for now (simple MVP approach)
	if event.Message != "" {
		nodeState.Error = fmt.Sprintf("Input required: %s", event.Message)
	} else {
		nodeState.Error = "Input required"
	}

	// Store updated state
	nodeStates[event.NodeID] = nodeState

	// Update database with new state
	nodeStatesJSON, err := json.Marshal(nodeStates)
	if err != nil {
		log.Printf("Failed to marshal node states for execution %s: %v", event.ExecutionID, err)
		return
	}

	// Update execution in database
	currentExecution.NodeStates = datatypes.JSON(nodeStatesJSON)
	currentExecution.Logs += fmt.Sprintf("Node %s requires input: %s at %s\n", event.NodeID, event.Message, time.Now().Format(time.RFC3339))

	if err := e.db.UpdateExecution(currentExecution); err != nil {
		log.Printf("Failed to update execution %s with input required state: %v", event.ExecutionID, err)
	}

	// Note: Cannot actually pause agent.Execute() as it's blocking
	// The input_required state will be visible in the database/API for external systems
}

// createAgentForNode creates an agent for the node based on its configuration
func (e *Executor) createAgentForNode(node *engine.Node) (Agent, error) {
	agentConfig, err := e.configLoader.GetAgentConfig(node.Agent)
	if err != nil {
		return nil, fmt.Errorf("failed to get agent config for '%s': %w", node.Agent, err)
	}

	// Validate request fields based on agent type
	if err := e.validateNodeRequest(node, agentConfig); err != nil {
		return nil, err
	}

	var agent Agent

	switch agentConfig.Type {
	case "stdio":
		command, ok := agentConfig.Config["command"].(string)
		if !ok {
			return nil, fmt.Errorf("stdio agent '%s' missing 'command' in config", node.Agent)
		}
		agent, err = NewStdioAgent(command)
	case "a2a":
		url, ok := agentConfig.Config["url"].(string)
		if !ok {
			return nil, fmt.Errorf("a2a agent '%s' missing 'url' in config", node.Agent)
		}
		agent = NewA2AAgent(url)
	case "acp":
		command, ok := agentConfig.Config["command"].(string)
		if !ok {
			return nil, fmt.Errorf("acp agent '%s' missing 'command' in config", node.Agent)
		}
		args, _ := agentConfig.Config["args"].([]string)
		workingDir, _ := node.Request["working_dir"].(string)
		agent, err = NewACPAgent(command, args, workingDir)
	default:
		return nil, fmt.Errorf("unknown agent type: %s", agentConfig.Type)
	}

	if err != nil {
		return nil, err
	}

	// Wire up event channel for agents that support event streaming
	if streamer, ok := agent.(EventStreamer); ok {
		streamer.SetEventChannel(e.eventChan)
	}

	return agent, nil
}

// validateNodeRequest validates request fields based on agent type
func (e *Executor) validateNodeRequest(node *engine.Node, agentConfig *config.AgentConfig) error {
	switch agentConfig.Type {
	case "stdio":
		if node.Request["prompt"] == nil {
			return fmt.Errorf("stdio agent '%s' requires 'prompt' in request", node.Agent)
		}
	case "a2a":
		if node.Request["task"] == nil && node.Request["prompt"] == nil {
			return fmt.Errorf("a2a agent '%s' requires 'task' or 'prompt' in request", node.Agent)
		}
	case "acp":
		if node.Request["prompt"] == nil {
			return fmt.Errorf("acp agent '%s' requires 'prompt' in request", node.Agent)
		}
		if node.Request["working_dir"] == nil {
			return fmt.Errorf("acp agent '%s' requires 'working_dir' in request", node.Agent)
		}
	default:
		return fmt.Errorf("unknown agent type: %s", agentConfig.Type)
	}
	return nil
}

// determineExecutionStatus determines final execution status with hardcoded stop-all behavior
func (e *Executor) determineExecutionStatus(execution *engine.Execution) string {
	e.mutex.RLock()
	defer e.mutex.RUnlock()

	if len(execution.NodeStates) == 0 {
		return constants.TaskStateCompleted
	}

	hasRunning := false
	hasFailed := false
	hasCancelled := false

	for _, nodeState := range execution.NodeStates {
		switch nodeState.Status {
		case constants.TaskStateSubmitted, constants.TaskStateWorking:
			hasRunning = true
		case constants.TaskStateFailed:
			hasFailed = true
		case constants.TaskStateCanceled:
			hasCancelled = true
		}
	}

	if hasRunning {
		return constants.TaskStateWorking
	}
	if hasFailed {
		return constants.TaskStateFailed
	}
	if hasCancelled {
		return constants.TaskStateCanceled
	}

	return constants.TaskStateCompleted
}
