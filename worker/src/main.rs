mod acp;
mod cli;
mod executor;
mod sync;

use anyhow::{Context, Result};
use clap::Parser;
use std::future::Future;
use std::pin::Pin;
use std::time::Duration;
use tracing_subscriber::EnvFilter;

use crate::cli::Args;
use crate::executor::{
    AgentCommand, AgentEvent, ExecutorHandle, SessionConfig, extract_prompt_text, start_executor,
};
use crate::sync::{
    RetryConfig, SyncRequest, SyncResponse, WorkerMessageEvent, perform_sync_long_poll,
    perform_sync_with_retry, post_worker_message,
};

/// How a session exited — lets the caller distinguish normal completion from shutdown.
enum SessionExit {
    Done,
    ShutdownRequested,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive(tracing::Level::INFO.into()))
        .init();

    let args = Args::parse();

    validate_startup(&args).await?;

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
        result = run_worker_loop(&args, &client) => result,
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
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
        .context("failed to build health check client")?;

    let health_url = format!("{}/api/health", args.scheduler_url);
    match client.get(&health_url).send().await {
        Ok(response) if response.status().is_success() => {
            tracing::debug!("Scheduler health check passed");
        }
        Ok(response) => {
            tracing::warn!(
                "Scheduler health check: status {} - will retry during sync",
                response.status()
            );
        }
        Err(e) => {
            tracing::warn!(
                "Scheduler unreachable at {}: {} - will retry during sync",
                args.scheduler_url,
                e
            );
        }
    }

    Ok(())
}

async fn run_worker_loop(args: &Args, client: &reqwest::Client) -> Result<()> {
    let retry_config = RetryConfig {
        startup_max_attempts: args.startup_max_attempts,
        reconnect_max_attempts: args.reconnect_max_attempts,
        retry_delay: args.retry_delay,
    };

    let mut has_connected = false;

    tracing::info!("Starting worker loop, syncing every {:?}", args.interval);

    loop {
        let response = perform_sync_with_retry(
            client,
            &args.scheduler_url,
            &SyncRequest::idle(),
            has_connected,
            &retry_config,
        )
        .await?;

        has_connected = true;

        match response {
            SyncResponse::NoAction => {
                tokio::time::sleep(args.interval).await;
            }
            SyncResponse::SessionAssigned { session_id, task } => {
                tracing::info!(
                    session_id = %session_id,
                    execution_id = %task.execution_id,
                    "Session assigned"
                );

                match run_session(args, client, &retry_config, &session_id, task).await {
                    Ok(SessionExit::Done) => {}
                    Ok(SessionExit::ShutdownRequested) => {
                        tracing::info!("Received shutdown command during session");
                        return Ok(());
                    }
                    Err(e) => {
                        tracing::error!(
                            session_id = %session_id,
                            error = %e,
                            "Session failed"
                        );
                    }
                }
                // Return to idle after session ends
            }
            SyncResponse::Command { command } => match command.as_str() {
                "shutdown" => {
                    tracing::info!("Received shutdown command");
                    return Ok(());
                }
                "cancel" => {
                    tracing::debug!("Received cancel while idle, ignoring");
                    tokio::time::sleep(args.interval).await;
                }
                other => {
                    tracing::warn!(command = other, "Unknown command while idle");
                    tokio::time::sleep(args.interval).await;
                }
            },
            other => {
                tracing::warn!("Unexpected response while idle: {:?}", other);
                tokio::time::sleep(args.interval).await;
            }
        }
    }
}

/// Create a long-poll future that owns its SyncRequest.
///
/// `perform_sync_long_poll` borrows its `&SyncRequest`, so we can't pass a
/// temporary directly into `Box::pin(...)` — the temporary would be dropped
/// before the future runs. This helper moves the owned request into the
/// async block.
fn start_long_poll<'a>(
    client: &'a reqwest::Client,
    scheduler_url: &'a str,
    session_id: &str,
    long_poll_timeout: Duration,
) -> Pin<Box<dyn Future<Output = Result<SyncResponse>> + Send + 'a>> {
    let request = SyncRequest::waiting_for_event(session_id);
    Box::pin(async move {
        perform_sync_long_poll(client, scheduler_url, &request, long_poll_timeout).await
    })
}

/// Drains mid-turn message events from the channel and POSTs them to the scheduler.
/// Serializes delivery to prevent burst-induced resource exhaustion.
async fn message_sender_task(
    client: reqwest::Client,
    scheduler_url: String,
    mut rx: tokio::sync::mpsc::UnboundedReceiver<WorkerMessageEvent>,
) {
    while let Some(event) = rx.recv().await {
        if let Err(e) = post_worker_message(&client, &scheduler_url, &event).await {
            tracing::warn!(error = %e, "failed to forward mid-turn message");
        }
    }
}

