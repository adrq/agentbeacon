.PHONY: build test test-nocache clean deps lint fmt build-all dev-tools pre-commit dev npm-install test-deps

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

# Build only test dependencies (mock-agent and embedded files)
test-deps: npm-install
	@echo "Building test dependencies..."
	@mkdir -p bin
	@echo "Building agentmaestro binary for tests..."
	go build -o bin/agentmaestro ./core/cmd/agentmaestro
	@echo "Building mock-agent for testing..."
	go build -o bin/mock-agent ./core/cmd/mock-agent
	@echo "Building frontend for embedded files..."
	cd web && npm run build

# Build target (includes frontend)
build: build-frontend
	@echo "Building agentmaestro..."
	@mkdir -p bin
	go build -o bin/agentmaestro ./core/cmd/agentmaestro
	@echo "Building mock-agent for testing..."
	go build -o bin/mock-agent ./core/cmd/mock-agent

# Run tests
test: test-deps
	@echo "Running tests..."
	go test ./... -v

# Run tests without cache
test-nocache: test-deps
	@echo "Running tests without cache..."
	go test -count=1 ./... -v

# Run A2A integration tests
test-e2e: test-deps
	@echo "Running E2E (A2A) tests with real binaries..."
	go test -tags e2e -v ./core/internal/api -run TestE2E

# Run target
run: build
	@echo "Starting agentmaestro server..."
	./bin/agentmaestro

# Install/update dependencies
deps:
	@echo "Installing dependencies..."
	go mod download
	go mod tidy

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	rm -rf bin/
	rm -rf dist/
	rm -rf core/cmd/agentmaestro/web/dist/
	cd web && rm -rf dist/
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

# Development mode - run backend in dev mode
dev-backend:
	@echo "Starting backend in development mode..."
	DEV_MODE=1 go run ./core/cmd/agentmaestro

# Development mode - run frontend dev server
dev-frontend:
	@echo "Starting Vite dev server..."
	cd web && npm run dev

# Development mode - run both backend and frontend (in separate terminals)
dev:
	@echo "To run in development mode, use two terminals:"
	@echo "Terminal 1: make dev-backend"
	@echo "Terminal 2: make dev-frontend"
	@echo "Then open http://localhost:5173 for development with HMR"
	@echo "Or run them in background with: make dev-backend & make dev-frontend"
