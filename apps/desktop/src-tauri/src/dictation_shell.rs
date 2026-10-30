//! Independent short voice input; stopping hides it while final text finishes in the background.

use std::sync::Mutex;
#[cfg(target_os = "macos")]
use tauri::{Emitter, PhysicalPosition};
use tauri::{Manager, WebviewWindow};
use tauri_plugin_clipboard_manager::ClipboardExt;
#[cfg(target_os = "macos")]
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_global_shortcut::ShortcutState;
#[cfg(target_os = "macos")]
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};

pub const WINDOW_LABEL: &str = "dictation";
const REQUEST_EVENT: &str = "gunther://dictation-request";

#[cfg(target_os = "macos")]
mod motion {
    use objc2::{
        msg_send,
        runtime::{AnyClass, AnyObject, Bool},
    };
    use std::time::Duration;
    use tauri::WebviewWindow;

    pub fn prepare(window: &WebviewWindow) {
        let (Ok(native), Some(colors)) = (window.ns_window(), AnyClass::get(c"NSColor")) else {
            return;
        };
        // Rounded native content with a clear backing keeps the overlay's corners and
        // system shadow clean, without changing any other Gunther window.
        unsafe {
            let native = native.cast::<AnyObject>();
            let clear: *mut AnyObject = msg_send![colors, clearColor];
            let _: () = msg_send![native, setOpaque: Bool::NO];
            let _: () = msg_send![native, setBackgroundColor: clear];
            let view: *mut AnyObject = msg_send![native, contentView];
            let _: () = msg_send![view, setWantsLayer: Bool::YES];
            let layer: *mut AnyObject = msg_send![view, layer];
            let _: () = msg_send![layer, setCornerRadius: 12.0_f64];
            let _: () = msg_send![layer, setMasksToBounds: Bool::YES];
            let _: () = msg_send![native, invalidateShadow];
        }
    }

    fn reduced() -> bool {
        let Some(workspace_class) = AnyClass::get(c"NSWorkspace") else {
            return false;
        };
        // Public AppKit display preference; no screen or application content is read.
        unsafe {
            let workspace: *mut AnyObject = msg_send![workspace_class, sharedWorkspace];
            let value: Bool = msg_send![workspace, accessibilityDisplayShouldReduceMotion];
            value.as_bool()
        }
    }

    // Called only from the app's main-thread window presentation functions.
    unsafe fn alpha(native: *mut AnyObject, value: f64, seconds: f64) {
        if seconds > 0.0 {
            if let Some(class) = AnyClass::get(c"NSAnimationContext") {
                let _: () = msg_send![class, beginGrouping];
                let context: *mut AnyObject = msg_send![class, currentContext];
                let _: () = msg_send![context, setDuration: seconds];
                let animator: *mut AnyObject = msg_send![native, animator];
                let _: () = msg_send![animator, setAlphaValue: value];
                let _: () = msg_send![class, endGrouping];
                return;
            }
        }
        let _: () = msg_send![native, setAlphaValue: value];
    }

    pub fn open(window: &WebviewWindow) {
        if let Ok(native) = window.ns_window() {
            let native = native.cast::<AnyObject>();
            let seconds = if reduced() { 0.0 } else { 0.12 };
            unsafe {
                alpha(native, if seconds > 0.0 { 0.0 } else { 1.0 }, 0.0);
            }
            let _ = window.show();
            let _ = window.set_focus();
            unsafe {
                alpha(native, 1.0, seconds);
            }
        } else {
            let _ = window.show();
        }
    }

    pub fn close(window: &WebviewWindow) -> Duration {
        let seconds = if reduced() { 0.0 } else { 0.10 };
        if let Ok(native) = window.ns_window() {
            unsafe {
                alpha(native.cast::<AnyObject>(), 0.0, seconds);
            }
        }
        if seconds > 0.0 {
            Duration::from_millis(120)
        } else {
            Duration::ZERO
        }
    }
}

#[derive(Default)]
pub struct DictationShell(Mutex<Requests>);

