package testutil

import (
	"fmt"
	"net"
	"testing"
)

// FindAvailablePort finds an available port in the given range.
// It tries to bind to each port in the range and returns the first available one.
// If no port is available in the range, it fails the test.
func FindAvailablePort(t *testing.T, start, end int) int {
	for port := start; port <= end; port++ {
		// Try to bind to the port to see if it's available
		addr := fmt.Sprintf(":%d", port)
		listener, err := net.Listen("tcp", addr)
		if err == nil {
			listener.Close()
			return port
		}
	}
	t.Fatalf("No available port found in range %d-%d", start, end)
	return 0
}
