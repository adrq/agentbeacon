use std::path::PathBuf;

pub mod a2a;
pub mod schemas;
pub mod validation;

pub use a2a::{A2AArtifact, A2ATaskStatus, Message, Part};
pub use validation::{
    ValidationError, validate_a2a_request, validate_sync_request, validate_sync_response,
};

pub fn agentbeacon_projects_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("AGENTBEACON_PROJECTS_DIR") {
        return PathBuf::from(dir);
    }
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string()))
        .join(".agentbeacon/projects")
}

pub fn execution_dir(project_id: &str, exec_id: &str) -> PathBuf {
    agentbeacon_projects_dir()
        .join(project_id)
        .join("executions")
        .join(exec_id)
}

pub fn session_dir(project_id: &str, exec_id: &str, session_id: &str) -> PathBuf {
    execution_dir(project_id, exec_id)
        .join("sessions")
        .join(session_id)
}
