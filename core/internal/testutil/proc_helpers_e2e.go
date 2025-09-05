package testutil

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

// TestProcess represents a spawned external binary used in E2E tests.
// Exported so other packages (api tests) can reference it when the e2e tag is enabled.
type TestProcess struct {
	Cmd     *exec.Cmd
	Port    int
	BaseURL string
	stdout  bytes.Buffer
	stderr  bytes.Buffer
}

// Stop terminates the process.
func (p *TestProcess) Stop() {
	if p == nil || p.Cmd == nil || p.Cmd.Process == nil {
		return
	}
	_ = p.Cmd.Process.Kill()
	done := make(chan struct{})
	go func() { p.Cmd.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
	}
}

// Logs returns combined stdout/stderr (for debugging on failure).
func (p *TestProcess) Logs() string {
	return fmt.Sprintf("STDOUT:\n%s\nSTDERR:\n%s", p.stdout.String(), p.stderr.String())
}

// StartAgentMaestroBinary launches the real agentmaestro-scheduler binary.
func StartAgentMaestroBinary(t *testing.T, port int, dbFile string) *TestProcess {
	t.Helper()
	bin := resolveBinaryPath(t, "agentmaestro-scheduler")
	cmd := exec.Command(bin, "-port", strconv.Itoa(port), "-driver", "sqlite3", "-db", dbFile)
	// Run from repo root so relative paths like examples/agents.yaml and ./bin/mock-agent resolve
	cmd.Dir = filepath.Dir(filepath.Dir(bin))

	proc := &TestProcess{Cmd: cmd, Port: port, BaseURL: fmt.Sprintf("http://localhost:%d", port)}
	cmd.Stdout = &proc.stdout
	cmd.Stderr = &proc.stderr

	if err := cmd.Start(); err != nil {
		t.Fatalf("failed to start agentmaestro: %v", err)
	}
	t.Cleanup(proc.Stop)

	waitForHTTP(t, proc.BaseURL+"/api/health", 20*time.Second, func(resp *http.Response, err error) bool {
		if err != nil {
			return false
		}
		return resp.StatusCode == http.StatusOK
	}, func() string { return proc.Logs() })

	return proc
}

// StartAgentMaestroBinaryWith launches the agentmaestro-scheduler binary with specified driver and dsn.
func StartAgentMaestroBinaryWith(t *testing.T, port int, driver, dsn string) *TestProcess {
	t.Helper()
	bin := resolveBinaryPath(t, "agentmaestro-scheduler")
	cmd := exec.Command(bin, "-port", strconv.Itoa(port), "-driver", driver, "-db", dsn)
	// Run from repo root so relative paths resolve
	cmd.Dir = filepath.Dir(filepath.Dir(bin))

	proc := &TestProcess{Cmd: cmd, Port: port, BaseURL: fmt.Sprintf("http://localhost:%d", port)}
	cmd.Stdout = &proc.stdout
	cmd.Stderr = &proc.stderr

	if err := cmd.Start(); err != nil {
		t.Fatalf("failed to start agentmaestro: %v", err)
	}
	t.Cleanup(proc.Stop)

	waitForHTTP(t, proc.BaseURL+"/api/health", 20*time.Second, func(resp *http.Response, err error) bool {
		if err != nil {
			return false
		}
		return resp.StatusCode == http.StatusOK
	}, func() string { return proc.Logs() })

	return proc
}

// StartMockAgentBinary launches the mock-agent in A2A mode.
func StartMockAgentBinary(t *testing.T, port int) *TestProcess {
	t.Helper()
	bin := resolveBinaryPath(t, "mock-agent")
	cmd := exec.Command(bin, "--mode", "a2a", "--port", strconv.Itoa(port))

	proc := &TestProcess{Cmd: cmd, Port: port, BaseURL: fmt.Sprintf("http://localhost:%d", port)}
	cmd.Stdout = &proc.stdout
	cmd.Stderr = &proc.stderr

	if err := cmd.Start(); err != nil {
		t.Fatalf("failed to start mock-agent: %v", err)
	}
	t.Cleanup(proc.Stop)

	waitForHTTP(t, proc.BaseURL+"/.well-known/agent-card.json", 10*time.Second, func(resp *http.Response, err error) bool {
		if err != nil {
			return false
		}
		return resp.StatusCode == http.StatusOK
	}, func() string { return proc.Logs() })

	return proc
}

// waitForHTTP polls an endpoint until predicate passes or timeout.
func waitForHTTP(t *testing.T, url string, timeout time.Duration, predicate func(*http.Response, error) bool, debug func() string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	ticker := time.NewTicker(150 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			t.Fatalf("timeout waiting for %s. Logs:\n%s", url, debug())
		case <-ticker.C:
			resp, err := http.Get(url)
			if resp != nil {
				io.Copy(io.Discard, resp.Body)
				resp.Body.Close()
			}
			if predicate(resp, err) {
				return
			}
		}
	}
}

// resolveBinaryPath attempts to locate the built binary from any package working directory.
func resolveBinaryPath(t *testing.T, name string) string {
	t.Helper()
	// try upwards from current dir looking for go.mod or bin/<name>
	dir, _ := os.Getwd()
	tried := []string{}
	for i := 0; i < 6 && dir != "/"; i++ {
		candidate := filepath.Join(dir, "bin", name)
		tried = append(tried, candidate)
		if st, err := os.Stat(candidate); err == nil && !st.IsDir() {
			return candidate
		}
		// stop early if go.mod found (root) and bin not present
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			break
		}
		dir = filepath.Dir(dir)
	}
	// fallback to relative path (maybe test invoked from root)
	fallback := filepath.Clean("bin/" + name)
	if st, err := os.Stat(fallback); err == nil && !st.IsDir() {
		return fallback
	}
	t.Fatalf("unable to locate binary %s; tried: %v. Did you run 'make test-deps'?", name, tried)
	return "" // unreachable
}
