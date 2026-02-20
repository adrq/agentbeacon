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
    /// Defaults to sqlite://scheduler-{port}.db when not specified.
    #[arg(long, env = "DATABASE_URL")]
    db_url: Option<String>,
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
    info!("Configuration: port={}", cli.port);

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

    // Seed default agents if table is empty (opt-out via AGENTBEACON_NO_SEED)
    if std::env::var("AGENTBEACON_NO_SEED").is_err() {
        let agent_count = db::agents::count(&db_pool)
            .await
            .context("Failed to count agents")?;
        if agent_count == 0 {
            info!("Seeding default agents...");
            let demo_id = uuid::Uuid::new_v4().to_string();
            db::agents::create(
                &db_pool,
                &demo_id,
                "Demo Agent",
                "acp",
                r#"{"command":"uv","args":["run","python","-m","agentmaestro.mock_agent","--mode","acp","--scenario","demo"],"timeout":60}"#,
                Some("Mock ACP agent for e2e testing"),
                None,
            )
            .await
            .context("Failed to seed Demo Agent")?;

            let claude_id = uuid::Uuid::new_v4().to_string();
            db::agents::create(
                &db_pool,
                &claude_id,
                "Claude Code",
                "claude_sdk",
                r#"{"command":"claude","args":[],"timeout":300,"env":{},"state_dir":"~/.claude"}"#,
                Some("Claude Code via Agent SDK"),
                None,
            )
            .await
            .context("Failed to seed Claude Code agent")?;
            info!("Seeded 2 default agents");
        }
    }

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
    let app_state = AppState::new(db_pool, task_queue, base_url, public_url, cli.port);
    let vite_dev_port = app_state.vite_dev_port;
    let app = create_router(app_state, dev_mode, cli.port);

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
