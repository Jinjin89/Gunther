use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, Runtime,
};

use crate::tray_glyph::{self, GlyphVariant};

pub const CAPTURE_WINDOW_LABEL: &str = "capture";
pub const CAPTURE_REQUEST_EVENT: &str = "gunther://capture-request";
pub const CAPTURE_CONTROL_EVENT: &str = "gunther://capture-control";
pub const CAPTURE_SAVED_EVENT: &str = "gunther://capture-saved";
/// Commands from the native menus that the main window carries out.
pub const MENU_COMMAND_EVENT: &str = "gunther://menu-command";
const OPEN_SEARCH_EVENT: &str = "gunther://open-search";

const TRAY_ID: &str = "gunther-status";
const MENU_STATUS: &str = "capture-status";
pub const MENU_OPEN_CAPTURE: &str = "open-capture";
pub const MENU_NEW_RECORDING: &str = "new-recording";
const MENU_QUICK_NOTE: &str = "quick-note";
const MENU_PAUSE_RESUME: &str = "pause-resume-recording";
const MENU_MARK: &str = "mark-recording";
const MENU_FINISH: &str = "finish-recording";
const MENU_SHOW_CAPTURE: &str = "show-capture";
const MENU_OPEN_MAIN: &str = "open-gunther";
pub const MENU_SEARCH: &str = "search-gunther";
pub const MENU_NEW_NOTE: &str = "new-note";
pub const MENU_SETTINGS: &str = "open-settings";
pub const MENU_SHORTCUTS: &str = "keyboard-shortcuts";
pub const MENU_TOGGLE_THEME: &str = "toggle-theme";
const MENU_QUIT: &str = crate::application_menu::QUIT_MENU_ID;

