package executor

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"os/exec"
	"sync"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/constants"
	"github.com/agentmaestro/agentmaestro/core/internal/engine"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/google/uuid"
	"gopkg.in/yaml.v3"
	"gorm.io/datatypes"
)

const (
	maxOutputBytes = 10 * 1024 * 1024 // 10MB output limit to prevent memory issues
)

type Task struct {
	NodeID    string
	Execution *engine.Execution
	Node      *engine.Node
}

type TaskResult struct {
	NodeID string
	Error  error
}

type WorkerPool struct {
	workers       int
	taskChan      chan Task
	resultChan    chan TaskResult
	ctx           context.Context
	cancel        context.CancelFunc
	wg            sync.WaitGroup
	executor      *Executor
	retryExecutor *RetryableExecutor
}

type Executor struct {
	db               *storage.DB
	mutex            sync.RWMutex
	activeExecutions map[string]context.CancelFunc
	dbMutex          sync.Mutex // Serialize database updates
}

func NewExecutor(db *storage.DB) *Executor {
	return &Executor{
		db:               db,
		activeExecutions: make(map[string]context.CancelFunc),
	}
}

func (e *Executor) Close() {
	// No cleanup needed in simplified MVP
}

func NewWorkerPool(ctx context.Context, workers int, executor *Executor) *WorkerPool {
	ctx, cancel := context.WithCancel(ctx)
	return &WorkerPool{
		workers:       workers,
		taskChan:      make(chan Task, workers*2),
		resultChan:    make(chan TaskResult, workers*2),
		ctx:           ctx,
		cancel:        cancel,
		executor:      executor,
		retryExecutor: NewRetryableExecutor(executor),
	}
}

func (wp *WorkerPool) Start() {
	for i := 0; i < wp.workers; i++ {
		wp.wg.Add(1)
		go wp.worker()
	}
}

func (wp *WorkerPool) Shutdown() {
	wp.cancel()
	close(wp.taskChan)
	wp.wg.Wait()
	close(wp.resultChan)
}

// worker processes tasks from taskChan until shutdown.
func (wp *WorkerPool) worker() {
	defer wp.wg.Done()
	defer func() {
		if r := recover(); r != nil {
			log.Printf("Worker panic recovered: %v", r)
		}
	}()

	for {
		select {
		case task, ok := <-wp.taskChan:
			if !ok {
				return
			}

			var err error
			func() {
				defer func() {
					if r := recover(); r != nil {
						err = fmt.Errorf("node execution panicked: %v", r)
						log.Printf("Node %s execution panic: %v", task.NodeID, r)

						// Mark execution as failed and send terminal update
						wp.executor.mutex.Lock()
						task.Execution.Status = constants.TaskStateFailed
						if task.Execution.CompletedAt == nil {
							completedAt := time.Now()
							task.Execution.CompletedAt = &completedAt
						}

						// Mark the node as failed
						nodeState := task.Execution.NodeStates[task.NodeID]
						nodeState.Status = constants.TaskStateFailed
						nodeState.Output = fmt.Sprintf("Node execution panicked: %v", r)
						if nodeState.EndedAt == nil {
							endedAt := time.Now()
							nodeState.EndedAt = &endedAt
						}
						task.Execution.NodeStates[task.NodeID] = nodeState
						wp.executor.mutex.Unlock()

						// Force terminal update regardless of channel state
						wp.executor.updateExecutionInDB(task.Execution, fmt.Sprintf("Node %s panicked: %v", task.NodeID, r))
					}
				}()
				err = wp.retryExecutor.executeNodeWithRetry(wp.ctx, task.Execution, task.Node)
			}()

			result := TaskResult{
				NodeID: task.NodeID,
				Error:  err,
			}

			select {
			case wp.resultChan <- result:
			case <-wp.ctx.Done():
				return
			}

		case <-wp.ctx.Done():
			return
		}
	}
}

// executeLevel executes a level with hardcoded stop-all behavior on node failures
func (wp *WorkerPool) executeLevel(execution *engine.Execution, nodeMap map[string]*engine.Node, level []string) error {
	if len(level) == 0 {
		return nil
	}

	validTasks := 0
	for _, nodeID := range level {
		node := nodeMap[nodeID]
		if node == nil {
			continue
		}

		if !wp.shouldExecuteNode(execution, node) {
			continue
		}

		task := Task{
			NodeID:    nodeID,
			Execution: execution,
			Node:      node,
		}

		select {
		case wp.taskChan <- task:
			validTasks++
		case <-wp.ctx.Done():
			return fmt.Errorf("task submission cancelled: %w", wp.ctx.Err())
		}
	}

	for i := 0; i < validTasks; i++ {
		select {
		case result := <-wp.resultChan:
			if result.Error != nil {
				node := nodeMap[result.NodeID]
				if node != nil {
					err := wp.handleNodeFailure(execution, node, result.Error)
					if err != nil {
						wp.cancel()
						return fmt.Errorf("failed to handle node failure: %w", err)
					}

					wp.cancel()
					for j := i + 1; j < validTasks; j++ {
						select {
						case <-wp.resultChan:
						case <-wp.ctx.Done():
							goto drainComplete
						}
					}
				drainComplete:
					return result.Error
				}
				// Node failure already handled above
			}
		case <-wp.ctx.Done():
			// Mark any running nodes as cancelled when context is cancelled
			wp.markRunningNodesAsCancelled(execution, nodeMap, level)
			return fmt.Errorf("result collection cancelled: %w", wp.ctx.Err())
		}
	}

	return nil
}

