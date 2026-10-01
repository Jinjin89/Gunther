use std::fs::{File, OpenOptions};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{Listener, Manager};

mod app_log;
mod application_menu;
mod capture_shell;
mod external_links;
mod leftover;
mod library_location;
mod tray_glyph;

#[derive(Default)]
struct BackendRuntime {
    child: Option<Child>,
    launch_nonce: Option<String>,
    /// Why the service of this launch stopped on its own, once it has.
    exited: Option<String>,
}

struct BackendProcess(Mutex<BackendRuntime>);
// Releases prefer their own uncommon port, so a development backend on 8787 never
// blocks the installed app. It stays below 32768, the range systems hand out
// for outgoing connections. When another program holds it, the service takes any
// free port and says which in its ready file.
const RELEASE_BACKEND_PORT: u16 = 28787;
const DEV_BACKEND_BASE_URL: &str = "http://127.0.0.1:8787";
const TOKEN_FILE_NAME: &str = "backend-auth-token";
/// How long to wait for the service, in tenths of a second.
const BACKEND_WAIT_TENTHS: u32 = 900;
const READY_TOKEN_DIR_NAME: &str = "backend-ready";

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct BackendConnection {
    base_url: String,
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

/// The launch's token and the port the service listens on (older services name none).
fn parse_ready_token(contents: &str, expected_nonce: &str) -> Option<(String, u16)> {
    let mut lines = contents.lines();
    if lines.next()? != expected_nonce {
        return None;
    }
    let token = lines.next()?;
    let port = match lines.next() {
        Some(port) => port.parse::<u16>().ok().filter(|port| *port != 0)?,
        None => RELEASE_BACKEND_PORT,
    };
    if lines.next().is_some()
        || token.len() != 43
        || !token
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return None;
    }
    Some((token.to_owned(), port))
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
    // A service a previous Gunther left running would hold the data folder.
    leftover::reclaim(&data_dir);
    let executable = backend_executable(app)?;
    log::info!(
        "Starting the knowledge service: {} (preferring port {RELEASE_BACKEND_PORT})",
        executable.display()
    );
    // Its output, a crash's traceback included, goes to today's backend log.
    let stdout = app_log::open_backend_file().or_else(|error| {
        log::warn!("The backend log file could not be opened ({error}); using backend.log");
        secure_open(&data_dir.join("backend.log"), true)
    })?;
    let stderr = stdout.try_clone()?;
    let mut command = Command::new(&executable);
    if let Some(folder) = app_log::folder() {
        command.arg("--log-dir").arg(folder);
    }
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
        .arg(RELEASE_BACKEND_PORT.to_string())
        // Another program on that port is left alone: the service takes a free one.
        .arg("--any-port-if-taken")
        .arg("--launch-nonce")
        .arg(&launch_nonce)
        // The helper exits when this pipe closes, so a crashed or force-quit
        // Gunther never leaves it running and holding the port.
        .arg("--exit-when-stdin-closes")
        .stdin(Stdio::piped())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr))
        .spawn()?;
    log::info!("The knowledge service is process {}", child.id());

    let state = app.state::<BackendProcess>();
    let mut runtime = state.0.lock().expect("backend lock poisoned");
    runtime.child = Some(child);
    runtime.launch_nonce = Some(launch_nonce.clone());
    runtime.exited = None;
    drop(runtime);
    watch_backend(app.clone(), launch_nonce);
    Ok(())
}

/// Notice when the service of this launch stops on its own, and say why in the log.
fn watch_backend(app: tauri::AppHandle, launch_nonce: String) {
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(500));
        let state = app.state::<BackendProcess>();
        let Ok(mut runtime) = state.0.lock() else {
            return;
        };
        // Stopped by Gunther, or started again: this launch is no longer watched.
        if runtime.launch_nonce.as_deref() != Some(launch_nonce.as_str()) {
            return;
        }
        let Some(child) = runtime.child.as_mut() else {
            return;
        };
        match child.try_wait() {
            Ok(None) => {}
            Ok(Some(status)) => {
                let reason = app_log::backend_stop_reason();
                let message = match reason {
                    Some(reason) => {
                        format!("Gunther's knowledge service stopped ({status}): {reason}")
                    }
                    None => format!("Gunther's knowledge service stopped ({status})"),
                };
                log::error!("{message}");
                runtime.child = None;
                runtime.exited = Some(message);
                return;
            }
            Err(error) => {
                log::warn!("Gunther could not check on its knowledge service: {error}");
                return;
            }
        }
    });
}

