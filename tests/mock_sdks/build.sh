#!/bin/bash
# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

# Build the mock Claude and Copilot SDKs. Each mock is installed under
# node_modules/ with the real SDK's package name, and `executors` links to the
# real executors/dist. Run with NODE_OPTIONS="--preserve-symlinks
# --preserve-symlinks-main", an executor loaded through that link resolves its
# SDK imports from this directory's node_modules, so it gets the mocks.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Remove previous build output
rm -rf _build node_modules/@anthropic-ai node_modules/@github executors

# Compile TypeScript into _build/
npx tsc

# Install each mock under the real SDK's package name
mkdir -p node_modules/@anthropic-ai/claude-agent-sdk
cp _build/claude-agent-sdk.js node_modules/@anthropic-ai/claude-agent-sdk/index.js
cat > node_modules/@anthropic-ai/claude-agent-sdk/package.json << 'PKGJSON'
{"name": "@anthropic-ai/claude-agent-sdk", "main": "index.js"}
PKGJSON

mkdir -p node_modules/@github/copilot-sdk
cp _build/copilot-sdk.js node_modules/@github/copilot-sdk/index.js
cat > node_modules/@github/copilot-sdk/package.json << 'PKGJSON'
{"name": "@github/copilot-sdk", "main": "index.js"}
PKGJSON

# Remove intermediate build output
rm -rf _build

# Link the real executor build (produced by `make executors`)
ln -sf "../../executors/dist" executors

echo "Mock SDKs built successfully."
