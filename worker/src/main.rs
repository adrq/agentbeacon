mod acp;
mod agent;
mod cli;
mod config;
mod sync;

use anyhow::{Context, Result};
use clap::Parser;
use std::sync::Arc;
use tokio::sync::Mutex;
use tokio::task::JoinHandle;
use tracing_subscriber::EnvFilter;

use crate::agent::{A2ATaskMetadata, cancel_a2a_task, execute_task};
use crate::cli::Args;
use crate::config::{AgentsConfig, load_agents_config};
use crate::sync::{
    RetryConfig, SyncRequest, SyncResponse, TaskAssignment, TaskResult, WorkerCommand,
    perform_sync_with_retry,
};

struct WorkerState {
    current_task: Option<TaskExecution>,
    pending_result: Option<TaskResult>,
    has_connected_successfully: bool,
}

struct TaskExecution {
    execution_id: String,
    node_id: String,
    handle: JoinHandle<TaskResult>,
    // Shared metadata updated by async task as soon as values are known
    // Cancel handler can read whatever is available without waiting for completion
    metadata: Arc<Mutex<A2ATaskMetadata>>,
}

impl WorkerState {
    fn new() -> Self {
        Self {
            current_task: None,
            pending_result: None,
            has_connected_successfully: false,
        }
    }

    fn mark_connected(&mut self) {
        self.has_connected_successfully = true;
    }

    fn is_working(&self) -> bool {
        self.current_task.is_some()
    }

    fn current_task_info(&self) -> Option<(String, String)> {
        self.current_task
            .as_ref()
            .map(|t| (t.execution_id.clone(), t.node_id.clone()))
    }

    fn cancel_current_task(&mut self) -> Option<JoinHandle<()>> {
        if let Some(task) = self.current_task.take() {
            tracing::info!(
                execution_id = %task.execution_id,
                node_id = %task.node_id,
                "Cancelling task"
            );
            let handle = task.handle;
            handle.abort();

            // Return watchdog handle to allow caller to await completion
            Some(tokio::spawn(async move {
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
                        tracing::error!(
                            "Task abort did not complete within 5s - task may be stuck"
                        );
                    }
                }
            }))
        } else {
            None
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive(tracing::Level::INFO.into()))
        .init();

    let args = Args::parse();

    validate_startup(&args).await?;

    let agents_config =
        load_agents_config(&args.agents_config).context("failed to load agents config")?;

    let client = reqwest::Client::builder()
        .timeout(args.http_timeout)
        .build()
        .context("failed to build HTTP client")?;

    tracing::info!(
        scheduler_url = %args.scheduler_url,
        interval = ?args.interval,
        "Worker started"
    );

    tokio::select! {
        result = run_worker_loop(args, agents_config, client) => {
            result
        }
        _ = shutdown_signal() => {
            tracing::info!("Received shutdown signal, exiting gracefully");
            Ok(())
        }
    }
}

#[cfg(unix)]
async fn shutdown_signal() {
    use tokio::signal::unix::{SignalKind, signal};

    let mut sigterm = signal(SignalKind::terminate()).expect("failed to install SIGTERM handler");
    let mut sigint = signal(SignalKind::interrupt()).expect("failed to install SIGINT handler");

    tokio::select! {
        _ = sigterm.recv() => {},
        _ = sigint.recv() => {},
    }
}

#[cfg(not(unix))]
async fn shutdown_signal() {
    tokio::signal::ctrl_c()
        .await
        .expect("failed to install Ctrl+C handler");
}

async fn validate_startup(args: &Args) -> Result<()> {
    if !args.agents_config.exists() {
        return Err(anyhow::anyhow!(
            "load agents config failed: file not found {}",
            args.agents_config.display()
        ));
    }

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
        .context("failed to build health check client")?;

    let health_url = format!("{}/api/health", args.scheduler_url);
    match client.get(&health_url).send().await {
        Ok(response) if response.status().is_success() => {
            tracing::debug!("Scheduler health check passed");
            Ok(())
        }
        Ok(response) => {
            tracing::warn!(
                "Scheduler health check failed: received status {} - will retry during sync loop",
                response.status()
            );
            Ok(())
        }
        Err(e) => {
            tracing::warn!(
                "Scheduler unreachable at {}: {} - will retry during sync loop",
                args.scheduler_url,
                e
            );
            Ok(())
        }
    }
}

async fn wait_for_task(
    current_task: &mut Option<TaskExecution>,
) -> Result<TaskResult, tokio::task::JoinError> {
    match current_task {
        Some(task) => (&mut task.handle).await,
        None => std::future::pending().await,
    }
}

