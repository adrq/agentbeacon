package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"sync"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/engine"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/google/uuid"
	"gopkg.in/yaml.v3"
	"gorm.io/datatypes"
)

// Task represents a unit of work for the worker pool.
type Task struct {
	NodeID    string
	Execution *engine.Execution
	Node      *engine.Node
}

// TaskResult represents the result of executing a task.
type TaskResult struct {
	NodeID string
	Error  error
}

// WorkerPool manages parallel execution of workflow nodes using a fixed pool of workers.
type WorkerPool struct {
	workers    int
	taskChan   chan Task
	resultChan chan TaskResult
	ctx        context.Context
	cancel     context.CancelFunc
	wg         sync.WaitGroup
	executor   *Executor
}

// Executor manages workflow execution
type Executor struct {
	db    *storage.DB
	mutex sync.RWMutex // Thread-safe access to execution state
}

// NewExecutor creates a new workflow executor
func NewExecutor(db *storage.DB) *Executor {
	return &Executor{
		db: db,
	}
}

// NewWorkerPool creates a worker pool with buffered channels (2x worker count).
func NewWorkerPool(workers int, executor *Executor) *WorkerPool {
	ctx, cancel := context.WithCancel(context.Background())
	return &WorkerPool{
		workers:    workers,
		taskChan:   make(chan Task, workers*2),
		resultChan: make(chan TaskResult, workers*2),
		ctx:        ctx,
		cancel:     cancel,
		executor:   executor,
	}
}

// Start spawns worker goroutines.
func (wp *WorkerPool) Start() {
	for i := 0; i < wp.workers; i++ {
		wp.wg.Add(1)
		go wp.worker()
	}
}

// Shutdown gracefully stops the worker pool.
func (wp *WorkerPool) Shutdown() {
	wp.cancel()
	close(wp.taskChan)
	wp.wg.Wait()
	close(wp.resultChan)
}

