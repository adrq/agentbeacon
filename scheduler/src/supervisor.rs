//! Worker process supervision.
//!
//! Manages worker subprocess lifecycle: on-demand spawning, instant SIGKILL,
//! zombie reaping, colored log streaming, and graceful shutdown.

use anyhow::{Context, Result};
use nix::sys::signal::{Signal, kill};
use nix::unistd::Pid;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::RwLock;
use tokio::time::{Duration, Instant, sleep};
use tracing::{info, warn};

/// ANSI color codes for log prefixes.
const COLORS: &[&str] = &[
    "\x1b[1;32m", // Green
    "\x1b[1;34m", // Blue
    "\x1b[1;35m", // Magenta
    "\x1b[1;36m", // Cyan
    "\x1b[1;33m", // Yellow
    "\x1b[1;31m", // Red
];

const COLOR_RESET: &str = "\x1b[0m";

fn is_terminal() -> bool {
    use std::io::IsTerminal;
    std::io::stdout().is_terminal()
}

/// Deterministic color for a process name based on hash.
fn get_color(name: &str) -> &'static str {
    if !is_terminal() {
        return "";
    }
    let hash: usize = name.bytes().fold(0usize, |acc, b| {
        acc.wrapping_mul(31).wrapping_add(b as usize)
    });
    COLORS[hash % COLORS.len()]
}

/// Resolve the worker binary path: sibling of current exe, then fallback.
pub fn worker_binary_path() -> PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        let sibling = exe
            .parent()
            .unwrap_or(Path::new("."))
            .join("agentbeacon-worker");
        if sibling.exists() {
            return sibling;
        }
    }
    PathBuf::from("./bin/agentbeacon-worker")
}

/// Worker process tracked by the supervisor. One field — just a PID.
/// The Child handle is owned by monitor_worker() (background task).
struct WorkerEntry {
    pid: u32,
}

pub enum KillResult {
    /// SIGKILL sent (process is dead) or ESRCH (already dead).
    Confirmed,
    /// UUID not in supervisor tracking (already killed or external worker).
    NotFound,
}

/// Supervises worker subprocesses with on-demand spawning, instant kill, and graceful shutdown.
#[derive(Clone)]
pub struct Supervisor {
    port: u16,
    worker_poll_interval: Option<String>,
    /// Idle timeout passed through to spawned worker processes.
    idle_timeout: Duration,
    /// Worker UUID -> PID. Protected by RwLock for concurrent access.
    workers: Arc<RwLock<HashMap<String, WorkerEntry>>>,
    shutting_down: Arc<RwLock<bool>>,
    /// Maximum concurrent workers. None = unlimited, Some(0) = disabled.
    max_workers: Option<usize>,
    /// Serializes spawn_worker() to prevent concurrent callers from
    /// exceeding max_workers. Protects against overlapping ticks.
    spawn_lock: Arc<tokio::sync::Mutex<()>>,
}

impl Supervisor {
    pub fn new(
        port: u16,
        worker_poll_interval: Option<String>,
        max_workers: Option<usize>,
        idle_timeout: Duration,
    ) -> Self {
        Self {
            port,
            worker_poll_interval,
            idle_timeout,
            workers: Arc::new(RwLock::new(HashMap::new())),
            shutting_down: Arc::new(RwLock::new(false)),
            max_workers,
            spawn_lock: Arc::new(tokio::sync::Mutex::new(())),
        }
    }

    pub async fn spawn_worker(&self) -> Result<String> {
        let _guard = self.spawn_lock.lock().await;
        if *self.shutting_down.read().await {
            anyhow::bail!("supervisor is shutting down");
        }
        if let Some(max) = self.max_workers {
            let count = self.workers.read().await.len();
            if count >= max {
                anyhow::bail!("max workers ({}) reached", max);
            }
        }

        let worker_id = uuid::Uuid::new_v4().to_string();
        let scheduler_url = format!("http://localhost:{}", self.port);
        let worker_bin = worker_binary_path();

        let mut cmd = Command::new(&worker_bin);
        cmd.arg("--scheduler-url").arg(&scheduler_url);
        cmd.arg("--worker-id").arg(&worker_id);
        cmd.arg("--idle-timeout")
            .arg(format!("{}s", self.idle_timeout.as_secs()));
        if let Some(interval) = &self.worker_poll_interval {
            cmd.arg("--interval").arg(interval);
        }
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
        crate::process_group::configure_child_process(&mut cmd);

        let color = get_color(&worker_id);
        let name = format!("worker-{}", &worker_id[..8]);
        let mut child = cmd.spawn().context(format!(
            "failed to spawn {name} from {}",
            worker_bin.display()
        ))?;

        let pid = child.id().context("no PID for child process")?;
        info!(worker_id = %worker_id, pid = pid, "Spawned worker");

        let stdout = child.stdout.take().context("failed to get stdout pipe")?;
        let stderr = child.stderr.take().context("failed to get stderr pipe")?;

        self.workers
            .write()
            .await
            .insert(worker_id.clone(), WorkerEntry { pid });

        // Log streaming tasks
        let name_out = name.clone();
        tokio::spawn(async move {
            stream_logs(name_out, color, stdout).await;
        });

        let name_err = name.clone();
        tokio::spawn(async move {
            stream_logs(name_err, color, stderr).await;
        });

        // Monitor task: sole owner of the Child handle. Reaps zombie on exit.
        let sup = self.clone();
        let wid = worker_id.clone();
        tokio::spawn(async move {
            sup.monitor_worker(wid, child).await;
        });

        Ok(worker_id)
    }

