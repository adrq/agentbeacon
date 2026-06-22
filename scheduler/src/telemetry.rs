use tracing_subscriber::{EnvFilter, fmt, layer::SubscriberExt, util::SubscriberInitExt};

/// Initialize tracing subscriber with JSON format
///
/// Logs include: timestamp, level, message, module, execution_id, workflow_id
/// Default level: INFO, configurable via RUST_LOG env var
pub fn init_telemetry() {
    // Default to info, but quiet Tantivy's per-commit INFO bookkeeping
    // (Preparing commit / committing / garbage collection / merges) which
    // otherwise floods stdout. An explicit RUST_LOG still wins entirely
    // (e.g. RUST_LOG=tantivy=info brings it back).
    let filter =
        EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info,tantivy=warn"));

    tracing_subscriber::registry()
        .with(filter)
        .with(
            fmt::layer()
                .json()
                .with_target(true)
                .with_thread_ids(false)
                .with_thread_names(false)
                .with_current_span(true),
        )
        .init();
}
