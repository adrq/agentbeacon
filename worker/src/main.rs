#[cfg(target_env = "musl")]
#[global_allocator]
static GLOBAL: mimalloc::MiMalloc = mimalloc::MiMalloc;

mod acp;
mod cli;
mod embedded_executors;
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
    AgentCommand, AgentEvent, ExecutorHandle, SessionConfig, extract_parts, start_executor,
};
use crate::sync::{
    CommandAction, ExecutorReport, RetryConfig, SyncRequest, SyncResponse, TurnMessage, TurnResult,
    WorkerMessageEvent, perform_sync_long_poll, perform_sync_with_retry, post_worker_message,
};

/// Time to wait for executor response to a control command (cancel/stop_turn)
/// before escalating to SIGINT.
const CONTROL_CMD_SDK_TIMEOUT: Duration = Duration::from_secs(5);

/// Time to wait after SIGINT before escalating to SIGKILL.
const CONTROL_CMD_SIGINT_TIMEOUT: Duration = Duration::from_secs(3);

/// How a session exited — lets the caller distinguish normal completion from shutdown.
#[allow(dead_code)]
enum SessionExit {
    Done,
    ShutdownRequested,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive(tracing::Level::INFO.into()))
        .init();

    let mut args = Args::parse();

    if args.setup {
        return run_setup(&args);
    }

    let scheduler_url = args.scheduler_url.take().expect(
        "scheduler_url must be set in daemon mode (enforced by clap required_unless_present)",
    );

    let worker_id = args
        .worker_id
        .take()
        .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

    if args.executors_dir.is_none() && std::env::var("AGENTBEACON_EXECUTORS_DIR").is_err() {
        let data_dir = embedded_executors::resolve_data_dir();
        let dir = embedded_executors::extract_if_needed(&data_dir)
            .context("Failed to extract embedded executors")?;
        args.executors_dir = Some(dir.to_string_lossy().to_string());
        args.node_modules_dir = Some(data_dir.join("node_modules").to_string_lossy().to_string());
    }

    validate_startup(&scheduler_url).await?;

    let client = reqwest::Client::builder()
        .timeout(args.http_timeout)
        .build()
        .context("failed to build HTTP client")?;

    tracing::info!(
        scheduler_url = %scheduler_url,
        worker_id = %worker_id,
        interval = ?args.interval,
        "Worker started"
    );

    tokio::select! {
        result = run_worker_loop(&scheduler_url, &worker_id, &args, &client) => result,
        _ = shutdown_signal() => {
            tracing::info!("Received shutdown signal, exiting gracefully");
            Ok(())
        }
    }
}

fn run_setup(args: &Args) -> Result<()> {
    if args.executors_dir.is_some() || std::env::var("AGENTBEACON_EXECUTORS_DIR").is_ok() {
        tracing::warn!(
            "AGENTBEACON_EXECUTORS_DIR is set — --setup installs to the default data directory, \
             not the override location. SDK dependencies for development must be installed manually."
        );
    }

    let data_dir = embedded_executors::resolve_data_dir();
    embedded_executors::extract_if_needed(&data_dir)
        .context("Failed to extract embedded executors")?;

    if args.status {
        return show_sdk_status(&data_dir);
    }

    embedded_executors::install_sdks(&data_dir)?;

    show_sdk_status(&data_dir)
}

fn show_sdk_status(data_dir: &std::path::Path) -> Result<()> {
    println!("AgentBeacon SDK status ({})", data_dir.display());
    for pkg in embedded_executors::SDK_PACKAGES {
        match embedded_executors::check_sdk_installed(data_dir, pkg.npm_package) {
            Some(version) => println!("  {}: installed (v{version})", pkg.driver),
            None => println!("  {}: not installed", pkg.driver),
        }
    }
    Ok(())
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

async fn validate_startup(scheduler_url: &str) -> Result<()> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
        .context("failed to build health check client")?;

    let health_url = format!("{}/api/health", scheduler_url);
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
                scheduler_url,
                e
            );
        }
    }

    Ok(())
}

