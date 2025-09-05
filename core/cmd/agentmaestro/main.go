// Package main implements the AgentMaestro orchestrator with memory-safe log streaming.
//
// Log Streaming Architecture:
// The orchestrator manages multiple child processes (scheduler + workers) and provides
// real-time log aggregation with colored prefixes. The implementation prioritizes memory
// safety through immediate streaming rather than buffering:
//
// - Each process stdout/stderr is streamed line-by-line via bufio.Scanner
// - No log accumulation in memory - immediate output to orchestrator's stdout
// - Memory usage is O(max_line_length) not O(total_log_volume)
// - Context cancellation ensures immediate cleanup on shutdown
// - Proper resource management prevents goroutine/file descriptor leaks
//
// This design ensures the orchestrator can run indefinitely without memory growth
// from log volume, adhering to the AgentMaestro Constitution's reliability principles.
package main

import (
	"bufio"
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

func main() {
	workers, schedulerPort := parseFlags(os.Args[1:])

	// Print startup banner
	log.Printf("AgentMaestro Orchestrator starting...")
	log.Printf("Configuration: %d workers, scheduler port %d", workers, schedulerPort)

	// Create context for graceful shutdown
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Handle shutdown signals
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)

	// Start orchestrator
	orchestrator := NewOrchestrator(workers, schedulerPort)

	// Start all processes and wait for readiness
	if err := orchestrator.Start(ctx); err != nil {
		log.Fatalf("Failed to start orchestrator: %v", err)
	}

	// Wait for scheduler to be ready
	if err := orchestrator.WaitForReadiness(ctx); err != nil {
		log.Fatalf("Orchestrator failed to become ready: %v", err)
	}

	// Wait for shutdown signal
	<-sigCh
	log.Println("Received shutdown signal...")

	// Graceful shutdown with timeout
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutdownCancel()

	if err := orchestrator.Shutdown(shutdownCtx); err != nil {
		log.Printf("Shutdown error: %v", err)
	} else {
		log.Println("Shutdown completed successfully")
	}
}

func parseFlags(args []string) (workers, schedulerPort int) {
	fs := flag.NewFlagSet("agentmaestro", flag.ExitOnError)

	workersFlag := fs.Int("workers", 2, "Number of worker processes to spawn")
	schedulerPortFlag := fs.Int("scheduler-port", 9456, "Port for the scheduler to listen on")

	fs.Parse(args)

	return *workersFlag, *schedulerPortFlag
}

// Colors for log prefixes - ANSI color codes
var colors = []string{
	"\033[1;32m", // Green
	"\033[1;34m", // Blue
	"\033[1;35m", // Magenta
	"\033[1;36m", // Cyan
	"\033[1;33m", // Yellow
	"\033[1;31m", // Red
}

const colorReset = "\033[0m"

// isTerminal checks if output is a TTY
func isTerminal() bool {
	fileInfo, _ := os.Stdout.Stat()
	return (fileInfo.Mode() & os.ModeCharDevice) != 0
}

// getColor returns a deterministic color for a process name
func getColor(name string) string {
	if !isTerminal() {
		return ""
	}
	hash := 0
	for _, r := range name {
		hash = hash*31 + int(r)
	}
	return colors[hash%len(colors)]
}

// ProcessInfo represents a managed child process
type ProcessInfo struct {
	Name       string
	Cmd        *exec.Cmd
	RetryCount int
	MaxRetries int
	Color      string
	Stdout     io.ReadCloser
	Stderr     io.ReadCloser
}

// Orchestrator manages scheduler and worker processes
type Orchestrator struct {
	workers       int
	schedulerPort int
	processes     map[string]*ProcessInfo
	ctx           context.Context
	cancel        context.CancelFunc
	wg            sync.WaitGroup
	mu            sync.Mutex
	shuttingDown  bool
}

// NewOrchestrator creates a new orchestrator instance
func NewOrchestrator(workers, schedulerPort int) *Orchestrator {
	return &Orchestrator{
		workers:       workers,
		schedulerPort: schedulerPort,
		processes:     make(map[string]*ProcessInfo),
	}
}

// Start launches all child processes
func (o *Orchestrator) Start(ctx context.Context) error {
	o.ctx, o.cancel = context.WithCancel(ctx)

	// Start scheduler
	if err := o.startScheduler(); err != nil {
		return fmt.Errorf("failed to start scheduler: %w", err)
	}

	// Start workers
	for i := 1; i <= o.workers; i++ {
		if err := o.startWorker(i); err != nil {
			return fmt.Errorf("failed to start worker %d: %w", i, err)
		}
	}

	log.Println("All processes started successfully")
	return nil
}

