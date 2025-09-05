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

	"github.com/agentmaestro/agentmaestro/core/internal/engine"
	"github.com/agentmaestro/agentmaestro/core/internal/executor"
	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
)

func main() {
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

// runWorkerLoop executes the main polling loop for task assignment.
func runWorkerLoop(ctx context.Context, orchestratorURL string, interval time.Duration) {
	client := &http.Client{Timeout: 30 * time.Second}
	pollURL := orchestratorURL + "/api/worker/poll"
	resultURL := orchestratorURL + "/api/worker/result"

	log.Printf("Starting worker loop, polling %s every %v", pollURL, interval)

	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			log.Println("Worker loop stopped")
			return
		case <-ticker.C:
			if err := pollAndExecute(ctx, client, pollURL, resultURL); err != nil {
				log.Printf("Poll and execute failed: %v", err)
			}
		}
	}
}

// PollResponse represents the response from the orchestrator poll endpoint
type PollResponse struct {
	Task *engine.Node `json:"task"`
}

// ResultRequest represents the request payload for posting results
type ResultRequest struct {
	NodeID string `json:"nodeId"`
	Status string `json:"status"`
	Output string `json:"output,omitempty"`
	Error  string `json:"error,omitempty"`
}

// ResultResponse represents the response from the orchestrator result endpoint
type ResultResponse struct {
	Accepted bool `json:"accepted"`
}

// pollAndExecute performs one cycle of polling and task execution
func pollAndExecute(ctx context.Context, client *http.Client, pollURL, resultURL string) error {
	// Poll for tasks
	req, err := http.NewRequestWithContext(ctx, "GET", pollURL, nil)
	if err != nil {
		return fmt.Errorf("failed to create poll request: %w", err)
	}

	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("poll request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("poll request returned status %d", resp.StatusCode)
	}

	// Parse poll response
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("failed to read poll response: %w", err)
	}

	var pollResp PollResponse
	if err := json.Unmarshal(body, &pollResp); err != nil {
		return fmt.Errorf("failed to parse poll response: %w", err)
	}

	// Check if there's a task to execute
	if pollResp.Task == nil {
		log.Println("No task available")
		return nil
	}

	log.Printf("Received task: %s", pollResp.Task.ID)

	// Execute the task
	result := executeTask(ctx, pollResp.Task)

	// Post the result
	return postResult(ctx, client, resultURL, result)
}

// executeTask executes a task using the mock-agent adapter and returns the result
func executeTask(ctx context.Context, task *engine.Node) *protocol.WorkerResult {
	log.Printf("Executing task %s with agent %s", task.ID, task.Agent)

	// Create agent for execution
	agent, err := createAgent(task)
	if err != nil {
		log.Printf("Failed to create agent for task %s: %v", task.ID, err)
		return protocol.NewFailedResult(task.ID, fmt.Errorf("agent creation failed: %w", err))
	}
	defer agent.Close()

	// Extract the task content from the request
	var taskContent string
	if request, ok := task.Request["task"].(string); ok {
		taskContent = request
	} else if prompt, ok := task.Request["prompt"].(string); ok {
		taskContent = prompt
	} else {
		taskContent = "default task"
	}

	// Execute the task using the agent
	output, err := agent.Execute(ctx, taskContent)
	if err != nil {
		log.Printf("Task %s execution failed: %v", task.ID, err)
		return protocol.NewFailedResult(task.ID, err)
	}

	log.Printf("Task %s completed successfully", task.ID)
	return protocol.NewCompletedResult(task.ID, output)
}

// createAgent creates an agent based on the task configuration
func createAgent(task *engine.Node) (executor.Agent, error) {
	// For MVP simplification, always use mock-agent via stdio
	// TODO: Add proper agent configuration lookup in future versions

	switch task.Agent {
	case "mock-agent", "test-agent", "default":
		// Use the mock-agent binary via stdio
		return executor.NewStdioAgent("./bin/mock-agent")
	default:
		// For unknown agents, also use mock-agent as fallback
		log.Printf("Unknown agent type '%s', falling back to mock-agent", task.Agent)
		return executor.NewStdioAgent("./bin/mock-agent")
	}
}

// postResult posts the execution result to the orchestrator
func postResult(ctx context.Context, client *http.Client, resultURL string, result *protocol.WorkerResult) error {
	// Convert WorkerResult to ResultRequest
	reqData := ResultRequest{
		NodeID: result.NodeID,
		Status: result.Status,
		Output: result.Output,
		Error:  result.Error,
	}

	jsonData, err := json.Marshal(reqData)
	if err != nil {
		return fmt.Errorf("failed to marshal result: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST", resultURL, bytes.NewBuffer(jsonData))
	if err != nil {
		return fmt.Errorf("failed to create result request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("result request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("result request returned status %d", resp.StatusCode)
	}

	// Parse result response
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("failed to read result response: %w", err)
	}

	var resultResp ResultResponse
	if err := json.Unmarshal(body, &resultResp); err != nil {
		return fmt.Errorf("failed to parse result response: %w", err)
	}

	if !resultResp.Accepted {
		return fmt.Errorf("result was not accepted by orchestrator")
	}

	log.Printf("Result posted successfully for task %s", result.NodeID)
	return nil
}
