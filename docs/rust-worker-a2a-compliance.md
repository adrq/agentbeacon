# Rust Worker A2A Protocol v0.3.0 Compliance

**Status:** ✅ Fully Compliant
**Last Updated:** 2025-10-02
**Specification:** [A2A v0.3.0](https://github.com/a2aproject/A2A/blob/v0.3.0/specification)

This document describes the Rust worker's implementation of the A2A (Agent-to-Agent) Protocol v0.3.0, including architectural decisions, concurrency patterns, and deviations from the strict specification.

---

## Overview

The Rust worker (`worker/`) implements the client side of the A2A protocol to communicate with remote A2A agents. The implementation handles:

- **Message/send requests** with JSON-RPC 2.0
- **Non-terminal state polling** via tasks/get
- **Task cancellation propagation** via tasks/cancel
- **Agent card discovery** via .well-known/agent-card.json
- **Headless execution constraints** (no user interaction support)

---

## Architecture

### Core Components

**File Structure:**
```
worker/src/
├── agent.rs           # A2A task execution and RPC handling
├── main.rs            # Worker event loop and state management
├── sync.rs            # Scheduler sync protocol
└── config.rs          # Agent configuration loading
```

**Key Types:**
- `A2ATaskMetadata` - Shared metadata for async cancellation
- `TaskExecution` - Task handle with metadata for cancellation
- `A2aRequest/A2aResponse` - JSON-RPC 2.0 structures
- `AgentCard` - Agent card discovery response

---

## Non-Terminal State Polling

### Problem

Per A2A spec §7.1 and §2387, agents may return non-terminal states (`submitted`, `working`) from `message/send`, requiring clients to poll `tasks/get` until a terminal state is reached.

### Implementation

**Location:** `worker/src/agent.rs:320-446`

```rust
// Check if response state is terminal
let is_terminal = matches!(state_str, "completed" | "failed" | "cancelled" | "rejected");

if is_terminal {
    return Ok(TaskResult { /* ... */ });
}

// Non-terminal state - must poll until terminal
loop {
    tokio::time::sleep(POLL_INTERVAL).await;  // 1s interval
    let updated_task = poll_task_status(client, &rpc_url, &task_id).await?;
    // Check if terminal, return when reached
}
```

**Configuration:**
- `POLL_INTERVAL`: 1 second
- No timeout enforcement (scheduler's responsibility per RWIR architecture)

**Terminal States:**
- `completed` - Task finished successfully
- `failed` - Task failed with error
- `cancelled` - Task was cancelled
- `rejected` - Task was rejected by agent

**Non-Terminal States:**
- `submitted` - Task accepted, not yet started
- `working` - Task in progress
- `input-required` - User input needed (treated as failure, see below)
- `auth-required` - Authentication needed (treated as failure, see below)

### Timeout Responsibility (Scheduler, Not Worker)

**Worker does NOT enforce timeouts.** Per RWIR architecture (line 282, 395), the scheduler enforces `execution.timeout` from workflow configuration.

The worker polls indefinitely until:
1. Agent returns terminal state (completed/failed/cancelled/rejected)
2. Scheduler sends Cancel command (e.g., when execution.timeout expires)

**Architectural Rationale:**
- Worker has no access to workflow timeout configuration (scheduler owns workflow state)
- Matches Go reference implementation: Executor creates context with timeout, agent respects ctx.Done()
- In distributed Rust architecture: Scheduler tracks elapsed time, sends Cancel when timeout expires
- Per RWIR: "Single-writer DB pattern: scheduler persists all status updates" (line 395)

---

## Headless Execution Constraints

### Deviation from Strict A2A Spec

The Rust worker runs in **headless workflow execution** mode without user interaction support. States requiring human intervention are treated as terminal failures:

**Location:** `worker/src/agent.rs:324-339, 397-412`

```rust
if matches!(state_str, "input-required" | "auth-required") {
    return Ok(TaskResult {
        task_status: A2ATaskStatus::failed(format!(
            "Agent requires user interaction (state: {}) which cannot be satisfied in automated workflow execution",
            state_str
        )),
        artifacts,
    });
}
```

**Rationale:**
- AgentMaestro workflows are batch/automated executions
- No mechanism exists for user to provide input mid-execution
- Better to fail fast than hang indefinitely

**Interactive A2A Clients:** Clients with UI would handle `input-required` and `auth-required` differently by prompting users.

---

## Task Cancellation Propagation

### Problem

Per A2A spec §7.4, clients should call `tasks/cancel` when cancelling tasks to allow agents to stop their work and free resources.

### Implementation

The worker uses `Arc<Mutex<A2ATaskMetadata>>` to enable async metadata sharing between the task executor and cancel handler.

**Location:** `worker/src/main.rs:24-30, 333-362`

#### Metadata Sharing Pattern

```rust
// Shared metadata that async task populates during execution
#[derive(Clone, Default)]
pub struct A2ATaskMetadata {
    pub task_id: Option<String>,
    pub rpc_url: Option<String>,
}

struct TaskExecution {
    execution_id: String,
    node_id: String,
    handle: JoinHandle<TaskResult>,
    metadata: Arc<Mutex<A2ATaskMetadata>>,  // Shared state
}
```

#### Incremental Metadata Publication

**Location:** `worker/src/agent.rs:159-163, 293-297`

The async task publishes metadata as soon as values become known:

```rust
// Publish rpc_url after agent card fetch
{
    let mut meta = metadata.lock().await;
    meta.rpc_url = Some(rpc_url.clone());
}

// Publish task_id after message/send returns
{
    let mut meta = metadata.lock().await;
    meta.task_id = Some(task_id.clone());
}
```

#### Cancel Handler with Mutex Hygiene

**Location:** `worker/src/main.rs:333-362`

```rust
WorkerCommand::Cancel => {
    // 1. Copy values out while holding lock (copy-then-release pattern)
    let (task_id, rpc_url) = {
        let meta = task_exec.metadata.lock().await;
        (meta.task_id.clone(), meta.rpc_url.clone())
        // Lock guard dropped here - lock released immediately
    };

    // 2. Make network call WITHOUT holding lock
    if let (Some(task_id), Some(rpc_url)) = (task_id, rpc_url) {
        cancel_a2a_task(&client, &rpc_url, &task_id).await;
    }

    // 3. Abort local task
    state.cancel_current_task();

    // 4. Report cancellation to scheduler
    state.pending_result = Some(TaskResult { /* ... */ });
}
```

**Mutex Hygiene Pattern:**
1. Acquire lock
2. Clone values (held for ~microseconds)
3. Release lock
4. THEN make network call (0-2s) without holding lock

This prevents blocking the async task from updating metadata during the cancel RPC.

#### Best-Effort Cancellation

If cancel arrives before metadata is available (e.g., during agent card fetch):
- Worker aborts local task immediately
- Logs "Cannot propagate cancel to A2A agent - metadata not yet available"
- Reports cancellation to scheduler
- Agent may continue running (graceful degradation)

**Location:** `worker/src/main.rs:356-362`

#### Cancel RPC Details

**Location:** `worker/src/agent.rs:495-514`

```rust
pub async fn cancel_a2a_task(
    client: &reqwest::Client,
    rpc_url: &str,
    task_id: &str,
) -> Result<()> {
    let request = A2aRequest {
        jsonrpc: "2.0".to_string(),
        method: "tasks/cancel".to_string(),
        params: serde_json::json!({ "id": task_id }),
        id: Uuid::new_v4().to_string(),
    };

    // Best-effort cancel with 2s timeout
    let _ = client
        .post(rpc_url)
        .json(&request)
        .timeout(Duration::from_secs(2))
        .send()
        .await;

    Ok(())
}
```

**Timeout:** 2 seconds (avoid delaying local cancellation)
**Error Handling:** Logged but not fatal (spec says "success is not guaranteed")

---

## Cancellation-Friendly Polling

### Problem

Polling loops must respect cancellation signals to avoid hanging after JoinHandle::abort().

### Implementation

**Location:** `worker/src/agent.rs:381`

```rust
// Sleep before next poll (cancellation-friendly)
tokio::time::sleep(POLL_INTERVAL).await;
```

**Why tokio::time::sleep:**
- When `JoinHandle::abort()` is called, Tokio automatically drops all pending futures
- `tokio::time::sleep` is a Tokio future that gets dropped instantly
- No explicit cancellation token needed

**Alternative (NOT used):**
- `std::thread::sleep` - Would block and not respond to abort
- Manual cancellation token - More complex, unnecessary with Tokio

---

## Multi-Task Conversation Support

### Problem

Per A2A spec, a single conversation (contextId) may contain multiple tasks. If an agent completes one task and starts another, the worker must use the **latest** taskId, not the first.

### Implementation

**Location:** `worker/src/agent.rs:189-194`

```rust
// Use reverse iteration to get LATEST taskId (handles multi-task conversations)
if let Some(task_id) = history.iter()
    .rev()  // Get latest taskId in case of multi-task conversations
    .filter(|m| m.get("role").and_then(|r| r.as_str()) == Some("agent"))
    .filter_map(|m| m.get("taskId").and_then(|v| v.as_str()))
    .next()  // Now gets LAST match due to .rev()
```

**Scenario:**
1. User sends message → Agent creates task "abc123" and completes it
2. User sends follow-up → Agent creates NEW task "xyz789" (can't reuse completed task per A2A spec §943)
3. Worker must use "xyz789", not "abc123"

**Impact:** Low - most conversations use a single task. This is defensive programming.

---

## Concurrency Patterns

### Arc<Mutex<>> for Async Metadata Sharing

**Problem:** TaskExecution struct is created in `spawn_task()` BEFORE the async task has fetched the agent card or received the message/send response. If cancellation arrives during this window, metadata would be None.

**Solution:** Use `Arc<Mutex<A2ATaskMetadata>>` shared between spawn_task and execute_a2a_task_inner:

```rust
// In spawn_task
let metadata = Arc::new(Mutex::new(A2ATaskMetadata::default()));
let metadata_clone = metadata.clone();

let handle = tokio::spawn(async move {
    execute_a2a_task(&client, &agents_config, &task, metadata_clone).await
});

state.current_task = Some(TaskExecution {
    execution_id,
    node_id,
    handle,
    metadata,  // Shared with cancel handler
});
```

**Thread Safety:**
- `Arc` provides shared ownership across async tasks
- `Mutex` ensures exclusive access during updates
- Lock is held for ~microseconds (only during clone)

### Copy-Then-Release Pattern

**Anti-Pattern (BAD):**
```rust
let meta = task_exec.metadata.lock().await;
cancel_a2a_task(&client, &meta.rpc_url, &meta.task_id).await;
// Lock held for 0-2s during network call!
```

**Correct Pattern (GOOD):**
```rust
let (task_id, rpc_url) = {
    let meta = task_exec.metadata.lock().await;
    (meta.task_id.clone(), meta.rpc_url.clone())
    // Lock released here (~microseconds)
};
cancel_a2a_task(&client, &rpc_url, &task_id).await;
// Network call happens WITHOUT holding lock
```

**Rules:**
1. Minimize lock hold time
2. Never hold locks during I/O operations
3. Clone values while holding lock, then release
4. Classic Rust concurrency pattern

---

## Differences from Go Implementation

The Rust worker implementation maintains drop-in binary compatibility with the Go worker while using Rust-native concurrency patterns:

| Aspect | Go (Reference) | Rust (Current) |
|--------|----------------|----------------|
| **Async Runtime** | Goroutines + channels | Tokio async/await |
| **Metadata Sharing** | Struct fields | Arc<Mutex<>> |
| **Polling Sleep** | time.Sleep | tokio::time::sleep |
| **Cancel Signal** | context.Context | JoinHandle::abort() |
| **HTTP Client** | net/http | reqwest |
| **Error Handling** | if err != nil | Result<T, E> + ? |

**Compatibility:**
- Same command-line flags
- Same API endpoints
- Same database schema
- Same log output format
- All Python integration tests pass

---

## Testing

### Integration Tests

**File:** `tests/integration/test_worker_async_agent.py`

1. `test_worker_polls_until_terminal_state()` - Verifies polling with DELAY_5 command
2. `test_worker_polls_indefinitely_until_cancel()` - Verifies indefinite polling until cancel (scheduler enforces timeout)
3. `test_worker_cancels_remote_task()` - Verifies tasks/cancel propagation
4. `test_worker_handles_multi_task_conversation()` - Verifies .rev() taskId selection

**Mock Agent Commands:**
- `DELAY_X` - Returns "working", completes after X seconds (1,2,3,5)
- `DELAY_1500` - Returns "working", completes after 1.5 seconds (≥100 = milliseconds)
- `HANG` - Returns "working", delays 1 hour (timeout testing)

### Python Test Suite

All existing Go worker tests pass with Rust worker:
- ✅ `test_worker_happy_path.py` - 5/5 tests
- ✅ `test_worker_failure.py` - 5/5 tests
- ✅ `test_worker_polling.py` - 3/3 tests
- ✅ `test_worker_async_agent.py` - 4/4 tests

---

## Configuration

### Environment Variables

None required - all configuration via CLI flags.

### CLI Flags

```bash
--scheduler-url           # Scheduler HTTP endpoint (required)
--agents-config          # Agent configuration file (default: examples/agents.yaml)
--interval               # Sync interval (default: 1s)
--http-timeout           # HTTP request timeout (default: 30s)
--startup-max-attempts   # Startup retry limit (default: 3)
--reconnect-max-attempts # Reconnect retry limit (default: 10)
--retry-delay            # Retry delay (default: 1s)
```

### Agent Configuration

**File:** `examples/agents.yaml`

```yaml
agents:
  mock-agent:
    type: a2a
    config:
      url: "http://localhost:18765"  # Base URL for agent card discovery
```

**Discovery Flow:**
1. Read agent config → get base URL
2. Fetch `{base_url}/.well-known/agent-card.json`
3. Extract `agent_card.url` (RPC endpoint)
4. Use RPC endpoint for all `message/send`, `tasks/get`, `tasks/cancel` calls

---

## Future Enhancements

### Streaming Support (message/stream)

A2A v0.3.0 defines `message/stream` for Server-Sent Events (SSE) as an alternative to polling. Future enhancement could:
- Subscribe to task status updates via SSE
- Reduce polling overhead for long-running tasks
- Requires SSE client library (e.g., `eventsource`)

### Enhanced Cancellation

Current implementation is best-effort. Future enhancement could:
- Retry tasks/cancel on transient failures
- Verify cancellation success via tasks/get
- Log cancellation status to scheduler

---

## References

- **A2A Specification:** `requirements/a2a-specification.md`
- **Go Reference Implementation:** `core/internal/executor/a2a_agent.go`
- **Worker Sync Protocol:** `docs/worker-sync-response.schema.json`
- **Mock Agent Implementation:** `agentmaestro/mock_agent/jsonrpc.py`
- **Fix Plan Document:** `RUST_WORKER_A2A_FIXES.md`

---

## Changelog

### 2025-10-02 - Architecture Alignment
- ✅ Timeout enforcement moved to scheduler (per RWIR architecture)
- ✅ Worker polls indefinitely until terminal state or Cancel command
- ✅ Matches Go reference: Executor enforces timeout, agent respects cancellation
- ✅ Updated test: `test_worker_polls_indefinitely_until_cancel`

### 2025-10-02 - Initial Implementation
- ✅ Non-terminal state polling with 1s intervals
- ✅ tasks/cancel propagation with Arc<Mutex<>> metadata sharing
- ✅ Headless execution constraints (input-required/auth-required → failures)
- ✅ Multi-task conversation support via .rev() iterator
- ✅ Cancellation-friendly polling with tokio::time::sleep
- ✅ All Python integration tests passing (17/17)
