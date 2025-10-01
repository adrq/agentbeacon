.PHONY: build build-go build-rust-workspace test test-go test-nocache test-nocache-go test-sqlite test-postgres clean deps lint fmt build-all dev-tools pre-commit dev npm-install test-deps test-deps-go build-worker build-worker-go build-scheduler install-bins test-int

# Default target
all: build

# Ensure npm dependencies are installed
npm-install:
	@echo "Installing npm dependencies..."
	cd web && npm install

# Build frontend first, then backend
build-frontend: npm-install
	@echo "Building frontend..."
	cd web && npm run build

# Build only test dependencies (Go - mock-agent and embedded files)
test-deps-go: npm-install
	@echo "Building Go test dependencies..."
	@mkdir -p bin
	@echo "Building frontend for embedded files..."
	cd web && npm run build
	@echo "Building agentmaestro binary for tests..."
	go build -o bin/agentmaestro ./core/cmd/agentmaestro
	@echo "Building agentmaestro-scheduler for tests..."
	@cp -r core/cmd/agentmaestro/web core/cmd/agentmaestro-scheduler/ 2>/dev/null || true
	go build -o bin/agentmaestro-scheduler ./core/cmd/agentmaestro-scheduler
	@echo "Building agentmaestro-worker for tests..."
	go build -o bin/agentmaestro-worker ./core/cmd/agentmaestro-worker

# Build Go binaries (includes frontend)
build-go: build-frontend
	@echo "Building Go binaries..."
	@mkdir -p bin
	go build -o bin/agentmaestro ./core/cmd/agentmaestro
	@echo "Building agentmaestro-scheduler..."
	@cp -r core/cmd/agentmaestro/web core/cmd/agentmaestro-scheduler/ 2>/dev/null || true
	go build -o bin/agentmaestro-scheduler ./core/cmd/agentmaestro-scheduler
	@echo "Building agentmaestro-worker..."
	go build -o bin/agentmaestro-worker ./core/cmd/agentmaestro-worker

# Build only the Go worker binary
build-worker-go:
	@echo "Building Go agentmaestro-worker..."
	@mkdir -p bin
	go build -o bin/agentmaestro-worker ./core/cmd/agentmaestro-worker

# Run Go tests
test-go: test-deps-go
	@echo "Running Go tests..."
	go test ./... -v

# Run Go tests without cache
test-nocache-go: test-deps-go
	@echo "Running Go tests without cache..."
	go test -count=1 ./... -v

# Run A2A integration tests (Go)
test-e2e: test-deps-go
	@echo "Running E2E (A2A) tests with real binaries..."
	go test -tags e2e -v ./core/internal/api -run TestE2E

# ==============================================================================
# Rust Build Targets (Default)
# ==============================================================================

# Build Rust binaries and install to bin/
build: install-bins

# Build all Rust workspace binaries in release mode
build-rust-workspace:
	@echo "Building Rust workspace..."
	cargo build --release

# Install Rust binaries to bin/ directory
install-bins: build-rust-workspace
	@echo "Installing Rust binaries to bin/..."
	@mkdir -p bin
	cp target/release/orchestrator bin/agentmaestro
	cp target/release/agentmaestro-scheduler bin/
	cp target/release/agentmaestro-worker bin/
	@echo "Rust binaries installed to bin/"

# Build only the Rust scheduler binary
build-scheduler:
	@echo "Building Rust scheduler..."
	cargo build --release --bin agentmaestro-scheduler
	@mkdir -p bin
	cp target/release/agentmaestro-scheduler bin/

# Build only the Rust worker binary
build-worker:
	@echo "Building Rust worker..."
	cargo build --release --bin agentmaestro-worker
	@mkdir -p bin
	cp target/release/agentmaestro-worker bin/

# Run Rust unit tests with both SQLite and PostgreSQL
test:
	@echo "=== Running Rust tests with SQLite ==="
	cargo test -- test-threads=1
	@echo ""
	@echo "=== Running Rust tests with PostgreSQL ==="
	DATABASE_URL=postgres://postgres:postgres@127.0.0.1/agentmaestro_test cargo test -- --test-threads=1
	@echo ""
	@echo "✅ All tests passed with both backends"

# Run Rust tests without cache (both backends)
test-nocache:
	@echo "=== Running Rust tests with SQLite (no cache) ==="
	cargo test -- --nocapture
	@echo ""
	@echo "=== Running Rust tests with PostgreSQL (no cache) ==="
	DATABASE_URL=postgres://postgres:postgres@127.0.0.1/agentmaestro_test cargo test -- --nocapture
	@echo ""
	@echo "✅ All tests passed with both backends"

# Run Rust tests with SQLite only
test-sqlite:
	@echo "Running Rust tests with SQLite..."
	cargo test

# Run Rust tests with PostgreSQL only
test-postgres:
	@echo "Running Rust tests with PostgreSQL..."
	DATABASE_URL=postgres://postgres:postgres@127.0.0.1/agentmaestro_test cargo test

# Build Rust binaries and run Python integration tests
test-int: build
	@echo "Running Python integration tests with Rust binaries..."
	uv run pytest -v tests

# Test dependencies (for backwards compatibility)
test-deps: build
	@echo "Rust binaries built and ready for testing"

# Run target
run: build
	@echo "Starting agentmaestro server..."
	./bin/agentmaestro

# Install/update dependencies
deps:
	@echo "Installing dependencies..."
	go mod download
	go mod tidy

# Clean build artifacts (both Rust and Go)
clean:
	@echo "Cleaning build artifacts..."
	rm -rf bin/
	rm -rf dist/
	rm -rf core/cmd/agentmaestro/web/dist/
	rm -rf core/cmd/agentmaestro-scheduler/web/
	cd web && rm -rf dist/
	cargo clean
	go clean

# Lint code (if staticcheck is installed)
lint:
	@echo "Running linter..."
	go vet ./...
	@if command -v staticcheck >/dev/null 2>&1; then \
		staticcheck ./...; \
	else \
		echo "staticcheck not installed, skipping static analysis"; \
	fi

# Format code
fmt:
	@echo "Formatting code..."
	go fmt ./...

# Cross-compile for multiple platforms (currently disabled - no main.go)
build-all:
	@echo "Cross-compilation disabled - main.go not implemented yet"
	@echo "Run 'make test' to test the current codebase"

# Install development tools
dev-tools:
	@echo "Installing development tools..."
	go install honnef.co/go/tools/cmd/staticcheck@latest
	go install golang.org/x/tools/cmd/goimports@latest

# Run pre-commit hooks
pre-commit:
	@echo "Running pre-commit hooks..."
	uv run pre-commit run --all-files

# Development mode - run backend in dev mode (Go)
dev-backend:
	@echo "Starting backend in development mode..."
	DEV_MODE=1 go run ./core/cmd/agentmaestro

# Development mode - run Rust scheduler in dev mode
dev-backend-rust:
	@echo "Starting Rust scheduler in development mode..."
	DEV_MODE=1 cargo run --bin agentmaestro-scheduler

# Development mode - run frontend dev server
dev-frontend:
	@echo "Starting Vite dev server..."
	cd web && npm run dev

# Development mode - run both backend and frontend (in separate terminals)
dev:
	@echo "To run in development mode, use two terminals:"
	@echo "Terminal 1: make dev-backend      (Go) or make dev-backend-rust (Rust)"
	@echo "Terminal 2: make dev-frontend"
	@echo "Then open http://localhost:5173 for development with HMR"
	@echo "Or run them in background with: make dev-backend & make dev-frontend"