const MENU_BAR_PREFERENCE_FILE: &str = "menu-bar.json";

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum CaptureKind {
    Note,
    Link,
    File,
    Image,
    Table,
    Recording,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RecordingContext {
    Lecture,
    Meeting,
    Memo,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CaptureLaunchRequest {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<CaptureKind>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recording_context: Option<RecordingContext>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_base_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CaptureStatus {
    pub phase: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub elapsed_seconds: Option<u64>,
    // RecorderSnapshot currently calls this field `seconds`. Accept both that
    // exact shape and the shell-oriented `elapsedSeconds` without rejecting
    // the rest of the snapshot as the frontend grows.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seconds: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub persistence: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transcript_words: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transcription_label: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub marked_moments: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub has_unreviewed_work: Option<bool>,
}

impl Default for CaptureStatus {
    fn default() -> Self {
        Self {
            phase: "idle".into(),
            elapsed_seconds: None,
            seconds: None,
            title: None,
            detail: None,
            persistence: Some("idle".into()),
            transcript_words: None,
            transcription_label: None,
            marked_moments: None,
            has_unreviewed_work: None,
        }
    }
}

impl CaptureStatus {
    fn normalized_phase(&self) -> String {
        self.phase.trim().to_ascii_lowercase()
    }

    fn normalized_persistence(&self) -> String {
        self.persistence
            .as_deref()
            .unwrap_or("idle")
            .trim()
            .to_ascii_lowercase()
    }

    fn elapsed(&self) -> u64 {
        self.elapsed_seconds.or(self.seconds).unwrap_or_default()
    }

    pub fn blocks_exit(&self) -> bool {
        matches!(
            self.normalized_phase().as_str(),
            "requesting"
                | "importing"
                | "recording"
                | "paused"
                | "finalizing"
                | "stopped"
                | "review"
                | "review-ready"
        ) || matches!(
            self.normalized_persistence().as_str(),
            "saving" | "recovering"
        ) || self.has_unreviewed_work.unwrap_or(false)
    }
}

/// Whether the menu bar item stays visible when nothing is being captured.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum MenuBarMode {
    #[default]
    Always,
    WhileCapturing,
}

#[derive(Default)]
struct CaptureShellRuntime {
    pending_launch: Option<CaptureLaunchRequest>,
    status: CaptureStatus,
    menu_bar_mode: MenuBarMode,
}

#[derive(Default)]
pub struct CaptureShell(Mutex<CaptureShellRuntime>);

/// What the menu shows. It changes with the capture, so the menu never offers
/// an action that cannot happen (no Pause while nothing is recording).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum MenuLayout {
    Ready,
    Live { paused: bool },
    Preparing,
    Review,
    Draft,
    Attention,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct TrayPresentation {
    glyph: GlyphVariant,
    /// Text beside the icon. Empty clears it: macOS keeps a previous title
    /// when it is set to nothing, so an ended recording would leave its timer.
    title: String,
    tooltip: String,
    status_text: String,
    layout: MenuLayout,
    quit_text: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
enum MenuEntry {
    Status,
    Separator,
    Action {
        id: &'static str,
        label: &'static str,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum MenuAction {
    OpenCapture,
    NewRecording,
    QuickNote,
    ShowCapture,
    Pause,
    Resume,
    Mark,
    Finish,
    OpenMain,
    Search,
    MainCommand(&'static str),
    Quit,
    Ignore,
}

struct TrayState<R: Runtime> {
    glyph: Option<GlyphVariant>,
    layout: Option<MenuLayout>,
    quit_text: String,
    title: Option<String>,
    visible: Option<bool>,
    status_item: Option<MenuItem<R>>,
}

/// "12:48", or "1:02:03" once past an hour: short enough for the menu bar.
fn compact_elapsed(seconds: u64) -> String {
    let hours = seconds / 3_600;
    let minutes = (seconds % 3_600) / 60;
    let seconds = seconds % 60;
    if hours > 0 {
        format!("{hours}:{minutes:02}:{seconds:02}")
    } else {
        format!("{minutes:02}:{seconds:02}")
    }
}

fn capture_title(status: &CaptureStatus) -> Option<String> {
    let title = status.title.as_deref()?.trim();
    if title.is_empty() || matches!(title, "Capture something" | "Untitled capture") {
        return None;
    }
    let mut short: String = title.chars().take(40).collect();
    if title.chars().count() > 40 {
        short.push('…');
    }
    Some(short)
}

fn presentation_for(status: &CaptureStatus) -> TrayPresentation {
    let phase = status.normalized_phase();
    let persistence = status.normalized_persistence();
    let elapsed = compact_elapsed(status.elapsed());
    let named = |label: String| match capture_title(status) {
        Some(title) => format!("{label} — {title}"),
        None => label,
    };
    let recording = phase == "recording";
    let paused = phase == "paused";
    let busy = matches!(phase.as_str(), "requesting" | "importing" | "finalizing")
        || matches!(persistence.as_str(), "saving" | "recovering");
    let review = matches!(phase.as_str(), "stopped" | "review" | "review-ready");
    let draft = status.has_unreviewed_work.unwrap_or(false) && !review;
    let error = phase == "error";

    let (glyph, title, status_text, tooltip, layout) = if recording {
        (
            GlyphVariant::Recording,
            format!(" {elapsed}"),
            named(format!("Recording · {elapsed}")),
            format!("Gunther — Recording {elapsed}"),
            MenuLayout::Live { paused: false },
        )
    } else if paused {
        (
            GlyphVariant::Paused,
            format!(" {elapsed}"),
            named(format!("Paused · {elapsed}")),
            format!("Gunther — Recording paused at {elapsed}"),
            MenuLayout::Live { paused: true },
        )
    } else if error {
        let detail = status
            .detail
            .as_deref()
            .filter(|detail| !detail.trim().is_empty())
            .unwrap_or("Capture needs attention");
        (
            GlyphVariant::Attention,
            String::new(),
            "Capture needs attention".into(),
            format!("Gunther — {detail}"),
            MenuLayout::Attention,
        )
    } else if busy {
        let label = match phase.as_str() {
            "requesting" => "Opening the microphone…",
            "importing" => "Importing audio…",
            "finalizing" => "Finishing the recording…",
            _ if persistence == "recovering" => "Recovering a capture…",
            _ => "Saving…",
        };
        (
            GlyphVariant::Preparing,
            String::new(),
            label.into(),
            format!("Gunther — {label}"),
            MenuLayout::Preparing,
        )
    } else if review {
        (
            GlyphVariant::Review,
            String::new(),
            named("Recording ready to review".into()),
            "Gunther — A recording is ready to review".into(),
            MenuLayout::Review,
        )
    } else if draft {
        (
            GlyphVariant::Draft,
            String::new(),
            named("Unsaved capture".into()),
            "Gunther — An unsaved capture is waiting".into(),
            MenuLayout::Draft,
        )
    } else {
        (
            GlyphVariant::Idle,
            String::new(),
            String::new(),
            "Gunther".into(),
            MenuLayout::Ready,
        )
    };

    TrayPresentation {
        glyph,
        title,
        tooltip,
        status_text,
        layout,
        quit_text: if status.blocks_exit() {
            "Quit Gunther…".into()
        } else {
            "Quit Gunther".into()
        },
    }
}

fn menu_entries(layout: MenuLayout) -> Vec<MenuEntry> {
    use MenuEntry::{Action, Separator, Status};
    let open_main = Action {
        id: MENU_OPEN_MAIN,
        label: "Open Gunther",
    };
    let mut entries = match layout {
        MenuLayout::Ready => vec![
            Action {
                id: MENU_OPEN_CAPTURE,
                label: "Capture…",
            },
            Action {
                id: MENU_NEW_RECORDING,
                label: "New Recording",
            },
            Separator,
            Action {
                id: MENU_SEARCH,
                label: "Search Gunther…",
            },
            open_main,
        ],
        MenuLayout::Live { paused } => vec![
            Status,
            Separator,
            Action {
                id: MENU_PAUSE_RESUME,
                label: if paused {
                    "Resume Recording"
                } else {
                    "Pause Recording"
                },
            },
            Action {
                id: MENU_MARK,
                label: "Mark Moment",
            },
            Action {
                id: MENU_FINISH,
                label: "Finish Recording…",
            },
            Separator,
            Action {
                id: MENU_SHOW_CAPTURE,
                label: "Show Recorder",
            },
            // The recording keeps going while a note, file or link is captured.
            Action {
                id: MENU_OPEN_CAPTURE,
                label: "Capture Something Else…",
            },
            open_main,
        ],
        MenuLayout::Preparing => vec![
            Status,
            Separator,
            Action {
                id: MENU_SHOW_CAPTURE,
                label: "Show Capture",
            },
            open_main,
        ],
        MenuLayout::Review => vec![
            Status,
            Separator,
            Action {
                id: MENU_SHOW_CAPTURE,
                label: "Review Recording…",
            },
            Action {
                id: MENU_OPEN_CAPTURE,
                label: "Capture Something Else…",
            },
            open_main,
        ],
        MenuLayout::Draft => vec![
            Status,
            Separator,
            Action {
                id: MENU_SHOW_CAPTURE,
                label: "Continue Capture…",
            },
            Action {
                id: MENU_NEW_RECORDING,
                label: "New Recording",
            },
            open_main,
        ],
        MenuLayout::Attention => vec![
            Status,
            Separator,
            Action {
                id: MENU_SHOW_CAPTURE,
                label: "Show Capture…",
            },
            open_main,
        ],
    };
    entries.push(Separator);
    entries.push(Action {
        id: MENU_QUIT,
        label: "Quit Gunther",
    });
    entries
}

fn menu_action(id: &str, status: &CaptureStatus) -> MenuAction {
    let phase = status.normalized_phase();
    match id {
        MENU_OPEN_CAPTURE => MenuAction::OpenCapture,
        MENU_NEW_RECORDING => MenuAction::NewRecording,
        MENU_QUICK_NOTE => MenuAction::QuickNote,
        MENU_SHOW_CAPTURE => MenuAction::ShowCapture,
        MENU_PAUSE_RESUME if phase == "paused" => MenuAction::Resume,
        MENU_PAUSE_RESUME if phase == "recording" => MenuAction::Pause,
        MENU_MARK if matches!(phase.as_str(), "recording" | "paused") => MenuAction::Mark,
        MENU_FINISH if matches!(phase.as_str(), "recording" | "paused") => MenuAction::Finish,
        MENU_OPEN_MAIN => MenuAction::OpenMain,
        MENU_SEARCH => MenuAction::Search,
        MENU_NEW_NOTE => MenuAction::MainCommand("new-note"),
        MENU_SETTINGS => MenuAction::MainCommand("settings"),
        MENU_SHORTCUTS => MenuAction::MainCommand("shortcuts"),
        MENU_TOGGLE_THEME => MenuAction::MainCommand("theme"),
        MENU_QUIT => MenuAction::Quit,
        _ => MenuAction::Ignore,
    }
}

fn can_replace_capture(status: &CaptureStatus) -> bool {
    !status.blocks_exit()
}

fn tray_visible(mode: MenuBarMode, layout: MenuLayout) -> bool {
    mode == MenuBarMode::Always || layout != MenuLayout::Ready
}

fn glyph_image(variant: GlyphVariant) -> (Image<'static>, bool) {
    let glyph = tray_glyph::render(variant);
    (
        Image::new_owned(glyph.rgba, tray_glyph::GLYPH_SIZE, tray_glyph::GLYPH_SIZE),
        glyph.template,
    )
}

fn runtime_snapshot<R: Runtime>(app: &AppHandle<R>) -> (CaptureStatus, MenuBarMode) {
    let shell = app.state::<CaptureShell>();
    let runtime = shell.0.lock().expect("capture shell lock poisoned");
    (runtime.status.clone(), runtime.menu_bar_mode)
}

fn status_snapshot<R: Runtime>(app: &AppHandle<R>) -> CaptureStatus {
    runtime_snapshot(app).0
}

fn build_menu<R: Runtime>(
    app: &AppHandle<R>,
    presentation: &TrayPresentation,
) -> tauri::Result<(Menu<R>, Option<MenuItem<R>>)> {
    let menu = Menu::new(app)?;
    let mut status_item = None;
    for entry in menu_entries(presentation.layout) {
        match entry {
            MenuEntry::Status => {
                let item = MenuItem::with_id(
                    app,
                    MENU_STATUS,
                    &presentation.status_text,
                    false,
                    None::<&str>,
                )?;
                menu.append(&item)?;
                status_item = Some(item);
            }
            MenuEntry::Separator => menu.append(&PredefinedMenuItem::separator(app)?)?,
            MenuEntry::Action { id, label } => {
                let label = if id == MENU_QUIT {
                    presentation.quit_text.as_str()
                } else {
                    label
                };
                menu.append(&MenuItem::with_id(app, id, label, true, None::<&str>)?)?;
            }
        }
    }
    Ok((menu, status_item))
}

fn apply_tray_presentation<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let (status, mode) = runtime_snapshot(app);
    let presentation = presentation_for(&status);
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return Ok(());
    };
    let state = app.state::<Mutex<TrayState<R>>>();
    let mut state = state.lock().expect("tray state lock poisoned");

    if state.glyph != Some(presentation.glyph) {
        let (icon, template) = glyph_image(presentation.glyph);
        tray.set_icon_with_as_template(Some(icon), template)?;
        state.glyph = Some(presentation.glyph);
    }
    if state.title.as_deref() != Some(presentation.title.as_str()) {
        tray.set_title(Some(presentation.title.as_str()))?;
        state.title = Some(presentation.title.clone());
    }
    tray.set_tooltip(Some(&presentation.tooltip))?;

    if state.layout != Some(presentation.layout) || state.quit_text != presentation.quit_text {
        // Rebuild only when the set of actions changes; the timer updates in place.
        let (menu, status_item) = build_menu(app, &presentation)?;
        tray.set_menu(Some(menu))?;
        state.layout = Some(presentation.layout);
        state.quit_text = presentation.quit_text.clone();
        state.status_item = status_item;
    } else if let Some(item) = &state.status_item {
        item.set_text(&presentation.status_text)?;
    }

    let visible = tray_visible(mode, presentation.layout);
    if state.visible != Some(visible) {
        tray.set_visible(visible)?;
        state.visible = Some(visible);
    }
    Ok(())
}

fn show_window<R: Runtime>(app: &AppHandle<R>, label: &str) -> Result<(), String> {
    let window = app
        .get_webview_window(label)
        .ok_or_else(|| format!("Gunther's {label} window is unavailable"))?;
    window
        .unminimize()
        .and_then(|_| window.show())
        .and_then(|_| window.set_focus())
        .map_err(|error| format!("Gunther could not show its {label} window: {error}"))
}

fn emit_capture_control<R: Runtime>(app: &AppHandle<R>, control: &'static str) {
    let _ = app.emit_to(CAPTURE_WINDOW_LABEL, CAPTURE_CONTROL_EVENT, control);
}

fn open_from_tray<R: Runtime>(app: &AppHandle<R>, request: CaptureLaunchRequest) {
    {
        let shell = app.state::<CaptureShell>();
        let mut runtime = shell.0.lock().expect("capture shell lock poisoned");
        // A busy Capture (a recording, import, or unsaved text) is never
        // replaced, so no fallback request is left to replace it after a
        // renderer reload.
        if can_replace_capture(&runtime.status) {
            runtime.pending_launch = Some(request.clone());
        }
    }
    let _ = show_window(app, CAPTURE_WINDOW_LABEL);
    // The live request always reaches the window: a busy sheet switches to the
    // asked type beside its recording and keeps unsaved text where it is.
    let _ = app.emit_to(CAPTURE_WINDOW_LABEL, CAPTURE_REQUEST_EVENT, request);
}

fn request_quit<R: Runtime>(app: &AppHandle<R>) {
    if status_snapshot(app).blocks_exit() {
        block_quit_and_show_capture(app);
    } else {
        app.exit(0);
    }
}

/// Bring the main window forward and let it carry out a menu command.
fn run_in_main_window<R: Runtime>(app: &AppHandle<R>, command: &'static str) {
    let _ = show_window(app, "main");
    // Only the main window listens; a broadcast reaches its global listener.
    let _ = app.emit(MENU_COMMAND_EVENT, command);
}

pub fn handle_menu_event<R: Runtime>(app: &AppHandle<R>, id: &str) {
    match menu_action(id, &status_snapshot(app)) {
        MenuAction::OpenCapture => open_from_tray(
            app,
            CaptureLaunchRequest {
                source: Some("menu-open-capture".into()),
                ..Default::default()
            },
        ),
        MenuAction::NewRecording => open_from_tray(
            app,
            CaptureLaunchRequest {
                kind: Some(CaptureKind::Recording),
                recording_context: Some(RecordingContext::Lecture),
                source: Some("menu-new-recording".into()),
                ..Default::default()
            },
        ),
        MenuAction::QuickNote => open_from_tray(
            app,
            CaptureLaunchRequest {
                kind: Some(CaptureKind::Note),
                source: Some("menu-quick-note".into()),
                ..Default::default()
            },
        ),
        MenuAction::ShowCapture => {
            let _ = show_window(app, CAPTURE_WINDOW_LABEL);
            emit_capture_control(app, "show");
        }
        MenuAction::Pause => emit_capture_control(app, "pause"),
        MenuAction::Resume => emit_capture_control(app, "resume"),
        MenuAction::Mark => emit_capture_control(app, "mark"),
        MenuAction::Finish => emit_capture_control(app, "finish"),
        MenuAction::OpenMain => {
            let _ = show_window(app, "main");
        }
        MenuAction::Search => {
            let _ = show_window(app, "main");
            let _ = app.emit(OPEN_SEARCH_EVENT, ());
        }
        MenuAction::MainCommand(command) => run_in_main_window(app, command),
        MenuAction::Quit => request_quit(app),
        MenuAction::Ignore => {}
    }
}

fn menu_bar_preference_path<R: Runtime>(app: &AppHandle<R>) -> Option<std::path::PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|directory| directory.join(MENU_BAR_PREFERENCE_FILE))
}

fn load_menu_bar_mode<R: Runtime>(app: &AppHandle<R>) -> MenuBarMode {
    menu_bar_preference_path(app)
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|contents| serde_json::from_str::<MenuBarPreference>(&contents).ok())
        .map(|preference| preference.mode)
        .unwrap_or_default()
}