async fn run_worker_loop(
    args: Args,
    agents_config: AgentsConfig,
    client: reqwest::Client,
) -> Result<()> {
    let mut interval = tokio::time::interval(args.interval);
    let mut state = WorkerState::new();

    // Extract retry config from CLI args
    let retry_config = RetryConfig {
        startup_max_attempts: args.startup_max_attempts,
        reconnect_max_attempts: args.reconnect_max_attempts,
        retry_delay: args.retry_delay,
    };

    tracing::info!(
        "Starting worker loop, syncing every {}s",
        args.interval.as_secs()
    );

    loop {
        tokio::select! {
            _ = interval.tick() => {
                if let Err(e) = handle_sync_tick(&client, &args.scheduler_url, &agents_config, &retry_config, &mut state).await {
                    // Check if this is a fatal error (scheduler unreachable after retry exhaustion)
                    if e.to_string().contains("scheduler unreachable") {
                        tracing::error!(error = %e, "scheduler unreachable - exiting worker");
                        return Err(e);
                    }
                    tracing::error!(error = %e, "Sync tick failed");
                }
            }

            result = wait_for_task(&mut state.current_task) => {
                match result {
                    Ok(task_result) => {
                        tracing::debug!(
                            execution_id = %task_result.execution_id,
                            node_id = %task_result.node_id,
                            state = %task_result.task_status.state,
                            "Background task completed"
                        );
                        state.pending_result = Some(task_result);
                        state.current_task = None;
                    }
                    Err(e) => {
                        if e.is_cancelled() {
                            tracing::debug!("Background task was cancelled");
                        } else {
                            tracing::error!(error = %e, "Background task panicked");
                        }
                        state.current_task = None;
                    }
                }
            }
        }
    }
}

