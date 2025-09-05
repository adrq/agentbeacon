package main

import (
	"context"
	"fmt"
	"net/http"
	"testing"
	"time"
)

func TestSchedulerIntegration(t *testing.T) {
	// Use a unique port to avoid conflicts
	port := ":19456"
	ready := make(chan struct{})

	// Create a context that we can cancel to stop the server
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Start server in background
	go func() {
		err := startServer(ctx, port, "sqlite3", ":memory:", ready)
		if err != nil && err != http.ErrServerClosed {
			t.Errorf("Server error: %v", err)
		}
	}()

	// Wait for server to be ready
	select {
	case <-ready:
		// Server is ready
	case <-time.After(5 * time.Second):
		t.Fatal("Server did not start within 5 seconds")
	}

	// Give the server a moment to fully bind to port
	time.Sleep(100 * time.Millisecond)

	// Test basic endpoints
	baseURL := fmt.Sprintf("http://localhost%s", port)

	tests := []struct {
		name       string
		path       string
		wantStatus int
	}{
		{
			name:       "root path",
			path:       "/",
			wantStatus: 200, // Should serve index.html
		},
		{
			name:       "agent card",
			path:       "/.well-known/agent-card.json",
			wantStatus: 200,
		},
		{
			name:       "api health (if exists)",
			path:       "/api/health",
			wantStatus: 200, // Assuming health endpoint exists
		},
	}

	client := &http.Client{Timeout: 2 * time.Second}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			url := baseURL + tt.path
			resp, err := client.Get(url)
			if err != nil {
				t.Fatalf("Failed to make request to %s: %v", url, err)
			}
			defer resp.Body.Close()

			if resp.StatusCode != tt.wantStatus {
				t.Errorf("GET %s returned status %d, want %d", tt.path, resp.StatusCode, tt.wantStatus)
			}
		})
	}

	// Stop the server
	cancel()

	// Give server time to shutdown gracefully
	time.Sleep(100 * time.Millisecond)
}
