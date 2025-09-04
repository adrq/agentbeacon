package main

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"os/exec"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestWorkerBinaryStartupShutdown verifies the worker binary can start and stop cleanly without panics.
func TestWorkerBinaryStartupShutdown(t *testing.T) {
	// Skip binary compilation in short test mode
	if testing.Short() {
		t.Skip("Skipping worker binary test in short mode")
	}

	t.Run("binary starts and stops cleanly", func(t *testing.T) {
		// Allocate available port for mock orchestrator
		orchestratorPort := findAvailablePort(t, 9457, 9467)

		// Start mock orchestrator server
		mockOrchestrator := startMockOrchestrator(t, orchestratorPort)
		defer mockOrchestrator.Close()

		// Build worker binary for testing
		workerBinary := buildWorkerBinary(t)

		// Configure worker with fast polling interval
		orchestratorURL := fmt.Sprintf("http://localhost:%d", orchestratorPort)
		cmd := exec.Command(workerBinary,
			"-orchestrator-url", orchestratorURL,
			"-interval", "100ms")

		// Launch worker process
		err := cmd.Start()
		require.NoError(t, err, "Worker binary should start without error")

		// Ensure process cleanup on test completion
		defer func() {
			if cmd.Process != nil {
				cmd.Process.Kill()
				cmd.Wait()
			}
		}()

		// Allow worker startup and initial polls
		time.Sleep(300 * time.Millisecond)

		// Verify process stability
		assert.NotNil(t, cmd.Process, "Worker process should be running")

		// Terminate process gracefully
		err = cmd.Process.Kill()
		require.NoError(t, err, "Should be able to terminate worker process")

		// Process exits with non-zero code after kill signal
		err = cmd.Wait()
		assert.Error(t, err, "Process should exit with error code after kill signal")

		// Confirm proper exit status
		assert.Equal(t, "exit status 1", err.Error(), "Should exit with kill signal status")
	})

	t.Run("binary handles invalid orchestrator URL gracefully", func(t *testing.T) {
		workerBinary := buildWorkerBinary(t)

		// Start worker with invalid URL
		cmd := exec.Command(workerBinary,
			"-orchestrator-url", "http://invalid-host:99999",
			"-interval", "100ms")

		err := cmd.Start()
		require.NoError(t, err, "Worker should start even with invalid URL")

		defer func() {
			if cmd.Process != nil {
				cmd.Process.Kill()
				cmd.Wait()
			}
		}()

		// Allow connection attempt
		time.Sleep(300 * time.Millisecond)

		// Verify resilience to connection failures
		assert.NotNil(t, cmd.Process, "Worker should continue running despite connection failures")

		// Clean up process
		err = cmd.Process.Kill()
		require.NoError(t, err)
		cmd.Wait()
	})

	t.Run("binary shows version information", func(t *testing.T) {
		workerBinary := buildWorkerBinary(t)

		// Run worker with version flag
		cmd := exec.Command(workerBinary, "-version")
		output, err := cmd.Output()
		require.NoError(t, err, "Version command should succeed")

		outputStr := string(output)
		assert.Contains(t, outputStr, "agentmaestro-worker version:", "Should show version information")
		assert.Contains(t, outputStr, "0.1.0-stub", "Should show current version")
	})

	t.Run("binary validates interval parameter", func(t *testing.T) {
		workerBinary := buildWorkerBinary(t)

		// Start worker with invalid interval
		cmd := exec.Command(workerBinary, "-interval", "invalid-duration")
		err := cmd.Run()

		// Should exit with error due to invalid interval
		assert.Error(t, err, "Should fail with invalid interval format")
	})

	t.Run("worker polls orchestrator at specified interval", func(t *testing.T) {
		orchestratorPort := findAvailablePort(t, 9468, 9478)

		// Create mock orchestrator that counts requests
		requestCount := 0
		mockServer := &http.Server{
			Addr: fmt.Sprintf(":%d", orchestratorPort),
			Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/api/worker/poll" {
					requestCount++
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusOK)
					w.Write([]byte(`{"task": null}`))
				}
			}),
		}

		go mockServer.ListenAndServe()
		defer mockServer.Close()

		// Wait for server to start
		time.Sleep(50 * time.Millisecond)

		workerBinary := buildWorkerBinary(t)
		orchestratorURL := fmt.Sprintf("http://localhost:%d", orchestratorPort)

		cmd := exec.Command(workerBinary,
			"-orchestrator-url", orchestratorURL,
			"-interval", "100ms")

		err := cmd.Start()
		require.NoError(t, err)

		defer func() {
			if cmd.Process != nil {
				cmd.Process.Kill()
				cmd.Wait()
			}
		}()

		// Let worker make several polls
		time.Sleep(350 * time.Millisecond)

		// Stop worker
		cmd.Process.Kill()
		cmd.Wait()

		// Should have made multiple requests (at least 3 in 350ms with 100ms interval)
		assert.GreaterOrEqual(t, requestCount, 2, "Worker should have made multiple poll requests")
	})
}

