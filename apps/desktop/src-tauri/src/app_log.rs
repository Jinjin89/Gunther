//! Gunther's log files, so a problem can be traced after it happened.
//!
//! Each process writes one file per day into `.gunther/logs` in the library folder:
//! `2026-09-30.app.log` holds this shell and its windows, `2026-09-30.backend.log`
//! the knowledge service. A day's file past `MAX_FILE_BYTES` goes on in
//! `2026-09-30.app.2.log`, and files older than `KEEP_DAYS` are deleted at start.
//! Every line starts with the local time, so the files of one day read as one story.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::RwLock;

use tauri::Manager;
use tauri_plugin_log::{fern, Target, TargetKind, WEBVIEW_TARGET};

pub const KEEP_DAYS: i64 = 14;
const MAX_FILE_BYTES: u64 = 20 * 1024 * 1024;
const MAX_TOTAL_BYTES: u64 = 500 * 1024 * 1024;
/// The shell and its windows; the knowledge service writes `BACKEND_NAME`.
const APP_NAME: &str = "app";
pub const BACKEND_NAME: &str = "backend";

static FOLDER: RwLock<Option<PathBuf>> = RwLock::new(None);

/// The folder log files go to now, once `install` has run.
pub fn folder() -> Option<PathBuf> {
    FOLDER.read().ok().and_then(|folder| folder.clone())
}

/// Write to `folder` from now on, e.g. after the library moved there.
pub fn set_folder(folder: PathBuf) {
    if let Ok(mut current) = FOLDER.write() {
        *current = Some(folder);
    }
}

/// `.gunther/logs` inside a library folder, next to the originals it keeps.
pub fn folder_in_library(library_root: &Path) -> PathBuf {
    library_root.join(".gunther").join("logs")
}

fn today() -> String {
    chrono::Local::now().format("%Y-%m-%d").to_string()
}

fn file_name(date: &str, name: &str, part: u32) -> String {
    if part <= 1 {
        format!("{date}.{name}.log")
    } else {
        format!("{date}.{name}.{part}.log")
    }
}

fn open_append(path: &Path) -> io::Result<File> {
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // Logs name files and folders of the library: private like the library's own data.
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    options.open(path)
}

/// Today's file for `name` in `folder`: the first part still under the size limit.
fn open_today(folder: &Path, date: &str, name: &str) -> io::Result<(File, PathBuf, u64)> {
    fs::create_dir_all(folder)?;
    let mut part = 1;
    loop {
        let path = folder.join(file_name(date, name, part));
        let size = fs::metadata(&path)
            .map(|metadata| metadata.len())
            .unwrap_or(0);
        if size < MAX_FILE_BYTES {
            return Ok((open_append(&path)?, path, size));
        }
        part += 1;
    }
}

/// Where the knowledge service's output goes: today's backend file, opened for appending.
pub fn open_backend_file() -> io::Result<File> {
    let folder = folder().ok_or_else(|| io::Error::other("the log folder is not known yet"))?;
    open_today(&folder, &today(), BACKEND_NAME).map(|(file, _, _)| file)
}

/// The path of today's backend file, for reading why the service stopped.
fn backend_file_path() -> Option<PathBuf> {
    let folder = folder()?;
    open_today(&folder, &today(), BACKEND_NAME)
        .ok()
        .map(|(_, path, _)| path)
}

/// A writer for one process's lines that moves to a new file each day, when a
/// file grows past the limit, and when the log folder moves.
struct DailyFile {
    name: &'static str,
    open: Option<OpenFile>,
}

struct OpenFile {
    folder: PathBuf,
    date: String,
    file: File,
    size: u64,
}

impl DailyFile {
    fn current(&mut self) -> io::Result<&mut OpenFile> {
        let folder = folder().ok_or_else(|| io::Error::other("the log folder is not known yet"))?;
        let date = today();
        let stale = match &self.open {
            Some(open) => open.folder != folder || open.date != date || open.size >= MAX_FILE_BYTES,
            None => true,
        };
        if stale {
            self.open = None;
            let (file, _, size) = open_today(&folder, &date, self.name)?;
            self.open = Some(OpenFile {
                folder,
                date,
                file,
                size,
            });
        }
        Ok(self.open.as_mut().expect("a log file was just opened"))
    }
}

