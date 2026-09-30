//! Stop a Gunther service left over from a run that ended badly, before starting a new one.
//!
//! The service stops by itself when its app goes away (see `desktop_server.py`,
//! `_stop_when_app_is_gone`), so a leftover is rare: an older version, or a service
//! that hung. The service names its process in `backend-owner.json` in the data
//! folder. That process is stopped only when it still is Gunther's service and no
//! running Gunther app started it; any other program, and a service another open
//! Gunther is using, are left alone.

use std::path::Path;

const OWNER_FILE_NAME: &str = "backend-owner.json";

/// Whether a process's executable is Gunther's service: `GuntherBackend` in the macOS
/// app, `gunther-backend` elsewhere (`ps` shows at most 15 characters of it on Linux).
fn is_service(command: &str) -> bool {
    let command = command.to_ascii_lowercase();
    command.contains("guntherbackend") || command.contains("gunther-backend")
}

/// Whether a process is a Gunther app (the shell that starts the service).
fn is_app(command: &str) -> bool {
    command.to_ascii_lowercase().contains("gunther") && !is_service(command)
}

/// The process named in the data folder, if any.
fn owner_pid(data_dir: &Path) -> Option<i32> {
    let text = std::fs::read_to_string(data_dir.join(OWNER_FILE_NAME)).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    let pid = value.get("pid")?.as_i64()?;
    // Never 0 or 1 (every process, or init) nor anything absurd.
    (2..=i64::from(i32::MAX))
        .contains(&pid)
        .then_some(pid as i32)
}

/// `ps -o ppid=,comm=` output: the parent's id, then the command (which may hold spaces).
fn parse_ps(output: &str) -> Option<(i32, String)> {
    let line = output.lines().find(|line| !line.trim().is_empty())?.trim();
    let (parent, command) = line.split_once(char::is_whitespace)?;
    Some((parent.parse().ok()?, command.trim().to_owned()))
}

/// What a leftover check decided, for the log and for tests.
#[derive(Debug, PartialEq, Eq)]
enum Verdict {
    /// Not Gunther's service (the id now belongs to another program): left alone.
    NotOurs,
    /// Gunther's service, started by a Gunther app that is still open: left alone.
    InUse,
    /// Gunther's service whose app is gone: stop it.
    Leftover,
}

fn judge(service_command: &str, parent_pid: i32, parent_command: Option<&str>) -> Verdict {
    if !is_service(service_command) {
        return Verdict::NotOurs;
    }
    if parent_pid > 1 && parent_command.is_some_and(is_app) {
        return Verdict::InUse;
    }
    Verdict::Leftover
}

#[cfg(unix)]
mod process {
    use std::process::Command;
    use std::time::{Duration, Instant};

    pub fn describe(pid: i32) -> Option<(i32, String)> {
        let output = Command::new("/bin/ps")
            .args(["-o", "ppid=,comm=", "-p", &pid.to_string()])
            .output()
            .ok()?;
        output
            .status
            .success()
            .then(|| super::parse_ps(&String::from_utf8_lossy(&output.stdout)))
            .flatten()
    }

    fn alive(pid: i32) -> bool {
        // SAFETY: signal 0 only asks whether the process exists; nothing is sent.
        unsafe { libc::kill(pid, 0) == 0 }
    }

    fn signal(pid: i32, signal: i32) {
        // SAFETY: `pid` was checked to be Gunther's own service just before.
        unsafe {
            libc::kill(pid, signal);
        }
    }

