#[cfg(target_env = "musl")]
#[global_allocator]
static GLOBAL: mimalloc::MiMalloc = mimalloc::MiMalloc;

use anyhow::{Context, Result};
use clap::Parser;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tokio::signal;
use tokio::sync::broadcast;
use tracing::{info, warn};

use scheduler::{
    app::{AppState, EventNotification, create_router},
    db,
    queue::TaskQueue,
    search::WikiSearchIndex,
    services,
    supervisor::Supervisor,
    telemetry,
};

/// AgentBeacon — Agent orchestration and execution tracking
#[derive(Parser, Debug)]
#[command(name = "agentbeacon")]
#[command(version)]
#[command(about = "AgentBeacon scheduler with optional worker supervision", long_about = None)]
struct Cli {
    /// Port to listen on
    #[arg(short, long, default_value = "9456", env = "AGENTBEACON_PORT")]
    port: u16,

    /// Database URL (SQLite or PostgreSQL)
    /// Examples:
    ///   sqlite:///tmp/scheduler.db
    ///   postgres://user:pass@localhost/dbname
    /// Defaults to sqlite://scheduler-{port}.db when not specified.
    #[arg(long, env = "DATABASE_URL")]
    db_url: Option<String>,

    /// Directory for wiki search index files.
    /// Defaults to wiki-index-{port}/ when not specified.
    #[arg(long, env = "AGENTBEACON_WIKI_INDEX_DIR")]
    wiki_index_dir: Option<String>,

    /// Maximum auto-spawned workers. Default: unlimited.
    /// Set to 0 to disable auto-spawning (for external/test workers).
    #[arg(long, env = "AGENTBEACON_MAX_WORKERS")]
    max_workers: Option<usize>,

    /// Max workers spawned per 10s tick (thundering-herd prevention)
    #[arg(long, default_value_t = 8, env = "AGENTBEACON_MAX_SPAWN_PER_TICK")]
    max_spawn_per_tick: usize,

    /// Idle timeout passed through to spawned worker processes
    #[arg(long, default_value = "300s", value_parser = parse_duration, env = "AGENTBEACON_IDLE_TIMEOUT")]
    idle_timeout: std::time::Duration,

    /// Worker sync polling interval (e.g., '1s', '500ms')
    #[arg(long)]
    worker_poll_interval: Option<String>,

    /// Run SDK setup (delegates to worker binary)
    #[arg(long, help_heading = "Setup")]
    setup: bool,

    /// Show installed SDK status without installing (use with --setup)
    #[arg(long, requires = "setup", help_heading = "Setup")]
    status: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    if cli.setup {
        return run_scheduler_setup(&cli);
    }

    telemetry::init_telemetry();

    sqlx::any::install_default_drivers();

    info!("AgentBeacon starting...");
    info!(
        "Configuration: port={}, max_workers={:?}",
        cli.port, cli.max_workers
    );

    bootstrap(cli).await
}

fn run_scheduler_setup(cli: &Cli) -> Result<()> {
    use scheduler::supervisor;

    let worker_bin = supervisor::worker_binary_path();
    let mut cmd = std::process::Command::new(&worker_bin);
    cmd.arg("--setup");
    if cli.status {
        cmd.arg("--status");
    }

    let status = cmd
        .status()
        .with_context(|| format!("failed to run {}", worker_bin.display()))?;

    if !status.success() {
        anyhow::bail!(
            "worker setup failed with exit code {}",
            status.code().unwrap_or(1)
        );
    }
    Ok(())
}

