# Worker Task Cancellation

**Component:** Rust Worker
**Protocol:** A2A v0.3.0
**Last Updated:** 2025-10-02

This document describes the Rust worker's task cancellation behavior, including race condition handling, timeout configuration, and best-effort propagation strategy.

---

## Overview

When the scheduler sends a cancel command, the worker must:
1. Propagate cancellation to the remote A2A agent (if metadata available)
2. Abort the local task execution
3. Report cancellation status to the scheduler

The implementation uses **best-effort cancellation** with graceful degradation when metadata is not yet available.

---

## Timeout vs Cancellation Responsibility

### Scheduler Enforces Timeout (Not Worker)

Per RWIR architecture, **the scheduler enforces `execution.timeout`** from workflow configuration. The worker does NOT track or enforce timeouts.

**Scheduler Responsibilities**:
- Read `execution.timeout` from workflow YAML
- Track elapsed time since task assignment
- Send Cancel command when timeout expires
- Default timeout: 300s (5 minutes) if not specified per workflow schema

**Worker Responsibilities**:
- Poll agent indefinitely until terminal state or Cancel command
- Respond to Cancel command by propagating to agent and reporting cancellation
- No timeout tracking (worker has no access to workflow configuration)

**Why This Separation**:
- Per RWIR line 282: "Support `execution.timeout` and `execution.retry` policies" (scheduler's job)
- Per RWIR line 395: "Single-writer DB pattern: scheduler persists all status updates"
- Matches Go reference: Executor creates context with timeout, agent checks ctx.Done()
- In distributed architecture: Scheduler owns workflow state, worker is stateless executor

### Example Timeline

```
T+0s:   Scheduler assigns task to worker (execution.timeout=300s from workflow)
T+1s:   Worker calls message/send on A2A agent
T+2s:   Agent returns state="working"
T+3s:   Worker polls tasks/get → state="working"
T+4s:   Worker polls tasks/get → state="working"
...
T+300s: Scheduler detects timeout expired
T+301s: Scheduler sends Cancel command to worker via /api/worker/sync
T+302s: Worker receives Cancel, calls tasks/cancel RPC on agent
T+303s: Worker aborts local task, reports status="cancelled" to scheduler
```

**Key Point**: Worker reports "cancelled" status (not "failed with timeout"). Timeout detection and enforcement is scheduler's responsibility.

---

## Cancellation Flow

### Normal Cancellation (Metadata Available)

```
Scheduler          Worker               A2A Agent
    |                 |                     |
    | Cancel Command  |                     |
    |--------------->|                     |
    |                 | 1. Read metadata    |
    |                 |    (task_id + url) |
    |                 |                     |
    |                 | 2. tasks/cancel RPC |
    |                 |-------------------->|
    |                 |                     | (agent stops work)
    |                 | 3. Abort local task |
    |                 |                     |
    |                 | 4. Report cancelled |
    |<----------------|                     |
```

**Timeline:**
- T+0ms: Receive cancel command
- T+0-10ms: Lock metadata, copy values, release lock
- T+10-2000ms: Send tasks/cancel RPC (2s timeout)
- T+2000-2100ms: Abort local JoinHandle
- T+2100-2200ms: Report cancelled status to scheduler

**Location:** `worker/src/main.rs:333-379`

### Early Cancellation (Metadata Not Available)

If cancel arrives during agent card fetch or before message/send completes:

```
Scheduler          Worker               A2A Agent
    |                 |                     |
    | Cancel Command  |                     |
    |--------------->|                     |
    |                 | 1. Check metadata   |
    |                 |    (None/None)     |
    |                 |                     |
    |                 | 2. Skip RPC         |
    |                 |                     |
    |                 | 3. Abort local task |
    |                 |                     |
    |                 | 4. Report cancelled |
    |<----------------|                     |
    |                 |                     | (agent may continue)
```

**Timeline:**
- T+0ms: Receive cancel command
- T+0-10ms: Lock metadata, check values (both None)
- T+10ms: Skip tasks/cancel RPC
- T+10-20ms: Abort local JoinHandle
- T+20-30ms: Report cancelled status to scheduler

**Impact:** Agent may continue running task, wasting compute resources until completion or timeout.

**Location:** `worker/src/main.rs:356-362`

---

## Race Conditions

### Race 1: Cancel During Agent Card Fetch

**Scenario:** Cancel arrives while worker is fetching `.well-known/agent-card.json`

**State:**
- `metadata.task_id`: None
- `metadata.rpc_url`: None

**Behavior:**
- Worker logs: "Cannot propagate cancel to A2A agent - metadata not yet available"
- Local task aborted immediately
- tasks/cancel NOT sent
- Agent continues running (no way to contact it yet)

**Probability:** Low (agent card fetch typically <100ms)

### Race 2: Cancel During message/send Wait

**Scenario:** Cancel arrives while worker is waiting for message/send HTTP response

**State:**
- `metadata.task_id`: None
- `metadata.rpc_url`: Some(url)

**Behavior:**
- Worker has RPC URL but not taskId
- Cannot call tasks/cancel (requires taskId parameter)
- Local task aborted immediately
- Agent may have started work but cancel not propagated

**Probability:** Low-Medium (message/send typically 50-500ms)

### Race 3: Cancel During Polling Loop

**Scenario:** Cancel arrives while worker is polling tasks/get in non-terminal state loop

**State:**
- `metadata.task_id`: Some(id)
- `metadata.rpc_url`: Some(url)

**Behavior:**
- Worker has full metadata
- tasks/cancel sent successfully
- Polling loop dropped via JoinHandle::abort()
- Agent receives cancellation and can clean up

**Probability:** High (most cancellations happen during task execution)

---

## Concurrency Details

### Metadata Sharing Architecture

```rust
// Created in spawn_task BEFORE async task runs
let metadata = Arc::new(Mutex::new(A2ATaskMetadata {
    task_id: None,
    rpc_url: None,
}));

// Shared with async task
let metadata_clone = metadata.clone();
tokio::spawn(async move {
    execute_a2a_task(..., metadata_clone).await
});

// Shared with cancel handler
state.current_task = Some(TaskExecution {
    ...,
    metadata,  // Cancel handler can read this
});
```

**Why Arc<Mutex<>>:**
- `Arc`: Shared ownership between spawn_task and async task
- `Mutex`: Exclusive access during updates/reads
- Values published incrementally as they become known

### Metadata Publication Timeline

```
spawn_task()                    (T+0ms)
  └─> Arc<Mutex<{None, None}>> created
      │
      └─> async execute_a2a_task starts
          │
          ├─> Agent card fetch      (T+0-100ms)
          │   └─> rpc_url published
          │       {None, Some(url)}
          │
          ├─> message/send RPC      (T+100-600ms)
          │   └─> task_id published
          │       {Some(id), Some(url)}
          │
          └─> Polling loop          (T+600ms - completion)
              {Some(id), Some(url)} maintained
```

**Cancel Window Analysis:**

| Time Window | Cancel Arrives | metadata State | tasks/cancel Sent? |
|-------------|----------------|----------------|-------------------|
| 0-100ms | During card fetch | {None, None} | ❌ No |
| 100-600ms | During message/send | {None, Some(url)} | ❌ No |
| 600ms+ | During polling | {Some(id), Some(url)} | ✅ Yes |

**Optimization Opportunity:** Could extract taskId from message/send request ID before response arrives, narrowing the window further.

### Mutex Lock Duration

**Anti-Pattern (BAD - 2000ms lock hold):**
```rust
let meta = task_exec.metadata.lock().await;
if let Some(task_id) = &meta.task_id {
    if let Some(rpc_url) = &meta.rpc_url {
        // PROBLEM: Lock held during network I/O!
        cancel_a2a_task(&client, rpc_url, task_id).await;
    }
}
```

**Correct Pattern (GOOD - ~5μs lock hold):**
```rust
// 1. Acquire lock and clone values (~5 microseconds)
let (task_id, rpc_url) = {
    let meta = task_exec.metadata.lock().await;
    (meta.task_id.clone(), meta.rpc_url.clone())
    // Lock guard dropped here - lock released
};

// 2. Make network call WITHOUT holding lock (0-2000ms)
if let (Some(task_id), Some(rpc_url)) = (task_id, rpc_url) {
    cancel_a2a_task(&client, &rpc_url, &task_id).await;
}
```

**Why This Matters:**
- Async task may be trying to update metadata during cancel
- Holding lock during network I/O blocks async task for up to 2 seconds
- Could prevent async task from publishing metadata it just received
- Copy-then-release pattern avoids this contention

---

## Timeout Configuration

### tasks/cancel RPC Timeout

**Value:** 2 seconds
**Location:** `worker/src/agent.rs:509`
**Rationale:**
- Cancel is best-effort, don't delay local cleanup
- Agent may be unresponsive or slow
- Scheduler expects quick cancellation acknowledgment

**Implementation:**
```rust
let _ = client
    .post(rpc_url)
    .json(&request)
    .timeout(Duration::from_secs(2))  // 2s timeout
    .send()
    .await;
```

**Behavior on Timeout:**
- Network call returns timeout error
- Error is logged but NOT propagated (Result discarded with `let _`)
- Cancellation proceeds with local abort
- Scheduler receives cancelled status

### Abort Watchdog Timeout

**Value:** 5 seconds
**Location:** `worker/src/main.rs:63-76`
**Rationale:**
- JoinHandle::abort() should complete instantly for well-behaved tasks
- Watchdog detects tasks that don't respond to abort
- Prevents worker from hanging on stuck tasks

**Implementation:**
```rust
match tokio::time::timeout(std::time::Duration::from_secs(5), handle).await {
    Ok(Ok(result)) => {
        tracing::warn!("Task completed before abort took effect: {:?}", result);
    }
    Ok(Err(e)) if e.is_cancelled() => {
        tracing::debug!("Task abort completed successfully");
    }
    Ok(Err(e)) => {
        tracing::error!("Task panicked during abort: {}", e);
    }
    Err(_) => {
        tracing::error!("Task abort did not complete within 5s - task may be stuck");
    }
}
```

**Cases:**
1. Task completes before abort: Log warning (race condition)
2. Task aborts successfully: Log debug (expected)
3. Task panics: Log error (bug)
4. Task stuck after 5s: Log error (bug or blocking code)

---

## Error Handling

### tasks/cancel RPC Errors

Per A2A spec §7.4:
> "The server will attempt to cancel the task, but success is not guaranteed"

**Error Codes (from A2A spec §8):**
- `-32001`: Task not found (already completed)
- `-32002`: Task not cancelable (terminal state)
- `-32603`: Internal error (transient)

**Worker Behavior:**
- All errors logged but not fatal
- Cancellation proceeds with local abort
- Scheduler always receives cancelled status

**Rationale:** Best-effort cancellation means failures are expected.

### Network Errors

**Possible Errors:**
- Connection refused (agent down)
- Timeout (slow agent)
- DNS resolution failure
- Network unreachable

**Worker Behavior:**
- Log warning with error details
- Complete local cancellation
- Report cancelled to scheduler

**Example Log:**
```
WARN Failed to cancel remote A2A task - agent may continue running
  task_id=abc123 error="connection refused"
```

---

## Best Practices

### For Agent Developers

1. **Implement tasks/cancel:** Honor cancellation requests when possible
2. **Clean up resources:** Release file handles, close connections, stop background work
3. **Return quickly:** Acknowledge cancel within 1-2 seconds
4. **Handle edge cases:** Support cancel during any state (submitted, working, etc.)

### For Workflow Authors

1. **Design for cancellation:** Assume any long-running task may be cancelled
2. **Use idempotent operations:** Cancelled tasks may partially complete
3. **Cleanup nodes:** Consider adding cleanup nodes for critical resources

### For Worker Operators

1. **Monitor logs:** Look for "metadata not yet available" patterns (indicates early cancels)
2. **Tune timeouts:** Adjust `--http-timeout` if agents are consistently slow
3. **Track resource usage:** Monitor agent processes for orphaned work

---

## Future Enhancements

### 1. Enhanced Metadata Pre-Population

**Problem:** metadata.task_id is None during message/send wait

**Solution:** Use message/send request ID as provisional taskId
- Agent typically uses request ID as task ID
- Allows cancel RPC during message/send wait
- Falls back to response task ID if different

**Impact:** Reduces "metadata not available" window from 100-600ms to 0-100ms

### 2. Retry on Transient Errors

**Problem:** Network blips may prevent cancel RPC

**Solution:** Retry tasks/cancel on specific error codes
- Retry on connection refused (agent restarting)
- Retry on timeout (slow network)
- Don't retry on -32001 (task not found)

**Impact:** Higher success rate for cancel propagation

### 3. Verification via tasks/get

**Problem:** No confirmation that agent actually cancelled

**Solution:** Call tasks/get after tasks/cancel
- Verify state transitioned to "cancelled"
- Report verification status to scheduler
- Allows scheduler to track cancel success rate

**Impact:** Better observability, potential retry logic

### 4. Configurable Timeouts

**Problem:** 2s cancel timeout may be too short for slow agents

**Solution:** Add CLI flags
- `--cancel-timeout` for tasks/cancel RPC
- `--abort-timeout` for watchdog
- Per-agent timeout configuration in agents.yaml

**Impact:** Flexibility for different deployment environments

---

## Debugging

### Check if Cancel was Propagated

**Worker Logs:**
```bash
# Successful propagation
grep "Propagating cancellation to remote A2A agent" worker.log

# Metadata not available
grep "metadata not yet available" worker.log

# RPC failure
grep "Failed to cancel remote A2A task" worker.log
```

### Check Agent Received Cancel

**Mock Agent Logs:**
```bash
# Look for tasks/cancel RPC
grep "tasks/cancel" mock-agent.log

# Check if task transitioned to cancelled
grep "state.*cancelled" mock-agent.log
```

### Verify Timing

**Add debug logging:**
```rust
// In cancel handler
tracing::debug!(
    "Cancel metadata check: task_id={:?} rpc_url={:?}",
    task_id, rpc_url
);
```

**Analyze logs:**
- If both None: Cancel arrived during agent card fetch (very early)
- If rpc_url Some but task_id None: Cancel during message/send (early)
- If both Some: Normal cancellation (expected)

---

## References

- **A2A Specification §7.4:** tasks/cancel method definition
- **A2A Specification §8:** Error codes
- **Implementation:** `worker/src/main.rs:333-379`, `worker/src/agent.rs:495-514`
- **Fix Plan:** `RUST_WORKER_A2A_FIXES.md` Issue #1 (lines 29-365)
- **Compliance Doc:** `docs/rust-worker-a2a-compliance.md`
