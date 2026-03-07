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

    /// Number of worker processes to supervise (0 = standalone scheduler)
    #[arg(long, default_value_t = 0)]
    workers: usize,

    /// Worker sync polling interval (e.g., '1s', '500ms')
    #[arg(long)]
    worker_poll_interval: Option<String>,
}

#[tokio::main]
async fn main() -> Result<()> {
    // Parse CLI arguments
    let cli = Cli::parse();

    // Initialize telemetry (JSON-formatted logs)
    telemetry::init_telemetry();

    // Register SQLx Any drivers (required for SQLx 0.8+ with Pool<Any>)
    sqlx::any::install_default_drivers();

    info!("AgentBeacon starting...");
    info!("Configuration: port={}, workers={}", cli.port, cli.workers);

    // Run bootstrap and startup flow
    bootstrap(cli).await
}

async fn bootstrap(cli: Cli) -> Result<()> {
    // Check for development mode
    let dev_mode = std::env::var("DEV_MODE").map(|v| v == "1").unwrap_or(false);

    // Resolve db_url: explicit value or port-derived default
    let db_url = cli
        .db_url
        .unwrap_or_else(|| format!("sqlite://scheduler-{}.db", cli.port));

    // Connect to database
    info!("Connecting to database: {}", db_url);
    let db_pool = db::pool::create(&db_url)
        .await
        .context("Failed to create database pool")?;

    // Verify database connection with health check query
    sqlx::query("SELECT 1")
        .fetch_one(db_pool.as_ref())
        .await
        .context("Database connection health check failed - verify database is accessible")?;
    info!("Database connection established and verified");

    // Run migrations
    info!("Running database migrations...");
    db::migrations::run(&db_pool, &db_url)
        .await
        .context("Failed to run database migrations")?;
    info!("Database migrations completed successfully");

    // Create task queue (database-only, no rebuild needed)
    info!("Initializing task queue...");
    let task_queue = Arc::new(TaskQueue::new(db_pool.clone()));
    let queue_len = task_queue
        .len()
        .await
        .context("Failed to get task queue length")?;
    info!("Task queue initialized with {queue_len} pending tasks");

    // Build base URL for agent card
    let base_url = format!("http://localhost:{}", cli.port);

    // Parse public URL from environment (for load balancer/reverse proxy scenarios)
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

    // Build wiki search index and rebuild from DB
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

    // Create event broadcast channel (needed by recovery + AppState)
    let (event_broadcast, _) = broadcast::channel::<EventNotification>(256);

    // Record startup time for recovery orphan detection
    let startup_time = chrono::Utc::now();

    // Read recovery config
    let max_recovery_attempts = std::env::var("AGENTBEACON_MAX_RECOVERY_ATTEMPTS")
        .ok()
        .and_then(|v| v.parse::<i64>().ok())
        .unwrap_or(2);
    let recovery_grace_secs = std::env::var("AGENTBEACON_RECOVERY_GRACE_SECS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(30)
        .max(3); // Floor: grace period must exceed SQLite's second-precision window

    // Build application state and router
    let app_state = AppState::new(
        db_pool,
        task_queue,
        base_url,
        public_url,
        cli.port,
        event_broadcast.clone(),
        wiki_search,
    );
    let vite_dev_port = app_state.vite_dev_port;
    let app = create_router(app_state.clone(), dev_mode, cli.port);

    // Bind to port
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

    // Spawn delayed recovery scan (runs after grace period to let workers reconnect)
    let recovery_pool = app_state.db_pool.clone();
    let recovery_queue = app_state.task_queue.clone();
    let recovery_broadcast = event_broadcast.clone();
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_secs(recovery_grace_secs)).await;
        info!(
            "Running recovery scan (grace period {}s elapsed)...",
            recovery_grace_secs
        );
        let stats = services::recovery::recover_orphaned_sessions(
            &recovery_pool,
            &recovery_queue,
            &recovery_broadcast,
            max_recovery_attempts,
            startup_time,
        )
        .await;
        if stats.recovered > 0 || stats.failed > 0 {
            info!(
                recovered = stats.recovered,
                failed = stats.failed,
                skipped = stats.skipped,
                "Recovery scan complete"
            );
        }
    });

    // Create supervisor (if workers requested) but don't start yet
    let supervisor = if cli.workers > 0 {
        Some(Supervisor::new(
            cli.workers,
            cli.port,
            cli.worker_poll_interval,
        ))
    } else {
        None
    };

    // Create shutdown channel for Axum graceful shutdown
    let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);

    // Spawn server task — begins accepting once the executor polls it.
    // Workers take seconds to start (process spawn, binary load, CLI parse),
    // so the server will be accepting before any worker's first HTTP poll.
    let mut shutdown_watch = shutdown_rx;
    let mut server_handle = tokio::spawn(async move {
        axum::serve(listener, app)
            .with_graceful_shutdown(async move {
                let _ = shutdown_watch.changed().await;
            })
            .await
    });

    // Start workers AFTER server is scheduled
    if let Some(ref sup) = supervisor {
        sup.start_workers().await?;
        info!(
            "AgentBeacon ready — scheduler and {} workers running on port {}",
            cli.workers, cli.port
        );
    }

    // Wait for EITHER a shutdown signal OR the server exiting unexpectedly.
    let server_error = tokio::select! {
        _ = wait_for_signal() => {
            // Normal shutdown path
            None
        }
        result = &mut server_handle => {
            warn!("Server exited unexpectedly, shutting down workers...");
            Some(result)
        }
    };

    // Shutdown ordering: workers first (they are HTTP clients of the scheduler),
    // then drain Axum.
    if let Some(sup) = supervisor
        && let Err(e) = sup.shutdown().await
    {
        warn!("Error during worker shutdown: {e}");
    }

    if let Some(result) = server_error {
        // Server already exited — propagate its error
        result??;
    } else {
        // Normal path — trigger Axum graceful shutdown, wait for drain
        let _ = shutdown_tx.send(true);
        server_handle.await??;
    }

    info!("AgentBeacon shut down gracefully");
    Ok(())
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
