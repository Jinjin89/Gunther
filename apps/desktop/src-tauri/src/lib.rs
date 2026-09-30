use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::{Listener, Manager};

mod application_menu;
mod capture_shell;
mod external_links;
mod library_location;
mod tray_glyph;

#[derive(Default)]
struct BackendRuntime {
    child: Option<Child>,
    launch_nonce: Option<String>,
}

struct BackendProcess(Mutex<BackendRuntime>);
// Releases use their own uncommon port, so a development backend on 8787 never
// blocks the installed app. It stays below 32768, the range systems hand out
// for outgoing connections.
const RELEASE_BACKEND_PORT: &str = "28787";
#[cfg(debug_assertions)]
const BACKEND_BASE_URL: &str = "http://127.0.0.1:8787";
#[cfg(not(debug_assertions))]
const BACKEND_BASE_URL: &str = "http://127.0.0.1:28787";
const TOKEN_FILE_NAME: &str = "backend-auth-token";
const READY_TOKEN_DIR_NAME: &str = "backend-ready";

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct BackendConnection {
    base_url: &'static str,
    auth_token: String,
}

#[cfg(unix)]
fn set_private_umask() {
    // SAFETY: umask is process-global but start_backend runs once during setup,
    // before worker threads create any Gunther-owned files.
    unsafe {
        libc::umask(0o077);
    }
}

#[cfg(not(unix))]
fn set_private_umask() {}

fn create_private_dir(path: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(path)?;
    let metadata = std::fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "private data path must be a real directory",
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

fn secure_open(path: &Path, append: bool) -> std::io::Result<File> {
    if let Ok(metadata) = std::fs::symlink_metadata(path) {
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "private file path must be a regular file",
            ));
        }
    }
    let mut options = OpenOptions::new();
    options.create(true).write(true).append(append);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let file = options.open(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(file)
}

fn secure_read(path: &Path) -> std::io::Result<String> {
    let metadata = std::fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "ready token path must be a regular file",
        ));
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options.open(path)?;
    if file.metadata()?.len() > 256 {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "ready token file is too large",
        ));
    }
    let mut contents = String::new();
    file.take(1024).read_to_string(&mut contents)?;
    Ok(contents)
}

fn generate_launch_nonce() -> std::io::Result<String> {
    let mut bytes = [0_u8; 32];
    getrandom::getrandom(&mut bytes)
        .map_err(|error| std::io::Error::other(format!("OS randomness unavailable: {error}")))?;
    let mut nonce = String::with_capacity(64);
    const HEX: &[u8; 16] = b"0123456789abcdef";
    for byte in bytes {
        nonce.push(HEX[(byte >> 4) as usize] as char);
        nonce.push(HEX[(byte & 0x0f) as usize] as char);
    }
    Ok(nonce)
}

fn ready_token_path(data_dir: &Path, launch_nonce: &str) -> PathBuf {
    data_dir
        .join(READY_TOKEN_DIR_NAME)
        .join(format!("{TOKEN_FILE_NAME}.{launch_nonce}"))
}

fn parse_ready_token(contents: &str, expected_nonce: &str) -> Option<String> {
    let mut lines = contents.lines();
    if lines.next()? != expected_nonce {
        return None;
    }
    let token = lines.next()?;
    if lines.next().is_some()
        || token.len() != 43
        || !token
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return None;
    }
    Some(token.to_owned())
}

fn log_backend_event(data_dir: &Path, message: &str) {
    let log_path = data_dir.join("backend.log");
    if let Ok(mut file) = secure_open(&log_path, true) {
        let _ = writeln!(file, "{message}");
    }
}

#[cfg(target_os = "macos")]
fn backend_executable(_app: &tauri::AppHandle) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let executable = std::env::current_exe()?;
    let contents_dir = executable
        .parent()
        .and_then(Path::parent)
        .ok_or("Gunther application bundle path is invalid")?;

    Ok(contents_dir
        .join("Helpers")
        .join("GuntherBackend.app")
        .join("Contents")
        .join("MacOS")
        .join("GuntherBackend"))
}

#[cfg(not(target_os = "macos"))]
fn backend_executable(app: &tauri::AppHandle) -> Result<PathBuf, Box<dyn std::error::Error>> {
    Ok(app
        .path()
        .resource_dir()?
        .join("backend")
        .join(format!("gunther-backend{}", std::env::consts::EXE_SUFFIX)))
}

