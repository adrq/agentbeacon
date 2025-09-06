// Package main implements the AgentMaestro external worker binary.
//
// This worker provides distributed task execution by polling the orchestrator
// for available tasks and submitting results. The current implementation provides
// stub behavior for development and testing purposes.
//
// Future versions will support real task assignment and execution for scalable
// distributed AI agent workflows.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/config"
	"github.com/agentmaestro/agentmaestro/core/internal/engine"
	"github.com/agentmaestro/agentmaestro/core/internal/executor"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
)

var configLoader *config.ConfigLoader

// WorkerState tracks the current worker status and task information
type WorkerState struct {
	status        protocol.WorkerStatus
	currentTask   *protocol.CurrentTask
	pendingResult *protocol.TaskResult
}

func main() {
	// Initialize config loader
	configLoader = config.NewConfigLoader("examples/agents.yaml")
	orchestratorURL, interval, showVersion := parseFlags(os.Args[1:])

	if showVersion {
		fmt.Printf("agentmaestro-worker version: %s\n", getVersion())
		return
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Handle shutdown signals
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-sigCh
		log.Println("Shutting down worker...")
		cancel()
	}()

	runWorkerLoop(ctx, orchestratorURL, interval)
}

// parseFlags processes command line arguments and returns configuration values.
func parseFlags(args []string) (orchestratorURL string, interval time.Duration, showVersion bool) {
	fs := flag.NewFlagSet("agentmaestro-worker", flag.ExitOnError)

	urlFlag := fs.String("orchestrator-url", "http://localhost:9456", "URL of the orchestrator to poll")
	intervalFlag := fs.String("interval", "5s", "Polling interval")
	versionFlag := fs.Bool("version", false, "Show version information")

	fs.Parse(args)

	orchestratorURL = *urlFlag
	showVersion = *versionFlag

	var err error
	interval, err = time.ParseDuration(*intervalFlag)
	if err != nil {
		log.Fatalf("Invalid interval format: %v", err)
	}

	return orchestratorURL, interval, showVersion
}

// getVersion returns the current worker version.
func getVersion() string {
	return "0.1.0-stub"
}

// runWorkerLoop executes the main sync loop for task assignment.
func runWorkerLoop(ctx context.Context, orchestratorURL string, interval time.Duration) {
	client := &http.Client{Timeout: 30 * time.Second}
	syncURL := orchestratorURL + "/api/worker/sync"

	// Initialize worker state
	state := &WorkerState{
		status: protocol.WorkerStatusIdle,
	}

	log.Printf("Starting worker loop, syncing with %s every %v", syncURL, interval)

	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			log.Println("Worker loop stopped")
			return
		case <-ticker.C:
			if err := syncAndExecute(ctx, client, syncURL, state); err != nil {
				log.Printf("Sync and execute failed: %v", err)
			}
		}
	}
}

// syncAndExecute performs one cycle of sync communication and task execution
func syncAndExecute(ctx context.Context, client *http.Client, syncURL string, state *WorkerState) error {
	// Build sync request with current worker state
	syncReq := buildSyncRequest(state)

	// Marshal sync request
	jsonData, err := json.Marshal(syncReq)
	if err != nil {
		return fmt.Errorf("failed to marshal sync request: %w", err)
	}

	// Send sync request
	req, err := http.NewRequestWithContext(ctx, "POST", syncURL, bytes.NewBuffer(jsonData))
	if err != nil {
		return fmt.Errorf("failed to create sync request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("sync request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("sync request returned status %d", resp.StatusCode)
	}

	// Parse sync response
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("failed to read sync response: %w", err)
	}

	var syncResp protocol.SyncResponse
	if err := json.Unmarshal(body, &syncResp); err != nil {
		return fmt.Errorf("failed to parse sync response: %w", err)
	}

	// Handle sync response
	return handleSyncResponse(ctx, &syncResp, state)
}

// buildSyncRequest creates a sync request based on current worker state
func buildSyncRequest(state *WorkerState) *protocol.SyncRequest {
	req := protocol.NewSyncRequest(state.status)

	// Add current task if worker is working
	if state.currentTask != nil {
		req.CurrentTask = state.currentTask
	}

	// Add pending result if available
	if state.pendingResult != nil {
		req.TaskResult = state.pendingResult
		// Clear pending result after including it in request
		state.pendingResult = nil
	}

	return req
}

