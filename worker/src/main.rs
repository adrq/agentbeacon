mod acp;
mod cli;
mod executor;
mod sync;

use anyhow::{Context, Result};
use clap::Parser;
use tracing_subscriber::EnvFilter;

use crate::cli::Args;
use crate::executor::{SessionConfig, start_executor};
use crate::sync::{
    RetryConfig, SyncRequest, SyncResponse, perform_sync_long_poll, perform_sync_with_retry,
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

    let cwd = std::env::current_dir()
        .context("failed to get current directory")?
        .to_string_lossy()
        .to_string();

    let config = SessionConfig {
        session_id: session_id.to_string(),
        execution_id: initial_task.execution_id.clone(),
        agent_type,
        agent_config,
        sandbox_config,
        cwd,
    };

    // Start executor
    let mut handle = match start_executor(config).await {
        Ok(h) => h,
        Err(e) => {
            tracing::error!(error = %e, "Failed to start executor");
            // Report failure so scheduler can transition session to failed
            let _ = perform_sync_with_retry(
                client,
                &args.scheduler_url,
                &SyncRequest::with_result(session_id, None, None, Some(format!("{e:#}"))),
                true,
                retry_config,
            )
            .await;
            return Err(e);
        }
    };

    let mut current_task_payload = initial_task.task_payload;

    loop {
        // Running: send prompt to agent
        let turn_result = handle.send_prompt(&current_task_payload).await;

        let (agent_session_id, error, output) = match turn_result {
            Ok(r) => (r.agent_session_id, r.error, r.output),
            Err(e) => {
                tracing::error!(error = %e, "Prompt execution failed");
                (None, Some(format!("{e:#}")), None)
            }
        };

        if let Some(ref err) = error {
            tracing::warn!(error = %err, "Turn completed with error");
        }

        // Report result to scheduler
        let response = perform_sync_with_retry(
            client,
            &args.scheduler_url,
            &SyncRequest::with_result(session_id, agent_session_id, output, error),
            true,
            retry_config,
        )
        .await?;

        match response {
            SyncResponse::PromptDelivery { task, .. } => {
                current_task_payload = task.task_payload;
                continue;
            }
            SyncResponse::SessionComplete { .. } => {
                tracing::info!(session_id = %session_id, "Session complete");
                handle.stop().await?;
                return Ok(SessionExit::Done);
            }
            SyncResponse::Command { command } => match command.as_str() {
                "cancel" => {
                    tracing::info!(session_id = %session_id, "Cancel during result report");
                    handle.cancel().await?;
                    handle.stop().await?;
                    return Ok(SessionExit::Done);
                }
                "shutdown" => {
                    handle.stop().await?;
                    return Ok(SessionExit::ShutdownRequested);
                }
                _ => {} // Fall through to WaitingForEvent
            },
            _ => {} // NoAction or unexpected → enter WaitingForEvent
        }

        // WaitingForEvent loop
        loop {
            let wait_response = perform_sync_long_poll(
                client,
                &args.scheduler_url,
                &SyncRequest::waiting_for_event(session_id),
                args.long_poll_timeout,
            )
            .await;

            match wait_response {
                Ok(SyncResponse::PromptDelivery { task, .. }) => {
                    current_task_payload = task.task_payload;
                    break; // Back to Running (outer loop)
                }
                Ok(SyncResponse::SessionComplete { .. }) => {
                    tracing::info!(session_id = %session_id, "Session complete");
                    handle.stop().await?;
                    return Ok(SessionExit::Done);
                }
                Ok(SyncResponse::Command { command }) => match command.as_str() {
                    "cancel" => {
                        tracing::info!(session_id = %session_id, "Cancel while waiting");
                        handle.cancel().await?;
                        handle.stop().await?;
                        return Ok(SessionExit::Done);
                    }
                    "shutdown" => {
                        handle.stop().await?;
                        return Ok(SessionExit::ShutdownRequested);
                    }
                    _ => {
                        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                    }
                },
                Ok(SyncResponse::NoAction) => {
                    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                }
                Ok(other) => {
                    tracing::warn!("Unexpected response while waiting: {:?}", other);
                    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                }
                Err(e) => {
                    tracing::warn!(error = %e, "Long-poll failed, retrying");
                    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
                }
            }
        }
    }
}
