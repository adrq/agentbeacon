use clap::Parser;
use std::path::PathBuf;
use std::time::Duration;

#[derive(Parser, Debug)]
#[command(name = "agentbeacon-worker")]
#[command(about = "AgentBeacon distributed task execution worker")]
pub struct Args {
    /// Scheduler URL for syncing and task retrieval
    #[arg(long, env = "SCHEDULER_URL")]
    pub scheduler_url: String,

    /// Sync polling interval (e.g., "5s", "100ms")
    #[arg(long, default_value = "5s", value_parser = parse_duration)]
    pub interval: Duration,

    /// Path to agents configuration file
    #[arg(long, default_value = "examples/agents.yaml")]
    pub agents_config: PathBuf,

    /// HTTP request timeout (e.g., "30s", "1m")
    #[arg(long, default_value = "30s", value_parser = parse_duration)]
    pub http_timeout: Duration,

    /// Maximum sync retry attempts during startup (before first successful connection)
    /// Production default: 10 attempts = ~5s total at default retry-delay
    #[arg(long, default_value = "10")]
    pub startup_max_attempts: usize,

    /// Maximum sync retry attempts after successful connection (when idle)
    /// Production default: 60 attempts = ~30s total at default retry-delay
    #[arg(long, default_value = "60")]
    pub reconnect_max_attempts: usize,

    /// Delay between sync retry attempts (e.g., "500ms", "1s")
    /// Production default: 500ms provides good balance of responsiveness and resource usage
    #[arg(long, default_value = "500ms", value_parser = parse_duration)]
    pub retry_delay: Duration,
}

fn parse_duration(s: &str) -> Result<Duration, humantime::DurationError> {
    humantime::parse_duration(s)
}