// handleSyncResponse processes the orchestrator's sync response
func handleSyncResponse(ctx context.Context, resp *protocol.SyncResponse, state *WorkerState) error {
	switch resp.Type {
	case protocol.SyncResponseNoAction:
		log.Println("No action from sync response")
		return nil

	case protocol.SyncResponseTaskAssigned:
		if resp.Task == nil {
			return fmt.Errorf("sync response indicated task assigned but no task provided")
		}
		return executeAssignedTask(ctx, resp.Task, state)

	case protocol.SyncResponseCommand:
		if resp.Command == nil {
			return fmt.Errorf("sync response indicated command but no command provided")
		}
		return handleWorkerCommand(resp.Command, state)

	default:
		return fmt.Errorf("unknown sync response type: %s", resp.Type)
	}
}

// executeAssignedTask executes a task assignment and updates worker state
func executeAssignedTask(ctx context.Context, task *protocol.TaskAssignment, state *WorkerState) error {
	log.Printf("Received task assignment: %s (execution: %s, agent: %s)", task.NodeID, task.ExecutionID, task.AgentType)

	// Update worker state to working
	state.status = protocol.WorkerStatusWorking
	state.currentTask = &protocol.CurrentTask{
		NodeID:      task.NodeID,
		ExecutionID: task.ExecutionID,
		StartTime:   time.Now().UTC().Format(time.RFC3339),
	}

	// Execute the task
	result := executeTaskWithAssignment(ctx, task)

	// Convert WorkerResult to TaskResult for sync protocol
	taskResult := &protocol.TaskResult{
		NodeID:      result.NodeID,
		ExecutionID: task.ExecutionID,
	}

	// Convert result to A2A format
	if result.Status == "completed" {
		taskResult.TaskStatus = protocol.A2ATaskStatus{
			State:     "completed",
			Timestamp: time.Now().UTC().Format(time.RFC3339),
			Message: &protocol.Message{
				Role: "agent",
				Parts: []protocol.Part{{
					Kind: "text",
					Text: result.Output,
				}},
			},
		}
	} else {
		taskResult.TaskStatus = protocol.A2ATaskStatus{
			State:     "failed",
			Timestamp: time.Now().UTC().Format(time.RFC3339),
			Message: &protocol.Message{
				Role: "agent",
				Parts: []protocol.Part{{
					Kind: "text",
					Text: result.Error,
				}},
			},
		}
	}

	// Store result for next sync request
	state.pendingResult = taskResult

	// Update worker state back to idle
	state.status = protocol.WorkerStatusIdle
	state.currentTask = nil

	// Log appropriate message based on actual task outcome
	if result.Status == "completed" {
		log.Printf("Task %s completed successfully", task.NodeID)
	} else {
		log.Printf("Task %s failed: %s", task.NodeID, result.Error)
	}
	return nil
}

// handleWorkerCommand processes control commands from the orchestrator
func handleWorkerCommand(command *protocol.WorkerCommand, state *WorkerState) error {
	log.Printf("Received worker command: %s", command.Action)

	switch command.Action {
	case protocol.CommandActionCancel:
		// Cancel current task if any
		if state.currentTask != nil {
			log.Printf("Cancelling task %s", state.currentTask.NodeID)
			state.status = protocol.WorkerStatusIdle
			state.currentTask = nil
		}
		return nil

	case protocol.CommandActionFail:
		// Fail current task if any
		if state.currentTask != nil {
			log.Printf("Failing task %s", state.currentTask.NodeID)

			// Create failed result
			reason := command.Reason
			if reason == "" {
				reason = "Task failed by command"
			}
			state.pendingResult = &protocol.TaskResult{
				NodeID:      state.currentTask.NodeID,
				ExecutionID: state.currentTask.ExecutionID,
				TaskStatus: protocol.A2ATaskStatus{
					State:     "failed",
					Timestamp: time.Now().UTC().Format(time.RFC3339),
					Message: &protocol.Message{
						Role: "agent",
						Parts: []protocol.Part{{
							Kind: "text",
							Text: reason,
						}},
					},
				},
			}

			state.status = protocol.WorkerStatusIdle
			state.currentTask = nil
		}
		return nil

	default:
		return fmt.Errorf("unknown worker command: %s", command.Action)
	}
}