async fn bootstrap(cli: Cli) -> Result<()> {
    let dev_mode = std::env::var("DEV_MODE").map(|v| v == "1").unwrap_or(false);

    let db_url = cli
        .db_url
        .unwrap_or_else(|| format!("sqlite://scheduler-{}.db", cli.port));

    info!("Connecting to database: {}", db_url);
    let db_pool = db::pool::create(&db_url)
        .await
        .context("Failed to create database pool")?;

    sqlx::query("SELECT 1")
        .fetch_one(db_pool.as_ref())
        .await
        .context("Database connection health check failed - verify database is accessible")?;
    info!("Database connection established and verified");

    info!("Running database migrations...");
    db::migrations::run(&db_pool, &db_url)
        .await
        .context("Failed to run database migrations")?;
    info!("Database migrations completed successfully");

    info!("Initializing task queue...");
    let task_queue = Arc::new(TaskQueue::new(db_pool.clone()));
    let queue_len = task_queue
        .len()
        .await
        .context("Failed to get task queue length")?;
    info!("Task queue initialized with {queue_len} pending tasks");

    let base_url = format!("http://localhost:{}", cli.port);

    let public_url = std::env::var("PUBLIC_URL").ok().and_then(|url| {
        let trimmed = url.trim();
        if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
            Some(trimmed.to_string())
        } else {
            warn!(
                public_url = %trimmed,
                "PUBLIC_URL must start with http:// or https://; ignoring invalid value"
            );
            None
        }
    });

    if let Some(ref url) = public_url {
        info!(public_url = %url, "Using PUBLIC_URL for agent card");
    }

    let wiki_index_dir = cli
        .wiki_index_dir
        .unwrap_or_else(|| format!("wiki-index-{}", cli.port));
    let wiki_search = WikiSearchIndex::new(PathBuf::from(&wiki_index_dir));
    info!(dir = %wiki_index_dir, "Initializing wiki search index");

    let rebuild_start = std::time::Instant::now();
    let project_ids = db::wiki::projects_with_pages(&db_pool)
        .await
        .context("Failed to query projects with wiki pages")?;
    let mut total_pages = 0usize;
    for pid in &project_ids {
        let pages = db::wiki::list_pages_for_indexing(&db_pool, pid)
            .await
            .context("Failed to load wiki pages for indexing")?;
        total_pages += pages.len();
        wiki_search
            .rebuild_project(pid, &pages)
            .context("Failed to rebuild wiki search index")?;
    }
    info!(
        projects = project_ids.len(),
        pages = total_pages,
        elapsed_ms = rebuild_start.elapsed().as_millis(),
        "Wiki search index rebuild complete"
    );

    let (event_broadcast, _) = broadcast::channel::<EventNotification>(256);

    let recovery_grace_secs = std::env::var("AGENTBEACON_RECOVERY_GRACE_SECS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(30)
        .max(3);

    let liveness_interval_secs = std::env::var("AGENTBEACON_LIVENESS_INTERVAL_SECS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(90)
        .max(5);

    let spawn_tick_secs = std::env::var("AGENTBEACON_SPAWN_TICK_SECS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(10)
        .max(1);

    let supervisor = Arc::new(Supervisor::new(
        cli.port,
        cli.worker_poll_interval.clone(),
        cli.max_workers,
        cli.idle_timeout,
    ));

    let app_state = AppState::new(
        db_pool,
        task_queue,
        base_url,
        public_url,
        cli.port,
        event_broadcast.clone(),
        wiki_search,
        supervisor.clone(),
    );
    let vite_dev_port = app_state.vite_dev_port;
    let app = create_router(app_state.clone(), dev_mode, cli.port);

    let addr = SocketAddr::from(([0, 0, 0, 0], cli.port));
    info!("Binding to {}", addr);

    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .context(format!("Failed to bind to {addr}"))?;

    if dev_mode {
        info!(
            "Starting scheduler on {} (DEV_MODE: redirecting to Vite dev server at localhost:{})",
            addr, vite_dev_port
        );
    } else {
        info!(
            "Starting scheduler on {} (PRODUCTION: serving embedded static files)",
            addr
        );
    }

    info!(
        liveness_interval_secs,
        recovery_grace_secs, "Liveness check configured"
    );
    let reconciler_pool = app_state.db_pool.clone();
    let tick_task_queue = app_state.task_queue.clone();
    let tick_heartbeats = app_state.worker_heartbeats.clone();
    let tick_hb_timeout = std::time::Duration::from_secs(app_state.heartbeat_timeout_secs);
    let tick_event_broadcast = event_broadcast.clone();
    let tick_supervisor = supervisor.clone();
    let max_spawn_per_tick = cli.max_spawn_per_tick;
    let max_workers_opt = cli.max_workers;
    tokio::spawn(async move {
        {
            let crashed_sessions = db::sessions::find_crashed_workerless(&reconciler_pool)
                .await
                .unwrap_or_default();
            for session in &crashed_sessions {
                if let Ok(exec) =
                    db::executions::get_by_id(&reconciler_pool, &session.execution_id).await
                {
                    let pending = db::sessions::count_pending_turns(&reconciler_pool, &session.id)
                        .await
                        .unwrap_or(0);
                    let _ = services::reconciler::reconcile(
                        &reconciler_pool,
                        session,
                        pending,
                        &exec,
                        true,
                        None,
                        Some(&tick_supervisor),
                    )
                    .await;
                }
            }
            if max_workers_opt != Some(0) {
                let claimable = db::sessions::count_claimable(&reconciler_pool)
                    .await
                    .unwrap_or(0);
                for _ in 0..claimable.min(max_spawn_per_tick) {
                    if let Err(e) = tick_supervisor.spawn_worker().await {
                        if e.to_string().contains("max workers") {
                            tracing::warn!(error = %e, "Startup spawn limit reached");
                        } else {
                            tracing::error!(error = %e, "Startup worker spawn failed");
                        }
                        break;
                    }
                }
            }
        }

        tokio::time::sleep(Duration::from_secs(recovery_grace_secs)).await;

        let reconciler_interval = Duration::from_secs(liveness_interval_secs);
        let spawn_interval = Duration::from_secs(spawn_tick_secs);
        let mut reconciler_tick = tokio::time::interval(reconciler_interval);
        let mut spawn_tick = tokio::time::interval(spawn_interval);
        reconciler_tick.tick().await;
        spawn_tick.tick().await;

        loop {
            tokio::select! {
                _ = reconciler_tick.tick() => {
                    let sessions = match db::sessions::find_reconcilable(&reconciler_pool).await {
                        Ok(s) => s,
                        Err(e) => {
                            tracing::warn!(error = %e, "Reconciler tick: list sessions failed");
                            continue;
                        }
                    };

                    let mut mutated = 0u64;
                    let heartbeats = tick_heartbeats.read().unwrap().clone();
                    for session in &sessions {
                        if let Some(ref wid) = session.worker_id {
                            let expired = match heartbeats.get(wid.as_str()) {
                                Some(last_seen) => last_seen.elapsed() > tick_hb_timeout,
                                None => true,
                            };
                            if expired {
                                if session.command_has_payload {
                                    let event_payload = serde_json::json!({
                                        "message": "Agent recovered from a crash. A message may have been lost."
                                    });
                                    let _ = crate::db::events::insert(
                                        &reconciler_pool,
                                        &session.execution_id,
                                        Some(&session.id),
                                        "platform",
                                        &serde_json::to_string(&event_payload).unwrap_or_default(),
                                    )
                                    .await;
                                }
                                tick_supervisor.kill_worker(wid).await;
                                let _ = services::transition::transition(
                                    &reconciler_pool,
                                    &session.execution_id,
                                    &session.id,
                                    services::transition::Action::DetectCrash,
                                )
                                .await;
                                mutated += 1;
                                continue;
                            }
                        }

                        if session.outcome.is_none()
                            && session.desired != "terminate"
                            && session.executor_state != "crashed"
                            && session.command_token.is_none()
                        {
                            continue;
                        }

                        let pending = db::sessions::count_pending_turns(&reconciler_pool, &session.id)
                            .await
                            .unwrap_or(0);
                        let execution = match db::executions::get_by_id(
                            &reconciler_pool,
                            &session.execution_id,
                        )
                        .await
                        {
                            Ok(e) => e,
                            Err(_) => continue,
                        };
                        match services::reconciler::reconcile(
                            &reconciler_pool,
                            session,
                            pending,
                            &execution,
                            false,
                            None,
                            Some(&tick_supervisor),
                        )
                        .await
                        {
                            Ok(services::reconciler::ReconcilerAction::Mutated) => {
                                mutated += 1;
                                let _ = tick_event_broadcast.send(EventNotification::persisted(
                                    session.execution_id.clone(),
                                    0,
                                ));
                            }
                            Ok(services::reconciler::ReconcilerAction::Repaired) => {
                                mutated += 1;
                                let _ = tick_event_broadcast.send(EventNotification::persisted(
                                    session.execution_id.clone(),
                                    0,
                                ));
                            }
                            Ok(services::reconciler::ReconcilerAction::SendCommand { .. }) => mutated += 1,
                            _ => {}
                        }
                    }
                    if mutated > 0 {
                        tick_task_queue.wake_waiters();
                        info!(mutated, "Liveness check complete");
                    }

                    let tracked = tick_supervisor.worker_ids().await;
                    tick_heartbeats.write().unwrap().retain(|wid, last_seen| {
                        tracked.contains(wid) || last_seen.elapsed() < tick_hb_timeout
                    });
                }

                _ = spawn_tick.tick() => {
                    if max_workers_opt == Some(0) {
                        continue;
                    }
                    let claimable = db::sessions::count_claimable(&reconciler_pool)
                        .await
                        .unwrap_or(0);
                    if claimable > 0 {
                        let to_spawn = claimable.min(max_spawn_per_tick);
                        for _ in 0..to_spawn {
                            if let Err(e) = tick_supervisor.spawn_worker().await {
                                if e.to_string().contains("max workers") {
                                    tracing::warn!(error = %e, "Spawn limit reached");
                                } else {
                                    tracing::error!(error = %e, "Worker spawn failed");
                                }
                                break;
                            }
                        }
                    }
                }
            }
        }
    });

    let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);

    let mut shutdown_watch = shutdown_rx;
    let mut server_handle = tokio::spawn(async move {
        axum::serve(listener, app)
            .with_graceful_shutdown(async move {
                let _ = shutdown_watch.changed().await;
            })
            .await
    });

    info!(
        "AgentBeacon ready — scheduler on port {} (max_workers={:?})",
        cli.port, cli.max_workers
    );

    let server_error = tokio::select! {
        _ = wait_for_signal() => {
            None
        }
        result = &mut server_handle => {
            warn!("Server exited unexpectedly, shutting down workers...");
            Some(result)
        }
    };

    if let Err(e) = supervisor.shutdown().await {
        warn!("Error during worker shutdown: {e}");
    }

    if let Some(result) = server_error {
        result??;
    } else {
        let _ = shutdown_tx.send(true);
        server_handle.await??;
    }

    info!("AgentBeacon shut down gracefully");
    Ok(())
}

fn parse_duration(s: &str) -> Result<std::time::Duration, humantime::DurationError> {
    humantime::parse_duration(s)
}

async fn wait_for_signal() {
    let ctrl_c = async {
        signal::ctrl_c()
            .await
            .expect("Failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        signal::unix::signal(signal::unix::SignalKind::terminate())
            .expect("Failed to install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {
            info!("Received SIGINT, shutting down...");
        },
        _ = terminate => {
            info!("Received SIGTERM, shutting down...");
        },
    }
}