#[derive(Deserialize, Serialize)]
struct MenuBarPreference {
    mode: MenuBarMode,
}

pub fn setup_tray<R: Runtime>(app: &tauri::App<R>) -> tauri::Result<()> {
    let handle = app.handle();
    // The saved preference is read before the icon appears, so a hidden
    // menu bar item never flashes at launch.
    let mode = load_menu_bar_mode(handle);
    app.state::<CaptureShell>()
        .0
        .lock()
        .expect("capture shell lock poisoned")
        .menu_bar_mode = mode;

    let presentation = presentation_for(&CaptureStatus::default());
    let (menu, status_item) = build_menu(handle, &presentation)?;
    let (icon, template) = glyph_image(presentation.glyph);
    let tray = TrayIconBuilder::with_id(TRAY_ID)
        .icon(icon)
        .icon_as_template(template)
        .tooltip(&presentation.tooltip)
        .menu(&menu)
        // A normal click opens the action menu, matching macOS status-item
        // conventions and keeping Pause/Mark/Finish discoverable.
        .show_menu_on_left_click(true)
        // App and tray menus share the Builder's single menu-event handler.
        // Registering a second tray handler would dispatch actions twice.
        .build(app)?;
    let visible = tray_visible(mode, presentation.layout);
    if !visible {
        tray.set_visible(false)?;
    }

    app.manage(Mutex::new(TrayState::<R> {
        glyph: Some(presentation.glyph),
        layout: Some(presentation.layout),
        quit_text: presentation.quit_text,
        title: None,
        visible: Some(visible),
        status_item,
    }));
    apply_tray_presentation(handle)
}

