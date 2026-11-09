// SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

#[cfg(target_env = "musl")]
#[global_allocator]
static GLOBAL: mimalloc::MiMalloc = mimalloc::MiMalloc;

use anyhow::{Context, Result};
use clap::Parser;
use common::DataDir;
use std::net::SocketAddr;
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
    services, storage,
    storage::DefaultLocation,
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
    /// SQLite URLs take a path and optionally ?mode=rwc, ?mode=rw or ?mode=ro.
    #[cfg_attr(
        feature = "installed-paths",
        doc = "Defaults to db/scheduler-{port}.db in the data directory ($AGENTBEACON_DATA_DIR or the platform data directory)."
    )]
    #[cfg_attr(
        not(feature = "installed-paths"),
        doc = "Defaults to scheduler-{port}.db in the current directory."
    )]
    #[arg(long, env = "DATABASE_URL")]
    db_url: Option<String>,

    /// Directory for wiki search index files.
    #[cfg_attr(
        feature = "installed-paths",
        doc = "Defaults to db/wiki-index-{port}/ in the data directory ($AGENTBEACON_DATA_DIR or the platform data directory)."
    )]
    #[cfg_attr(
        not(feature = "installed-paths"),
        doc = "Defaults to wiki-index-{port}/ in the current directory."
    )]
    #[arg(long, env = "AGENTBEACON_WIKI_INDEX_DIR")]
    wiki_index_dir: Option<String>,

    /// Maximum auto-spawned workers. Default: unlimited.
    /// Set to 0 to disable auto-spawning (for externally supervised workers).
    #[arg(long, env = "AGENTBEACON_MAX_WORKERS")]
    max_workers: Option<usize>,

    /// Max workers spawned per 10s tick (thundering-herd prevention)
    #[arg(long, default_value_t = 8, env = "AGENTBEACON_MAX_SPAWN_PER_TICK")]
    max_spawn_per_tick: usize,

    /// Idle timeout passed through to spawned worker processes
    #[arg(long, default_value = "300s", value_parser = parse_duration, env = "AGENTBEACON_IDLE_TIMEOUT")]
    idle_timeout: std::time::Duration,

    /// How long a login stays valid (e.g., '30d', '12h')
    #[arg(long, default_value = "30d", value_parser = parse_duration, env = "AGENTBEACON_LOGIN_TTL")]
    login_ttl: std::time::Duration,

    /// Worker sync polling interval (e.g., '1s', '500ms')
    #[arg(long)]
    worker_poll_interval: Option<String>,

    /// Install or update agent SDKs, then exit
    #[arg(long, help_heading = "Setup")]
    setup: bool,

    /// Create the user or change its password, then exit
    #[arg(long, conflicts_with = "setup", help_heading = "Setup")]
    setup_user: bool,

    /// Show installed SDK status without installing (use with --setup)
    #[arg(long, requires = "setup", help_heading = "Setup")]
    status: bool,

    /// Audit the database for orphan answer sources (post-cutover detector), then exit.
    /// Read-only; runs no migrations. Exit 0 = none, 1 = orphans found, 2 = error.
    /// Emits one NDJSON object per orphan on stdout; diagnostics go to stderr.
    #[arg(long, help_heading = "Admin")]
    detect_orphans: bool,
}

fn main() -> Result<()> {
    use scheduler::services::auth::setup;

    let cli = Cli::parse();

    let setup_credentials = setup::credentials_from_env();
    unsafe {
        std::env::remove_var(setup::USERNAME_ENV);
        std::env::remove_var(setup::PASSWORD_ENV);
    }

    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .context("Failed to build the async runtime")?
        .block_on(run(cli, setup_credentials))
}

