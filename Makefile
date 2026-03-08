.PHONY: all build build-frontend build-rust-workspace build-scheduler build-worker install-bins npm-install executors test test-rust test-int test-e2e test-all build-musl build-musl-x64 build-musl-arm64 test-musl build-wheel-x64 build-wheel-arm64 build-wheels test-packaging build-npm-x64 build-npm-arm64 build-npm-wrapper build-npm test-npm run clean pre-commit dev-backend dev-frontend

RUST_STRICT_FLAGS ?= -Dwarnings

# Directory containing the zig binary from the ziglang PyPI package
ZIG_DIR = $(shell uv run python -c 'import os, ziglang; print(os.path.dirname(ziglang.__file__))')

# Default target
all: build-frontend executors build

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
	RUSTFLAGS="$(RUST_STRICT_FLAGS) $${RUSTFLAGS}" cargo build --release

# Install Rust binaries to bin/ directory
install-bins: build-rust-workspace
	@echo "Installing Rust binaries to bin/..."
	@mkdir -p bin
	cp target/release/agentbeacon bin/
	cp target/release/agentbeacon-worker bin/
	@echo "Rust binaries installed to bin/"

# Build only the Rust scheduler binary
build-scheduler:
	@echo "Building Rust scheduler..."
	RUSTFLAGS="$(RUST_STRICT_FLAGS) $${RUSTFLAGS}" cargo build --release --bin agentbeacon
	@mkdir -p bin
	cp target/release/agentbeacon bin/

# Build only the Rust worker binary
build-worker:
	@echo "Building Rust worker..."
	RUSTFLAGS="$(RUST_STRICT_FLAGS) $${RUSTFLAGS}" cargo build --release --bin agentbeacon-worker
	@mkdir -p bin
	cp target/release/agentbeacon-worker bin/

# Build fully static x86_64 musl binaries
build-musl-x64: build-frontend executors
	@command -v cargo-zigbuild >/dev/null 2>&1 || { echo "Installing cargo-zigbuild..."; cargo install cargo-zigbuild; }
	@rustup target list --installed | grep -q x86_64-unknown-linux-musl || rustup target add x86_64-unknown-linux-musl
	@echo "Building musl-static x86_64 binaries..."
	PATH="$(ZIG_DIR):$$PATH" RUSTFLAGS="$(RUST_STRICT_FLAGS) $${RUSTFLAGS}" cargo zigbuild --target x86_64-unknown-linux-musl --release
	@output=$$(readelf -d target/x86_64-unknown-linux-musl/release/agentbeacon) \
		|| { echo "ERROR: readelf failed on agentbeacon"; exit 1; }; \
		if echo "$$output" | grep -q NEEDED; then echo "ERROR: agentbeacon has dynamic dependencies"; exit 1; fi
	@output=$$(readelf -d target/x86_64-unknown-linux-musl/release/agentbeacon-worker) \
		|| { echo "ERROR: readelf failed on agentbeacon-worker"; exit 1; }; \
		if echo "$$output" | grep -q NEEDED; then echo "ERROR: agentbeacon-worker has dynamic dependencies"; exit 1; fi
	@echo "musl-static x86_64 binaries verified at target/x86_64-unknown-linux-musl/release/"

# Build fully static aarch64 musl binaries (cross-compiled from x86_64)
build-musl-arm64: build-frontend executors
	@command -v cargo-zigbuild >/dev/null 2>&1 || { echo "Installing cargo-zigbuild..."; cargo install cargo-zigbuild; }
	@rustup target list --installed | grep -q aarch64-unknown-linux-musl || rustup target add aarch64-unknown-linux-musl
	@echo "Building musl-static aarch64 binaries (cross-compiling)..."
	PATH="$(ZIG_DIR):$$PATH" RUSTFLAGS="$(RUST_STRICT_FLAGS) $${RUSTFLAGS}" cargo zigbuild --target aarch64-unknown-linux-musl --release
	@output=$$(readelf -d target/aarch64-unknown-linux-musl/release/agentbeacon) \
		|| { echo "ERROR: readelf failed on agentbeacon"; exit 1; }; \
		if echo "$$output" | grep -q NEEDED; then echo "ERROR: agentbeacon has dynamic dependencies"; exit 1; fi
	@file target/aarch64-unknown-linux-musl/release/agentbeacon | grep -q "aarch64" \
		|| { echo "ERROR: agentbeacon is not aarch64"; exit 1; }
	@output=$$(readelf -d target/aarch64-unknown-linux-musl/release/agentbeacon-worker) \
		|| { echo "ERROR: readelf failed on agentbeacon-worker"; exit 1; }; \
		if echo "$$output" | grep -q NEEDED; then echo "ERROR: agentbeacon-worker has dynamic dependencies"; exit 1; fi
	@file target/aarch64-unknown-linux-musl/release/agentbeacon-worker | grep -q "aarch64" \
		|| { echo "ERROR: agentbeacon-worker is not aarch64"; exit 1; }
	@echo "musl-static aarch64 binaries verified at target/aarch64-unknown-linux-musl/release/"