impl Write for DailyFile {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let open = self.current()?;
        open.file.write_all(buf)?;
        open.size += buf.len() as u64;
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        match &mut self.open {
            Some(open) => open.file.flush(),
            None => Ok(()),
        }
    }
}

/// Delete files older than `KEEP_DAYS`, then the oldest until all fit in `MAX_TOTAL_BYTES`.
pub fn prune(folder: &Path, today: &str, cutoff: &str) {
    let Ok(entries) = fs::read_dir(folder) else {
        return;
    };
    let mut files: Vec<(String, u64)> = entries
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_file()))
        .filter_map(|entry| {
            let name = entry.file_name().into_string().ok()?;
            let size = entry.metadata().ok()?.len();
            is_log_file(&name).then_some((name, size))
        })
        .collect();
    // Named by date first, so sorting by name puts the oldest first.
    files.sort();
    let mut total: u64 = files.iter().map(|(_, size)| size).sum();
    for (name, size) in files {
        let old = name[..10] < *cutoff;
        let over = total > MAX_TOTAL_BYTES && !name.starts_with(today);
        if (old || over) && fs::remove_file(folder.join(&name)).is_ok() {
            total -= size;
        }
    }
}

fn is_log_file(name: &str) -> bool {
    let bytes = name.as_bytes();
    name.len() > 15
        && name.ends_with(".log")
        && bytes[..10].iter().enumerate().all(|(index, byte)| {
            if index == 4 || index == 7 {
                *byte == b'-'
            } else {
                byte.is_ascii_digit()
            }
        })
        && bytes[10] == b'.'
}

/// A short name for where a line came from: `ui` for the windows, `app` for this shell.
fn source(target: &str) -> String {
    if target.starts_with(WEBVIEW_TARGET) {
        return "ui".into();
    }
    match target.strip_prefix("gunther_desktop_lib") {
        Some("") => "app".into(),
        Some(rest) => format!("app{}", rest.replace("::", "/")),
        None => target.to_owned(),
    }
}

/// The log plugin, writing to the daily files (and the terminal in development).
pub fn plugin<R: tauri::Runtime>() -> tauri::plugin::TauriPlugin<R> {
    let level = if cfg!(debug_assertions) {
        log::LevelFilter::Debug
    } else {
        log::LevelFilter::Info
    };
    let files = fern::Dispatch::new().chain(Box::new(DailyFile {
        name: APP_NAME,
        open: None,
    }) as Box<dyn Write + Send>);
    let mut builder = tauri_plugin_log::Builder::new()
        .clear_targets()
        .level(level)
        // Tauri and the web view explain themselves at debug level; their warnings are kept.
        .level_for("tauri", log::LevelFilter::Warn)
        .level_for("tao", log::LevelFilter::Warn)
        .level_for("wry", log::LevelFilter::Warn)
        .format(|out, message, record| {
            out.finish(format_args!(
                "{} {:<5} {} {}",
                chrono::Local::now().format("%Y-%m-%d %H:%M:%S%.3f%:z"),
                record.level(),
                source(record.target()),
                message
            ))
        })
        .target(Target::new(TargetKind::Dispatch(files)));
    if cfg!(debug_assertions) {
        builder = builder.target(Target::new(TargetKind::Stdout));
    }
    builder.build()
}

/// Choose the log folder, clear out old files, and log panics too. Call once, in setup.
pub fn install(app: &tauri::AppHandle, library_root: Option<&Path>) -> PathBuf {
    let fallback = app
        .path()
        .app_log_dir()
        .unwrap_or_else(|_| std::env::temp_dir().join("Gunther").join("logs"));
    // Development runs its own backend with its own library; its app logs stay out of it.
    let wanted = library_root
        .filter(|_| !cfg!(debug_assertions))
        .map(folder_in_library);
    let folder = match wanted {
        Some(folder) if fs::create_dir_all(&folder).is_ok() => folder,
        // The library may be on a disk that is not connected: log beside the app instead.
        _ => fallback,
    };
    set_folder(folder.clone());
    let cutoff = (chrono::Local::now() - chrono::Duration::days(KEEP_DAYS))
        .format("%Y-%m-%d")
        .to_string();
    prune(&folder, &today(), &cutoff);

    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        log::error!(
            "Gunther stopped on an internal error: {info}\n{}",
            std::backtrace::Backtrace::force_capture()
        );
        previous(info);
    }));
    folder
}

