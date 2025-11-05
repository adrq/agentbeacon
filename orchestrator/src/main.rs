//! AgentMaestro Orchestrator - Process supervisor for scheduler and worker processes
//!
//! This binary manages the lifecycle of the AgentMaestro system by spawning and
//! monitoring the scheduler and worker processes. It provides:
//! - Colored log aggregation with TTY detection
//! - Memory-safe line-by-line log streaming (O(max_line_length))
//! - Health check polling for scheduler readiness
//! - Automatic crash detection and restart (up to 5 retries)
//! - Graceful shutdown with SIGTERM/SIGKILL handling
//!
//! Architecture matches the Go implementation for drop-in replacement compatibility.

use anyhow::{Context, Result};
use clap::Parser;
use nix::sys::signal::{Signal, kill};
use nix::unistd::Pid;
use std::collections::HashMap;
use std::process::Stdio;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{Mutex, RwLock};
use tokio::time::{Duration, Instant, sleep};

/// ANSI color codes for log prefixes
const COLORS: &[&str] = &[
    "\x1b[1;32m", // Green
    "\x1b[1;34m", // Blue
    "\x1b[1;35m", // Magenta
    "\x1b[1;36m", // Cyan
    "\x1b[1;33m", // Yellow
    "\x1b[1;31m", // Red
];

const COLOR_RESET: &str = "\x1b[0m";

/// Check if stdout is a TTY (for colored output)
fn is_terminal() -> bool {
    use std::io::IsTerminal;
    std::io::stdout().is_terminal()
}

/// Get deterministic color for a process name
fn get_color(name: &str) -> &'static str {
    if !is_terminal() {
        return "";
    }
    let hash: usize = name.bytes().fold(0usize, |acc, b| {
        acc.wrapping_mul(31).wrapping_add(b as usize)
    });
    COLORS[hash % COLORS.len()]
}

/// Command-line arguments
#[derive(Parser, Debug)]
#[command(name = "agentmaestro")]
#[command(about = "AgentMaestro Orchestrator - Process supervisor", long_about = None)]
struct Args {
    /// Number of worker processes to spawn
    #[arg(long, default_value_t = 2)]
    workers: usize,

    /// Port for the scheduler to listen on
    #[arg(long, default_value_t = 9456)]
    scheduler_port: u16,

    /// Worker sync polling interval (e.g., '1s', '500ms'). If not specified, workers use their default (5s)
    #[arg(long)]
    worker_poll_interval: Option<String>,
}

/// Information about a managed child process
struct ProcessInfo {
    child: Child,
}

/// Handle to a process (PID stored outside mutex for lock-free signal sending)
struct ProcessHandle {
    pid: u32,
    info: Arc<Mutex<ProcessInfo>>,
}

/// Orchestrator manages scheduler and worker processes
#[derive(Clone)]
struct Orchestrator {
    workers: usize,
    scheduler_port: u16,
    worker_poll_interval: Option<String>,
    processes: Arc<RwLock<HashMap<String, ProcessHandle>>>,
    shutting_down: Arc<RwLock<bool>>,
}

impl Orchestrator {
    fn new(workers: usize, scheduler_port: u16, worker_poll_interval: Option<String>) -> Self {
        Self {
            workers,
            scheduler_port,
            worker_poll_interval,
            processes: Arc::new(RwLock::new(HashMap::new())),
            shutting_down: Arc::new(RwLock::new(false)),
        }
    }

    /// Start all child processes (scheduler + workers)
    async fn start(&self) -> Result<()> {
        // Start scheduler
        self.start_scheduler().await?;

        // Start workers
        for i in 1..=self.workers {
            self.start_worker(i).await?;
        }

        println!("All processes started successfully");
        Ok(())
    }

    /// Wait for scheduler to be ready (health check polling)
    async fn wait_for_readiness(&self) -> Result<()> {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(1))
            .build()?;

        let health_url = format!("http://localhost:{}/api/health", self.scheduler_port);
        let deadline = Instant::now() + Duration::from_secs(30);