pub fn blocks_exit<R: Runtime>(app: &AppHandle<R>) -> bool {
    status_snapshot(app).blocks_exit()
}

pub fn block_quit_and_show_capture<R: Runtime>(app: &AppHandle<R>) {
    let _ = show_window(app, CAPTURE_WINDOW_LABEL);
    emit_capture_control(app, "quit-blocked");
}

#[derive(Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CaptureSavedPayload {
    #[serde(default)]
    capture_continues: bool,
}

/// Whether a saved capture left something open (a recording beside a note).
pub fn saved_capture_continues(payload: &str) -> bool {
    serde_json::from_str::<CaptureSavedPayload>(payload)
        .unwrap_or_default()
        .capture_continues
}

pub fn reset_after_capture_saved<R: Runtime>(app: &AppHandle<R>) {
    {
        let shell = app.state::<CaptureShell>();
        let mut runtime = shell.0.lock().expect("capture shell lock poisoned");
        runtime.pending_launch = None;
        runtime.status = CaptureStatus::default();
    }
    let _ = apply_tray_presentation(app);
}

#[tauri::command]
pub fn open_capture_window(app: AppHandle, request: CaptureLaunchRequest) -> Result<(), String> {
    if request
        .target_base_id
        .as_ref()
        .is_some_and(|id| id.len() > 256)
        || request
            .source
            .as_ref()
            .is_some_and(|source| source.len() > 128)
    {
        return Err("The capture launch request is too large".into());
    }
    open_from_tray(&app, request);
    Ok(())
}