    fn gone_within(pid: i32, limit: Duration) -> bool {
        let started = Instant::now();
        while started.elapsed() < limit {
            if !alive(pid) {
                return true;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        !alive(pid)
    }

    /// Ask it to stop, then make it.
    pub fn stop(pid: i32) -> bool {
        signal(pid, libc::SIGTERM);
        if gone_within(pid, Duration::from_secs(3)) {
            return true;
        }
        log::warn!("The leftover service {pid} did not stop when asked; stopping it now");
        signal(pid, libc::SIGKILL);
        gone_within(pid, Duration::from_secs(2))
    }
}

/// Stop the service a previous Gunther left running on this data folder, if there is one.
#[cfg(unix)]
pub fn reclaim(data_dir: &Path) {
    let Some(pid) = owner_pid(data_dir) else {
        return;
    };
    let Some((parent, command)) = process::describe(pid) else {
        // Not running any more: nothing to do.
        return;
    };
    let parent_command = process::describe(parent).map(|(_, command)| command);
    match judge(&command, parent, parent_command.as_deref()) {
        Verdict::NotOurs => {}
        Verdict::InUse => log::warn!(
            "Gunther's service {pid} is in use by another open Gunther (process {parent})"
        ),
        Verdict::Leftover => {
            log::warn!("Stopping a Gunther service left over from an earlier run (process {pid})");
            if process::stop(pid) {
                log::info!("The leftover service {pid} has stopped");
            } else {
                log::error!("The leftover service {pid} could not be stopped");
            }
        }
    }
}

/// Windows has no `ps`; the service still stops by itself when the app's pipe closes.
#[cfg(not(unix))]
pub fn reclaim(_data_dir: &Path) {}

#[cfg(test)]
mod tests {
    use super::{judge, owner_pid, parse_ps, Verdict};
    use std::fs;

    #[test]
    fn only_gunthers_own_leftover_service_is_stopped() {
        let mac_service =
            "/Applications/Gunther.app/Contents/Helpers/GuntherBackend.app/Contents/MacOS/GuntherBackend";
        let mac_app = "/Applications/Gunther.app/Contents/MacOS/gunther-desktop";
        // Its app is gone: macOS gave it to launchd, or a new parent that is not Gunther.
        assert_eq!(
            judge(mac_service, 1, Some("/sbin/launchd")),
            Verdict::Leftover
        );
        assert_eq!(
            judge("gunther-backend", 812, Some("systemd")),
            Verdict::Leftover
        );
        assert_eq!(judge(mac_service, 4242, None), Verdict::Leftover);
        // Another open Gunther is using it.
        assert_eq!(judge(mac_service, 4242, Some(mac_app)), Verdict::InUse);
        assert_eq!(
            judge("gunther-backend", 4242, Some("gunther-desktop")),
            Verdict::InUse
        );
        // The id was reused by some other program: never touched.
        assert_eq!(judge("postgres", 1, Some("launchd")), Verdict::NotOurs);
        assert_eq!(
            judge(
                "/Applications/Gunther.app/Contents/MacOS/gunther-desktop",
                1,
                None
            ),
            Verdict::NotOurs
        );
    }

    #[test]
    fn ps_output_is_read_with_spaces_in_paths() {
        assert_eq!(
            parse_ps("    1 /Applications/My Apps/Gunther.app/Contents/MacOS/x\n"),
            Some((
                1,
                "/Applications/My Apps/Gunther.app/Contents/MacOS/x".into()
            ))
        );
        assert_eq!(parse_ps(""), None);
        assert_eq!(parse_ps("abc def"), None);
    }

    #[test]
    fn the_owner_is_read_from_the_data_folder() {
        let dir = std::env::temp_dir().join(format!("gunther-owner-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        assert_eq!(owner_pid(&dir), None);
        fs::write(dir.join("backend-owner.json"), "{\"pid\": 4321}\n").unwrap();
        assert_eq!(owner_pid(&dir), Some(4321));
        for bad in [
            "{\"pid\": 1}",
            "{\"pid\": 0}",
            "{\"pid\": -5}",
            "{}",
            "nonsense",
        ] {
            fs::write(dir.join("backend-owner.json"), bad).unwrap();
            assert_eq!(owner_pid(&dir), None, "{bad}");
        }
        let _ = fs::remove_dir_all(&dir);
    }
}