#[derive(Clone, serde::Serialize)]
pub struct DictationRequest {
    serial: u64,
    listening: bool,
}

#[derive(Default)]
struct Requests {
    serial: u64,
    pressed: bool,
    listening: bool,
    positioned: bool,
    closing: bool,
}

impl Requests {
    fn key(&mut self, state: ShortcutState) -> bool {
        match state {
            ShortcutState::Pressed if !self.pressed => {
                self.pressed = true;
                true
            }
            ShortcutState::Released => {
                self.pressed = false;
                false
            }
            _ => false,
        }
    }
    fn request(&mut self, listening: bool) -> DictationRequest {
        self.serial = self.serial.saturating_add(1);
        self.listening = listening;
        self.closing = !listening;
        self.current()
    }
    fn current(&self) -> DictationRequest {
        DictationRequest {
            serial: self.serial,
            listening: self.listening,
        }
    }
    fn finish(&mut self, serial: u64) -> bool {
        if self.serial == serial || !self.listening {
            self.listening = false;
            true
        } else {
            false
        }
    }
}

#[cfg(target_os = "macos")]
fn present_request(app: &tauri::AppHandle, request: DictationRequest) -> Result<(), String> {
    // Stop capture immediately; display motion is independent of the audio/clipboard work.
    app.emit_to(WINDOW_LABEL, REQUEST_EVENT, request.clone())
        .map_err(|e| e.to_string())?;
    let handle = app.clone();
    app.run_on_main_thread(move || {
        let current = handle
            .state::<DictationShell>()
            .0
            .lock()
            .map(|r| r.serial == request.serial)
            .unwrap_or(false);
        if current {
            if let Err(error) = present_window(&handle, request) {
                log::error!("Voice-input window could not update: {error}");
            }
        }
    })
    .map_err(|e| e.to_string())
}

#[cfg(target_os = "macos")]
fn present_window(app: &tauri::AppHandle, request: DictationRequest) -> Result<(), String> {
    let window = app
        .get_webview_window(WINDOW_LABEL)
        .ok_or("Voice input is unavailable.")?;
    if request.listening {
        let positioned = app
            .state::<DictationShell>()
            .0
            .lock()
            .map_err(|e| e.to_string())?
            .positioned;
        if !positioned {
            if let Some(monitor) = window.primary_monitor().map_err(|e| e.to_string())? {
                let size = window.outer_size().map_err(|e| e.to_string())?;
                let origin = monitor.position();
                let screen = monitor.size();
                let x = origin.x + (screen.width.saturating_sub(size.width) / 2) as i32;
                let y = origin.y + screen.height.saturating_sub(size.height) as i32
                    - (76.0 * monitor.scale_factor()) as i32;
                window
                    .set_position(PhysicalPosition::new(x, y.max(origin.y)))
                    .map_err(|e| e.to_string())?;
                app.state::<DictationShell>()
                    .0
                    .lock()
                    .map_err(|e| e.to_string())?
                    .positioned = true;
            }
        }
        motion::open(&window);
    } else {
        let delay = motion::close(&window);
        let handle = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(delay);
            let ui_handle = handle.clone();
            let _ = handle.run_on_main_thread(move || {
                let should_hide = {
                    let state = ui_handle.state::<DictationShell>();
                    let Ok(mut requests) = state.0.lock() else {
                        return;
                    };
                    let hide = requests.serial == request.serial && !requests.listening;
                    if hide {
                        requests.closing = false;
                    }
                    hide
                };
                if should_hide {
                    if let Some(window) = ui_handle.get_webview_window(WINDOW_LABEL) {
                        let _ = window.hide();
                    }
                }
            });
        });
    }
    Ok(())
}