        loop {
            if Instant::now() > deadline {
                anyhow::bail!("health check failed: scheduler timeout after 30 seconds");
            }

            if let Ok(resp) = client.get(&health_url).send().await {
                if resp.status().is_success() {
                    println!("Orchestrator ready - all processes started and scheduler responding");
                    return Ok(());
                }
            }

            sleep(Duration::from_millis(100)).await;
        }
    }

    /// Start the scheduler process
    async fn start_scheduler(&self) -> Result<()> {
        let name = "scheduler".to_string();
        let mut cmd = Command::new("./bin/agentmaestro-scheduler");
        cmd.arg("--port")
            .arg(self.scheduler_port.to_string())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        self.start_process(name, cmd, true).await
    }

    /// Start a worker process
    async fn start_worker(&self, id: usize) -> Result<()> {
        let name = format!("worker-{id}");
        let scheduler_url = format!("http://localhost:{}", self.scheduler_port);

        let mut cmd = Command::new("./bin/agentmaestro-worker");
        cmd.arg("--scheduler-url").arg(&scheduler_url);

        if let Some(interval) = &self.worker_poll_interval {
            cmd.arg("--interval").arg(interval);
        }

        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

        self.start_process(name, cmd, false).await
    }

    /// Start an individual process and set up monitoring
    async fn start_process(
        &self,
        name: String,
        mut cmd: Command,
        is_scheduler: bool,
    ) -> Result<()> {
        let color = get_color(&name);
        let mut child = cmd.spawn().context(format!("failed to spawn {name}"))?;

        let pid = child.id().context("no PID for child process")?;
        println!("Started {name} (PID: {pid})");

        // Get stdout/stderr pipes
        let stdout = child.stdout.take().context("failed to get stdout pipe")?;
        let stderr = child.stderr.take().context("failed to get stderr pipe")?;

        // Create ProcessInfo and ProcessHandle
        let proc_info = Arc::new(Mutex::new(ProcessInfo { child }));
        let handle = ProcessHandle {
            pid,
            info: proc_info.clone(),
        };

        // Store process
        self.processes.write().await.insert(name.clone(), handle);

        // Spawn log streaming tasks
        let name_clone = name.clone();
        tokio::spawn(async move {
            Self::stream_logs(name_clone.clone(), color, stdout).await;
        });

        let name_clone = name.clone();
        tokio::spawn(async move {
            Self::stream_logs(name_clone.clone(), color, stderr).await;
        });

        // Spawn process monitor with restart loop
        let orchestrator = self.clone();
        let name_clone = name.clone();
        tokio::spawn(async move {
            orchestrator
                .monitor_process_loop(name_clone, is_scheduler, color)
                .await;
        });

        Ok(())
    }

    /// Stream logs from a process pipe (memory-safe line-by-line)
    async fn stream_logs(
        name: String,
        color: &'static str,
        reader: impl tokio::io::AsyncRead + Unpin,
    ) {
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

    /// Monitor a process and restart on crash (with retry loop)
    async fn monitor_process_loop(&self, name: String, is_scheduler: bool, color: &'static str) {
        let mut retry_count = 0u32;
        let max_retries = 5u32;

        loop {
            // Wait for process to exit (clone Arc to avoid holding read lock)
            let proc_info = {
                let processes = self.processes.read().await;
                processes.get(&name).map(|handle| handle.info.clone())
            };

            let exit_status = if let Some(proc_info) = proc_info {
                let mut proc = proc_info.lock().await;
                proc.child.wait().await
            } else {
                return; // Process removed, exit monitor
            };

            // Check if we're shutting down
            if *self.shutting_down.read().await {
                return;
            }

            // Process crashed
            if let Err(e) = exit_status {
                eprintln!("Process {name} exited unexpectedly: {e}");
            } else {
                eprintln!("Process {name} exited unexpectedly");
            }

            retry_count += 1;

            if retry_count >= max_retries {
                eprintln!("Process {name} exceeded max retries ({max_retries}), not restarting");

                // If scheduler failed, exit orchestrator
                if is_scheduler {
                    std::process::exit(2);
                }
                return;
            }

            eprintln!(
                "Restarting {} (attempt {}/{}) in 1 second...",
                name,
                retry_count + 1,
                max_retries
            );

            sleep(Duration::from_secs(1)).await;

            // Check again if we're shutting down
            if *self.shutting_down.read().await {
                return;
            }

            // Restart the process
            if let Err(e) = self.restart_process(&name, is_scheduler, color).await {
                eprintln!("Failed to restart {name}: {e}");
                return;
            }
        }
    }

    /// Restart a crashed process
    async fn restart_process(
        &self,
        name: &str,
        is_scheduler: bool,
        color: &'static str,
    ) -> Result<()> {
        let mut cmd = if is_scheduler {
            let mut cmd = Command::new("./bin/agentmaestro-scheduler");
            cmd.arg("--port").arg(self.scheduler_port.to_string());
            cmd
        } else {
            let scheduler_url = format!("http://localhost:{}", self.scheduler_port);
            let mut cmd = Command::new("./bin/agentmaestro-worker");
            cmd.arg("--scheduler-url").arg(&scheduler_url);

            if let Some(interval) = &self.worker_poll_interval {
                cmd.arg("--interval").arg(interval);
            }

            cmd
        };

        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

        let mut child = cmd.spawn().context(format!("failed to restart {name}"))?;
        let pid = child.id().context("no PID for restarted process")?;
        println!("Restarted {name} (PID: {pid})");

        // Get pipes
        let stdout = child.stdout.take().context("failed to get stdout pipe")?;
        let stderr = child.stderr.take().context("failed to get stderr pipe")?;

        // Create new ProcessInfo and ProcessHandle
        let proc_info = Arc::new(Mutex::new(ProcessInfo { child }));
        let handle = ProcessHandle {
            pid,
            info: proc_info.clone(),
        };

        // Update processes map
        self.processes
            .write()
            .await
            .insert(name.to_string(), handle);

        // Spawn new log streaming tasks
        let name_str = name.to_string();
        tokio::spawn(async move {
            Self::stream_logs(name_str.clone(), color, stdout).await;
        });

        let name_str = name.to_string();
        tokio::spawn(async move {
            Self::stream_logs(name_str.clone(), color, stderr).await;
        });

        Ok(())
    }

    /// Gracefully shutdown all processes
    async fn shutdown(&self) -> Result<()> {
        println!("Shutting down all processes...");

        // Mark as shutting down to prevent monitor restarts
        *self.shutting_down.write().await = true;

        // Collect all PIDs (lock briefly, copy PIDs, release)
        let pids_and_names: Vec<(u32, String)> = {
            let processes = self.processes.read().await;
            let mut result = Vec::new();
            for (name, handle) in processes.iter() {
                result.push((handle.pid, name.clone()));
            }
            result
        };

        // Send SIGTERM to all processes (no locks held)
        for (pid, name) in &pids_and_names {
            match kill(Pid::from_raw(*pid as i32), Signal::SIGTERM) {
                Ok(_) => println!("Sent SIGTERM to {name}"),
                Err(e) => eprintln!("Failed to send SIGTERM to {name}: {e}"),
            }
        }

        // Wait for monitors to finish (they'll exit when processes die)
        sleep(Duration::from_secs(10)).await;

        // Check if any processes still alive, send SIGKILL
        let mut any_alive = false;
        for (pid, name) in &pids_and_names {
            // Use signal 0 to check if process exists (doesn't actually send a signal)
            if let Ok(()) = kill(Pid::from_raw(*pid as i32), None) {
                // Process still alive
                any_alive = true;
                eprintln!("Process {name} still alive, sending SIGKILL");
                let _ = kill(Pid::from_raw(*pid as i32), Signal::SIGKILL);
            }
        }

        if any_alive {
            // Give SIGKILL a moment to work
            sleep(Duration::from_secs(1)).await;
        }

        println!("All processes exited gracefully");
        Ok(())
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();

    // Print startup banner
    println!("AgentMaestro Orchestrator starting...");
    println!(
        "Configuration: {} workers, scheduler port {}",
        args.workers, args.scheduler_port
    );

    // Create orchestrator
    let orchestrator =
        Orchestrator::new(args.workers, args.scheduler_port, args.worker_poll_interval);

    // Start all processes
    orchestrator.start().await?;

    // Wait for scheduler readiness
    orchestrator.wait_for_readiness().await?;

    // Setup signal handler
    let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let mut sigint = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())?;

    // Wait for shutdown signal
    tokio::select! {
        _ = sigterm.recv() => {
            println!("Received shutdown signal...");
        }
        _ = sigint.recv() => {
            println!("Received shutdown signal...");
        }
    }

    // Graceful shutdown
    orchestrator.shutdown().await?;
    println!("Shutdown completed successfully");

    Ok(())
}