async fn run_worker_loop(
    scheduler_url: &str,
    worker_id: &str,
    args: &Args,
    client: &reqwest::Client,
) -> Result<()> {
    let retry_config = RetryConfig {
        startup_max_attempts: args.startup_max_attempts,
        reconnect_max_attempts: args.reconnect_max_attempts,
        retry_delay: args.retry_delay,
    };

    let mut has_connected = false;

    tracing::info!("Starting worker loop (long-poll)");

    loop {
        let response = perform_sync_with_retry(
            client,
            scheduler_url,
            &SyncRequest::empty(worker_id),
            has_connected,
            &retry_config,
        )
        .await?;

        has_connected = true;

        match response {
            SyncResponse::NoAction => {}
            SyncResponse::Command { token, action } => match action {
                CommandAction::Assign {
                    session_id,
                    execution_id,
                    payload,
                    resume,
                    cwd,
                    driver,
                    agent_config: cmd_agent_config,
                    agent_session_id,
                    project_id,
                    mcp_servers,
                    next_msg_seq,
                } => {
                    tracing::info!(
                        session_id = %session_id,
                        resume = resume,
                        "Session assigned"
                    );

                    match run_session(
                        scheduler_url,
                        worker_id,
                        args,
                        client,
                        &retry_config,
                        &session_id,
                        &execution_id,
                        &token,
                        payload,
                        resume,
                        cwd,
                        driver,
                        cmd_agent_config,
                        agent_session_id,
                        project_id,
                        mcp_servers,
                        next_msg_seq,
                    )
                    .await
                    {
                        Ok(SessionExit::Done) => {}
                        Ok(SessionExit::ShutdownRequested) => {
                            tracing::info!("Shutdown requested during session");
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
                }
                other => {
                    tracing::warn!("Unexpected command while idle: {:?}", other);
                }
            },
        }
    }
}

/// Drains mid-turn message events from the channel and POSTs them to the scheduler.
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

/// Create a long-poll future that owns its SyncRequest.
fn start_long_poll<'a>(
    client: &'a reqwest::Client,
    scheduler_url: &'a str,
    worker_id: &str,
    report: Option<ExecutorReport>,
    ack_token: Option<String>,
    long_poll_timeout: Duration,
) -> Pin<Box<dyn Future<Output = Result<SyncResponse>> + Send + 'a>> {
    let mut request = SyncRequest::empty(worker_id);
    request.executor_report = report;
    request.command_ack = ack_token;
    Box::pin(async move {
        perform_sync_long_poll(client, scheduler_url, &request, long_poll_timeout).await
    })
}

