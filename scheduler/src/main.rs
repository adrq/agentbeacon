use anyhow::{Context, Result};
use clap::Parser;
use std::net::SocketAddr;
use std::sync::Arc;
use tokio::signal;
use tracing::{info, warn};

use scheduler::{
    app::{AppState, create_router},
    db,
    queue::TaskQueue,
    telemetry,
};

/// AgentBeacon Scheduler - Agent orchestration and execution tracking
#[derive(Parser, Debug)]
#[command(name = "agentbeacon-scheduler")]
#[command(version = "1.0.0")]
#[command(about = "Rust scheduler with database layer and REST API", long_about = None)]
struct Cli {
    /// Port to listen on
    #[arg(short, long, default_value = "9456")]
    port: u16,

    /// Database URL (SQLite or PostgreSQL)
    /// Examples:
    ///   sqlite:///tmp/scheduler.db
    ///   postgres://user:pass@localhost/dbname
    #[arg(long, env = "DATABASE_URL", default_value = "sqlite://scheduler.db")]
    db_url: String,
}

#[tokio::main]
async fn main() -> Result<()> {
    // Parse CLI arguments
    let cli = Cli::parse();

    // Initialize telemetry (JSON-formatted logs)
    telemetry::init_telemetry();

    // Register SQLx Any drivers (required for SQLx 0.8+ with Pool<Any>)
    sqlx::any::install_default_drivers();

    info!("AgentBeacon Scheduler starting...");
    info!("Configuration: port={}, db_url={}", cli.port, cli.db_url);

    // Run bootstrap and startup flow
    bootstrap(cli).await
}

async fn bootstrap(cli: Cli) -> Result<()> {
    // Check for development mode
    let dev_mode = std::env::var("DEV_MODE").map(|v| v == "1").unwrap_or(false);

    // Connect to database
    info!("Connecting to database: {}", cli.db_url);
    let db_pool = db::pool::create(&cli.db_url)
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
    db::migrations::run(&db_pool, &cli.db_url)
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

    // Build application state and router
    let app_state = AppState::new(db_pool, task_queue, base_url, public_url);
    let app = create_router(app_state, dev_mode, cli.port);

    // Bind to port
    let addr = SocketAddr::from(([0, 0, 0, 0], cli.port));
    info!("Binding to {}", addr);

    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .context(format!("Failed to bind to {addr}"))?;

    if dev_mode {
        info!(
            "Starting scheduler on {} (DEV_MODE: redirecting to Vite dev server at localhost:5173)",
            addr
        );
    } else {
        info!(
            "Starting scheduler on {} (PRODUCTION: serving embedded static files)",
            addr
        );
    }

    // Start server with graceful shutdown
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .context("Server error")?;

    info!("AgentBeacon Scheduler shut down gracefully");
    Ok(())
}

/// Handle SIGTERM and SIGINT for graceful shutdown
async fn shutdown_signal() {
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
            info!("Received SIGINT (Ctrl+C), shutting down...");
        },
        _ = terminate => {
            info!("Received SIGTERM, shutting down...");
        },
    }
}
