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
	"context"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

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
	client := &http.Client{Timeout: 5 * time.Second}
	pollURL := orchestratorURL + "/api/worker/poll"

	// Reference protocol types to validate import linkage
	var _ protocol.Task

	log.Printf("Starting worker loop, polling %s every %v", pollURL, interval)

	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			log.Println("Worker loop stopped")
			return
		case <-ticker.C:
			req, err := http.NewRequestWithContext(ctx, "GET", pollURL, nil)
			if err != nil {
				log.Printf("Poll request creation failed: %v", err)
				continue
			}

			resp, err := client.Do(req)
			if err != nil {
				log.Printf("Poll failed: %v", err)
				continue
			}
			resp.Body.Close()
			log.Printf("Poll completed: no task available")
		}
	}
}