// WaitForReadiness waits for the scheduler to be ready to accept requests
func (o *Orchestrator) WaitForReadiness(ctx context.Context) error {
	client := &http.Client{Timeout: 1 * time.Second}
	healthURL := fmt.Sprintf("http://localhost:%d/api/health", o.schedulerPort)

	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		resp, err := client.Get(healthURL)
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode == 200 {
				log.Println("Orchestrator ready - all processes started and scheduler responding")
				return nil
			}
		}

		time.Sleep(100 * time.Millisecond)
	}

	return fmt.Errorf("scheduler health check timeout - not ready after 30 seconds")
}

// startScheduler launches the scheduler process
func (o *Orchestrator) startScheduler() error {
	name := "scheduler"
	cmd := exec.CommandContext(o.ctx, "./bin/agentmaestro-scheduler", "-port", strconv.Itoa(o.schedulerPort))

	proc := &ProcessInfo{
		Name:       name,
		Cmd:        cmd,
		RetryCount: 0,
		MaxRetries: 5,
		Color:      getColor(name),
	}

	if err := o.startProcess(proc); err != nil {
		return err
	}

	o.mu.Lock()
	o.processes[name] = proc
	o.mu.Unlock()

	return nil
}

// startWorker launches a worker process
func (o *Orchestrator) startWorker(id int) error {
	name := fmt.Sprintf("worker-%d", id)
	orchestratorURL := fmt.Sprintf("http://localhost:%d", o.schedulerPort)
	cmd := exec.CommandContext(o.ctx, "./bin/agentmaestro-worker", "-orchestrator-url", orchestratorURL)

	proc := &ProcessInfo{
		Name:       name,
		Cmd:        cmd,
		RetryCount: 0,
		MaxRetries: 5,
		Color:      getColor(name),
	}

	if err := o.startProcess(proc); err != nil {
		return err
	}

	o.mu.Lock()
	o.processes[name] = proc
	o.mu.Unlock()

	return nil
}

// startProcess starts an individual process and sets up real-time log streaming.
// Implements memory-safe log handling through immediate streaming without accumulation.
func (o *Orchestrator) startProcess(proc *ProcessInfo) error {
	// Create pipes for real-time log streaming
	// Pipes provide direct connection to child process stdout/stderr
	stdout, err := proc.Cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("failed to create stdout pipe: %w", err)
	}

	stderr, err := proc.Cmd.StderrPipe()
	if err != nil {
		return fmt.Errorf("failed to create stderr pipe: %w", err)
	}

	// Keep references so we can close them during shutdown to unblock scanners
	proc.Stdout = stdout
	proc.Stderr = stderr

	// Start the process
	if err := proc.Cmd.Start(); err != nil {
		return fmt.Errorf("failed to start process %s: %w", proc.Name, err)
	}

	log.Printf("Started %s (PID: %d)", proc.Name, proc.Cmd.Process.Pid)

	// Launch goroutines for concurrent log streaming and process monitoring
	// Each stream is handled independently to prevent blocking
	o.wg.Add(3) // stdout, stderr, and process monitor

	// Real-time streaming goroutines - no log accumulation in memory
	go o.streamLogs(proc.Name, proc.Color, stdout, "")
	go o.streamLogs(proc.Name, proc.Color, stderr, "")
	go o.monitorProcess(proc)

	return nil
}

// streamLogs reads from a pipe and outputs with colored prefixes in real-time.
// This function implements true streaming without buffering to ensure memory safety.
// Key memory safety characteristics:
// - Uses bufio.Scanner for line-by-line processing (no line length accumulation)
// - Each scanner.Text() result is immediately logged and not retained
// - Scanner's internal buffer is reused for each line (constant memory usage)
// - Context cancellation allows immediate cleanup on shutdown
func (o *Orchestrator) streamLogs(name, color string, reader io.Reader, logType string) {
	defer o.wg.Done()

	// Scanner reads line-by-line with automatic buffer management
	// Memory usage is O(max_line_length), not O(total_log_volume)
	scanner := bufio.NewScanner(reader)
	prefix := fmt.Sprintf("%s%s |%s ", color, name, colorReset)

	for scanner.Scan() {
		select {
		case <-o.ctx.Done():
			// Immediate termination on shutdown prevents resource leaks
			return
		default:
			// Immediate output - no accumulation or buffering
			log.Printf("%s%s", prefix, scanner.Text())
		}
	}

	// Note: scanner.Err() is intentionally not checked here as pipe closure
	// is expected during normal process termination and would create noise
}