fn start_backend(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    if cfg!(debug_assertions) {
        return Ok(());
    }

    set_private_umask();
    let data_dir = app.path().app_data_dir()?;
    create_private_dir(&data_dir)?;
    create_private_dir(&data_dir.join(READY_TOKEN_DIR_NAME))?;
    let launch_nonce = generate_launch_nonce()?;
    log_backend_event(&data_dir, "Starting Gunther's bundled knowledge service");
    let log_path = data_dir.join("backend.log");
    let stdout = secure_open(&log_path, true)?;
    let stderr = stdout.try_clone()?;
    let mut command = Command::new(backend_executable(app)?);
    // The folder chosen in Settings; without one the backend uses ~/Gunther.
    if let Some(root) = library_location::chosen_root(&data_dir) {
        command.env("LIBRARY_ROOT", root);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let child = command
        .arg("--data-dir")
        .arg(&data_dir)
        .arg("--port")
        .arg(RELEASE_BACKEND_PORT)
        .arg("--launch-nonce")
        .arg(&launch_nonce)
        // The helper exits when this pipe closes, so a crashed or force-quit
        // Gunther never leaves it running and holding the port.
        .arg("--exit-when-stdin-closes")
        .stdin(Stdio::piped())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr))
        .spawn()?;

    let state = app.state::<BackendProcess>();
    let mut runtime = state.0.lock().expect("backend lock poisoned");
    runtime.child = Some(child);
    runtime.launch_nonce = Some(launch_nonce);
    Ok(())
}

/// Stop the bundled backend and forget its launch, e.g. before moving the libraries.
fn stop_backend(app: &tauri::AppHandle) {
    let state = app.state::<BackendProcess>();
    let (child, launch_nonce) = {
        let mut runtime = state.0.lock().expect("backend lock poisoned");
        (runtime.child.take(), runtime.launch_nonce.take())
    };
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
    if let (Ok(data_dir), Some(launch_nonce)) = (app.path().app_data_dir(), launch_nonce) {
        let _ = std::fs::remove_file(ready_token_path(&data_dir, &launch_nonce));
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct LibraryLocation {
    /// The folder chosen in Settings, or None for the default.
    chosen: Option<String>,
    /// Only the installed app starts its own backend; in development it runs on its own.
    can_change: bool,
}

#[tauri::command]
fn library_location(app: tauri::AppHandle) -> Result<LibraryLocation, String> {
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|_| "Gunther could not locate its private data directory".to_string())?;
    Ok(LibraryLocation {
        chosen: library_location::chosen_root(&data_dir).map(|path| path.display().to_string()),
        can_change: !cfg!(debug_assertions),
    })
}

/// Where the libraries would go if `picked` were chosen, or why they cannot go there.
#[tauri::command]
fn library_destination(
    app: tauri::AppHandle,
    from: String,
    picked: String,
) -> Result<String, String> {
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|_| "Gunther could not locate its private data directory".to_string())?;
    let from = PathBuf::from(from);
    let to = library_location::destination(&from, Path::new(&picked));
    match library_location::problem_with(&from, &to, &data_dir) {
        Some(problem) => Err(problem),
        None => Ok(to.display().to_string()),
    }
}

/// Move the libraries from where the backend keeps them now to `to`, then start the
/// backend there. The backend is stopped meanwhile, so no file changes under the move.
#[tauri::command(async)]
fn change_library_location(
    app: tauri::AppHandle,
    from: String,
    to: String,
) -> Result<String, String> {
    if cfg!(debug_assertions) {
        return Err(
            "In development the backend runs on its own: set LIBRARY_ROOT for it and restart it."
                .into(),
        );
    }
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|_| "Gunther could not locate its private data directory".to_string())?;
    let from = PathBuf::from(from);
    let to = PathBuf::from(to);
    if !from.is_absolute() {
        return Err("Gunther could not tell where your libraries are now.".into());
    }
    if let Some(problem) = library_location::problem_with(&from, &to, &data_dir) {
        return Err(problem);
    }
    // Same rule as quitting: a recording in progress keeps the service running.
    if capture_shell::blocks_exit(&app) {
        return Err("Finish or stop the recording first, then move your libraries.".into());
    }
    log_backend_event(
        &data_dir,
        &format!(
            "Moving the libraries from {} to {}",
            from.display(),
            to.display()
        ),
    );
    stop_backend(&app);
    let outcome = library_location::move_library(&from, &to).and_then(|()| {
        library_location::save_choice(&data_dir, &to).map_err(|error| {
            // Not remembered: put the folder back so the backend finds it where it looks.
            let _ = library_location::move_library(&to, &from);
            error
        })
    });
    if let Err(error) = start_backend(&app) {
        log_backend_event(&data_dir, &format!("[launch error] {error}"));
        return Err(format!(
            "Gunther's local service did not start again: {error}"
        ));
    }
    outcome.map(|()| to.display().to_string())
}

fn create_capture_window(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    if app
        .get_webview_window(capture_shell::CAPTURE_WINDOW_LABEL)
        .is_some()
    {
        return Ok(());
    }
    let config = app
        .config()
        .app
        .windows
        .iter()
        .find(|window| window.label == capture_shell::CAPTURE_WINDOW_LABEL)
        .ok_or("Gunther's capture window configuration is missing")?;

    // `backgroundThrottling: disabled` asks WebKit not to throttle this hidden
    // webview, and the setting is only available on macOS 14+. It is a
    // scheduling policy, not an audio-durability guarantee. WebKit does not
    // expose it on macOS 11–13, where long sessions require moving microphone
    // capture out of the webview.
    tauri::WebviewWindowBuilder::from_config(app.handle(), config)?.build()?;
    Ok(())
}

