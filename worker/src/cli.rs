use clap::Parser;
use std::time::Duration;

#[derive(Parser, Debug)]
#[command(name = "agentbeacon-worker")]
#[command(version)]
#[command(about = "AgentBeacon distributed task execution worker")]
pub struct Args {
    /// Scheduler URL for syncing and task retrieval
    #[arg(long, env = "SCHEDULER_URL")]
    pub scheduler_url: String,

    /// Sync polling interval (e.g., "5s", "100ms")
    #[arg(long, default_value = "5s", value_parser = parse_duration)]
    pub interval: Duration,

    /// HTTP request timeout (e.g., "30s", "1m")
    #[arg(long, default_value = "30s", value_parser = parse_duration)]
    pub http_timeout: Duration,

    /// Long-poll timeout for waiting_for_event syncs (e.g., "40s")
    #[arg(long, default_value = "40s", value_parser = parse_duration)]
    pub long_poll_timeout: Duration,

    /// Maximum sync retry attempts during startup
    #[arg(long, default_value = "10")]
    pub startup_max_attempts: usize,

    /// Maximum sync retry attempts after successful connection
    #[arg(long, default_value = "60")]
    pub reconnect_max_attempts: usize,

    /// Delay between sync retry attempts (e.g., "500ms", "1s")
    #[arg(long, default_value = "500ms", value_parser = parse_duration)]
    pub retry_delay: Duration,

    /// Path to Node.js binary (for Claude executor)
    #[arg(long, env = "AGENTBEACON_NODE_PATH")]
    pub node_path: Option<String>,

    /// Override path to executor JS files (optional, for development).
    /// When not set, the worker extracts embedded executors to the platform data directory.
    #[arg(long, env = "AGENTBEACON_EXECUTORS_DIR")]
    pub executors_dir: Option<String>,

    /// Max time with no agent output during an active turn before killing it
    #[arg(long, default_value = "30m", value_parser = parse_inactivity_timeout)]
    pub inactivity_timeout: Duration,

    /// Internal: resolved node_modules path for embedded executor mode.
    /// Not a CLI flag — set programmatically during startup.
    #[arg(skip)]
    pub node_modules_dir: Option<String>,
}

fn parse_duration(s: &str) -> Result<Duration, humantime::DurationError> {
    humantime::parse_duration(s)
}

fn parse_inactivity_timeout(s: &str) -> Result<Duration, String> {
    let d = humantime::parse_duration(s).map_err(|e| e.to_string())?;
    if d.as_secs() < 1 {
        return Err("inactivity timeout must be at least 1s".into());
    }
    Ok(d)
}
