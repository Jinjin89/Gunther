use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, Runtime,
};

pub const CAPTURE_WINDOW_LABEL: &str = "capture";
pub const CAPTURE_REQUEST_EVENT: &str = "gunther://capture-request";
pub const CAPTURE_CONTROL_EVENT: &str = "gunther://capture-control";
pub const CAPTURE_SAVED_EVENT: &str = "gunther://capture-saved";

const TRAY_ID: &str = "gunther-status";
const MENU_STATUS: &str = "capture-status";
const MENU_OPEN_CAPTURE: &str = "open-capture";
const MENU_NEW_RECORDING: &str = "new-recording";
const MENU_QUICK_NOTE: &str = "quick-note";
const MENU_PAUSE_RESUME: &str = "pause-resume-recording";
const MENU_MARK: &str = "mark-recording";
const MENU_FINISH: &str = "finish-recording";
const MENU_OPEN_MAIN: &str = "open-gunther";
const MENU_QUIT: &str = crate::application_menu::QUIT_MENU_ID;

const GLYPH_SIZE: u32 = 20;

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

#[derive(Default)]
struct CaptureShellRuntime {
    pending_launch: Option<CaptureLaunchRequest>,
    status: CaptureStatus,
}

#[derive(Default)]
pub struct CaptureShell(Mutex<CaptureShellRuntime>);

