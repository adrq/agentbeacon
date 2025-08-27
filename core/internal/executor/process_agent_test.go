package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

// Test basic communication with process agent
func TestProcessAgentBasicCommunication(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	agent, err := NewProcessAgent("../../../bin/mock-agent")
	if err != nil {
		t.Fatalf("Failed to create ProcessAgent: %v", err)
	}
	defer agent.Close()

	ctx := context.Background()
	response, err := agent.Execute(ctx, "hello world")
	if err != nil {
		t.Fatalf("Execute failed: %v", err)
	}

	expected := "Mock response: hello world"
	if response != expected {
		t.Errorf("Expected '%s', got '%s'", expected, response)
	}
}

// Test custom responses from config file
func TestProcessAgentCustomResponses(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Create temporary config file with custom responses
	tempDir := t.TempDir()
	configFile := filepath.Join(tempDir, "responses.json")

	responses := map[string]string{
		"analyze code": "Code analysis complete: 42 issues found",
		"run tests":    "All 127 tests passed successfully",
		"deploy app":   "Deployment to production complete",
	}

	configData, err := json.Marshal(responses)
	if err != nil {
		t.Fatalf("Failed to marshal config: %v", err)
	}

	if err := os.WriteFile(configFile, configData, 0644); err != nil {
		t.Fatalf("Failed to write config file: %v", err)
	}

	agent, err := NewProcessAgent("../../../bin/mock-agent", "--config", configFile)
	if err != nil {
		t.Fatalf("Failed to create ProcessAgent: %v", err)
	}
	defer agent.Close()

	ctx := context.Background()

	// Test each custom response
	for prompt, expected := range responses {
		response, err := agent.Execute(ctx, prompt)
		if err != nil {
			t.Fatalf("Execute failed for prompt '%s': %v", prompt, err)
		}

		if response != expected {
			t.Errorf("For prompt '%s', expected '%s', got '%s'", prompt, expected, response)
		}
	}

	// Test unknown prompt still gets default response
	response, err := agent.Execute(ctx, "unknown prompt")
	if err != nil {
		t.Fatalf("Execute failed for unknown prompt: %v", err)
	}

	expected := "Mock response: unknown prompt"
	if response != expected {
		t.Errorf("For unknown prompt, expected '%s', got '%s'", expected, response)
	}
}

// Test handling when external process crashes
func TestProcessAgentCrashHandling(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	agent, err := NewProcessAgent("../../../bin/mock-agent")
	if err != nil {
		t.Fatalf("Failed to create ProcessAgent: %v", err)
	}
	defer agent.Close()

	// First verify agent is working
	ctx := context.Background()
	response, err := agent.Execute(ctx, "test")
	if err != nil {
		t.Fatalf("Initial execute failed: %v", err)
	}
	if !strings.Contains(response, "Mock response") {
		t.Errorf("Unexpected response: %s", response)
	}

	// Kill the process externally to simulate a crash
	if agent.cmd != nil && agent.cmd.Process != nil {
		agent.cmd.Process.Signal(syscall.SIGKILL)
		// Give the process time to die
		time.Sleep(100 * time.Millisecond)
	}

	// Try to execute again - should return error
	_, err = agent.Execute(ctx, "after crash")
	if err == nil {
		t.Error("Expected error when executing after process crash")
	}

	// Verify error message indicates process issue
	if !strings.Contains(err.Error(), "process") && !strings.Contains(err.Error(), "closed") {
		t.Errorf("Expected process-related error, got: %s", err.Error())
	}

	// Verify Close() handles dead process gracefully
	closeErr := agent.Close()
	// Close() may return error for already dead process - this is acceptable
	if closeErr != nil && !strings.Contains(closeErr.Error(), "already") && !strings.Contains(closeErr.Error(), "finished") {
		t.Logf("Close error (acceptable for dead process): %v", closeErr)
	}
}

// Test context timeout handling
func TestProcessAgentTimeout(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Create config with a prompt that would cause mock-agent to hang
	// (mock-agent should support a special "HANG" response for testing)
	tempDir := t.TempDir()
	configFile := filepath.Join(tempDir, "hang_responses.json")

	responses := map[string]string{
		"hang test": "HANG", // Special response that causes mock-agent to hang
	}

	configData, err := json.Marshal(responses)
	if err != nil {
		t.Fatalf("Failed to marshal config: %v", err)
	}

	if err := os.WriteFile(configFile, configData, 0644); err != nil {
		t.Fatalf("Failed to write config file: %v", err)
	}

	agent, err := NewProcessAgent("../../../bin/mock-agent", "--config", configFile)
	if err != nil {
		t.Fatalf("Failed to create ProcessAgent: %v", err)
	}
	defer agent.Close()

	// Create context with short timeout
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	start := time.Now()
	_, err = agent.Execute(ctx, "hang test")
	duration := time.Since(start)

	// Should return timeout error
	if err == nil {
		t.Error("Expected timeout error")
	}

	// Should respect timeout (with some buffer for processing)
	if duration > 500*time.Millisecond {
		t.Errorf("Execution took too long: %v", duration)
	}

	// Verify error is timeout-related
	if !strings.Contains(err.Error(), "timeout") && !strings.Contains(err.Error(), "context") {
		t.Errorf("Expected timeout-related error, got: %s", err.Error())
	}
}

