//! Worker process supervision.
//!
//! Manages the lifecycle of N worker subprocesses: spawning, colored log
//! streaming, crash detection with auto-restart, and graceful shutdown.
//! Ported from the standalone orchestrator binary.

use anyhow::{Context, Result};
use nix::sys::signal::{Signal, kill};
use nix::unistd::Pid;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{Mutex, RwLock};
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
fn worker_binary_path() -> PathBuf {
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

struct ProcessInfo {
    child: Child,
}

/// PID stored outside mutex for lock-free signal sending.
struct ProcessHandle {
    pid: u32,
    info: Arc<Mutex<ProcessInfo>>,
}

/// Supervises N worker subprocesses with crash restart and graceful shutdown.
#[derive(Clone)]
pub struct Supervisor {
    workers: usize,
    port: u16,
    worker_poll_interval: Option<String>,
    processes: Arc<RwLock<HashMap<String, ProcessHandle>>>,
    shutting_down: Arc<RwLock<bool>>,
}

impl Supervisor {
    pub fn new(workers: usize, port: u16, worker_poll_interval: Option<String>) -> Self {
        Self {
            workers,
            port,
            worker_poll_interval,
            processes: Arc::new(RwLock::new(HashMap::new())),
            shutting_down: Arc::new(RwLock::new(false)),
        }
    }

    /// Spawn all worker processes. Transactional: if any spawn fails, already-started
    /// workers are shut down before returning the error.
    pub async fn start_workers(&self) -> Result<()> {
        for i in 1..=self.workers {
            if let Err(e) = self.start_worker(i).await {
                warn!("Worker {i} failed to start: {e}. Cleaning up already-started workers...");
                // Best-effort cleanup of already-started workers
                let _ = self.shutdown().await;
                return Err(e).context(format!("failed to start worker {i}"));
            }
        }
        Ok(())
    }

    async fn start_worker(&self, id: usize) -> Result<()> {
        let name = format!("worker-{id}");
        let scheduler_url = format!("http://localhost:{}", self.port);
        let worker_bin = worker_binary_path();

        let mut cmd = Command::new(&worker_bin);
        cmd.arg("--scheduler-url").arg(&scheduler_url);

        if let Some(interval) = &self.worker_poll_interval {
            cmd.arg("--interval").arg(interval);
        }

        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

        let color = get_color(&name);
        let mut child = cmd.spawn().context(format!(
            "failed to spawn {name} from {}",
            worker_bin.display()
        ))?;

        let pid = child.id().context("no PID for child process")?;
        info!("Started {name} (PID: {pid})");

        let stdout = child.stdout.take().context("failed to get stdout pipe")?;
        let stderr = child.stderr.take().context("failed to get stderr pipe")?;

        let proc_info = Arc::new(Mutex::new(ProcessInfo { child }));
        let handle = ProcessHandle {
            pid,
            info: proc_info,
        };

        self.processes.write().await.insert(name.clone(), handle);

        // Log streaming tasks
        let name_out = name.clone();
        tokio::spawn(async move {
            stream_logs(name_out, color, stdout).await;
        });

        let name_err = name.clone();
        tokio::spawn(async move {
            stream_logs(name_err, color, stderr).await;
        });

        // Monitor task with restart loop
        let sup = self.clone();
        let name_mon = name.clone();
        tokio::spawn(async move {
            sup.monitor_process_loop(name_mon, color).await;
        });

        Ok(())
    }

    /// Monitor a worker process and restart on crash (up to 5 retries).
    async fn monitor_process_loop(&self, name: String, color: &'static str) {
        let mut retry_count = 0u32;
        let max_retries = 5u32;

        loop {
            // Wait for process to exit
            let proc_info = {
                let processes = self.processes.read().await;
                processes.get(&name).map(|handle| handle.info.clone())
            };

            let exit_status = if let Some(proc_info) = proc_info {
                let mut proc = proc_info.lock().await;
                proc.child.wait().await
            } else {
                return;
            };

            if *self.shutting_down.read().await {
                return;
            }

            match exit_status {
                Err(e) => warn!("Process {name} exited unexpectedly: {e}"),
                Ok(status) => warn!("Process {name} exited unexpectedly with {status}"),
            }

            retry_count += 1;

            if retry_count >= max_retries {
                warn!("Process {name} exceeded max retries ({max_retries}), not restarting");
                return;
            }

            warn!(
                "Restarting {} (attempt {}/{}) in 1 second...",
                name,
                retry_count + 1,
                max_retries
            );

            sleep(Duration::from_secs(1)).await;

            if *self.shutting_down.read().await {
                return;
            }

            if let Err(e) = self.restart_worker(&name, color).await {
                warn!("Failed to restart {name}: {e}");
                return;
            }
        }
    }

    async fn restart_worker(&self, name: &str, color: &'static str) -> Result<()> {
        let scheduler_url = format!("http://localhost:{}", self.port);
        let worker_bin = worker_binary_path();

        let mut cmd = Command::new(&worker_bin);
        cmd.arg("--scheduler-url").arg(&scheduler_url);

        if let Some(interval) = &self.worker_poll_interval {
            cmd.arg("--interval").arg(interval);
        }

        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

        let mut child = cmd.spawn().context(format!(
            "failed to restart {name} from {}",
            worker_bin.display()
        ))?;
        let pid = child.id().context("no PID for restarted process")?;
        info!("Restarted {name} (PID: {pid})");

        let stdout = child.stdout.take().context("failed to get stdout pipe")?;
        let stderr = child.stderr.take().context("failed to get stderr pipe")?;

        let proc_info = Arc::new(Mutex::new(ProcessInfo { child }));
        let handle = ProcessHandle {
            pid,
            info: proc_info,
        };

        self.processes
            .write()
            .await
            .insert(name.to_string(), handle);

        let name_out = name.to_string();
        tokio::spawn(async move {
            stream_logs(name_out, color, stdout).await;
        });

        let name_err = name.to_string();
        tokio::spawn(async move {
            stream_logs(name_err, color, stderr).await;
        });

        Ok(())
    }

    /// Gracefully shut down all workers: SIGTERM, poll for exit, SIGKILL survivors.
    pub async fn shutdown(&self) -> Result<()> {
        info!("Shutting down workers...");
        *self.shutting_down.write().await = true;

        // Collect PIDs (brief lock)
        let pids_and_names: Vec<(u32, String)> = {
            let processes = self.processes.read().await;
            processes
                .iter()
                .map(|(name, handle)| (handle.pid, name.clone()))
                .collect()
        };

        // SIGTERM all workers
        for (pid, name) in &pids_and_names {
            match kill(Pid::from_raw(*pid as i32), Signal::SIGTERM) {
                Ok(_) => info!("Sent SIGTERM to {name} (PID {pid})"),
                Err(e) => warn!("Failed to send SIGTERM to {name}: {e}"),
            }
        }

        // Poll until all workers exit or 10s timeout
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let any_alive = pids_and_names
                .iter()
                .any(|(pid, _)| kill(Pid::from_raw(*pid as i32), None).is_ok());
            if !any_alive {
                break;
            }
            if Instant::now() > deadline {
                for (pid, name) in &pids_and_names {
                    if kill(Pid::from_raw(*pid as i32), None).is_ok() {
                        warn!("Worker {name} still alive after 10s, sending SIGKILL");
                        let _ = kill(Pid::from_raw(*pid as i32), Signal::SIGKILL);
                    }
                }
                sleep(Duration::from_millis(500)).await;
                break;
            }
            sleep(Duration::from_millis(200)).await;
        }

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