    /// Instant kill. Sends SIGKILL (non-blocking syscall) and removes from tracking.
    /// monitor_worker() reaps the zombie in the background.
    pub async fn kill_worker(&self, worker_id: &str) -> KillResult {
        let pid = match self.workers.write().await.remove(worker_id) {
            Some(entry) => entry.pid,
            None => return KillResult::NotFound,
        };
        crate::process_group::kill_process_group(pid, Signal::SIGKILL);
        info!(worker_id = %worker_id, pid = pid, "Killed worker");
        KillResult::Confirmed
    }

    pub async fn worker_count(&self) -> usize {
        self.workers.read().await.len()
    }

    /// Returns the set of tracked worker UUIDs (for heartbeat cleanup).
    pub async fn worker_ids(&self) -> HashSet<String> {
        self.workers.read().await.keys().cloned().collect()
    }

    /// Sole owner of the Child handle. Reaps the zombie and detects unexpected exits.
    async fn monitor_worker(&self, worker_id: String, mut child: Child) {
        let exit_status = child.wait().await;
        if *self.shutting_down.read().await {
            return;
        }

        // Remove from tracking if still present. If kill_worker() already
        // removed it, was_tracked is false and we skip the log.
        let was_tracked = self.workers.write().await.remove(&worker_id).is_some();
        if was_tracked {
            match exit_status {
                Ok(status) if status.success() => {
                    info!(worker_id = %worker_id, "Worker exited cleanly (idle timeout)");
                }
                Ok(status) => {
                    warn!(worker_id = %worker_id, status = %status, "Worker exited unexpectedly")
                }
                Err(e) => {
                    warn!(worker_id = %worker_id, error = %e, "Worker exited unexpectedly")
                }
            }
        }
    }

    /// Gracefully shut down all workers: SIGTERM, poll for exit, SIGKILL survivors.
    pub async fn shutdown(&self) -> Result<()> {
        info!("Shutting down workers...");
        *self.shutting_down.write().await = true;

        let pids_and_ids: Vec<(u32, String)> = {
            let workers = self.workers.read().await;
            workers
                .iter()
                .map(|(uuid, entry)| (entry.pid, uuid.clone()))
                .collect()
        };

        // SIGTERM all workers
        for (pid, worker_id) in &pids_and_ids {
            match kill(Pid::from_raw(-(*pid as i32)), Signal::SIGTERM) {
                Ok(_) => info!(worker_id = %worker_id, pid = pid, "Sent SIGTERM to process group"),
                Err(e) => {
                    warn!(worker_id = %worker_id, pid = pid, error = %e, "SIGTERM to process group failed")
                }
            }
        }

        // Poll until all exit or 10s timeout, then SIGKILL survivors
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let any_alive = pids_and_ids
                .iter()
                .any(|(pid, _)| kill(Pid::from_raw(*pid as i32), None).is_ok());
            if !any_alive {
                break;
            }
            if Instant::now() > deadline {
                for (pid, worker_id) in &pids_and_ids {
                    if kill(Pid::from_raw(*pid as i32), None).is_ok() {
                        warn!(worker_id = %worker_id, pid = pid, "Still alive after 10s, sending SIGKILL to process group");
                        crate::process_group::kill_process_group(*pid, Signal::SIGKILL);
                    }
                }
                sleep(Duration::from_millis(500)).await;
                break;
            }
            sleep(Duration::from_millis(200)).await;
        }

        self.workers.write().await.clear();
        info!("All workers shut down");
        Ok(())
    }
}

/// Stream lines from a process pipe with colored prefix.
async fn stream_logs(name: String, color: &'static str, reader: impl tokio::io::AsyncRead + Unpin) {
    let prefix = if color.is_empty() {
        format!("{name} | ")
    } else {
        format!("{color}{name} |{COLOR_RESET} ")
    };

    let mut lines = BufReader::new(reader).lines();

    while let Ok(Some(line)) = lines.next_line().await {
        println!("{prefix}{line}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_kill_worker_not_found() {
        let sup = Supervisor::new(9999, None, None, Duration::from_secs(300));
        match sup.kill_worker("nonexistent-uuid").await {
            KillResult::NotFound => {}
            KillResult::Confirmed => panic!("expected NotFound for unknown UUID"),
        }
    }
}
