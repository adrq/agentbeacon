.PHONY: build test test-nocache clean deps lint fmt build-all dev-tools pre-commit dev

# Default target
all: build

# Build target (currently no main.go exists)
build:
	@echo "No main binary to build yet - main.go not implemented"
	@echo "Run 'make test' to test the current codebase"
	@echo "When implemented, binary will be named: agentmaestro-bin"

# Run tests
test:
	@echo "Running tests..."
	go test ./...

# Run tests without cache
test-nocache:
	@echo "Running tests without cache..."
	go test -count=1 ./...

# Run target (currently no main binary exists)
run:
	@echo "No main binary to run yet - main.go not implemented"
	@echo "Run 'make test' to test the current codebase"

# Install/update dependencies
deps:
	@echo "Installing dependencies..."
	go mod download
	go mod tidy

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	rm -f agentmaestro-bin
	rm -rf dist/
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

# Development mode
dev:
	@echo "Development mode not implemented yet"
	@echo "Available commands:"
	@echo "  make test      - Run tests"
	@echo "  make lint      - Run linters"
	@echo "  make fmt       - Format code"