#[tauri::command]
pub fn hide_capture_window(app: AppHandle) -> Result<(), String> {
    app.get_webview_window(CAPTURE_WINDOW_LABEL)
        .ok_or_else(|| "Gunther's capture window is unavailable".to_string())?
        .hide()
        .map_err(|error| format!("Gunther could not hide Capture: {error}"))
}

#[tauri::command]
pub fn show_main_window(app: AppHandle) -> Result<(), String> {
    show_window(&app, "main")
}

#[tauri::command]
pub fn take_capture_launch_request(app: AppHandle) -> Option<CaptureLaunchRequest> {
    app.state::<CaptureShell>()
        .0
        .lock()
        .expect("capture shell lock poisoned")
        .pending_launch
        .take()
}

#[tauri::command]
pub fn update_capture_status(app: AppHandle, status: CaptureStatus) -> Result<(), String> {
    if status.phase.len() > 64
        || status.title.as_ref().is_some_and(|title| title.len() > 512)
        || status
            .detail
            .as_ref()
            .is_some_and(|detail| detail.len() > 2_048)
    {
        return Err("The capture status payload is too large".into());
    }
    let blocks_exit = status.blocks_exit();
    let shell = app.state::<CaptureShell>();
    let mut runtime = shell
        .0
        .lock()
        .map_err(|_| "Gunther's capture state is unavailable".to_string())?;
    if blocks_exit {
        // Once the renderer owns an active/importing/review session, the
        // fallback launch slot must not survive a reload and replace it.
        runtime.pending_launch = None;
    }
    runtime.status = status;
    drop(runtime);
    apply_tray_presentation(&app)
        .map_err(|error| format!("Gunther could not update the menu bar: {error}"))
}