struct TrayControls<R: Runtime> {
    status: MenuItem<R>,
    pause_resume: MenuItem<R>,
    mark: MenuItem<R>,
    finish: MenuItem<R>,
    quit: MenuItem<R>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum GlyphVariant {
    Idle,
    Draft,
    Recording,
    Paused,
    Busy,
    Review,
    Error,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct TrayPresentation {
    glyph: GlyphVariant,
    icon_is_template: bool,
    title: Option<String>,
    tooltip: String,
    status_text: String,
    pause_text: String,
    pause_enabled: bool,
    mark_enabled: bool,
    finish_enabled: bool,
    quit_text: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum MenuAction {
    OpenCapture,
    NewRecording,
    QuickNote,
    Pause,
    Resume,
    Mark,
    Finish,
    OpenMain,
    Quit,
    Ignore,
}

fn format_elapsed(seconds: u64) -> String {
    let hours = seconds / 3_600;
    let minutes = (seconds % 3_600) / 60;
    let seconds = seconds % 60;
    format!("{hours:02}:{minutes:02}:{seconds:02}")
}

fn presentation_for(status: &CaptureStatus) -> TrayPresentation {
    let phase = status.normalized_phase();
    let persistence = status.normalized_persistence();
    let elapsed = format_elapsed(status.elapsed());
    let recording = phase == "recording";
    let paused = phase == "paused";
    let busy = matches!(phase.as_str(), "requesting" | "importing" | "finalizing")
        || matches!(persistence.as_str(), "saving" | "recovering");
    let recording_review = matches!(phase.as_str(), "stopped" | "review" | "review-ready");
    let draft_open = status.has_unreviewed_work.unwrap_or(false) && !recording_review;
    let error = phase == "error";

    let (glyph, icon_is_template, title, status_text, tooltip) = if recording {
        (
            GlyphVariant::Recording,
            false,
            Some(format!(" {elapsed}")),
            format!("● Recording · {elapsed}"),
            format!("Gunther — Recording {elapsed}"),
        )
    } else if paused {
        (
            GlyphVariant::Paused,
            false,
            Some(format!(" {elapsed}")),
            format!("Ⅱ Paused · {elapsed}"),
            format!("Gunther — Recording paused at {elapsed}"),
        )
    } else if error {
        let detail = status
            .detail
            .as_deref()
            .filter(|detail| !detail.trim().is_empty())
            .unwrap_or("Capture needs attention");
        (
            GlyphVariant::Error,
            false,
            None,
            "Gunther · Capture needs attention".into(),
            format!("Gunther — {detail}"),
        )
    } else if busy {
        let label = match phase.as_str() {
            "requesting" => "Requesting microphone…",
            "importing" => "Importing source…",
            "finalizing" => "Finalizing recording…",
            _ if persistence == "recovering" => "Recovering capture…",
            _ => "Saving capture…",
        };
        (
            GlyphVariant::Busy,
            true,
            None,
            format!("Gunther · {label}"),
            format!("Gunther — {label}"),
        )
    } else if recording_review {
        (
            GlyphVariant::Review,
            true,
            None,
            "Gunther · Review ready".into(),
            "Gunther — Capture ready to review".into(),
        )
    } else if draft_open {
        (
            GlyphVariant::Draft,
            true,
            None,
            "Gunther · Capture draft open".into(),
            "Gunther — Unsaved capture draft open".into(),
        )
    } else {
        (
            GlyphVariant::Idle,
            true,
            None,
            "Gunther · Ready".into(),
            "Gunther — Ready to capture".into(),
        )
    };

    TrayPresentation {
        glyph,
        icon_is_template,
        title,
        tooltip,
        status_text,
        pause_text: if paused {
            "Resume Recording".into()
        } else {
            "Pause Recording".into()
        },
        pause_enabled: recording || paused,
        mark_enabled: recording || paused,
        finish_enabled: recording || paused,
        quit_text: if status.blocks_exit() {
            "Quit Gunther…".into()
        } else {
            "Quit Gunther".into()
        },
    }
}

fn menu_action(id: &str, status: &CaptureStatus) -> MenuAction {
    match id {
        MENU_OPEN_CAPTURE => MenuAction::OpenCapture,
        MENU_NEW_RECORDING => MenuAction::NewRecording,
        MENU_QUICK_NOTE => MenuAction::QuickNote,
        MENU_PAUSE_RESUME if status.normalized_phase() == "paused" => MenuAction::Resume,
        MENU_PAUSE_RESUME if status.normalized_phase() == "recording" => MenuAction::Pause,
        MENU_MARK if matches!(status.normalized_phase().as_str(), "recording" | "paused") => {
            MenuAction::Mark
        }
        MENU_FINISH if matches!(status.normalized_phase().as_str(), "recording" | "paused") => {
            MenuAction::Finish
        }
        MENU_OPEN_MAIN => MenuAction::OpenMain,
        MENU_QUIT => MenuAction::Quit,
        _ => MenuAction::Ignore,
    }
}

fn can_replace_capture(status: &CaptureStatus) -> bool {
    !status.blocks_exit()
}

fn set_pixel(rgba: &mut [u8], x: i32, y: i32, color: [u8; 4]) {
    if x < 0 || y < 0 || x >= GLYPH_SIZE as i32 || y >= GLYPH_SIZE as i32 {
        return;
    }
    let index = ((y as u32 * GLYPH_SIZE + x as u32) * 4) as usize;
    rgba[index..index + 4].copy_from_slice(&color);
}

fn horizontal_line(rgba: &mut [u8], x0: i32, x1: i32, y: i32, color: [u8; 4]) {
    for x in x0..=x1 {
        set_pixel(rgba, x, y, color);
    }
}

fn vertical_line(rgba: &mut [u8], x: i32, y0: i32, y1: i32, color: [u8; 4]) {
    for y in y0..=y1 {
        set_pixel(rgba, x, y, color);
    }
}

fn draw_capture_frame(rgba: &mut [u8], color: [u8; 4]) {
    for thickness in 0..2 {
        let edge = 2 + thickness;
        let far = GLYPH_SIZE as i32 - 3 - thickness;
        horizontal_line(rgba, edge, edge + 4, edge, color);
        vertical_line(rgba, edge, edge, edge + 4, color);
        horizontal_line(rgba, far - 4, far, edge, color);
        vertical_line(rgba, far, edge, edge + 4, color);
        horizontal_line(rgba, edge, edge + 4, far, color);
        vertical_line(rgba, edge, far - 4, far, color);
        horizontal_line(rgba, far - 4, far, far, color);
        vertical_line(rgba, far, far - 4, far, color);
    }
}

fn draw_circle(rgba: &mut [u8], radius: f32, filled: bool, color: [u8; 4]) {
    let center = (GLYPH_SIZE as f32 - 1.0) / 2.0;
    for y in 0..GLYPH_SIZE as i32 {
        for x in 0..GLYPH_SIZE as i32 {
            let dx = x as f32 - center;
            let dy = y as f32 - center;
            let distance = (dx * dx + dy * dy).sqrt();
            let should_draw = if filled {
                distance <= radius
            } else {
                (distance - radius).abs() <= 0.8
            };
            if should_draw {
                set_pixel(rgba, x, y, color);
            }
        }
    }
}

fn draw_check(rgba: &mut [u8], color: [u8; 4]) {
    for offset in 0..3 {
        set_pixel(rgba, 6 + offset, 10 + offset, color);
        set_pixel(rgba, 9 + offset, 12 - offset, color);
        set_pixel(rgba, 12 + offset, 9 - offset, color);
    }
}

fn draw_exclamation(rgba: &mut [u8], color: [u8; 4]) {
    for x in 9..=10 {
        vertical_line(rgba, x, 6, 11, color);
        set_pixel(rgba, x, 14, color);
    }
}

fn glyph_image(variant: GlyphVariant) -> (Image<'static>, bool) {
    let mut rgba = vec![0_u8; (GLYPH_SIZE * GLYPH_SIZE * 4) as usize];
    let template = [0, 0, 0, 255];
    let recording = [255, 69, 58, 255];
    let paused = [255, 159, 10, 255];
    let error = [255, 69, 58, 255];
    let (color, is_template) = match variant {
        GlyphVariant::Recording => (recording, false),
        GlyphVariant::Paused => (paused, false),
        GlyphVariant::Error => (error, false),
        _ => (template, true),
    };
    draw_capture_frame(&mut rgba, color);

    match variant {
        GlyphVariant::Idle => draw_circle(&mut rgba, 3.0, false, color),
        GlyphVariant::Draft => draw_circle(&mut rgba, 2.7, true, color),
        GlyphVariant::Recording => draw_circle(&mut rgba, 3.6, true, color),
        GlyphVariant::Paused => {
            for x in 7..=8 {
                vertical_line(&mut rgba, x, 6, 13, color);
            }
            for x in 11..=12 {
                vertical_line(&mut rgba, x, 6, 13, color);
            }
        }
        GlyphVariant::Busy => {
            draw_circle(&mut rgba, 3.5, false, color);
            for y in 5..=7 {
                for x in 8..=11 {
                    set_pixel(&mut rgba, x, y, [0, 0, 0, 0]);
                }
            }
        }
        GlyphVariant::Review => draw_check(&mut rgba, color),
        GlyphVariant::Error => draw_exclamation(&mut rgba, color),
    }

    (Image::new_owned(rgba, GLYPH_SIZE, GLYPH_SIZE), is_template)
}

fn status_snapshot<R: Runtime>(app: &AppHandle<R>) -> CaptureStatus {
    app.state::<CaptureShell>()
        .0
        .lock()
        .expect("capture shell lock poisoned")
        .status
        .clone()
}

fn apply_tray_presentation<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let presentation = presentation_for(&status_snapshot(app));
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let (icon, is_template) = glyph_image(presentation.glyph);
        tray.set_icon_with_as_template(Some(icon), is_template)?;
        tray.set_title(presentation.title.as_deref())?;
        tray.set_tooltip(Some(&presentation.tooltip))?;
    }
    let controls = app.state::<TrayControls<R>>();
    controls.status.set_text(&presentation.status_text)?;
    controls.pause_resume.set_text(&presentation.pause_text)?;
    controls
        .pause_resume
        .set_enabled(presentation.pause_enabled)?;
    controls.mark.set_enabled(presentation.mark_enabled)?;
    controls.finish.set_enabled(presentation.finish_enabled)?;
    controls.quit.set_text(&presentation.quit_text)?;
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
    let preserve_current_capture = {
        let shell = app.state::<CaptureShell>();
        let mut runtime = shell.0.lock().expect("capture shell lock poisoned");
        if !can_replace_capture(&runtime.status) {
            true
        } else {
            runtime.pending_launch = Some(request.clone());
            false
        }
    };
    let _ = show_window(app, CAPTURE_WINDOW_LABEL);
    if preserve_current_capture {
        // A recording, import, finalization, or unsaved review is a singleton.
        // New entry points may reveal it but must never leave a launch request
        // that could replace it after a renderer reload.
        emit_capture_control(app, "show");
    } else {
        let _ = app.emit_to(CAPTURE_WINDOW_LABEL, CAPTURE_REQUEST_EVENT, request);
    }
}

fn request_quit<R: Runtime>(app: &AppHandle<R>) {
    if status_snapshot(app).blocks_exit() {
        block_quit_and_show_capture(app);
    } else {
        app.exit(0);
    }
}

pub fn handle_menu_event<R: Runtime>(app: &AppHandle<R>, id: &str) {
    match menu_action(id, &status_snapshot(app)) {
        MenuAction::OpenCapture => open_from_tray(
            app,
            CaptureLaunchRequest {
                source: Some("tray-open-capture".into()),
                ..Default::default()
            },
        ),
        MenuAction::NewRecording => open_from_tray(
            app,
            CaptureLaunchRequest {
                kind: Some(CaptureKind::Recording),
                recording_context: Some(RecordingContext::Lecture),
                source: Some("tray-new-recording".into()),
                ..Default::default()
            },
        ),
        MenuAction::QuickNote => open_from_tray(
            app,
            CaptureLaunchRequest {
                kind: Some(CaptureKind::Note),
                source: Some("tray-quick-note".into()),
                ..Default::default()
            },
        ),
        MenuAction::Pause => emit_capture_control(app, "pause"),
        MenuAction::Resume => emit_capture_control(app, "resume"),
        MenuAction::Mark => emit_capture_control(app, "mark"),
        MenuAction::Finish => emit_capture_control(app, "finish"),
        MenuAction::OpenMain => {
            let _ = show_window(app, "main");
        }
        MenuAction::Quit => request_quit(app),
        MenuAction::Ignore => {}
    }
}

pub fn setup_tray<R: Runtime>(app: &tauri::App<R>) -> tauri::Result<()> {
    let status = MenuItem::with_id(app, MENU_STATUS, "Gunther · Ready", false, None::<&str>)?;
    let open_capture =
        MenuItem::with_id(app, MENU_OPEN_CAPTURE, "Open Capture…", true, None::<&str>)?;
    let new_recording = MenuItem::with_id(
        app,
        MENU_NEW_RECORDING,
        "New Recording…",
        true,
        None::<&str>,
    )?;
    let quick_note = MenuItem::with_id(app, MENU_QUICK_NOTE, "Quick Note…", true, None::<&str>)?;
    let pause_resume = MenuItem::with_id(
        app,
        MENU_PAUSE_RESUME,
        "Pause Recording",
        false,
        None::<&str>,
    )?;
    let mark = MenuItem::with_id(app, MENU_MARK, "Mark Moment", false, None::<&str>)?;
    let finish = MenuItem::with_id(app, MENU_FINISH, "Finish Recording", false, None::<&str>)?;
    let open_main = MenuItem::with_id(app, MENU_OPEN_MAIN, "Open Gunther", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, MENU_QUIT, "Quit Gunther", true, None::<&str>)?;
    let separator_one = PredefinedMenuItem::separator(app)?;
    let separator_two = PredefinedMenuItem::separator(app)?;
    let separator_three = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(
        app,
        &[
            &status,
            &separator_one,
            &open_capture,
            &new_recording,
            &quick_note,
            &separator_two,
            &pause_resume,
            &mark,
            &finish,
            &separator_three,
            &open_main,
            &quit,
        ],
    )?;

    let (idle_icon, _) = glyph_image(GlyphVariant::Idle);
    TrayIconBuilder::with_id(TRAY_ID)
        .icon(idle_icon)
        .icon_as_template(true)
        .tooltip("Gunther — Ready to capture")
        .menu(&menu)
        // A normal click opens the action menu, matching macOS status-item
        // conventions and keeping Pause/Mark/Finish discoverable.
        .show_menu_on_left_click(true)
        // App and tray menus share the Builder's single menu-event handler.
        // Registering a second tray handler would dispatch actions twice.
        .build(app)?;

    app.manage(TrayControls {
        status,
        pause_resume,
        mark,
        finish,
        quit,
    });
    apply_tray_presentation(app.handle())
}

pub fn blocks_exit<R: Runtime>(app: &AppHandle<R>) -> bool {
    status_snapshot(app).blocks_exit()
}

pub fn block_quit_and_show_capture<R: Runtime>(app: &AppHandle<R>) {
    let _ = show_window(app, CAPTURE_WINDOW_LABEL);
    emit_capture_control(app, "quit-blocked");
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

#[cfg(test)]
mod tests {
    use super::{
        can_replace_capture, format_elapsed, glyph_image, menu_action, presentation_for,
        CaptureStatus, GlyphVariant, MenuAction, GLYPH_SIZE, MENU_FINISH, MENU_MARK,
        MENU_PAUSE_RESUME, MENU_QUIT,
    };

    fn status(phase: &str, seconds: u64) -> CaptureStatus {
        CaptureStatus {
            phase: phase.into(),
            seconds: Some(seconds),
            ..CaptureStatus::default()
        }
    }

    #[test]
    fn elapsed_time_stays_unambiguous_for_long_sessions() {
        assert_eq!(format_elapsed(0), "00:00:00");
        assert_eq!(format_elapsed(3_723), "01:02:03");
        assert_eq!(format_elapsed(14_401), "04:00:01");
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
    fn recording_and_paused_presentations_are_visible_and_actionable() {
        let recording = presentation_for(&status("recording", 3_723));
        assert_eq!(recording.glyph, GlyphVariant::Recording);
        assert!(!recording.icon_is_template);
        assert_eq!(recording.title.as_deref(), Some(" 01:02:03"));
        assert_eq!(recording.pause_text, "Pause Recording");
        assert!(recording.pause_enabled && recording.mark_enabled && recording.finish_enabled);
        assert_eq!(recording.quit_text, "Quit Gunther…");

        let paused = presentation_for(&status("paused", 3_723));
        assert_eq!(paused.glyph, GlyphVariant::Paused);
        assert_eq!(paused.pause_text, "Resume Recording");
        assert_eq!(
            menu_action(MENU_PAUSE_RESUME, &status("paused", 1)),
            MenuAction::Resume
        );
    }

    #[test]
    fn idle_menu_ignores_recording_only_actions() {
        let idle = status("idle", 0);
        assert_eq!(menu_action(MENU_PAUSE_RESUME, &idle), MenuAction::Ignore);
        assert_eq!(menu_action(MENU_MARK, &idle), MenuAction::Ignore);
        assert_eq!(menu_action(MENU_FINISH, &idle), MenuAction::Ignore);
        assert_eq!(menu_action(MENU_QUIT, &idle), MenuAction::Quit);
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
    fn unsaved_non_recording_capture_is_visible_in_the_menu_bar() {
        let draft = presentation_for(&CaptureStatus {
            phase: "idle".into(),
            has_unreviewed_work: Some(true),
            ..CaptureStatus::default()
        });
        assert_eq!(draft.glyph, GlyphVariant::Draft);
        assert_eq!(draft.status_text, "Gunther · Capture draft open");
        assert_eq!(draft.quit_text, "Quit Gunther…");
    }

    #[test]
    fn generated_glyphs_are_small_rgba_images_with_state_specific_centers() {
        let (idle, idle_template) = glyph_image(GlyphVariant::Idle);
        let (draft, draft_template) = glyph_image(GlyphVariant::Draft);
        let (recording, recording_template) = glyph_image(GlyphVariant::Recording);
        let (paused, paused_template) = glyph_image(GlyphVariant::Paused);
        assert_eq!((idle.width(), idle.height()), (GLYPH_SIZE, GLYPH_SIZE));
        assert_eq!(idle.rgba().len(), (GLYPH_SIZE * GLYPH_SIZE * 4) as usize);
        assert!(idle_template);
        assert!(draft_template);
        assert!(!recording_template);
        assert!(!paused_template);

        let center = (((GLYPH_SIZE / 2) * GLYPH_SIZE + GLYPH_SIZE / 2) * 4) as usize;
        assert_eq!(idle.rgba()[center + 3], 0, "idle center remains hollow");
        assert_eq!(&draft.rgba()[center..center + 4], &[0, 0, 0, 255]);
        assert_eq!(&recording.rgba()[center..center + 4], &[255, 69, 58, 255]);
        assert_eq!(
            paused.rgba()[center + 3],
            0,
            "paused glyph has a center gap"
        );
    }
}