// `async` runs this off the main thread, so the wait below never freezes the window.
#[tauri::command(async)]
fn backend_connection(app: tauri::AppHandle) -> Result<BackendConnection, String> {
    if cfg!(debug_assertions) {
        return Ok(BackendConnection {
            base_url: BACKEND_BASE_URL,
            auth_token: String::new(),
        });
    }

    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|_| "Gunther could not locate its private data directory".to_string())?;
    let launch_nonce = app
        .state::<BackendProcess>()
        .0
        .lock()
        .map_err(|_| "Gunther's local service state is unavailable".to_string())?
        .launch_nonce
        .clone()
        .ok_or_else(|| "Gunther's local knowledge service did not launch".to_string())?;
    let token_path = ready_token_path(&data_dir, &launch_nonce);
    // First launch can be slow while antivirus scans the freshly installed helper.
    for _ in 0..300 {
        if let Ok(contents) = secure_read(&token_path) {
            if let Some(token) = parse_ready_token(&contents, &launch_nonce) {
                return Ok(BackendConnection {
                    base_url: BACKEND_BASE_URL,
                    auth_token: token,
                });
            }
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err("Gunther's authenticated local knowledge service did not start".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .manage(BackendProcess(Mutex::new(BackendRuntime::default())))
        .manage(capture_shell::CaptureShell::default())
        .menu(application_menu::build)
        .on_menu_event(|app, event| {
            capture_shell::handle_menu_event(app, event.id().as_ref());
        })
        .on_window_event(|window, event| {
            if matches!(window.label(), "main" | capture_shell::CAPTURE_WINDOW_LABEL) {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .setup(|app| {
            if let Err(error) = start_backend(app.handle()) {
                eprintln!("Gunther's local knowledge service could not start: {error}");
                if let Ok(data_dir) = app.path().app_data_dir() {
                    log_backend_event(&data_dir, &format!("[launch error] {error}"));
                }
            }
            create_capture_window(app)?;
            capture_shell::setup_tray(app)?;
            let handle = app.handle().clone();
            app.listen(capture_shell::CAPTURE_SAVED_EVENT, move |event| {
                // Saving a note beside a running recording leaves the recording's
                // menu bar state alone; the renderer keeps reporting it.
                if !capture_shell::saved_capture_continues(event.payload()) {
                    capture_shell::reset_after_capture_saved(&handle);
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_connection,
            library_location,
            library_destination,
            change_library_location,
            capture_shell::open_capture_window,
            capture_shell::hide_capture_window,
            capture_shell::show_main_window,
            capture_shell::take_capture_launch_request,
            capture_shell::update_capture_status,
            capture_shell::menu_bar_mode,
            capture_shell::set_menu_bar_mode,
            external_links::open_external_url,
        ])
        .build(tauri::generate_context!())
        .expect("error while building Gunther desktop");

    app.run(|app_handle, event| match event {
        tauri::RunEvent::ExitRequested { api, .. } if capture_shell::blocks_exit(app_handle) => {
            api.prevent_exit();
            capture_shell::block_quit_and_show_capture(app_handle);
        }
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Reopen { .. } => {
            let _ = capture_shell::show_main_window(app_handle.clone());
        }
        tauri::RunEvent::Exit => stop_backend(app_handle),
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::{generate_launch_nonce, parse_ready_token, ready_token_path};
    use std::path::Path;

    #[test]
    fn launch_nonces_are_unique_lowercase_hex() {
        let first = generate_launch_nonce().expect("OS randomness");
        let second = generate_launch_nonce().expect("OS randomness");
        assert_ne!(first, second);
        assert_eq!(first.len(), 64);
        assert!(first.bytes().all(|byte| byte.is_ascii_hexdigit()));
        assert_eq!(first, first.to_ascii_lowercase());
    }

    #[test]
    fn ready_token_is_bound_to_the_expected_nonce() {
        let nonce = "a".repeat(64);
        let other = "b".repeat(64);
        let token = "A".repeat(43);
        let contents = format!("{nonce}\n{token}\n");
        assert_eq!(parse_ready_token(&contents, &nonce), Some(token));
        assert_eq!(parse_ready_token(&contents, &other), None);
        assert_eq!(parse_ready_token("malformed", &nonce), None);
        assert_eq!(
            parse_ready_token(&format!("{nonce}\n{}\n", "A".repeat(44)), &nonce),
            None
        );
        assert_eq!(
            parse_ready_token(&format!("{contents}extra\n"), &nonce),
            None
        );
    }

    #[test]
    fn every_nonce_has_an_isolated_ready_path() {
        let first = ready_token_path(Path::new("/private/data"), &"1".repeat(64));
        let second = ready_token_path(Path::new("/private/data"), &"2".repeat(64));
        assert_ne!(first, second);
        assert!(first.starts_with("/private/data/backend-ready"));
    }
}