// executeTaskWithAssignment executes a task using the assignment details
func executeTaskWithAssignment(ctx context.Context, task *protocol.TaskAssignment) *protocol.WorkerResult {
	log.Printf("Executing task %s with agent %s", task.NodeID, task.AgentType)

	// Create agent based on assignment
	agent, err := createAgentFromAssignment(task)
	if err != nil {
		log.Printf("Failed to create agent for task %s: %v", task.NodeID, err)
		return protocol.NewFailedResult(task.NodeID, fmt.Errorf("agent creation failed: %w", err))
	}
	defer agent.Close()

	// Execute the task using the agent
	output, err := agent.Execute(ctx, task.Prompt)
	if err != nil {
		log.Printf("Task %s execution failed: %v", task.NodeID, err)
		return protocol.NewFailedResult(task.NodeID, err)
	}

	log.Printf("Task %s completed successfully", task.NodeID)
	return protocol.NewCompletedResult(task.NodeID, output)
}

// createAgentFromAssignment creates an agent based on the task assignment details
func createAgentFromAssignment(task *protocol.TaskAssignment) (executor.Agent, error) {
	// Get agent config from ConfigLoader
	agentConfig, err := configLoader.GetAgentConfig(task.AgentType)
	if err != nil {
		return nil, fmt.Errorf("failed to get agent config for '%s': %w", task.AgentType, err)
	}

	// Create agent based on type
	switch agentConfig.Type {
	case "stdio":
		command, ok := agentConfig.Config["command"].(string)
		if !ok {
			return nil, fmt.Errorf("stdio agent '%s' missing 'command' in config", task.AgentType)
		}

		// Handle args properly
		var args []string
		if argsInterface, ok := agentConfig.Config["args"].([]interface{}); ok {
			for _, arg := range argsInterface {
				if argStr, ok := arg.(string); ok {
					args = append(args, argStr)
				}
			}
		}

		return executor.NewStdioAgent(command, args...)
	case "a2a":
		url, ok := agentConfig.Config["url"].(string)
		if !ok {
			return nil, fmt.Errorf("a2a agent '%s' missing 'url' in config", task.AgentType)
		}
		return executor.NewA2AAgent(url), nil
	case "acp":
		command, ok := agentConfig.Config["command"].(string)
		if !ok {
			return nil, fmt.Errorf("acp agent '%s' missing 'command' in config", task.AgentType)
		}

		// Handle args for ACP agents
		var args []string
		if argsInterface, ok := agentConfig.Config["args"].([]interface{}); ok {
			for _, arg := range argsInterface {
				if argStr, ok := arg.(string); ok {
					args = append(args, argStr)
				}
			}
		}

		return executor.NewACPAgent(command, args, "")
	default:
		return nil, fmt.Errorf("unknown agent type: %s", agentConfig.Type)
	}
}

// createAgent creates an agent based on the task configuration
func createAgent(task *engine.Node) (executor.Agent, error) {
	// Get agent config from ConfigLoader
	agentConfig, err := configLoader.GetAgentConfig(task.Agent)
	if err != nil {
		return nil, fmt.Errorf("failed to get agent config for '%s': %w", task.Agent, err)
	}

	// Create agent based on type
	switch agentConfig.Type {
	case "stdio":
		command, ok := agentConfig.Config["command"].(string)
		if !ok {
			return nil, fmt.Errorf("stdio agent '%s' missing 'command' in config", task.Agent)
		}

		// Handle args properly (unlike executor which has a bug here)
		var args []string
		if argsInterface, ok := agentConfig.Config["args"].([]interface{}); ok {
			for _, arg := range argsInterface {
				if argStr, ok := arg.(string); ok {
					args = append(args, argStr)
				}
			}
		}

		return executor.NewStdioAgent(command, args...)
	case "a2a":
		url, ok := agentConfig.Config["url"].(string)
		if !ok {
			return nil, fmt.Errorf("a2a agent '%s' missing 'url' in config", task.Agent)
		}
		return executor.NewA2AAgent(url), nil
	case "acp":
		command, ok := agentConfig.Config["command"].(string)
		if !ok {
			return nil, fmt.Errorf("acp agent '%s' missing 'command' in config", task.Agent)
		}

		// Handle args for ACP agents
		var args []string
		if argsInterface, ok := agentConfig.Config["args"].([]interface{}); ok {
			for _, arg := range argsInterface {
				if argStr, ok := arg.(string); ok {
					args = append(args, argStr)
				}
			}
		}

		return executor.NewACPAgent(command, args, "")
	default:
		return nil, fmt.Errorf("unknown agent type: %s", agentConfig.Type)
	}
}