#[tauri::command]
pub fn menu_bar_mode(app: AppHandle) -> MenuBarMode {
    runtime_snapshot(&app).1
}

#[tauri::command]
pub fn set_menu_bar_mode(app: AppHandle, mode: MenuBarMode) -> Result<(), String> {
    app.state::<CaptureShell>()
        .0
        .lock()
        .map_err(|_| "Gunther's capture state is unavailable".to_string())?
        .menu_bar_mode = mode;
    if let Some(path) = menu_bar_preference_path(&app) {
        if let Some(directory) = path.parent() {
            let _ = std::fs::create_dir_all(directory);
        }
        let contents = serde_json::to_string(&MenuBarPreference { mode })
            .map_err(|error| format!("The menu bar preference could not be saved: {error}"))?;
        std::fs::write(&path, contents)
            .map_err(|error| format!("The menu bar preference could not be saved: {error}"))?;
    }
    apply_tray_presentation(&app)
        .map_err(|error| format!("Gunther could not update the menu bar: {error}"))
}

#[cfg(test)]
mod tests {
    use super::{
        can_replace_capture, compact_elapsed, menu_action, menu_entries, presentation_for,
        saved_capture_continues, tray_visible, CaptureStatus, GlyphVariant, MenuAction,
        MenuBarMode, MenuEntry, MenuLayout, MENU_FINISH, MENU_MARK, MENU_NEW_NOTE,
        MENU_NEW_RECORDING, MENU_OPEN_CAPTURE, MENU_PAUSE_RESUME, MENU_QUIT, MENU_SETTINGS,
        MENU_SHOW_CAPTURE,
    };