#[allow(clippy::too_many_arguments)]
async fn run_session(
    scheduler_url: &str,
    worker_id: &str,
    args: &Args,
    client: &reqwest::Client,
    retry_config: &RetryConfig,
    session_id: &str,
    execution_id: &str,
    initial_token: &str,
    initial_payload: Option<serde_json::Value>,
    resume: bool,
    cwd: Option<String>,
    driver: serde_json::Value,
    cmd_agent_config: serde_json::Value,
    initial_agent_session_id: Option<String>,
    cmd_project_id: Option<String>,
    cmd_mcp_servers: Option<serde_json::Value>,
    next_msg_seq: i64,
) -> Result<SessionExit> {
    let agent_type = driver
        .get("platform")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();

    let agent_config =
        resolve_agent_config(cmd_agent_config, &initial_payload, &driver, &agent_type)?;

    let sandbox_config = driver
        .get("config")
        .cloned()
        .unwrap_or(serde_json::Value::Null);

    let project_id = cmd_project_id.or_else(|| {
        initial_payload
            .as_ref()
            .and_then(|p| p.get("project_id"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
    });

    let user_mcp_servers = cmd_mcp_servers.unwrap_or_else(|| {
        initial_payload
            .as_ref()
            .and_then(|p| p.get("mcp_servers").cloned())
            .unwrap_or(serde_json::Value::Null)
    });

    let resolved_cwd = cwd.unwrap_or_else(|| {
        std::env::current_dir()
            .unwrap_or_else(|e| {
                tracing::warn!(error = %e, "current_dir() failed, falling back to /tmp");
                std::path::PathBuf::from("/tmp")
            })
            .to_string_lossy()
            .to_string()
    });

    let task_payload = {
        let mut p = if resume && initial_payload.is_none() {
            serde_json::json!({
                "message": {
                    "role": "ROLE_USER",
                    "parts": [{"text": "[System] Session recovered after interruption. Resume where you left off."}]
                }
            })
        } else {
            initial_payload
                .clone()
                .unwrap_or_else(|| serde_json::json!({}))
        };
        if resume && let Some(ref asid) = initial_agent_session_id {
            p["resumeSessionId"] = serde_json::json!(asid);
        }
        p
    };

    let config = SessionConfig {
        session_id: session_id.to_string(),
        execution_id: execution_id.to_string(),
        agent_type,
        agent_config,
        sandbox_config,
        cwd: resolved_cwd,
        scheduler_url: scheduler_url.to_string(),
        node_path: args.node_path.clone(),
        executors_dir: args.executors_dir.clone(),
        node_modules_dir: args.node_modules_dir.clone(),
        inactivity_timeout: args.inactivity_timeout,
        project_id,
        user_mcp_servers,
    };

    let executor = match start_executor(config).await {
        Ok(h) => h,
        Err(e) => {
            tracing::error!(error = %e, "Failed to start executor");
            let _ = perform_sync_with_retry(
                client,
                scheduler_url,
                &SyncRequest::with_report_and_ack(
                    worker_id,
                    ExecutorReport {
                        session_id: session_id.to_string(),
                        executor_state: "crashed".to_string(),
                        agent_session_id: None,
                    },
                    initial_token,
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
        child_pid,
    } = executor;

    let (msg_fwd_tx, msg_fwd_rx) = tokio::sync::mpsc::unbounded_channel::<WorkerMessageEvent>();
    let sender_client = client.clone();
    let sender_url = scheduler_url.to_string();
    let sender_handle = tokio::spawn(async move {
        message_sender_task(sender_client, sender_url, msg_fwd_rx).await;
    });

    let _ = cmd_tx.send(AgentCommand::Start(task_payload));

    let mut agent_session_id: Option<String> = initial_agent_session_id;
    let mut msg_seq: i64 = next_msg_seq;
    let mut turn_messages: Vec<TurnMessage> = Vec::new();
    let mut last_ack: Option<String> = Some(initial_token.to_string());
    let mut agent_busy = true;
    let mut cancel_ack_pending = false;
    let mut pending_stop_token: Option<String> = None;

    let mut escalation_deadline: Option<tokio::time::Instant> = None;
    let mut escalation_stage: u8 = 0;

    let mut poll_fut: Option<Pin<Box<dyn Future<Output = Result<SyncResponse>> + Send>>> = None;

    let exit = loop {
        tokio::select! {
            biased;

            event = event_rx.recv() => {
                match event {
                    Some(AgentEvent::TurnComplete(result)) => {
                        agent_busy = false;
                        agent_session_id = result.agent_session_id.clone()
                            .or(agent_session_id);

                        let mut messages_for_sync = std::mem::take(&mut turn_messages);

                        if messages_for_sync.is_empty()
                            && let Some(output) = result.output.clone() {
                                msg_seq += 1;
                                messages_for_sync.push(TurnMessage { msg_seq, payload: output });
                            }

                        drop(poll_fut.take());

                        let report = ExecutorReport {
                            session_id: session_id.to_string(),
                            executor_state: "idle".to_string(),
                            agent_session_id: agent_session_id.clone(),
                        };
                        let turn_result = TurnResult {
                            session_id: session_id.to_string(),
                            messages: messages_for_sync,
                            error: result.error,
                            error_kind: result.error_kind.map(|ek| ek.as_str().to_string()),
                            stderr: result.stderr,
                        };

                        let mut req = SyncRequest::with_report_and_result(
                            worker_id, report, turn_result,
                        );
                        if cancel_ack_pending {
                            req.command_ack = last_ack.take();
                        } else if let Some(stop_token) = pending_stop_token.take() {
                            req.command_ack = Some(stop_token);
                        } else {
                            req.command_ack = last_ack.take();
                        }

                        let response = match perform_sync_with_retry(
                            client, scheduler_url, &req, true, retry_config,
                        ).await {
                            Ok(r) => r,
                            Err(e) => {
                                tracing::error!(error = %e, "failed to report turn result");
                                let _ = cmd_tx.send(AgentCommand::Stop);
                                break SessionExit::Done;
                            }
                        };

                        let was_cancel_ack = cancel_ack_pending;
                        cancel_ack_pending = false;
                        escalation_deadline = None;
                        escalation_stage = 0;

                        match response {
                            SyncResponse::Command { token, action } => {
                                if was_cancel_ack {
                                    break SessionExit::Done;
                                }
                                if let Some(exit) = handle_command(
                                    &cmd_tx, &token, &action, session_id,
                                    &mut agent_busy, &mut last_ack,
                                    &mut cancel_ack_pending, &mut pending_stop_token,
                                ) {
                                    break exit;
                                }
                                if cancel_ack_pending || pending_stop_token.is_some() {
                                    escalation_deadline = Some(tokio::time::Instant::now() + CONTROL_CMD_SDK_TIMEOUT);
                                    escalation_stage = 0;
                                }
                                let ack_for_poll = if pending_stop_token.is_some() { None } else { last_ack.clone() };
                                poll_fut = Some(start_long_poll(
                                    client, scheduler_url, worker_id,
                                    None, ack_for_poll, args.long_poll_timeout,
                                ));
                            }
                            SyncResponse::NoAction => {
                                if was_cancel_ack {
                                    break SessionExit::Done;
                                }
                                poll_fut = Some(start_long_poll(
                                    client, scheduler_url, worker_id,
                                    None, last_ack.clone(), args.long_poll_timeout,
                                ));
                            }
                        }
                    }
                    Some(AgentEvent::Init { session_id: sid }) => {
                        agent_session_id = Some(sid);
                        if poll_fut.is_none() {
                            let report = ExecutorReport {
                                session_id: session_id.to_string(),
                                executor_state: "running".to_string(),
                                agent_session_id: agent_session_id.clone(),
                            };
                            poll_fut = Some(start_long_poll(
                                client, scheduler_url, worker_id,
                                Some(report), last_ack.clone(),
                                args.long_poll_timeout,
                            ));
                        }
                    }
                    Some(AgentEvent::Message { output, ephemeral }) => {
                        msg_seq += 1;
                        if !ephemeral {
                            turn_messages.push(TurnMessage { msg_seq, payload: output.clone() });
                        }
                        let _ = msg_fwd_tx.send(WorkerMessageEvent {
                            worker_id: worker_id.to_string(),
                            session_id: session_id.to_string(),
                            execution_id: execution_id.to_string(),
                            msg_seq,
                            payload: output,
                            ephemeral,
                        });
                    }
                    Some(AgentEvent::ProcessDied { error, stderr }) => {
                        let report = ExecutorReport {
                            session_id: session_id.to_string(),
                            executor_state: "crashed".to_string(),
                            agent_session_id: agent_session_id.clone(),
                        };
                        let turn_result = TurnResult {
                            session_id: session_id.to_string(),
                            messages: std::mem::take(&mut turn_messages),
                            error: Some(error),
                            error_kind: Some("executor_failed".to_string()),
                            stderr,
                        };
                        let mut req = SyncRequest::with_report_and_result(
                            worker_id, report, turn_result,
                        );
                        if cancel_ack_pending {
                            req.command_ack = last_ack.take();
                        } else if let Some(stop_token) = pending_stop_token.take() {
                            req.command_ack = Some(stop_token);
                        } else {
                            req.command_ack = last_ack.take();
                        }
                        let _ = perform_sync_with_retry(
                            client, scheduler_url, &req, true, retry_config,
                        ).await;
                        break SessionExit::Done;
                    }
                    None => {
                        let pending_ack = if cancel_ack_pending {
                            last_ack.take()
                        } else {
                            pending_stop_token.take().or_else(|| last_ack.take())
                        };
                        if let Some(ack) = pending_ack {
                            let mut req = SyncRequest::empty(worker_id);
                            req.command_ack = Some(ack);
                            let _ = perform_sync_with_retry(
                                client, scheduler_url, &req, true, retry_config,
                            ).await;
                        }
                        break SessionExit::Done;
                    }
                }
            }

            response = async {
                match poll_fut.as_mut() {
                    Some(f) => f.await,
                    None => std::future::pending().await,
                }
            }, if poll_fut.is_some() => {
                poll_fut = None;
                let _ = &poll_fut;

                match response {
                    Ok(SyncResponse::Command { token, action }) => {
                        if !cancel_ack_pending {
                            last_ack = None;
                        }

                        if let Some(exit) = handle_command(
                            &cmd_tx, &token, &action, session_id,
                            &mut agent_busy, &mut last_ack,
                            &mut cancel_ack_pending, &mut pending_stop_token,
                        ) {
                            break exit;
                        }
                        if cancel_ack_pending || pending_stop_token.is_some() {
                            escalation_deadline = Some(tokio::time::Instant::now() + CONTROL_CMD_SDK_TIMEOUT);
                            escalation_stage = 0;
                        }
                    }
                    Ok(SyncResponse::NoAction) => {
                        if !cancel_ack_pending {
                            last_ack = None;
                        }
                    }
                    Err(e) => {
                        tracing::warn!(error = %e, "long-poll error");
                        tokio::time::sleep(Duration::from_secs(1)).await;
                    }
                }

                let state = if agent_busy { "running" } else { "idle" };
                let report = ExecutorReport {
                    session_id: session_id.to_string(),
                    executor_state: state.to_string(),
                    agent_session_id: agent_session_id.clone(),
                };
                let ack_for_poll = if cancel_ack_pending || pending_stop_token.is_some() {
                    None
                } else {
                    last_ack.clone()
                };
                poll_fut = Some(start_long_poll(
                    client, scheduler_url, worker_id,
                    Some(report), ack_for_poll,
                    args.long_poll_timeout,
                ));
            }

            _ = async {
                match escalation_deadline {
                    Some(deadline) => tokio::time::sleep_until(deadline).await,
                    None => std::future::pending().await,
                }
            }, if escalation_deadline.is_some() => {
                escalation_stage += 1;
                match escalation_stage {
                    1 => {
                        tracing::warn!("Escalation: executor did not respond to control command, sending SIGINT");
                        if let Some(pid) = child_pid {
                            let _ = nix::sys::signal::kill(
                                nix::unistd::Pid::from_raw(pid as i32),
                                nix::sys::signal::Signal::SIGINT,
                            );
                        }
                        escalation_deadline = Some(tokio::time::Instant::now() + CONTROL_CMD_SIGINT_TIMEOUT);
                    }
                    _ => {
                        tracing::warn!("Escalation: SIGINT timed out, sending SIGKILL");
                        if let Some(pid) = child_pid {
                            let _ = nix::sys::signal::kill(
                                nix::unistd::Pid::from_raw(pid as i32),
                                nix::sys::signal::Signal::SIGKILL,
                            );
                        }
                        escalation_deadline = None;
                    }
                }
            }
        }
    };

    drop(cmd_tx);
    drop(msg_fwd_tx);
    let _ = task_handle.await;
    let _ = sender_handle.await;

    Ok(exit)
}

/// Handle a command from the scheduler. Returns Some(SessionExit) if the session should end.
#[allow(clippy::too_many_arguments)]
fn handle_command(
    cmd_tx: &tokio::sync::mpsc::UnboundedSender<AgentCommand>,
    token: &str,
    action: &CommandAction,
    session_id: &str,
    agent_busy: &mut bool,
    last_ack: &mut Option<String>,
    cancel_ack_pending: &mut bool,
    pending_stop_token: &mut Option<String>,
) -> Option<SessionExit> {
    match action {
        CommandAction::FeedTurn { payload, .. } => {
            match extract_parts(payload) {
                Ok(parts) => {
                    *agent_busy = true;
                    let _ = cmd_tx.send(AgentCommand::Prompt(parts));
                    *last_ack = Some(token.to_string());
                }
                Err(e) => {
                    tracing::error!(error = %e, "bad feed_turn payload, withholding ack");
                }
            }
            None
        }
        CommandAction::StopTurn { .. } => {
            if *agent_busy {
                let _ = cmd_tx.send(AgentCommand::StopTurn);
                *pending_stop_token = Some(token.to_string());
            } else {
                *last_ack = Some(token.to_string());
            }
            None
        }
        CommandAction::Cancel { .. } => {
            let _ = cmd_tx.send(AgentCommand::Cancel);
            *pending_stop_token = None;
            *last_ack = Some(token.to_string());
            *cancel_ack_pending = true;
            None
        }
        CommandAction::Assign { .. } => {
            tracing::warn!("unexpected assign command during session {session_id}");
            None
        }
    }
}

fn is_sdk_agent(agent_type: &str) -> bool {
    matches!(agent_type, "claude_sdk" | "copilot_sdk")
}

/// Resolve agent_config from the assign command, with fallback for non-SDK agents.
fn resolve_agent_config(
    cmd_agent_config: serde_json::Value,
    initial_payload: &Option<serde_json::Value>,
    driver: &serde_json::Value,
    agent_type: &str,
) -> Result<serde_json::Value> {
    if cmd_agent_config.is_object() {
        return Ok(cmd_agent_config);
    }

    if is_sdk_agent(agent_type) {
        anyhow::bail!("missing required agent_config (agent_type={agent_type})");
    }

    if let Some(payload_ac) = initial_payload
        .as_ref()
        .and_then(|p| p.get("agent_config"))
        .filter(|v| v.is_object())
    {
        Ok(payload_ac.clone())
    } else {
        Ok(driver
            .get("config")
            .cloned()
            .unwrap_or(serde_json::Value::Null))
    }
}
