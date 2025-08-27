package executor

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os/exec"
	"strings"
	"time"
)

// Agent defines the interface for workflow execution agents
type Agent interface {
	Execute(ctx context.Context, prompt string) (string, error)
	Close() error
}

// ProcessAgent implements Agent interface by spawning external processes
type ProcessAgent struct {
	path   string
	args   []string
	cmd    *exec.Cmd
	stdin  io.WriteCloser
	stdout *bufio.Reader
}

// NewProcessAgent creates a new process-based agent
func NewProcessAgent(path string, args ...string) (*ProcessAgent, error) {
	// Verify executable exists
	if _, err := exec.LookPath(path); err != nil {
		return nil, fmt.Errorf("executable not found: %s", path)
	}

	agent := &ProcessAgent{
		path: path,
		args: args,
	}

	if err := agent.start(); err != nil {
		return nil, fmt.Errorf("failed to start process: %w", err)
	}

	return agent, nil
}

// start initializes the external process
func (p *ProcessAgent) start() error {
	p.cmd = exec.Command(p.path, p.args...)

	// Set up stdin pipe
	stdin, err := p.cmd.StdinPipe()
	if err != nil {
		return fmt.Errorf("failed to create stdin pipe: %w", err)
	}
	p.stdin = stdin

	// Set up stdout pipe
	stdout, err := p.cmd.StdoutPipe()
	if err != nil {
		stdin.Close()
		return fmt.Errorf("failed to create stdout pipe: %w", err)
	}
	p.stdout = bufio.NewReader(stdout)

	// Start the process
	if err := p.cmd.Start(); err != nil {
		stdin.Close()
		stdout.Close()
		return fmt.Errorf("failed to start process: %w", err)
	}

	return nil
}

// Execute sends a prompt to the external process and returns the response
func (p *ProcessAgent) Execute(ctx context.Context, prompt string) (string, error) {
	if p.cmd == nil || p.stdin == nil || p.stdout == nil {
		return "", fmt.Errorf("process not initialized or already closed")
	}

	// Check if process is still alive
	select {
	case <-ctx.Done():
		return "", ctx.Err()
	default:
	}

	// Send prompt to stdin
	_, err := fmt.Fprintln(p.stdin, prompt)
	if err != nil {
		return "", fmt.Errorf("failed to send prompt to process: %w", err)
	}

	// Read response from stdout with timeout
	responseChan := make(chan string, 1)
	errorChan := make(chan error, 1)

	go func() {
		response, err := p.stdout.ReadString('\n')
		if err != nil {
			errorChan <- fmt.Errorf("failed to read response from process: %w", err)
			return
		}
		responseChan <- response
	}()

	// Wait for response or timeout
	select {
	case response := <-responseChan:
		return strings.TrimSpace(response), nil
	case err := <-errorChan:
		return "", err
	case <-ctx.Done():
		return "", ctx.Err()
	}
}

// Close terminates the external process and cleans up resources
func (p *ProcessAgent) Close() error {
	var lastErr error

	// Close stdin to signal process to exit gracefully
	if p.stdin != nil {
		if err := p.stdin.Close(); err != nil {
			lastErr = err
		}
		p.stdin = nil
	}

	// Wait for process to exit or force kill it
	if p.cmd != nil && p.cmd.Process != nil {
		// Try graceful shutdown first
		done := make(chan error, 1)
		go func() {
			done <- p.cmd.Wait()
		}()

		select {
		case err := <-done:
			// Process exited
			if err != nil && !strings.Contains(err.Error(), "signal: killed") {
				lastErr = err
			}
		case <-time.After(100 * time.Millisecond):
			// Force kill if process doesn't exit gracefully
			if err := p.cmd.Process.Kill(); err != nil && !strings.Contains(err.Error(), "already finished") {
				lastErr = err
			}
			<-done // Wait for Wait() to return
		}
		p.cmd = nil
	}

	p.stdout = nil
	return lastErr
}