// worker processes tasks from taskChan until shutdown.
func (wp *WorkerPool) worker() {
	defer wp.wg.Done()

	for {
		select {
		case task, ok := <-wp.taskChan:
			if !ok {
				return
			}

			err := wp.executor.executeNode(wp.ctx, task.Execution, task.Node)

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

// executeLevelWithNodeMap executes a level using pre-built node map for performance.
func (wp *WorkerPool) executeLevelWithNodeMap(execution *engine.Execution, nodeMap map[string]*engine.Node, level []string) error {
	if len(level) == 0 {
		return nil
	}

	validTasks := 0
	for _, nodeID := range level {
		node := nodeMap[nodeID]
		if node == nil {
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

	var errors []error
	for i := 0; i < validTasks; i++ {
		select {
		case result := <-wp.resultChan:
			if result.Error != nil {
				errors = append(errors, result.Error)
			}
		case <-wp.ctx.Done():
			return fmt.Errorf("result collection cancelled: %w", wp.ctx.Err())
		}
	}

	if len(errors) > 0 {
		return errors[0]
	}

	return nil
}

// ExecutionInfo represents basic execution information for API responses
type ExecutionInfo struct {
	ID          string
	WorkflowID  string
	Status      string
	StartedAt   time.Time
	CompletedAt *time.Time
}

// StartWorkflow starts executing a workflow by name asynchronously.
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

	executionID := uuid.New().String()
	nodeStates := make(map[string]engine.NodeState)

	for _, node := range workflow.Nodes {
		nodeStates[node.ID] = engine.NodeState{
			Status: "pending",
		}
	}

	nodeStatesJSON, err := json.Marshal(nodeStates)
	if err != nil {
		return nil, fmt.Errorf("failed to serialize node states: %w", err)
	}

	execution := &engine.Execution{
		ID:         executionID,
		WorkflowID: workflowName,
		Status:     "running",
		NodeStates: nodeStates,
		StartedAt:  time.Now(),
	}

	dbExecution := &storage.Execution{
		ID:           executionID,
		WorkflowName: workflowName,
		Status:       "running",
		NodeStates:   datatypes.JSON(nodeStatesJSON),
		Logs:         fmt.Sprintf("Started workflow execution at %s\n", time.Now().Format(time.RFC3339)),
		StartedAt:    execution.StartedAt,
	}

	if err := e.db.CreateExecution(dbExecution); err != nil {
		return nil, fmt.Errorf("failed to create execution record: %w", err)
	}

	go e.executeWorkflow(execution, &workflow)

	return &ExecutionInfo{
		ID:          execution.ID,
		WorkflowID:  execution.WorkflowID,
		Status:      execution.Status,
		StartedAt:   execution.StartedAt,
		CompletedAt: execution.CompletedAt,
	}, nil
}

// executeWorkflow runs complete workflow execution using dependency levels and worker pool.
func (e *Executor) executeWorkflow(execution *engine.Execution, workflow *engine.Workflow) error {
	levels, err := engine.TopologicalSort(workflow.Nodes)
	if err != nil {
		execution.Status = "failed"
		completedAt := time.Now()
		execution.CompletedAt = &completedAt
		e.updateExecutionInDB(execution, fmt.Sprintf("Workflow validation failed: %v", err))
		return fmt.Errorf("workflow validation failed: %w", err)
	}

	pool := NewWorkerPool(5, e)
	pool.Start()
	defer pool.Shutdown()

	nodeMap := make(map[string]*engine.Node)
	for i := range workflow.Nodes {
		nodeMap[workflow.Nodes[i].ID] = &workflow.Nodes[i]
	}

	for levelIndex, level := range levels {
		if err := pool.executeLevelWithNodeMap(execution, nodeMap, level); err != nil {
			execution.Status = "failed"
			completedAt := time.Now()
			execution.CompletedAt = &completedAt
			e.updateExecutionInDB(execution, fmt.Sprintf("Execution failed at level %d: %v", levelIndex, err))
			return fmt.Errorf("execution failed at level %d: %w", levelIndex, err)
		}
	}

	execution.Status = "completed"
	completedAt := time.Now()
	execution.CompletedAt = &completedAt

	e.updateExecutionInDB(execution, "Workflow execution completed successfully")
	return nil
}

// executeNode runs a single workflow node with dedicated agent process.
func (e *Executor) executeNode(ctx context.Context, execution *engine.Execution, node *engine.Node) error {
	agent, err := e.createAgentForNode(node)
	if err != nil {
		return fmt.Errorf("failed to create agent for node %s: %w", node.ID, err)
	}
	defer agent.Close()

	e.mutex.Lock()
	nodeState := execution.NodeStates[node.ID]
	nodeState.Status = "running"
	nodeState.StartedAt = time.Now()
	execution.NodeStates[node.ID] = nodeState
	e.mutex.Unlock()

	e.updateExecutionInDB(execution, fmt.Sprintf("Started executing node: %s", node.ID))

	result, err := agent.Execute(ctx, node.Prompt)

	endedAt := time.Now()
	e.mutex.Lock()
	nodeState = execution.NodeStates[node.ID]
	nodeState.EndedAt = &endedAt

	if err != nil {
		nodeState.Status = "failed"
		nodeState.Error = err.Error()
		execution.NodeStates[node.ID] = nodeState
		e.mutex.Unlock()

		e.updateExecutionInDB(execution, fmt.Sprintf("Node %s failed: %v", node.ID, err))
		return fmt.Errorf("node %s execution failed: %w", node.ID, err)
	}

	nodeState.Status = "completed"
	nodeState.Output = result
	execution.NodeStates[node.ID] = nodeState
	e.mutex.Unlock()

	e.updateExecutionInDB(execution, fmt.Sprintf("Node %s completed successfully", node.ID))
	return nil
}

// updateExecutionInDB persists execution state. DB errors don't fail workflow execution.
func (e *Executor) updateExecutionInDB(execution *engine.Execution, logMessage string) {
	e.mutex.RLock()
	nodeStatesJSON, err := json.Marshal(execution.NodeStates)
	e.mutex.RUnlock()

	if err != nil {
		return
	}

	currentExecution, err := e.db.GetExecution(execution.ID)
	if err != nil {
		return
	}

	dbExecution := &storage.Execution{
		ID:           execution.ID,
		WorkflowName: execution.WorkflowID,
		Status:       execution.Status,
		NodeStates:   datatypes.JSON(nodeStatesJSON),
		Logs:         currentExecution.Logs + logMessage + "\n",
		StartedAt:    execution.StartedAt,
		CompletedAt:  execution.CompletedAt,
	}

	e.db.UpdateExecution(dbExecution)
}

// createAgentForNode creates an agent for the node. MVP uses mock-agent for all types.
func (e *Executor) createAgentForNode(node *engine.Node) (Agent, error) {
	switch node.Agent {
	case "mock-agent", "demo-agent", "test-agent-2":
		return NewProcessAgent("../../../bin/mock-agent")
	default:
		return NewProcessAgent("../../../bin/mock-agent")
	}
}
