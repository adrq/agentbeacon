.PHONY: all build build-frontend build-rust-workspace build-scheduler build-worker install-bins npm-install executors test test-sqlite test-postgres test-int test-e2e test-all run clean pre-commit dev-backend dev-frontend

# Default target
all: build executors build-frontend

executors:
	cd executors && npm install && npm run build

npm-install:
	@echo "Installing npm dependencies..."
	cd web && npm install

build-frontend: npm-install
	@echo "Building frontend..."
	cd web && npm run build

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
	cp target/release/orchestrator bin/agentbeacon
	cp target/release/agentbeacon-scheduler bin/
	cp target/release/agentbeacon-worker bin/
	@echo "Rust binaries installed to bin/"

# Build only the Rust scheduler binary
build-scheduler:
	@echo "Building Rust scheduler..."
	cargo build --release --bin agentbeacon-scheduler
	@mkdir -p bin
	cp target/release/agentbeacon-scheduler bin/

# Build only the Rust worker binary
build-worker:
	@echo "Building Rust worker..."
	cargo build --release --bin agentbeacon-worker
	@mkdir -p bin
	cp target/release/agentbeacon-worker bin/

# Run Rust unit tests with both SQLite and PostgreSQL
test:
	@echo "=== Running Rust tests with SQLite ==="
	cargo test -- --test-threads=1
	@echo ""
	@echo "=== Running Rust tests with PostgreSQL ==="
	DATABASE_URL=postgres://postgres:postgres@127.0.0.1/agentbeacon_test cargo test -- --test-threads=1
	@echo ""
	@echo "All tests passed with both backends"

test-sqlite:
	@echo "Running Rust tests with SQLite..."
	cargo test -- --test-threads=1

test-postgres:
	@echo "Running Rust tests with PostgreSQL..."
	DATABASE_URL=postgres://postgres:postgres@127.0.0.1/agentbeacon_test cargo test -- --test-threads=1

# Build Rust binaries and run Python integration tests
test-int: build
	@echo "Running Python integration tests with Rust binaries..."
	uv run pytest -v tests

# Boot system, seed agents, run Playwright E2E tests, tear down
test-e2e: all
	@echo "Starting E2E test environment..."
	@AGENTBEACON_PORT=$${AGENTBEACON_PORT:-9456} ./scripts/e2e.sh --fresh --run-tests

test-all: test test-int test-e2e
	@echo "All tests passed successfully!"

# Run target
run: build
	@echo "Starting AgentBeacon on port $${AGENTBEACON_PORT:-9456}..."
	./bin/agentbeacon --scheduler-port $${AGENTBEACON_PORT:-9456}

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	rm -rf bin/*
	rm -rf dist/*
	cd web && rm -rf dist/
	rm -rf executors/dist
	cargo clean

# Run pre-commit hooks on all staged files
pre-commit:
	@echo "Running pre-commit hooks on changed files..."
	uv run pre-commit run

# Development mode - run scheduler in dev mode
dev-backend:
	@echo "Starting scheduler in dev mode on port $${AGENTBEACON_PORT:-9456}..."
	DEV_MODE=1 cargo run --bin agentbeacon-scheduler -- --port $${AGENTBEACON_PORT:-9456}

# Development mode - run frontend dev server
dev-frontend:
	@echo "Starting Vite dev server (proxy → port $${AGENTBEACON_PORT:-9456})..."
	cd web && AGENTBEACON_PORT=$${AGENTBEACON_PORT:-9456} npm run dev
