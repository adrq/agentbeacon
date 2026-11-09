// SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use std::path::PathBuf;

pub mod a2a;
pub mod sandbox;
pub mod schemas;
pub mod validation;

pub use a2a::{A2AArtifact, A2ATaskStatus, Message, Part};

pub fn agentbeacon_projects_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("AGENTBEACON_PROJECTS_DIR") {
        return PathBuf::from(dir);
    }
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string()))
        .join(".agentbeacon/projects")
}

/// Where AgentBeacon's data directory is, as seen by the scheduler.
#[derive(Debug, Clone, PartialEq)]
pub enum DataDir {
    /// `AGENTBEACON_DATA_DIR` is set (possibly to a non-UTF-8 value).
    Override(PathBuf),
    /// The platform data directory, e.g. `~/.local/share/agentbeacon`.
    Platform(PathBuf),
    /// Neither is available.
    Unavailable,
}

/// `AGENTBEACON_DATA_DIR`, else the platform data directory joined with `agentbeacon`.
pub fn platform_data_dir() -> DataDir {
    if let Some(dir) = std::env::var_os("AGENTBEACON_DATA_DIR") {
        return DataDir::Override(PathBuf::from(dir));
    }
    match dirs::data_dir() {
        Some(dir) => DataDir::Platform(dir.join("agentbeacon")),
        None => DataDir::Unavailable,
    }
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