async fn run(cli: Cli, setup_credentials: Result<Option<(String, String)>, String>) -> Result<()> {
    if cli.setup {
        return run_scheduler_setup(&cli);
    }

    if cli.setup_user {
        return run_user_setup(&cli, setup_credentials).await;
    }

    if cli.detect_orphans {
        sqlx::any::install_default_drivers();
        let data_dir = common::platform_data_dir();
        let location =
            match storage::resolve_db(cli.db_url.as_deref(), cli.port, default_location(&data_dir))
            {
                Ok(location) => location,
                Err(e) => {
                    eprintln!("{e}");
                    std::process::exit(2);
                }
            };
        let db_url = detector_db_url(location.url);
        let code = db::detector::run_orphan_detector(&db_url).await;
        std::process::exit(code);
    }

    telemetry::init_telemetry();

    if std::env::var_os("AGENTBEACON_TEST_FAIL_AT").is_some() {
        warn!(
            "AGENTBEACON_TEST_FAIL_AT is set — a test-only transaction failpoint is active; \
             unset it outside tests"
        );
    }

    if std::env::var_os("AGENTBEACON_TEST_PAUSE_AT").is_some() {
        warn!(
            "AGENTBEACON_TEST_PAUSE_AT is set — a test-only pause is active; unset it outside tests"
        );
    }

    sqlx::any::install_default_drivers();

    info!("AgentBeacon starting...");
    info!(
        "Configuration: port={}, max_workers={:?}",
        cli.port, cli.max_workers
    );

    bootstrap(cli).await
}

/// Normalize a detector target URL.
fn detector_db_url(url: String) -> String {
    if !url.starts_with("sqlite:") {
        return url;
    }
    let (base, query) = match url.split_once('?') {
        Some((b, q)) => (b.to_string(), Some(q)),
        None => (url.clone(), None),
    };
    let mut params: Vec<String> = query
        .map(|q| {
            q.split('&')
                .filter(|p| !p.is_empty() && !p.starts_with("mode="))
                .map(String::from)
                .collect()
        })
        .unwrap_or_default();
    params.push("mode=ro".to_string());
    format!("{base}?{}", params.join("&"))
}

const DEFAULT_PORT: u16 = 9456;

const INSTALLED_PATHS: bool = cfg!(feature = "installed-paths");

/// The default storage location for this build.
fn default_location(data_dir: &DataDir) -> DefaultLocation<'_> {
    if INSTALLED_PATHS {
        DefaultLocation::DataDir(data_dir)
    } else {
        DefaultLocation::WorkingDir
    }
}