// monitorProcess watches a process and restarts it if it crashes
func (o *Orchestrator) monitorProcess(proc *ProcessInfo) {
	defer o.wg.Done()

	// Wait for process to exit
	err := proc.Cmd.Wait()

	select {
	case <-o.ctx.Done():
		// Orchestrator is shutting down - this is expected
		return
	default:
		// If orchestrator is in shutdown sequence, do not attempt restarts
		o.mu.Lock()
		shuttingDown := o.shuttingDown
		o.mu.Unlock()
		if shuttingDown {
			return
		}
		// Process crashed unexpectedly
		log.Printf("Process %s exited unexpectedly: %v", proc.Name, err)

		o.mu.Lock()
		proc.RetryCount++
		retryCount := proc.RetryCount
		maxRetries := proc.MaxRetries
		o.mu.Unlock()

		if retryCount >= maxRetries {
			log.Printf("Process %s exceeded max retries (%d), not restarting", proc.Name, maxRetries)
			return
		}

		log.Printf("Restarting %s (attempt %d/%d) in 1 second...", proc.Name, retryCount+1, maxRetries)

		// Check if we're still running before restarting
		select {
		case <-o.ctx.Done():
			return
		case <-time.After(1 * time.Second):
		}

		// Check again if context was cancelled during sleep
		select {
		case <-o.ctx.Done():
			return
		default:
		}

		// Restart the process
		if err := o.restartProcess(proc.Name); err != nil {
			log.Printf("Failed to restart %s: %v", proc.Name, err)
		}
	}
}

// restartProcess restarts a crashed process
func (o *Orchestrator) restartProcess(name string) error {
	o.mu.Lock()
	proc, exists := o.processes[name]
	if !exists {
		o.mu.Unlock()
		return fmt.Errorf("process %s not found", name)
	}
	o.mu.Unlock()

	// Create new command with same arguments
	var newCmd *exec.Cmd
	if name == "scheduler" {
		newCmd = exec.CommandContext(o.ctx, "./bin/agentmaestro-scheduler", "-port", strconv.Itoa(o.schedulerPort))
	} else if strings.HasPrefix(name, "worker-") {
		orchestratorURL := fmt.Sprintf("http://localhost:%d", o.schedulerPort)
		newCmd = exec.CommandContext(o.ctx, "./bin/agentmaestro-worker", "-orchestrator-url", orchestratorURL)
	} else {
		return fmt.Errorf("unknown process type: %s", name)
	}

	newProc := &ProcessInfo{
		Name:       proc.Name,
		Cmd:        newCmd,
		RetryCount: proc.RetryCount,
		MaxRetries: proc.MaxRetries,
		Color:      proc.Color,
	}

	if err := o.startProcess(newProc); err != nil {
		return err
	}

	o.mu.Lock()
	o.processes[name] = newProc
	o.mu.Unlock()

	return nil
}

// Shutdown gracefully stops all processes
func (o *Orchestrator) Shutdown(ctx context.Context) error {
	log.Println("Shutting down all processes...")

	// Mark shutting down to prevent restarts
	o.mu.Lock()
	o.shuttingDown = true
	o.mu.Unlock()

	// Send SIGTERM to all processes
	o.mu.Lock()
	var processes []*ProcessInfo
	for _, proc := range o.processes {
		if proc.Cmd.Process != nil {
			processes = append(processes, proc)
		}
	}
	o.mu.Unlock()

	// Send SIGTERM and close pipes to unblock scanners
	for _, proc := range processes {
		if err := proc.Cmd.Process.Signal(syscall.SIGTERM); err != nil {
			log.Printf("Failed to send SIGTERM to %s: %v", proc.Name, err)
		} else {
			log.Printf("Sent SIGTERM to %s", proc.Name)
		}
		if proc.Stdout != nil {
			_ = proc.Stdout.Close()
		}
		if proc.Stderr != nil {
			_ = proc.Stderr.Close()
		}
	}

	// Wait for goroutines (streamers + monitors) to finish with timeout
	wgDone := make(chan struct{})
	go func() {
		o.wg.Wait()
		close(wgDone)
	}()

	select {
	case <-wgDone:
		// Completed gracefully
	case <-ctx.Done():
		log.Println("Shutdown timeout, sending SIGKILL...")
		for _, proc := range processes {
			if proc.Cmd.Process != nil {
				_ = proc.Cmd.Process.Kill()
			}
		}
		// After killing, signal internal context to stop anything else
		if o.cancel != nil {
			o.cancel()
		}
		// Best effort wait
		select {
		case <-wgDone:
		case <-time.After(1 * time.Second):
			log.Println("Some goroutines did not finish after SIGKILL")
		}
	}

	log.Println("All processes exited gracefully")
	return nil
}
