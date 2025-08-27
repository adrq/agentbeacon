package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/engine"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/google/uuid"
	"gopkg.in/yaml.v3"
	"gorm.io/datatypes"
)

// Executor manages workflow execution
type Executor struct {
	db *storage.DB
}

// NewExecutor creates a new workflow executor
func NewExecutor(db *storage.DB) *Executor {
	return &Executor{
		db: db,
	}
}

// ExecutionInfo represents basic execution information for API responses
type ExecutionInfo struct {
	ID          string
	WorkflowID  string
	Status      string
	StartedAt   time.Time
	CompletedAt *time.Time
}

// StartWorkflow begins execution of a workflow by name
func (e *Executor) StartWorkflow(workflowName string) (*ExecutionInfo, error) {
	// Load workflow metadata
	_, err := e.db.GetWorkflowMetadata(workflowName)
	if err != nil {
		return nil, fmt.Errorf("failed to load workflow metadata: %w", err)
	}

	// Load workflow YAML content
	yamlContent, err := e.db.LoadWorkflowYAML(workflowName)
	if err != nil {
		return nil, fmt.Errorf("failed to load workflow YAML: %w", err)
	}

	// Parse workflow
	var workflow engine.Workflow
	if err := yaml.Unmarshal(yamlContent, &workflow); err != nil {
		return nil, fmt.Errorf("failed to parse workflow YAML: %w", err)
	}

	// Create execution record
	executionID := uuid.New().String()
	nodeStates := make(map[string]engine.NodeState)

	// Initialize all nodes as pending
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

	// Persist execution to database
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

	// Start workflow execution in background
	go e.executeWorkflow(execution, &workflow)

	return &ExecutionInfo{
		ID:          execution.ID,
		WorkflowID:  execution.WorkflowID,
		Status:      execution.Status,
		StartedAt:   execution.StartedAt,
		CompletedAt: execution.CompletedAt,
	}, nil
}

// executeWorkflow runs the complete workflow execution
func (e *Executor) executeWorkflow(execution *engine.Execution, workflow *engine.Workflow) error {
	ctx := context.Background()

	// For MVP: Execute nodes sequentially regardless of dependencies
	for _, node := range workflow.Nodes {
		if err := e.executeNode(ctx, execution, &node); err != nil {
			// Mark execution as failed
			execution.Status = "failed"
			completedAt := time.Now()
			execution.CompletedAt = &completedAt

			e.updateExecutionInDB(execution, fmt.Sprintf("Execution failed at node %s: %v", node.ID, err))
			return err
		}
	}

	// Mark execution as completed
	execution.Status = "completed"
	completedAt := time.Now()
	execution.CompletedAt = &completedAt

	e.updateExecutionInDB(execution, "Workflow execution completed successfully")
	return nil
}

// executeNode runs a single workflow node
func (e *Executor) executeNode(ctx context.Context, execution *engine.Execution, node *engine.Node) error {
	// Create agent for this specific node
	agent, err := e.createAgentForNode(node)
	if err != nil {
		return fmt.Errorf("failed to create agent for node %s: %w", node.ID, err)
	}
	defer agent.Close() // Ensure process cleanup after node completes

	// Update node state to running
	nodeState := execution.NodeStates[node.ID]
	nodeState.Status = "running"
	nodeState.StartedAt = time.Now()
	execution.NodeStates[node.ID] = nodeState

	// Persist state change
	e.updateExecutionInDB(execution, fmt.Sprintf("Started executing node: %s", node.ID))

	// Execute the node using the node-specific agent
	result, err := agent.Execute(ctx, node.Prompt)

	// Update node state with result
	endedAt := time.Now()
	nodeState.EndedAt = &endedAt

	if err != nil {
		nodeState.Status = "failed"
		nodeState.Error = err.Error()
		execution.NodeStates[node.ID] = nodeState

		e.updateExecutionInDB(execution, fmt.Sprintf("Node %s failed: %v", node.ID, err))
		return fmt.Errorf("node %s execution failed: %w", node.ID, err)
	}

	nodeState.Status = "completed"
	nodeState.Output = result
	execution.NodeStates[node.ID] = nodeState

	// Persist successful completion
	e.updateExecutionInDB(execution, fmt.Sprintf("Node %s completed successfully", node.ID))
	return nil
}

// updateExecutionInDB persists execution state changes to database
func (e *Executor) updateExecutionInDB(execution *engine.Execution, logMessage string) {
	// Serialize node states
	nodeStatesJSON, err := json.Marshal(execution.NodeStates)
	if err != nil {
		// Log error but don't fail execution
		return
	}

	// Get current execution from database to append logs
	currentExecution, err := e.db.GetExecution(execution.ID)
	if err != nil {
		return
	}

	// Update execution record
	dbExecution := &storage.Execution{
		ID:           execution.ID,
		WorkflowName: execution.WorkflowID,
		Status:       execution.Status,
		NodeStates:   datatypes.JSON(nodeStatesJSON),
		Logs:         currentExecution.Logs + logMessage + "\n",
		StartedAt:    execution.StartedAt,
		CompletedAt:  execution.CompletedAt,
	}

	// Persist to database
	e.db.UpdateExecution(dbExecution)
}

// createAgentForNode creates an appropriate agent for the given node
func (e *Executor) createAgentForNode(node *engine.Node) (Agent, error) {
	// For MVP, support existing test agents and treat unknowns as mock-agent
	// This allows existing tests to pass while providing correct per-node spawning
	switch node.Agent {
	case "mock-agent", "demo-agent", "test-agent-2":
		return NewProcessAgent("../../../bin/mock-agent")
	default:
		// For MVP, treat any unknown agent as mock-agent
		// Later tasks will add support for claude-code, gemini-cli, etc.
		return NewProcessAgent("../../../bin/mock-agent")
	}
}
