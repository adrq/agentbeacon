package main

import (
	"context"
	"embed"
	"flag"
	"fmt"
	"io/fs"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/agentmaestro/agentmaestro/core/internal/api"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
)

//go:embed web/dist/*
var staticFiles embed.FS

func main() {
	port, driver, dsn := parseFlags(os.Args[1:])

	// Initialize database
	db, err := storage.Open(driver, dsn)
	if err != nil {
		log.Fatalf("Failed to initialize database: %v", err)
	}
	defer db.Close()

	// Create context for graceful shutdown
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Handle shutdown signals
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-sigCh
		log.Println("Shutting down server...")
		cancel()
	}()

	// Start server
	if err := startServer(ctx, port, driver, dsn, nil); err != nil && err != http.ErrServerClosed {
		log.Fatalf("Server error: %v", err)
	}
}

func parseFlags(args []string) (port, driver, dsn string) {
	fs := flag.NewFlagSet("agentmaestro", flag.ExitOnError)

	portFlag := fs.String("port", "9456", "Port to listen on")
	driverFlag := fs.String("driver", "sqlite3", "Database driver (sqlite3 or postgres)")
	dbFlag := fs.String("db", "", "Database connection string")

	fs.Parse(args)

	port = ":" + *portFlag
	driver = *driverFlag
	dsn = *dbFlag

	// Set default DSN if not provided
	if dsn == "" {
		if driver == "sqlite3" {
			dsn = storage.DefaultDBPath()
		} else {
			dsn = os.Getenv("DATABASE_URL")
		}
	}

	return port, driver, dsn
}

func startServer(ctx context.Context, addr, driver, dsn string, ready chan<- struct{}) error {
	// Initialize database
	db, err := storage.Open(driver, dsn)
	if err != nil {
		return fmt.Errorf("database initialization failed: %w", err)
	}
	defer db.Close()

	// Create HTTP server
	mux := http.NewServeMux()

	// Check if we're in development mode
	devMode := os.Getenv("DEV_MODE") == "1"

	if devMode {
		// In dev mode, redirect to Vite dev server
		mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/" || r.URL.Path == "/index.html" {
				http.Redirect(w, r, "http://localhost:5173", http.StatusTemporaryRedirect)
				return
			}
			// For other paths, also redirect to Vite dev server
			viteURL := "http://localhost:5173" + r.URL.Path
			http.Redirect(w, r, viteURL, http.StatusTemporaryRedirect)
		})
	} else {
		// Production mode - serve embedded static files
		staticFS, err := fs.Sub(staticFiles, "web/dist")
		if err != nil {
			// If embedded files don't exist, create a fallback handler
			mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/" {
					w.WriteHeader(http.StatusNotFound)
					w.Write([]byte("Static files not found - run 'make build' to build frontend"))
					return
				}
				w.WriteHeader(http.StatusNotFound)
			})
		} else {
			fileServer := http.FileServer(http.FS(staticFS))
			mux.Handle("/", fileServer)
		}
	}

	// Add REST API endpoints
	restHandler := api.NewRestHandler(db)
	mux.Handle("/api/", restHandler)

	server := &http.Server{
		Addr:    addr,
		Handler: mux,
	}

	// Start server in goroutine
	serverError := make(chan error, 1)
	go func() {
		if devMode {
			log.Printf("Starting server on %s (DEV_MODE: redirecting to Vite dev server at localhost:5173)", addr)
		} else {
			log.Printf("Starting server on %s (PRODUCTION: serving embedded static files)", addr)
		}
		err := server.ListenAndServe()
		if err != nil {
			serverError <- err
		}
	}()

	// Signal ready immediately - if there's an error, it will come through serverError
	if ready != nil {
		go func() {
			// Small delay to let server start binding to port
			time.Sleep(5 * time.Millisecond)
			close(ready)
		}()
	}

	// Wait for context cancellation or server error
	select {
	case <-ctx.Done():
		// Graceful shutdown
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		return server.Shutdown(shutdownCtx)
	case err := <-serverError:
		return err
	}
}