/// Stop the bundled backend and forget its launch, e.g. before moving the libraries.
fn stop_backend(app: &tauri::AppHandle) {
    let state = app.state::<BackendProcess>();
    let (child, launch_nonce) = {
        let mut runtime = state.0.lock().expect("backend lock poisoned");
        (runtime.child.take(), runtime.launch_nonce.take())
    };
    if let Some(mut child) = child {
        log::info!("Stopping the knowledge service (process {})", child.id());
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

/// Opens the print sheet for what the window shows (an output laid out for paper); the
/// sheet's PDF button saves the file.
///
/// `window.print()` does nothing in macOS's WKWebView, so the web layer asks the shell,
/// which uses Tauri's own call (macOS 11 and later). Other systems print from the web layer.
#[tauri::command]
fn print_output(window: tauri::WebviewWindow) -> Result<(), String> {
    window.print().map_err(|error| error.to_string())
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
    log::info!(
        "Moving the libraries from {} to {}",
        from.display(),
        to.display()
    );
    stop_backend(&app);
    let outcome = library_location::move_library(&from, &to).and_then(|()| {
        library_location::save_choice(&data_dir, &to).map_err(|error| {
            // Not remembered: put the folder back so the backend finds it where it looks.
            let _ = library_location::move_library(&to, &from);
            error
        })
    });
    match &outcome {
        // The logs moved with the library; keep writing where they are now.
        Ok(()) => {
            app_log::set_folder(app_log::folder_in_library(&to));
            log::info!("Moved the libraries to {}", to.display());
        }
        Err(error) => log::error!("The libraries were not moved: {error}"),
    }
    if let Err(error) = start_backend(&app) {
        log::error!("The knowledge service did not start again: {error}");
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
            base_url: DEV_BACKEND_BASE_URL.into(),
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
    let started = Instant::now();
    // The first launch after installing can take a minute while macOS checks every
    // file of the freshly installed helper; a helper that stopped is noticed at once.
    for attempt in 1..=BACKEND_WAIT_TENTHS {
        if let Ok(contents) = secure_read(&token_path) {
            if let Some((token, port)) = parse_ready_token(&contents, &launch_nonce) {
                log::info!(
                    "Connected to the knowledge service on port {port} after {} ms",
                    started.elapsed().as_millis()
                );
                return Ok(BackendConnection {
                    base_url: format!("http://127.0.0.1:{port}"),
                    auth_token: token,
                });
            }
        }
        if let Some(exited) = backend_exit(&app) {
            return Err(exited);
        }
        if attempt % 100 == 0 {
            log::info!(
                "Still waiting for the knowledge service ({} s)",
                attempt / 10
            );
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    log::error!(
        "The knowledge service did not start within {} s",
        BACKEND_WAIT_TENTHS / 10
    );
    Err(format!(
        "Gunther's knowledge service did not start within {} seconds.",
        BACKEND_WAIT_TENTHS / 10
    ))
}

/// Why this launch's service stopped on its own, if it has.
fn backend_exit(app: &tauri::AppHandle) -> Option<String> {
    app.state::<BackendProcess>()
        .0
        .lock()
        .ok()
        .and_then(|runtime| runtime.exited.clone())
}

/// Start the service again after it stopped or did not start, from the startup screen.
#[tauri::command(async)]
fn restart_backend(app: tauri::AppHandle) -> Result<(), String> {
    if cfg!(debug_assertions) {
        return Ok(());
    }
    log::info!("Starting the knowledge service again, as asked");
    stop_backend(&app);
    start_backend(&app).map_err(|error| {
        log::error!("The knowledge service could not start: {error}");
        format!("Gunther's knowledge service could not start: {error}")
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        // Opening Gunther while it runs shows the running one, which already has
        // the service; a second app would find the data folder taken.
        .plugin(tauri_plugin_single_instance::init(
            |app, _arguments, _folder| {
                log::info!("Gunther was opened again; showing the running one");
                let _ = capture_shell::show_main_window(app.clone());
            },
        ))
        .plugin(app_log::plugin())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .manage(BackendProcess(Mutex::new(BackendRuntime::default())))
        .manage(capture_shell::CaptureShell::default())
        .menu(application_menu::build)
        .on_menu_event(|app, event| {
            log::info!("Menu: {}", event.id().as_ref());
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
            let data_dir = app.path().app_data_dir().ok();
            // The library folder the backend will use: the one chosen in Settings, or ~/Gunther.
            let library_root = data_dir
                .as_deref()
                .and_then(library_location::chosen_root)
                .or_else(|| app.path().home_dir().ok().map(|home| home.join("Gunther")));
            let logs = app_log::install(app.handle(), library_root.as_deref());
            log::info!(
                "Gunther {} starting on {} {} (process {})",
                app.package_info().version,
                std::env::consts::OS,
                std::env::consts::ARCH,
                std::process::id()
            );
            log::info!(
                "Data {}; library {}; logs {}",
                data_dir
                    .as_deref()
                    .map(Path::display)
                    .map(|path| path.to_string())
                    .unwrap_or_default(),
                library_root
                    .as_deref()
                    .map(Path::display)
                    .map(|path| path.to_string())
                    .unwrap_or_default(),
                logs.display()
            );
            if let Err(error) = start_backend(app.handle()) {
                log::error!("Gunther's local knowledge service could not start: {error}");
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
            restart_backend,
            app_log::logs_folder,
            app_log::show_logs_folder,
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
            print_output,
        ])
        .build(tauri::generate_context!())
        .expect("error while building Gunther desktop");

    app.run(|app_handle, event| match event {
        tauri::RunEvent::ExitRequested { api, .. } if capture_shell::blocks_exit(app_handle) => {
            log::info!("Quitting waits: a recording is in progress");
            api.prevent_exit();
            capture_shell::block_quit_and_show_capture(app_handle);
        }
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Reopen { .. } => {
            let _ = capture_shell::show_main_window(app_handle.clone());
        }
        tauri::RunEvent::Exit => {
            log::info!("Gunther is quitting");
            stop_backend(app_handle);
        }
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
        // A service that names no port listens on the usual one.
        assert_eq!(
            parse_ready_token(&contents, &nonce),
            Some((token.clone(), 28787))
        );
        assert_eq!(
            parse_ready_token(&format!("{contents}41234\n"), &nonce),
            Some((token.clone(), 41234))
        );
        assert_eq!(parse_ready_token(&format!("{contents}0\n"), &nonce), None);
        assert_eq!(
            parse_ready_token(&format!("{contents}99999\n"), &nonce),
            None
        );
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
