package main

import (
	"context"
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestServerStartsOnSpecifiedPort(t *testing.T) {
	// Use a random available port for testing
	listener, err := net.Listen("tcp", ":0")
	require.NoError(t, err)
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()

	// Create temporary database for test
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	// Start server in goroutine
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	serverReady := make(chan struct{})
	serverError := make(chan error, 1)

	go func() {
		err := startServer(ctx, fmt.Sprintf(":%d", port), "sqlite3", dbPath, serverReady)
		if err != nil {
			serverError <- err
		}
	}()

	// Wait for server to be ready or timeout
	select {
	case <-serverReady:
		// Server started successfully
	case err := <-serverError:
		t.Fatalf("Server failed to start: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("Server failed to start within timeout")
	}

	// Test that server is responding on the correct port
	resp, err := http.Get(fmt.Sprintf("http://localhost:%d/", port))
	require.NoError(t, err)
	defer resp.Body.Close()

	// Server should respond (even if 404 for now since static files don't exist)
	assert.True(t, resp.StatusCode == 200 || resp.StatusCode == 404)
}

func TestServerServesStaticFilesAtRoot(t *testing.T) {
	// Use a random available port for testing
	listener, err := net.Listen("tcp", ":0")
	require.NoError(t, err)
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()

	// Create temporary database for test
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	// Start server in goroutine
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	serverReady := make(chan struct{})
	serverError := make(chan error, 1)

	go func() {
		err := startServer(ctx, fmt.Sprintf(":%d", port), "sqlite3", dbPath, serverReady)
		if err != nil {
			serverError <- err
		}
	}()

	// Wait for server to be ready
	select {
	case <-serverReady:
	case err := <-serverError:
		t.Fatalf("Server failed to start: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("Server failed to start within timeout")
	}

	// Test root path serves static files
	resp, err := http.Get(fmt.Sprintf("http://localhost:%d/", port))
	require.NoError(t, err)
	defer resp.Body.Close()

	// Should attempt to serve static files (may be 404 if files don't exist, but not 500)
	assert.NotEqual(t, 500, resp.StatusCode, "Server should not return 500 for static file requests")

	// Test static file path
	resp2, err := http.Get(fmt.Sprintf("http://localhost:%d/index.html", port))
	require.NoError(t, err)
	defer resp2.Body.Close()

	// Should attempt to serve static files
	assert.NotEqual(t, 500, resp2.StatusCode, "Server should not return 500 for static file requests")
}

func TestServerRespondsToAPIEndpoints(t *testing.T) {
	// Use a random available port for testing
	listener, err := net.Listen("tcp", ":0")
	require.NoError(t, err)
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()

	// Create temporary database for test
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	// Start server in goroutine
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	serverReady := make(chan struct{})
	serverError := make(chan error, 1)

	go func() {
		err := startServer(ctx, fmt.Sprintf(":%d", port), "sqlite3", dbPath, serverReady)
		if err != nil {
			serverError <- err
		}
	}()

	// Wait for server to be ready
	select {
	case <-serverReady:
	case err := <-serverError:
		t.Fatalf("Server failed to start: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("Server failed to start within timeout")
	}

	// Test /api/workflows endpoint exists
	resp, err := http.Get(fmt.Sprintf("http://localhost:%d/api/workflows", port))
	require.NoError(t, err)
	defer resp.Body.Close()

	// Should respond to workflows endpoint
	assert.Equal(t, 200, resp.StatusCode, "Workflows endpoint should return 200 OK")

	// Test /api/configs endpoint exists
	resp2, err := http.Get(fmt.Sprintf("http://localhost:%d/api/configs", port))
	require.NoError(t, err)
	defer resp2.Body.Close()

	// Should respond to configs endpoint
	assert.Equal(t, 200, resp2.StatusCode, "Configs endpoint should return 200 OK")
}

func TestGracefulShutdownHandling(t *testing.T) {
	// Use a random available port for testing
	listener, err := net.Listen("tcp", ":0")
	require.NoError(t, err)
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()

	// Create temporary database for test
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	// Start server in goroutine
	ctx, cancel := context.WithCancel(context.Background())

	serverReady := make(chan struct{})
	serverError := make(chan error, 1)
	serverDone := make(chan struct{})

	go func() {
		defer close(serverDone)
		err := startServer(ctx, fmt.Sprintf(":%d", port), "sqlite3", dbPath, serverReady)
		if err != nil && err != http.ErrServerClosed {
			serverError <- err
		}
	}()

	// Wait for server to be ready
	select {
	case <-serverReady:
	case err := <-serverError:
		t.Fatalf("Server failed to start: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("Server failed to start within timeout")
	}

	// Verify server is running
	resp, err := http.Get(fmt.Sprintf("http://localhost:%d/", port))
	require.NoError(t, err)
	resp.Body.Close()

	// Cancel context to trigger graceful shutdown
	cancel()

	// Wait for server to shut down
	select {
	case <-serverDone:
		// Server shut down successfully
	case err := <-serverError:
		t.Fatalf("Server error during shutdown: %v", err)
	case <-time.After(10 * time.Second):
		t.Fatal("Server failed to shut down within timeout")
	}

	// Verify server is no longer accepting connections
	_, err = http.Get(fmt.Sprintf("http://localhost:%d/", port))
	assert.Error(t, err, "Server should not accept connections after shutdown")
}

func TestFlagParsing(t *testing.T) {
	tests := []struct {
		name     string
		args     []string
		wantPort string
		wantDB   string
		wantDrv  string
	}{
		{
			name:     "default flags",
			args:     []string{},
			wantPort: ":9456",
			wantDB:   "", // Will use default path
			wantDrv:  "sqlite3",
		},
		{
			name:     "custom port",
			args:     []string{"-port", "8080"},
			wantPort: ":8080",
			wantDB:   "",
			wantDrv:  "sqlite3",
		},
		{
			name:     "custom database",
			args:     []string{"-db", "/tmp/test.db"},
			wantPort: ":9456",
			wantDB:   "/tmp/test.db",
			wantDrv:  "sqlite3",
		},
		{
			name:     "postgres driver",
			args:     []string{"-driver", "postgres", "-db", "postgres://user:pass@localhost/db"},
			wantPort: ":9456",
			wantDB:   "postgres://user:pass@localhost/db",
			wantDrv:  "postgres",
		},
		{
			name:     "all custom flags",
			args:     []string{"-port", "3000", "-driver", "postgres", "-db", "postgres://test"},
			wantPort: ":3000",
			wantDB:   "postgres://test",
			wantDrv:  "postgres",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Reset flags for each test
			flag.CommandLine = flag.NewFlagSet(os.Args[0], flag.ExitOnError)

			port, driver, dbPath := parseFlags(tt.args)

			assert.Equal(t, tt.wantPort, port)
			assert.Equal(t, tt.wantDrv, driver)
			if tt.wantDB != "" {
				assert.Equal(t, tt.wantDB, dbPath)
			}
		})
	}
}

func TestServerHandlesInvalidDatabaseConfig(t *testing.T) {
	// Use a random available port for testing
	listener, err := net.Listen("tcp", ":0")
	require.NoError(t, err)
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()

	// Start server with invalid database config
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	serverReady := make(chan struct{})
	serverError := make(chan error, 1)

	go func() {
		// Use invalid database path (directory that can't be created)
		invalidPath := "/invalid/path/that/cannot/be/created.db"
		err := startServer(ctx, fmt.Sprintf(":%d", port), "sqlite3", invalidPath, serverReady)
		if err != nil {
			serverError <- err
		}
	}()

	// Server should fail to start due to invalid database config
	select {
	case <-serverReady:
		t.Fatal("Server should not start with invalid database config")
	case err := <-serverError:
		assert.Error(t, err, "Server should return error for invalid database config")
		assert.Contains(t, err.Error(), "database", "Error should mention database issue")
	case <-time.After(5 * time.Second):
		t.Fatal("Test timed out waiting for server error")
	}
}

func TestServerHandlesPortInUse(t *testing.T) {
	// Use a random available port for testing
	listener, err := net.Listen("tcp", ":0")
	require.NoError(t, err)
	port := listener.Addr().(*net.TCPAddr).Port
	// Keep listener open to block the port

	// Create temporary database for test
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	// Try to start server on the blocked port
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	serverReady := make(chan struct{})
	serverError := make(chan error, 1)

	go func() {
		err := startServer(ctx, fmt.Sprintf(":%d", port), "sqlite3", dbPath, serverReady)
		if err != nil {
			serverError <- err
		}
	}()

	// Server should fail to start due to port being in use
	select {
	case <-serverReady:
		t.Fatal("Server should not start when port is in use")
	case err := <-serverError:
		assert.Error(t, err, "Server should return error when port is in use")
	case <-time.After(5 * time.Second):
		t.Fatal("Test timed out waiting for server error")
	}

	listener.Close()
}