/// Why the knowledge service stopped, as it wrote in its log, if it said.
pub fn backend_stop_reason() -> Option<String> {
    const MARKER: &str = "knowledge service stopped: ";
    let path = backend_file_path()?;
    let mut file = File::open(path).ok()?;
    let length = file.metadata().ok()?.len();
    file.seek(SeekFrom::Start(length.saturating_sub(64 * 1024)))
        .ok()?;
    let mut tail = Vec::new();
    file.read_to_end(&mut tail).ok()?;
    let tail = String::from_utf8_lossy(&tail);
    tail.lines()
        .rev()
        .find_map(|line| {
            line.split_once(MARKER)
                .map(|(_, reason)| reason.trim().to_owned())
        })
        .filter(|reason| !reason.is_empty())
}

#[tauri::command]
pub fn logs_folder() -> Option<String> {
    folder().map(|folder| folder.display().to_string())
}

/// Open the log folder in Finder (or the system's file manager).
#[tauri::command]
pub fn show_logs_folder() -> Result<(), String> {
    let folder = folder().ok_or("Gunther has not chosen a log folder yet.")?;
    fs::create_dir_all(&folder)
        .map_err(|error| format!("The log folder could not be made: {error}"))?;
    #[cfg(target_os = "macos")]
    let mut command = std::process::Command::new("/usr/bin/open");
    #[cfg(target_os = "windows")]
    let mut command = std::process::Command::new("explorer");
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let mut command = std::process::Command::new("xdg-open");
    command
        .arg(&folder)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("The log folder could not be opened: {error}"))
}

#[cfg(test)]
mod tests {
    use super::{file_name, is_log_file, open_today, prune, source, MAX_FILE_BYTES};
    use std::fs;
    use std::path::PathBuf;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("gunther-logs-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("scratch folder");
        dir
    }

    #[test]
    fn files_are_named_by_day_then_process() {
        assert_eq!(file_name("2026-09-30", "app", 1), "2026-09-30.app.log");
        assert_eq!(
            file_name("2026-09-30", "backend", 3),
            "2026-09-30.backend.3.log"
        );
        assert!(is_log_file("2026-09-30.app.log"));
        assert!(is_log_file("2026-09-30.backend.2.log"));
        assert!(!is_log_file("backend.log"));
        assert!(!is_log_file("2026-09-30.app.txt"));
        assert!(!is_log_file("notes-09-30.app.log"));
    }

    #[test]
    fn a_full_file_goes_on_in_the_next_part() {
        let dir = scratch("parts");
        let (_, first, _) = open_today(&dir, "2026-09-30", "app").unwrap();
        assert_eq!(first, dir.join("2026-09-30.app.log"));
        fs::File::options()
            .write(true)
            .open(&first)
            .unwrap()
            .set_len(MAX_FILE_BYTES)
            .unwrap();
        let (_, second, size) = open_today(&dir, "2026-09-30", "app").unwrap();
        assert_eq!(second, dir.join("2026-09-30.app.2.log"));
        assert_eq!(size, 0);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn old_days_are_deleted_and_other_files_left_alone() {
        let dir = scratch("prune");
        for name in [
            "2026-09-01.app.log",
            "2026-09-01.backend.2.log",
            "2026-09-20.backend.log",
            "2026-09-30.app.log",
            "README.txt",
        ] {
            fs::write(dir.join(name), "x").unwrap();
        }
        prune(&dir, "2026-09-30", "2026-09-16");
        let mut left: Vec<String> = fs::read_dir(&dir)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().into_string().unwrap())
            .collect();
        left.sort();
        assert_eq!(
            left,
            ["2026-09-20.backend.log", "2026-09-30.app.log", "README.txt"]
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn sources_are_short() {
        assert_eq!(
            source("webview::http://tauri.localhost/assets/index.js:1:2"),
            "ui"
        );
        assert_eq!(source("webview"), "ui");
        assert_eq!(source("gunther_desktop_lib"), "app");
        assert_eq!(
            source("gunther_desktop_lib::capture_shell"),
            "app/capture_shell"
        );
        assert_eq!(source("tauri::manager"), "tauri::manager");
    }
}