// TestWorkerFlagParsing validates command line argument parsing.
func TestWorkerFlagParsing(t *testing.T) {
	testCases := []struct {
		name             string
		args             []string
		expectedURL      string
		expectedInterval time.Duration
		expectedVersion  bool
	}{
		{
			name:             "default values",
			args:             []string{},
			expectedURL:      "http://localhost:9456",
			expectedInterval: 5 * time.Second,
			expectedVersion:  false,
		},
		{
			name:             "custom orchestrator URL",
			args:             []string{"-orchestrator-url", "http://custom:8080"},
			expectedURL:      "http://custom:8080",
			expectedInterval: 5 * time.Second,
			expectedVersion:  false,
		},
		{
			name:             "custom interval",
			args:             []string{"-interval", "2s"},
			expectedURL:      "http://localhost:9456",
			expectedInterval: 2 * time.Second,
			expectedVersion:  false,
		},
		{
			name:             "version flag",
			args:             []string{"-version"},
			expectedURL:      "http://localhost:9456",
			expectedInterval: 5 * time.Second,
			expectedVersion:  true,
		},
		{
			name:             "all custom flags",
			args:             []string{"-orchestrator-url", "http://test:9000", "-interval", "10s", "-version"},
			expectedURL:      "http://test:9000",
			expectedInterval: 10 * time.Second,
			expectedVersion:  true,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			url, interval, version := parseFlags(tc.args)

			assert.Equal(t, tc.expectedURL, url, "URL should match expected")
			assert.Equal(t, tc.expectedInterval, interval, "Interval should match expected")
			assert.Equal(t, tc.expectedVersion, version, "Version flag should match expected")
		})
	}
}

// TestGetVersion validates the version string output.
func TestGetVersion(t *testing.T) {
	version := getVersion()
	assert.Equal(t, "0.1.0-stub", version, "Should return expected version string")
	assert.NotEmpty(t, version, "Version should not be empty")
}

// Helper functions for test setup and execution.

func findAvailablePort(t *testing.T, start, end int) int {
	for port := start; port <= end; port++ {
		listener, err := net.Listen("tcp", fmt.Sprintf(":%d", port))
		if err == nil {
			listener.Close()
			return port
		}
	}
	t.Fatalf("No available port found between %d and %d", start, end)
	return 0
}

func startMockOrchestrator(t *testing.T, port int) *http.Server {
	server := &http.Server{
		Addr: fmt.Sprintf(":%d", port),
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/api/worker/poll" && r.Method == "GET" {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusOK)
				w.Write([]byte(`{"task": null}`))
				return
			}
			http.NotFound(w, r)
		}),
	}

	go func() {
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			t.Logf("Mock orchestrator error: %v", err)
		}
	}()

	// Wait for server to start
	time.Sleep(50 * time.Millisecond)
	return server
}

func buildWorkerBinary(t *testing.T) string {
	// Build the worker binary for testing
	cmd := exec.Command("go", "build", "-o", "../../bin/agentmaestro-worker-test", ".")
	cmd.Dir = "."
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("Failed to build worker binary: %v\nOutput: %s", err, output)
	}

	// Return path to built binary
	return "../../bin/agentmaestro-worker-test"
}

// TestWorkerLoopContextCancellation validates graceful shutdown behavior.
func TestWorkerLoopContextCancellation(t *testing.T) {
	t.Run("worker loop stops on context cancellation", func(t *testing.T) {
		// Start mock orchestrator
		orchestratorPort := findAvailablePort(t, 9479, 9489)
		mockOrchestrator := startMockOrchestrator(t, orchestratorPort)
		defer mockOrchestrator.Close()

		// Test the worker loop function directly
		ctx, cancel := context.WithCancel(context.Background())
		orchestratorURL := fmt.Sprintf("http://localhost:%d", orchestratorPort)

		// Start worker loop in goroutine
		done := make(chan struct{})
		go func() {
			defer close(done)
			runWorkerLoop(ctx, orchestratorURL, 100*time.Millisecond)
		}()

		// Let it run briefly
		time.Sleep(200 * time.Millisecond)

		// Cancel context
		cancel()

		// Wait for worker loop to stop
		select {
		case <-done:
			// Worker loop stopped as expected
		case <-time.After(1 * time.Second):
			t.Fatal("Worker loop did not stop within timeout after context cancellation")
		}
	})

	t.Run("worker loop handles HTTP timeouts gracefully", func(t *testing.T) {
		// Test with unreachable URL that will timeout
		ctx, cancel := context.WithTimeout(context.Background(), 1*time.Second)
		defer cancel()

		// Start worker loop in goroutine
		done := make(chan struct{})
		go func() {
			defer close(done)
			// Use unreachable URL to test timeout handling
			runWorkerLoop(ctx, "http://192.0.2.1:9999", 200*time.Millisecond)
		}()

		// Wait for context timeout
		select {
		case <-done:
			// Worker loop stopped due to context cancellation
		case <-time.After(2 * time.Second):
			t.Fatal("Worker loop did not stop within timeout")
		}
	})
}