async fn run_user_setup(
    cli: &Cli,
    setup_credentials: Result<Option<(String, String)>, String>,
) -> Result<()> {
    use scheduler::services::auth::setup::{self, SetupMode, SetupOutcome};
    use std::io::IsTerminal;

    sqlx::any::install_default_drivers();
    let data_dir = common::platform_data_dir();
    let location =
        match storage::resolve_db(cli.db_url.as_deref(), cli.port, default_location(&data_dir)) {
            Ok(location) => location,
            Err(e) => {
                eprintln!("{e}");
                std::process::exit(1);
            }
        };
    let target = storage::describe(&location.url);
    println!("User database: {target}");
    if !location.given {
        eprintln!(
            "warning: no database URL given; using {target}. To use a different database, \
             pass --db-url or set DATABASE_URL, here and when starting AgentBeacon."
        );
    }
    if std::io::stdin().is_terminal() && storage::needs_create_confirmation(&location) {
        let create =
            setup::confirm_default_yes("No database exists at this location. Create it? [Y/n] ")
                .map_err(anyhow::Error::msg)?;
        if !create {
            eprintln!(
                "No database was created. Pass --db-url or set DATABASE_URL to use a different database."
            );
            std::process::exit(1);
        }
    }
    if let Some(path) = &location.default_path
        && let Err(e) = storage::prepare_default(path)
    {
        eprintln!("{e}");
        std::process::exit(1);
    }

    let pool = db::pool::create(&location.url)
        .await
        .context("Failed to create database pool")?;
    db::migrations::run(&pool, &location.url)
        .await
        .context("Failed to run database migrations")?;
    let supplied = setup_credentials.map_err(anyhow::Error::msg)?;
    let mode = SetupMode::detect();
    match setup::run(&pool, supplied, &mode).await {
        Ok(SetupOutcome::Created) => println!("User created in {target}."),
        Ok(SetupOutcome::PasswordChanged) => {
            println!("Password changed in {target}. Every existing login was signed out.")
        }
        Ok(SetupOutcome::Unchanged) => println!("User unchanged in {target}."),
        Ok(SetupOutcome::Skipped) => println!("User exists in {target}; nothing to change."),
        Err(message) => {
            eprintln!("setup failed: {message}");
            std::process::exit(1);
        }
    }
    pool.close().await;
    if location.given {
        println!(
            "Start AgentBeacon with the same --db-url or DATABASE_URL, or it will not find this user."
        );
    } else if cli.port != DEFAULT_PORT {
        println!(
            "Start AgentBeacon with --port {port} (or AGENTBEACON_PORT={port}) to use this database.",
            port = cli.port
        );
    }
    Ok(())
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

    let data_dir = common::platform_data_dir();
    let location =
        storage::resolve_db(cli.db_url.as_deref(), cli.port, default_location(&data_dir))?;
    let wiki_index_dir = storage::resolve_wiki_index_dir(
        cli.wiki_index_dir.as_deref(),
        cli.port,
        default_location(&data_dir),
    )?;
    if let Some(path) = &location.default_path {
        storage::prepare_default(path)?;
    }
    if INSTALLED_PATHS && cli.wiki_index_dir.is_none() {
        storage::prepare_default_index(&wiki_index_dir)?;
    }
    let db_url = location.url;
    let target = storage::describe(&db_url);

    info!("Connecting to database: {target}");
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

    match db::users::get_single(&db_pool).await {
        Ok(None) => warn!(
            "No user exists in {target}; run `agentbeacon --setup-user` with the same --port or --db-url to create one"
        ),
        Ok(Some(_)) => {}
        Err(e) => warn!(error = %e, "Could not check for a user"),
    }

    let _pause_summary = services::restart_park::park_live_sessions_on_boot(&db_pool).await;

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

    let wiki_search = WikiSearchIndex::new(wiki_index_dir.clone());
    info!(dir = %wiki_index_dir.display(), "Initializing wiki search index");

    let rebuild_start = std::time::Instant::now();
    let pages = db::wiki::list_all_pages_for_indexing(&db_pool)
        .await
        .context("Failed to load wiki pages for indexing")?;
    let tags_by_page = db::wiki::tags_by_page(&db_pool)
        .await
        .context("Failed to load wiki page tags for indexing")?;
    let no_tags: Vec<String> = Vec::new();
    let entries: Vec<scheduler::search::IndexedPage<'_>> = pages
        .iter()
        .map(|page| scheduler::search::IndexedPage {
            page,
            tags: tags_by_page.get(&page.id).unwrap_or(&no_tags),
        })
        .collect();
    wiki_search
        .rebuild_all(&entries)
        .context("Failed to rebuild wiki search index")?;
    info!(
        pages = entries.len(),
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
        Some(db_pool.clone()),
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
    )
    .with_login_ttl(cli.login_ttl);
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
                                    let _ = crate::db::events::insert_locked(
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

                    let externals = tick_supervisor.external_ids().await;
                    let aged_out: Vec<String> = {
                        let heartbeats = tick_heartbeats.read().unwrap();
                        externals
                            .into_iter()
                            .filter(|wid| {
                                heartbeats
                                    .get(wid)
                                    .is_none_or(|last_seen| last_seen.elapsed() > tick_hb_timeout)
                            })
                            .collect()
                    };
                    for wid in &aged_out {
                        tick_supervisor.kill_worker(wid).await;
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