// markRunningNodesAsCancelled marks any running nodes in the current level as cancelled
func (wp *WorkerPool) markRunningNodesAsCancelled(execution *engine.Execution, nodeMap map[string]*engine.Node, level []string) {
	// Skip node state updates here to avoid deadlocks
	// The main execution thread will handle marking all remaining nodes as cancelled
}

// handleNodeFailure handles node failure with hardcoded stop-all behavior
func (wp *WorkerPool) handleNodeFailure(execution *engine.Execution, failedNode *engine.Node, nodeErr error) error {
	wp.executor.mutex.Lock()
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
	wp.executor.mutex.Unlock()

	wp.executor.updateExecutionInDB(execution, "Execution failed - cancelling all pending nodes")

	return nil
}

// shouldExecuteNode determines if a node should be executed
func (wp *WorkerPool) shouldExecuteNode(execution *engine.Execution, node *engine.Node) bool {
	wp.executor.mutex.RLock()
	defer wp.executor.mutex.RUnlock()

	if execution.Status != constants.TaskStateWorking {
		return false
	}

	nodeState, exists := execution.NodeStates[node.ID]
	if !exists {
		return false
	}

	return nodeState.Status == constants.TaskStateSubmitted
}

type ExecutionInfo struct {
	ID          string
	WorkflowID  string
	Status      string
	StartedAt   time.Time
	CompletedAt *time.Time
}

func (e *Executor) StartWorkflow(workflowName string) (*ExecutionInfo, error) {
	_, err := e.db.GetWorkflowMetadata(workflowName)
	if err != nil {
		return nil, fmt.Errorf("failed to load workflow metadata: %w", err)
	}

	yamlContent, err := e.db.LoadWorkflowYAML(workflowName)
	if err != nil {
		return nil, fmt.Errorf("failed to load workflow YAML: %w", err)
	}

	var workflow engine.Workflow
	if err := yaml.Unmarshal(yamlContent, &workflow); err != nil {
		return nil, fmt.Errorf("failed to parse workflow YAML: %w", err)
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
		nodeStates[node.ID] = engine.NodeState{
			Status: constants.TaskStateSubmitted,
		}
	}

	nodeStatesJSON, err := json.Marshal(nodeStates)
	if err != nil {
		return nil, fmt.Errorf("failed to serialize node states: %w", err)
	}

	execution := &engine.Execution{
		ID:         executionID,
		WorkflowID: workflowName,
		Status:     constants.TaskStateWorking,
		NodeStates: nodeStates,
		StartedAt:  time.Now(),
	}

	dbExecution := &storage.Execution{
		ID:           executionID,
		WorkflowName: workflowName,
		Status:       constants.TaskStateWorking,
		NodeStates:   datatypes.JSON(nodeStatesJSON),
		A2ATasks:     datatypes.JSON("{}"),
		Logs:         fmt.Sprintf("Started workflow execution at %s\n", time.Now().Format(time.RFC3339)),
		StartedAt:    execution.StartedAt,
	}

	if err := e.db.CreateExecution(dbExecution); err != nil {
		return nil, fmt.Errorf("failed to create execution record: %w", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	e.mutex.Lock()
	e.activeExecutions[executionID] = cancel
	e.mutex.Unlock()

	go func() {
		if err := e.executeWorkflow(ctx, execution, &workflow); err != nil {
			log.Printf("Workflow execution %s failed: %v", executionID, err)
		}
		e.mutex.Lock()
		delete(e.activeExecutions, executionID)
		e.mutex.Unlock()
	}()

	e.mutex.RLock()
	info := &ExecutionInfo{
		ID:          execution.ID,
		WorkflowID:  execution.WorkflowID,
		Status:      execution.Status,
		StartedAt:   execution.StartedAt,
		CompletedAt: execution.CompletedAt,
	}
	e.mutex.RUnlock()

	return info, nil
}

// executeWorkflow runs complete workflow execution using dependency levels and worker pool.
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

	// Use hardcoded stop-all behavior for MVP

	pool := NewWorkerPool(ctx, 5, e)
	pool.Start()
	defer pool.Shutdown()

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

		if err := pool.executeLevel(execution, nodeMap, level); err != nil {
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

	e.mutex.Lock()
	nodeState := execution.NodeStates[node.ID]
	nodeState.Status = constants.TaskStateWorking
	nodeState.StartedAt = time.Now()
	execution.NodeStates[node.ID] = nodeState
	e.mutex.Unlock()

	e.updateExecutionInDB(execution, fmt.Sprintf("Started executing node: %s", node.ID))

	result, err := agent.Execute(nodeCtx, node.Prompt)

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

// createAgentForNode creates an agent for the node based on its configuration
func (e *Executor) createAgentForNode(node *engine.Node) (Agent, error) {
	// Future: Check node.AgentURL for A2A agents
	// if node.AgentURL != "" {
	//     return NewA2AAgent(node.AgentURL)
	// }

	// For now, always use stdio agent with mock-agent
	agentPaths := []string{
		"../../../bin/mock-agent",
		"./bin/mock-agent",
		"bin/mock-agent",
	}

	var agentPath string
	for _, path := range agentPaths {
		if _, err := exec.LookPath(path); err == nil {
			agentPath = path
			break
		}
		if _, err := os.Stat(path); err == nil {
			agentPath = path
			break
		}
	}

	if agentPath == "" {
		return nil, fmt.Errorf("mock-agent binary not found in any of the expected paths: %v", agentPaths)
	}

	return NewStdioAgent(agentPath)
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