#[cfg(target_os = "macos")]
pub fn setup(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let config = app
        .config()
        .app
        .windows
        .iter()
        .find(|c| c.label == WINDOW_LABEL)
        .ok_or("Voice-input window configuration is missing.")?;
    let window = tauri::WebviewWindowBuilder::from_config(app.handle(), config)?.build()?;
    motion::prepare(&window);
    let shortcut = Shortcut::new(Some(Modifiers::ALT), Code::Space);
    if let Err(error) = app
        .global_shortcut()
        .on_shortcut(shortcut, |app, _, event| {
            let visible = app
                .get_webview_window(WINDOW_LABEL)
                .and_then(|w| w.is_visible().ok())
                .unwrap_or(false);
            let request = {
                let state = app.state::<DictationShell>();
                let Ok(mut requests) = state.0.lock() else {
                    return;
                };
                if !requests.key(event.state()) {
                    return;
                }
                let listening = !(requests.listening || (visible && !requests.closing));
                requests.request(listening)
            };
            if let Err(error) = present_request(app, request) {
                log::error!("Voice-input shortcut failed: {error}");
            }
        })
    {
        log::error!("Could not register Option+Space: {error}");
        app.dialog().message("Option+Space could not be registered for voice input. Another application may already be using it.").title("Voice input shortcut unavailable").show(|_| {});
    }
    Ok(())
}

#[cfg(not(target_os = "macos"))]
pub fn setup(_app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    Ok(())
}

#[tauri::command]
pub fn dictation_request(app: tauri::AppHandle) -> Result<DictationRequest, String> {
    app.state::<DictationShell>()
        .0
        .lock()
        .map(|r| r.current())
        .map_err(|e| e.to_string())
}

fn voice_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != WINDOW_LABEL {
        return Err("This command belongs to the voice-input window.".into());
    }
    Ok(())
}

#[cfg(target_os = "macos")]
pub fn stop(app: &tauri::AppHandle) -> Result<(), String> {
    let request = app
        .state::<DictationShell>()
        .0
        .lock()
        .map_err(|e| e.to_string())?
        .request(false);
    present_request(app, request)
}

#[tauri::command]
pub fn stop_dictation_window(app: tauri::AppHandle, window: WebviewWindow) -> Result<(), String> {
    voice_window(&window)?;
    #[cfg(target_os = "macos")]
    return stop(&app);
    #[cfg(not(target_os = "macos"))]
    window.hide().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn dictation_finished(
    app: tauri::AppHandle,
    window: WebviewWindow,
    serial: u64,
) -> Result<(), String> {
    voice_window(&window)?;
    {
        let state = app.state::<DictationShell>();
        let mut requests = state.0.lock().map_err(|e| e.to_string())?;
        requests.finish(serial);
    }
    Ok(())
}

#[tauri::command]
pub fn copy_dictation_text(
    app: tauri::AppHandle,
    window: WebviewWindow,
    text: String,
) -> Result<(), String> {
    voice_window(&window)?;
    if text.trim().is_empty() || text.len() > 200_000 {
        return Err("There is no completed voice-input text to copy.".into());
    }
    app.clipboard().write_text(text).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn held_keys_do_not_toggle_twice_and_the_second_press_stops() {
        let mut state = Requests::default();
        assert!(state.key(ShortcutState::Pressed));
        assert!(state.request(true).listening);
        assert!(!state.key(ShortcutState::Pressed));
        assert!(!state.key(ShortcutState::Released));
        assert!(state.key(ShortcutState::Pressed));
        let listening = !state.listening;
        let request = state.request(listening);
        assert!(!request.listening);
        assert_eq!(request.serial, 2);
    }
    #[test]
    fn an_older_completion_must_not_hide_a_new_take() {
        let mut state = Requests::default();
        let first = state.request(true);
        state.request(false);
        let next = state.request(true);
        assert!(!state.finish(first.serial));
        assert!(state.listening);
        assert!(state.finish(next.serial));
        assert!(!state.listening);
    }
    #[test]
    fn a_new_open_interrupts_the_previous_close_motion() {
        let mut state = Requests::default();
        state.request(true);
        let close = state.request(false);
        assert!(state.closing);
        let open = state.request(true);
        assert!(!state.closing);
        assert!(open.serial > close.serial);
        assert!(!state.finish(close.serial));
        assert!(state.listening);
    }
}
