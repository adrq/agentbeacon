use nix::sys::signal::Signal;
use nix::unistd::Pid;

#[cfg(unix)]
pub fn configure_child_process(cmd: &mut tokio::process::Command) {
    cmd.process_group(0);
    cmd.kill_on_drop(true);

    #[cfg(target_os = "linux")]
    {
        let parent_pid = std::process::id();
        unsafe {
            cmd.pre_exec(move || {
                if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                // Guard against the fork-to-pre_exec race: if the parent died
                // between fork() and prctl(), PDEATHSIG was never armed for
                // the real parent. Detect via getppid() change and fail spawn.
                // Allocation-free: from_raw_os_error is async-signal-safe.
                // ESRCH ("no such process") signals the parent is gone.
                if libc::getppid() as u32 != parent_pid {
                    return Err(std::io::Error::from_raw_os_error(libc::ESRCH));
                }
                Ok(())
            });
        }
    }
}

pub fn kill_process_group(pid: u32, signal: Signal) {
    if pid == 0 {
        return;
    }
    let _ = nix::sys::signal::kill(Pid::from_raw(-(pid as i32)), signal);
}

pub async fn kill_child_group(child: &mut tokio::process::Child) {
    if let Some(pid) = child.id() {
        kill_process_group(pid, Signal::SIGKILL);
    }
    let _ = child.wait().await;
}
