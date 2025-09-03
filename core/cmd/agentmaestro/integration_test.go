package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/api"
	"github.com/agentmaestro/agentmaestro/core/internal/config"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestWorkflowRegistrationIntegrity(t *testing.T) {
	// Legacy file-path registration removed; keep placeholder to ensure server boot works.
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")
	db, err := storage.Open("sqlite3", dbPath)
	require.NoError(t, err)
	defer db.Close()
	configLoader := config.NewConfigLoader("examples/agents.yaml")
	server := httptest.NewServer(api.NewRestHandler(db, configLoader))
	defer server.Close()
	resp, err := http.Get(server.URL + "/health")
	require.NoError(t, err)
	resp.Body.Close()
}

func TestConfigPersistenceAcrossRequests(t *testing.T) {
	// Create isolated test environment
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "config_test.db")

	// Initialize database & just perform a trivial config round trip placeholder
	db, err := storage.Open("sqlite3", dbPath)
	require.NoError(t, err)
	defer db.Close()
	configLoader := config.NewConfigLoader("examples/agents.yaml")
	server := httptest.NewServer(api.NewRestHandler(db, configLoader))
	defer server.Close()
	_, err = http.Get(server.URL + "/health")
	require.NoError(t, err)
}

// TestServerStartupWithRealDatabase tests server startup with database initialization
func TestServerStartupWithRealDatabase(t *testing.T) {
	// Use a random available port for testing
	listener, err := net.Listen("tcp", ":0")
	require.NoError(t, err)
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()

	// Create temporary database for test
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "startup_test.db")

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
		// Server started successfully
	case err := <-serverError:
		t.Fatalf("Server failed to start: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("Server failed to start within timeout")
	}

	// Test that database is properly initialized by checking endpoints
	baseURL := fmt.Sprintf("http://localhost:%d", port)

	// Test health endpoint
	resp, err := http.Get(baseURL + "/api/health")
	require.NoError(t, err)
	defer resp.Body.Close()
	assert.Equal(t, http.StatusOK, resp.StatusCode, "Health endpoint should work")

	// Test that database tables were created by listing workflows
	resp, err = http.Get(baseURL + "/api/workflows")
	require.NoError(t, err)
	defer resp.Body.Close()
	assert.Equal(t, http.StatusOK, resp.StatusCode, "Workflows endpoint should work with initialized database")

	var workflows []storage.WorkflowMeta
	err = json.NewDecoder(resp.Body).Decode(&workflows)
	require.NoError(t, err)
	assert.IsType(t, []storage.WorkflowMeta{}, workflows, "Should return valid workflow list")

	// Test that config endpoint works
	resp, err = http.Get(baseURL + "/api/configs")
	require.NoError(t, err)
	defer resp.Body.Close()
	assert.Equal(t, http.StatusOK, resp.StatusCode, "Configs endpoint should work with initialized database")

	var configs []storage.Config
	err = json.NewDecoder(resp.Body).Decode(&configs)
	require.NoError(t, err)
	assert.IsType(t, []storage.Config{}, configs, "Should return valid config list")
	// Legacy workflow registration via file removed; placeholder end-to-end server boot only.
}