async fn handle_sync_tick(
    client: &reqwest::Client,
    scheduler_url: &str,
    agents_config: &AgentsConfig,
    retry_config: &RetryConfig,
    state: &mut WorkerState,
) -> Result<()> {
    let sync_request = build_sync_request(state);

    tracing::info!(status = ?sync_request.status, "syncing with scheduler via /api/worker/sync");

    // Use retry logic with connection state tracking
    let sync_response = perform_sync_with_retry(
        client,
        scheduler_url,
        &sync_request,
        state.has_connected_successfully,
        retry_config,
    )
    .await?;

    // Mark successful connection on first success
    if !state.has_connected_successfully {
        state.mark_connected();
        tracing::info!("Successfully connected to scheduler");
    }

    match sync_response {
        SyncResponse::NoAction => {
            // Scheduler acknowledged our result by returning NoAction
            if state.pending_result.is_some() {
                tracing::debug!("Scheduler acknowledged task result");
                state.pending_result = None;
            } else {
                tracing::info!("No action from sync response");
            }
        }
        SyncResponse::TaskAssigned { task } => {
            if state.pending_result.is_some() {
                tracing::error!(
                    "Scheduler sent new task before acknowledging previous result - will retry result submission"
                );
                // DO NOT clear pending_result - will retry on next sync
                return Ok(());
            }
            if state.is_working() {
                tracing::warn!("Received task assignment while already working - ignoring");
            } else {
                tracing::info!(
                    execution_id = %task.execution_id,
                    node_id = %task.node_id,
                    agent = %task.agent,
                    task_payload = ?task.task,
                    workflow_registry_id = ?task.workflow_registry_id,
                    workflow_ref = ?task.workflow_ref,
                    "Received task assignment"
                );
                spawn_task(client.clone(), agents_config.clone(), *task, state);
            }
        }
        SyncResponse::Command { command } => {
            if state.pending_result.is_some() {
                tracing::warn!(
                    "Received command while pending result exists - processing command first"
                );
            }
            match command {
                WorkerCommand::Cancel => {
                    if let Some(task_exec) = &state.current_task {
                        let execution_id = task_exec.execution_id.clone();
                        let node_id = task_exec.node_id.clone();

                        tracing::info!(
                            execution_id = %execution_id,
                            node_id = %node_id,
                            "Processing cancel command"
                        );

                        // 1. Try to cancel remote A2A task OR local ACP task (best-effort)
                        // CRITICAL: Copy values out while holding lock, then release BEFORE network call
                        // Holding lock during await would block async task from updating metadata
                        let (task_id, rpc_url, acp_session_id, acp_cancel_tx) = {
                            let meta = task_exec.metadata.lock().await;
                            (
                                meta.task_id.clone(),
                                meta.rpc_url.clone(),
                                meta.acp_session_id.clone(),
                                meta.acp_cancel_tx.clone(),
                            )
                            // Lock guard dropped here - lock released immediately
                        };

                        if let (Some(task_id), Some(rpc_url)) = (task_id, rpc_url) {
                            // A2A agent - propagate cancellation via HTTP
                            tracing::info!(
                                task_id = %task_id,
                                "Propagating cancellation to remote A2A agent"
                            );

                            if let Err(e) = cancel_a2a_task(client, &rpc_url, &task_id).await {
                                tracing::warn!(
                                    task_id = %task_id,
                                    error = %e,
                                    "Failed to cancel remote A2A task - agent may continue running"
                                );
                            }
                        } else if let (Some(session_id), Some(cancel_tx)) =
                            (acp_session_id, acp_cancel_tx)
                        {
                            // ACP agent - trigger graceful cancellation via channel
                            tracing::info!(
                                session_id = %session_id,
                                "Triggering graceful cancellation for ACP agent"
                            );

                            if let Err(e) = crate::acp::cancel_acp_task(&cancel_tx).await {
                                // Failed to send cancel signal - fall through to abort
                                tracing::warn!(
                                    session_id = %session_id,
                                    error = %e,
                                    "Failed to trigger ACP cancellation - will abort immediately"
                                );
                            } else {
                                // Signal sent successfully - wait for graceful completion
                                // Executor will send session/cancel and wait up to 10s for agent response
                                // Give it 15s total (10s grace + 5s buffer) before forcing abort
                                let completion_timeout = std::time::Duration::from_secs(15);

                                // Take the task out to await it
                                if let Some(mut task) = state.current_task.take() {
                                    match tokio::time::timeout(completion_timeout, &mut task.handle)
                                        .await
                                    {
                                        Ok(Ok(result)) => {
                                            // Task completed gracefully with proper cancellation result
                                            tracing::info!(
                                                session_id = %session_id,
                                                "ACP task completed gracefully after cancellation"
                                            );
                                            state.pending_result = Some(result);
                                            // Don't report synthetic canceled status - use executor's result
                                            return Ok(());
                                        }
                                        Ok(Err(e)) => {
                                            // Task panicked during cancellation
                                            tracing::error!(
                                                session_id = %session_id,
                                                error = %e,
                                                "Task panicked during cancellation"
                                            );
                                        }
                                        Err(_) => {
                                            // Timeout - executor didn't complete gracefully, force abort
                                            tracing::warn!(
                                                session_id = %session_id,
                                                "Cancellation grace period exceeded (15s), forcing abort"
                                            );
                                            task.handle.abort();
                                        }
                                    }
                                    // Put task back for cleanup if we didn't return early
                                    state.current_task = Some(task);
                                }
                            }
                        } else {
                            // Task is still initializing or metadata not yet available
                            tracing::debug!(
                                "Cannot propagate cancel - metadata not yet available (task will be aborted locally)"
                            );
                        }

                        // 2. Abort local JoinHandle (for A2A or failed ACP cancellation)
                        if let Some(watchdog) = state.cancel_current_task()
                            && let Err(e) = watchdog.await
                        {
                            tracing::error!("Watchdog task panicked: {}", e);
                        }

                        // 3. Report cancellation to scheduler (only if we didn't return early with graceful result)
                        state.pending_result = Some(crate::sync::TaskResult {
                            execution_id,
                            node_id,
                            task_status: common::A2ATaskStatus::canceled(
                                "Task cancelled by scheduler".to_string(),
                            ),
                            artifacts: None,
                        });
                    } else {
                        tracing::warn!("Received cancel command but no task is running");
                    }
                }
                WorkerCommand::Shutdown => {
                    tracing::info!("Received shutdown command");
                    std::process::exit(0);
                }
            }
        }
    }

    Ok(())
}

fn build_sync_request(state: &WorkerState) -> SyncRequest {
    if let Some(result) = &state.pending_result {
        SyncRequest::completed(result.clone())
    } else if let Some((execution_id, node_id)) = state.current_task_info() {
        SyncRequest::working(execution_id, node_id)
    } else {
        SyncRequest::idle()
    }
}

fn spawn_task(
    client: reqwest::Client,
    agents_config: AgentsConfig,
    task: TaskAssignment,
    state: &mut WorkerState,
) {
    let execution_id = task.execution_id.clone();
    let node_id = task.node_id.clone();

    // Create shared metadata (initially empty)
    let metadata = Arc::new(Mutex::new(A2ATaskMetadata::default()));
    let metadata_clone = metadata.clone();

    let handle =
        tokio::spawn(
            async move { execute_task(&client, &agents_config, &task, metadata_clone).await },
        );

    state.current_task = Some(TaskExecution {
        execution_id,
        node_id,
        handle,
        metadata,
    });
}