    fn status(phase: &str, seconds: u64) -> CaptureStatus {
        CaptureStatus {
            phase: phase.into(),
            seconds: Some(seconds),
            ..CaptureStatus::default()
        }
    }

    fn action_ids(layout: MenuLayout) -> Vec<&'static str> {
        menu_entries(layout)
            .into_iter()
            .filter_map(|entry| match entry {
                MenuEntry::Action { id, .. } => Some(id),
                _ => None,
            })
            .collect()
    }

    #[test]
    fn elapsed_time_stays_unambiguous_for_long_sessions() {
        assert_eq!(compact_elapsed(0), "00:00");
        assert_eq!(compact_elapsed(768), "12:48");
        assert_eq!(compact_elapsed(14_401), "4:00:01");
        assert_eq!(compact_elapsed(3_723), "1:02:03");
    }

    #[test]
    fn active_and_unsaved_review_states_block_exit() {
        for phase in [
            "requesting",
            "importing",
            "recording",
            "paused",
            "finalizing",
            "stopped",
            "review-ready",
        ] {
            assert!(status(phase, 10).blocks_exit(), "{phase} must block exit");
        }
        assert!(CaptureStatus {
            phase: "stopped".into(),
            persistence: Some("saving".into()),
            ..CaptureStatus::default()
        }
        .blocks_exit());
        assert!(CaptureStatus {
            phase: "idle".into(),
            has_unreviewed_work: Some(true),
            ..CaptureStatus::default()
        }
        .blocks_exit());
        assert!(!status("idle", 0).blocks_exit());
    }

    #[test]
    fn idle_is_the_calm_mark_with_no_timer_and_no_recording_controls() {
        let idle = presentation_for(&status("idle", 0));
        assert_eq!(idle.glyph, GlyphVariant::Idle);
        assert_eq!(idle.title, "", "an empty title clears a previous timer");
        assert_eq!(idle.layout, MenuLayout::Ready);
        assert_eq!(idle.quit_text, "Quit Gunther");
        let ids = action_ids(MenuLayout::Ready);
        assert!(!ids.contains(&MENU_PAUSE_RESUME));
        assert!(!ids.contains(&MENU_MARK));
        assert!(!ids.contains(&MENU_FINISH));
        assert_eq!(ids.last(), Some(&MENU_QUIT));
    }

    #[test]
    fn recording_and_paused_show_the_timer_and_their_controls() {
        let recording = presentation_for(&CaptureStatus {
            title: Some("Lecture 7".into()),
            ..status("recording", 3_723)
        });
        assert_eq!(recording.glyph, GlyphVariant::Recording);
        assert_eq!(recording.title, " 1:02:03");
        assert_eq!(recording.status_text, "Recording · 1:02:03 — Lecture 7");
        assert_eq!(recording.layout, MenuLayout::Live { paused: false });
        assert_eq!(recording.quit_text, "Quit Gunther…");
        let ids = action_ids(recording.layout);
        for id in [MENU_PAUSE_RESUME, MENU_MARK, MENU_FINISH, MENU_SHOW_CAPTURE] {
            assert!(ids.contains(&id), "{id} while recording");
        }

        let paused = presentation_for(&status("paused", 768));
        assert_eq!(paused.glyph, GlyphVariant::Paused);
        assert_eq!(paused.title, " 12:48");
        assert!(menu_entries(paused.layout).contains(&MenuEntry::Action {
            id: MENU_PAUSE_RESUME,
            label: "Resume Recording",
        }));
        assert_eq!(
            menu_action(MENU_PAUSE_RESUME, &status("paused", 1)),
            MenuAction::Resume
        );
    }

    #[test]
    fn ending_a_recording_clears_the_timer() {
        let recording = presentation_for(&status("recording", 90));
        let review = presentation_for(&status("stopped", 90));
        let saved = presentation_for(&CaptureStatus::default());
        assert_eq!(recording.title, " 01:30");
        assert_eq!(review.title, "");
        assert_eq!(review.glyph, GlyphVariant::Review);
        assert_eq!(review.layout, MenuLayout::Review);
        assert_eq!(saved.title, "");
        assert_eq!(saved.glyph, GlyphVariant::Idle);
    }

    #[test]
    fn waiting_and_busy_states_offer_only_what_applies() {
        let draft = presentation_for(&CaptureStatus {
            phase: "idle".into(),
            has_unreviewed_work: Some(true),
            ..CaptureStatus::default()
        });
        assert_eq!(draft.glyph, GlyphVariant::Draft);
        assert_eq!(draft.status_text, "Unsaved capture");
        assert_eq!(
            action_ids(draft.layout),
            vec![
                MENU_SHOW_CAPTURE,
                MENU_NEW_RECORDING,
                "open-gunther",
                MENU_QUIT
            ]
        );

        let preparing = presentation_for(&status("requesting", 0));
        assert_eq!(preparing.glyph, GlyphVariant::Preparing);
        assert_eq!(preparing.status_text, "Opening the microphone…");

        let error = presentation_for(&CaptureStatus {
            detail: Some("Microphone access was denied".into()),
            ..status("error", 0)
        });
        assert_eq!(error.glyph, GlyphVariant::Attention);
        assert_eq!(error.tooltip, "Gunther — Microphone access was denied");
    }

    #[test]
    fn recording_controls_are_ignored_when_nothing_is_recording() {
        let idle = status("idle", 0);
        assert_eq!(menu_action(MENU_PAUSE_RESUME, &idle), MenuAction::Ignore);
        assert_eq!(menu_action(MENU_MARK, &idle), MenuAction::Ignore);
        assert_eq!(menu_action(MENU_FINISH, &idle), MenuAction::Ignore);
        assert_eq!(menu_action(MENU_QUIT, &idle), MenuAction::Quit);
        assert_eq!(
            menu_action(MENU_SETTINGS, &idle),
            MenuAction::MainCommand("settings")
        );
        assert_eq!(
            menu_action(MENU_NEW_NOTE, &idle),
            MenuAction::MainCommand("new-note")
        );
    }

    #[test]
    fn active_or_unsaved_capture_cannot_be_replaced_by_another_entry_point() {
        assert!(can_replace_capture(&status("idle", 0)));
        for phase in ["requesting", "importing", "recording", "paused", "stopped"] {
            assert!(!can_replace_capture(&status(phase, 10)));
        }
        assert!(!can_replace_capture(&CaptureStatus {
            phase: "idle".into(),
            has_unreviewed_work: Some(true),
            ..CaptureStatus::default()
        }));
    }

    #[test]
    fn other_captures_stay_reachable_while_a_recording_runs() {
        for layout in [MenuLayout::Live { paused: false }, MenuLayout::Review] {
            assert!(
                action_ids(layout).contains(&MENU_OPEN_CAPTURE),
                "{layout:?}"
            );
        }
        assert!(action_ids(MenuLayout::Draft).contains(&MENU_NEW_RECORDING));
    }

    #[test]
    fn saving_beside_a_recording_keeps_its_menu_bar_state() {
        assert!(saved_capture_continues(
            r#"{"message":"Saved","captureContinues":true}"#
        ));
        assert!(!saved_capture_continues(
            r#"{"message":"Saved","captureContinues":false}"#
        ));
        assert!(!saved_capture_continues(r#"{"message":"Saved"}"#));
        assert!(!saved_capture_continues("not json"));
    }

    #[test]
    fn the_menu_bar_item_can_step_aside_until_something_is_captured() {
        assert!(tray_visible(MenuBarMode::Always, MenuLayout::Ready));
        assert!(!tray_visible(
            MenuBarMode::WhileCapturing,
            MenuLayout::Ready
        ));
        assert!(tray_visible(
            MenuBarMode::WhileCapturing,
            MenuLayout::Live { paused: false }
        ));
        assert!(tray_visible(MenuBarMode::WhileCapturing, MenuLayout::Draft));
    }
}