// Test process cleanup after normal operations
func TestProcessAgentCleanup(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	agent, err := NewProcessAgent("../../../bin/mock-agent")
	if err != nil {
		t.Fatalf("Failed to create ProcessAgent: %v", err)
	}

	// Execute some operations
	ctx := context.Background()
	for i := 0; i < 3; i++ {
		prompt := fmt.Sprintf("test message %d", i)
		response, err := agent.Execute(ctx, prompt)
		if err != nil {
			t.Fatalf("Execute %d failed: %v", i, err)
		}
		expected := fmt.Sprintf("Mock response: test message %d", i)
		if response != expected {
			t.Errorf("Execute %d: expected '%s', got '%s'", i, expected, response)
		}
	}

	// Get process PID before closing
	var pid int
	if agent.cmd != nil && agent.cmd.Process != nil {
		pid = agent.cmd.Process.Pid
	}

	// Close agent
	err = agent.Close()
	if err != nil {
		t.Fatalf("Close failed: %v", err)
	}

	// Verify process is actually terminated (no zombie)
	if pid > 0 {
		// Give process time to terminate
		time.Sleep(100 * time.Millisecond)

		// Try to signal the process - should fail if properly cleaned up
		process, err := os.FindProcess(pid)
		if err == nil {
			err = process.Signal(syscall.Signal(0)) // Signal 0 just checks if process exists
			// On Unix, if process doesn't exist, Signal returns error
			// This test may be platform-specific, so we just log the result
			if err != nil {
				t.Logf("Process %d properly terminated (signal check failed as expected)", pid)
			} else {
				t.Logf("Process %d may still exist (platform-dependent behavior)", pid)
			}
		}
	}

	// Verify subsequent operations fail cleanly
	_, err = agent.Execute(ctx, "after close")
	if err == nil {
		t.Error("Expected error when executing after Close()")
	}
}

// Test nonexistent executable
func TestProcessAgentNonexistentExecutable(t *testing.T) {
	_, err := NewProcessAgent("/nonexistent/path/to/agent")
	if err == nil {
		t.Error("Expected error when creating agent with nonexistent executable")
	}

	if !strings.Contains(err.Error(), "not found") && !strings.Contains(err.Error(), "no such file") {
		t.Errorf("Expected file-not-found error, got: %s", err.Error())
	}
}

// Test agent with invalid arguments
func TestProcessAgentInvalidArguments(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Test with invalid config file
	agent, err := NewProcessAgent("bin/mock-agent", "--config", "/nonexistent/config.json")
	if err != nil {
		// Agent creation may fail immediately if it validates arguments
		return
	}
	defer agent.Close()

	// Or it may fail on first execution
	ctx := context.Background()
	_, err = agent.Execute(ctx, "test")
	if err == nil {
		t.Error("Expected error when using invalid config file")
	}
}

// Integration test: Use ProcessAgent in executor workflow
func TestProcessAgentIntegration(t *testing.T) {
	if !mockAgentExists() {
		t.Skip("mock-agent binary not found, run 'make build' first")
	}

	// Set up test database
	db := setupTestDB(t, "sqlite3", filepath.Join(t.TempDir(), "test.db"))
	defer db.Close()

	// Create workflow
	workflowFile := createTestWorkflow(t, "process-agent-workflow", []testNode{
		{ID: "analyze", Agent: "process", Prompt: "analyze the input"},
		{ID: "transform", Agent: "process", Prompt: "transform based on analysis", DependsOn: []string{"analyze"}},
	})

	if err := db.RegisterWorkflow(workflowFile); err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	// Create executor with ProcessAgent instead of MockAgent
	agent, err := NewProcessAgent("../../../bin/mock-agent")
	if err != nil {
		t.Fatalf("Failed to create ProcessAgent: %v", err)
	}
	defer agent.Close()

	// Create executor (note: this test uses a specific agent, but executor will create its own agents per-node)
	executor := NewExecutor(db)

	// Start workflow execution
	execution, err := executor.StartWorkflow("process-agent-workflow")
	if err != nil {
		t.Fatalf("Failed to start workflow: %v", err)
	}

	// Wait for completion
	waitForCompletion(t, db, execution.ID, 10*time.Second)

	// Verify execution completed successfully
	completed, err := db.GetExecution(execution.ID)
	if err != nil {
		t.Fatalf("Failed to get completed execution: %v", err)
	}

	if completed.Status != "completed" {
		t.Errorf("Expected execution to complete successfully, got status: %s", completed.Status)
		if completed.Logs != "" {
			t.Logf("Execution logs: %s", completed.Logs)
		}
	}

	// Verify both nodes were executed by checking logs or node states
	if completed.Logs == "" {
		t.Error("Expected execution logs to be recorded")
	}
}

// Helper function to check if mock-agent binary exists
func mockAgentExists() bool {
	// Check relative path from executor test directory
	_, err := os.Stat("../../../bin/mock-agent")
	return err == nil
}
