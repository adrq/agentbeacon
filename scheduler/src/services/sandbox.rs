use common::sandbox::SandboxPolicy;

use crate::error::SchedulerError;

/// Parse session.sandbox_policy JSON into a validated SandboxPolicy.
/// Returns Err on malformed/invalid data — callers must fail the operation
/// rather than silently degrading to no enforcement.
pub fn parse_sandbox_policy(sandbox_policy: &str) -> Result<SandboxPolicy, SchedulerError> {
    serde_json::from_str(sandbox_policy).map_err(|e| {
        SchedulerError::Database(format!(
            "malformed sandbox_policy in DB: {sandbox_policy:?}: {e}"
        ))
    })
}

/// Serialize a validated SandboxPolicy to a JSON Value for driver.config transport.
pub fn build_sandbox_driver_config(
    policy: &SandboxPolicy,
) -> Result<serde_json::Value, SchedulerError> {
    serde_json::to_value(policy)
        .map_err(|e| SchedulerError::Database(format!("serialize sandbox_policy failed: {e}")))
}