async fn run_session(
    args: &Args,
    client: &reqwest::Client,
    retry_config: &RetryConfig,
    session_id: &str,
    initial_task: crate::sync::TaskAssignment,
) -> Result<SessionExit> {
    let task_payload = &initial_task.task_payload;

    let agent_type = task_payload
        .get("agent_type")
        .and_then(|v| v.as_str())
        .unwrap_or("acp")
        .to_string();

    let agent_config = task_payload
        .get("agent_config")
        .cloned()
        .unwrap_or_default();

    let sandbox_config = task_payload
        .get("sandbox_config")
        .cloned()
        .unwrap_or_default();

    let cwd = task_payload
        .get("cwd")
        .and_then(|v| v.as_str())
        .filter(|s| !s.trim().is_empty())
        .map(|s| s.to_string())
        .unwrap_or_else(|| {
            std::env::current_dir()
                .unwrap_or_else(|e| {
                    tracing::warn!(error = %e, "current_dir() failed, falling back to /tmp");
                    std::path::PathBuf::from("/tmp")
                })
                .to_string_lossy()
                .to_string()
        });

    let config = SessionConfig {
        session_id: session_id.to_string(),
        execution_id: initial_task.execution_id.clone(),
        agent_type,
        agent_config,
        sandbox_config,
        cwd,
        scheduler_url: args.scheduler_url.clone(),
        node_path: args.node_path.clone(),
        executors_dir: args.executors_dir.clone(),
    };

    // Start executor
    let executor = match start_executor(config).await {
        Ok(h) => h,
        Err(e) => {
            tracing::error!(error = %e, "Failed to start executor");
            // Report failure so scheduler can transition session to failed
            let _ = perform_sync_with_retry(
                client,
                &args.scheduler_url,
                &SyncRequest::with_result(
                    session_id,
                    None,
                    None,
                    Some(format!("{e:#}")),
                    Some("executor_failed".into()),
                    None,
                ),
                true,
                retry_config,
            )
            .await;
            return Err(e);
        }
    };

    let ExecutorHandle {
        cmd_tx,
        mut event_rx,
        task_handle,
    } = executor;

    // Mid-turn message forwarding channel + sender task
    let (msg_fwd_tx, msg_fwd_rx) = tokio::sync::mpsc::unbounded_channel::<WorkerMessageEvent>();
    let sender_client = client.clone();
    let sender_url = args.scheduler_url.clone();
    let sender_handle = tokio::spawn(async move {
        message_sender_task(sender_client, sender_url, msg_fwd_rx).await;
    });

    // Start the agent with the initial task payload
    let _ = cmd_tx.send(AgentCommand::Start(initial_task.task_payload.clone()));

    // Long-poll is NOT started yet — defer until agent is initialized (Init event)
    // or the first TurnComplete. This prevents premature task delivery before the
    // agent has processed its initial prompt.
    let mut poll_fut: Option<Pin<Box<dyn Future<Output = Result<SyncResponse>> + Send>>> = None;

    let mut agent_session_id: Option<String> = None;
    let mut turns_in_flight: usize = 1; // starts at 1 — processing initial prompt
    let mut cancelling = false;
    let mut completing = false;
    let mut mid_turn_forwarded = false;

    let exit = loop {
        tokio::select! {
            biased; // prefer agent events (drain before checking scheduler)

            // Branch A: Agent event
            event = event_rx.recv() => {
                match event {
                    Some(AgentEvent::TurnComplete(result)) => {
                        turns_in_flight = turns_in_flight.saturating_sub(1);
                        agent_session_id = result.agent_session_id.clone()
                            .or(agent_session_id);

                        let is_cancelled = cancelling
                            || result.error_kind.as_ref()
                                .is_some_and(|ek| ek.as_str() == "cancelled");

                        // Suppress output if already forwarded via mid-turn messages
                        let output_for_sync = if mid_turn_forwarded {
                            None
                        } else {
                            result.output
                        };
                        mid_turn_forwarded = false;

                        // Drop any in-flight long-poll future before sending
                        // sync-with-result (side-effect: cancels the request).
                        drop(poll_fut.take());

                        // Report result to scheduler
                        let response = match perform_sync_with_retry(client, &args.scheduler_url,
                            &SyncRequest::with_result(session_id,
                                agent_session_id.clone(),
                                output_for_sync, result.error,
                                result.error_kind.map(|ek| ek.as_str().to_string()),
                                result.stderr,
                            ), true, retry_config,
                        ).await {
                            Ok(r) => r,
                            Err(e) => {
                                tracing::error!(error = %e, "failed to report result to scheduler");
                                let _ = cmd_tx.send(AgentCommand::Stop);
                                break SessionExit::Done;
                            }
                        };

                        if is_cancelled || completing {
                            let _ = cmd_tx.send(AgentCommand::Stop);
                            break SessionExit::Done;
                        }

                        match response {
                            SyncResponse::PromptDelivery { task, .. } => {
                                match extract_prompt_text(&task.task_payload) {
                                    Ok(text) => {
                                        turns_in_flight += 1;
                                        let _ = cmd_tx.send(AgentCommand::Prompt(text));
                                    }
                                    Err(e) => {
                                        tracing::error!(error = %e, "bad task payload");
                                        let _ = perform_sync_with_retry(client,
                                            &args.scheduler_url,
                                            &SyncRequest::with_result(session_id,
                                                agent_session_id.clone(),
                                                None, Some(format!("Bad task payload: {e}")),
                                                Some("internal_error".into()),
                                                None),
                                            true, retry_config).await;
                                    }
                                }
                                // Start long-poll for mid-turn messages
                                poll_fut = Some(start_long_poll(
                                    client, &args.scheduler_url,
                                    session_id, args.long_poll_timeout,
                                ));
                            }
                            SyncResponse::SessionComplete { .. } => {
                                let _ = cmd_tx.send(AgentCommand::Stop);
                                break SessionExit::Done;
                            }
                            SyncResponse::Command { command } if command == "cancel" => {
                                let _ = cmd_tx.send(AgentCommand::Cancel);
                                break SessionExit::Done;
                            }
                            SyncResponse::Command { command } if command == "shutdown" => {
                                let _ = cmd_tx.send(AgentCommand::Stop);
                                break SessionExit::ShutdownRequested;
                            }
                            _ => {
                                // NoAction — agent is idle, start long-poll to
                                // wait for next task (user message, etc.)
                                poll_fut = Some(start_long_poll(
                                    client, &args.scheduler_url,
                                    session_id, args.long_poll_timeout,
                                ));
                            }
                        }
                    }
                    Some(AgentEvent::Init { session_id: sid }) => {
                        agent_session_id = Some(sid);
                        // Agent is initialized and processing the initial prompt.
                        // Start the long-poll now so mid-turn messages can be delivered.
                        if poll_fut.is_none() {
                            poll_fut = Some(start_long_poll(
                                client, &args.scheduler_url,
                                session_id, args.long_poll_timeout,
                            ));
                        }
                    }
                    Some(AgentEvent::Message { output }) => {
                        mid_turn_forwarded = true;
                        let _ = msg_fwd_tx.send(WorkerMessageEvent {
                            session_id: session_id.to_string(),
                            execution_id: initial_task.execution_id.clone(),
                            payload: output,
                        });
                    }
                    Some(AgentEvent::ProcessDied { error, stderr }) => {
                        // Report failure to scheduler
                        let _ = perform_sync_with_retry(client, &args.scheduler_url,
                            &SyncRequest::with_result(session_id, agent_session_id.clone(),
                                None, Some(error), Some("executor_failed".into()),
                                stderr),
                            true, retry_config).await;
                        break SessionExit::Done;
                    }
                    None => {
                        // Channel closed — executor task exited
                        break SessionExit::Done;
                    }
                }
            }

            // Branch B: Scheduler long-poll (only active when poll_fut is Some)
            response = async {
                match poll_fut.as_mut() {
                    Some(f) => f.await,
                    None => std::future::pending().await,
                }
            }, if poll_fut.is_some() => {
                // poll_fut was consumed, clear it before processing
                poll_fut = None;

                match response {
                    Ok(SyncResponse::PromptDelivery { task, .. }) => {
                        if !cancelling {
                            match extract_prompt_text(&task.task_payload) {
                                Ok(text) => {
                                    turns_in_flight += 1;
                                    let _ = cmd_tx.send(AgentCommand::Prompt(text));
                                }
                                Err(e) => {
                                    tracing::error!(error = %e, "bad task payload");
                                    let _ = perform_sync_with_retry(client,
                                        &args.scheduler_url,
                                        &SyncRequest::with_result(session_id,
                                            agent_session_id.clone(),
                                            None, Some(format!("Bad task payload: {e}")),
                                            Some("internal_error".into()),
                                            None),
                                        true, retry_config).await;
                                }
                            }
                        }
                    }
                    Ok(SyncResponse::Command { command }) if command == "cancel" => {
                        let _ = cmd_tx.send(AgentCommand::Cancel);
                        cancelling = true;
                        // Don't restart the long-poll — only listen for TurnComplete/ProcessDied
                        continue;
                    }
                    Ok(SyncResponse::Command { command }) if command == "shutdown" => {
                        let _ = cmd_tx.send(AgentCommand::Stop);
                        break SessionExit::ShutdownRequested;
                    }
                    Ok(SyncResponse::SessionComplete { .. }) => {
                        if turns_in_flight > 0 {
                            // Cancel the in-progress turn so the executor can SIGTERM the subprocess
                            let _ = cmd_tx.send(AgentCommand::Cancel);
                            completing = true;
                            continue;
                        } else {
                            let _ = cmd_tx.send(AgentCommand::Stop);
                            break SessionExit::Done;
                        }
                    }
                    Ok(_) => {
                        // NoAction — normal, will restart long-poll below
                    }
                    Err(e) => {
                        tracing::warn!(error = %e, "long-poll error");
                        tokio::time::sleep(std::time::Duration::from_secs(1)).await;
                    }
                }
                // Restart the long-poll (cancel path skips this via `continue`)
                poll_fut = Some(start_long_poll(
                    client, &args.scheduler_url,
                    session_id, args.long_poll_timeout,
                ));
            }
        }
    };

    // Graceful shutdown: drop channels to signal background tasks, await them
    drop(cmd_tx);
    drop(msg_fwd_tx);
    let _ = task_handle.await;
    let _ = sender_handle.await;

    Ok(exit)
}