# Build both musl targets
build-musl: build-musl-x64 build-musl-arm64
	@echo "All musl-static binaries built successfully."

# Build and verify musl-static binaries
test-musl: build-musl
	@echo "Running musl binary verification tests..."
	uv run pytest -v -m musl tests

# Generate PyPI wheel for x86_64 musl
build-wheel-x64: build-musl-x64
	uv run python scripts/build_wheel.py --target x86_64-unknown-linux-musl --output-dir dist/

# Generate PyPI wheel for aarch64 musl
build-wheel-arm64: build-musl-arm64
	uv run python scripts/build_wheel.py --target aarch64-unknown-linux-musl --output-dir dist/

# Generate both platform wheels
build-wheels: build-wheel-x64 build-wheel-arm64

# Run packaging tests (builds musl for host architecture first)
test-packaging: build-musl-$(if $(filter aarch64,$(shell uname -m)),arm64,x64)
	uv run pytest -v -m packaging tests

# Generate npm platform package for x86_64
build-npm-x64: build-musl-x64
	uv run python scripts/build_npm.py platform --target x86_64-unknown-linux-musl --output-dir dist/npm/

# Generate npm platform package for aarch64
build-npm-arm64: build-musl-arm64
	uv run python scripts/build_npm.py platform --target aarch64-unknown-linux-musl --output-dir dist/npm/

# Generate npm wrapper package
build-npm-wrapper:
	uv run python scripts/build_npm.py wrapper --output-dir dist/npm/

# Generate all npm packages (both platform + wrapper)
# CI target: requires both architectures. For local dev, use build-npm-x64 or build-npm-arm64.
build-npm: build-npm-x64 build-npm-arm64 build-npm-wrapper

# Run npm packaging tests (builds musl for host architecture first)
test-npm: build-musl-$(if $(filter aarch64,$(shell uname -m)),arm64,x64)
	uv run pytest -v -m npm tests

# Run Rust unit and integration tests
test: all
	@echo "Running Rust tests..."
	RUSTFLAGS="$(RUST_STRICT_FLAGS) $${RUSTFLAGS}" cargo test -- --test-threads=1

test-rust: all
	@echo "Running Rust tests..."
	RUSTFLAGS="$(RUST_STRICT_FLAGS) $${RUSTFLAGS}" cargo test -- --test-threads=1

# Build Rust binaries and run Python integration tests
test-int: all
	@echo "Running Python integration tests with Rust binaries..."
	uv run pytest -n4 -v tests

# Boot system, seed agents, run Playwright E2E tests, tear down
test-e2e: all
	@echo "Starting E2E test environment..."
	@AGENTBEACON_PORT=$${AGENTBEACON_PORT:-9456} ./scripts/e2e.sh --fresh --run-tests

test-all: test-rust test-int test-e2e
	@echo "All tests passed successfully!"

# Run target
run: all
	@echo "Starting AgentBeacon on port $${AGENTBEACON_PORT:-9456}..."
	@touch scheduler-$${AGENTBEACON_PORT:-9456}.db
	AGENTBEACON_EXECUTORS_DIR=$${AGENTBEACON_EXECUTORS_DIR:-$(CURDIR)/executors/dist} \
		./bin/agentbeacon --port $${AGENTBEACON_PORT:-9456} --workers 2

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
	DEV_MODE=1 cargo run --bin agentbeacon -- --port $${AGENTBEACON_PORT:-9456}

# Development mode - run frontend dev server
dev-frontend:
	@echo "Starting Vite dev server (proxy → port $${AGENTBEACON_PORT:-9456})..."
	cd web && AGENTBEACON_PORT=$${AGENTBEACON_PORT:-9456} npm run dev
