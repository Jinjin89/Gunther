//! Independent short voice input; stopping hides it while final text finishes in the background.

use std::sync::Mutex;
#[cfg(target_os = "macos")]
use tauri::Emitter;
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
mod native_window {
    use objc2::{
        msg_send,
        runtime::{AnyClass, AnyObject, Bool},
    };
    use objc2_foundation::{NSPoint, NSRect, NSSize};
    use std::time::Duration;
    use tauri::WebviewWindow;

    struct Screen {
        bounds: NSRect,
        visible: NSRect,
    }

    fn panel_origin(pointer: NSPoint, screens: &[Screen], size: NSSize) -> Option<NSPoint> {
        let distance = |bounds: NSRect| {
            let dx = (bounds.origin.x - pointer.x)
                .max(pointer.x - bounds.origin.x - bounds.size.width)
                .max(0.0);
            let dy = (bounds.origin.y - pointer.y)
                .max(pointer.y - bounds.origin.y - bounds.size.height)
                .max(0.0);
            dx * dx + dy * dy
        };
        let screen = screens
            .iter()
            .min_by(|a, b| distance(a.bounds).total_cmp(&distance(b.bounds)))?;
        let frame = screen.visible;
        Some(NSPoint::new(
            frame.origin.x + ((frame.size.width - size.width) / 2.0).max(0.0),
            // Keep the existing 76-point clearance, within the usable screen area.
            frame.origin.y + 76.0_f64.min((frame.size.height - size.height).max(0.0)),
        ))
    }

    unsafe fn position(native: *mut AnyObject) -> Result<(), String> {
        let screen_class = AnyClass::get(c"NSScreen").ok_or("macOS displays are unavailable.")?;
        let event_class =
            AnyClass::get(c"NSEvent").ok_or("The pointer position is unavailable.")?;
        let pointer: NSPoint = msg_send![event_class, mouseLocation];
        let displays: *mut AnyObject = msg_send![screen_class, screens];
        let count: usize = msg_send![displays, count];
        let screens = (0..count)
            .map(|index| {
                let screen: *mut AnyObject = msg_send![displays, objectAtIndex: index];
                Screen {
                    bounds: msg_send![screen, frame],
                    visible: msg_send![screen, visibleFrame],
                }
            })
            .collect::<Vec<_>>();
        let frame: NSRect = msg_send![native, frame];
        let origin = panel_origin(pointer, &screens, frame.size)
            .ok_or("No display is available for voice input.")?;
        // AppKit screen frames, mouse position and window frame all use logical points.
        // A physical position would be converted using the window's *previous* display
        // scale, which can put it off screen when the destination has another scale.
        let _: () = msg_send![native, setFrameOrigin: origin];
        log::info!(
            "Voice-input window positioned at ({}, {}) in macOS points",
            origin.x,
            origin.y
        );
        Ok(())
    }

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

    pub fn open(window: &WebviewWindow) -> Result<(), String> {
        let native = window.ns_window().map_err(|error| error.to_string())?;
        let native = native.cast::<AnyObject>();
        let seconds = if reduced() { 0.0 } else { 0.12 };
        unsafe {
            position(native)?;
            alpha(native, if seconds > 0.0 { 0.0 } else { 1.0 }, 0.0);
        }
        window.show().map_err(|error| error.to_string())?;
        // Order the overlay above the frontmost application without activating Gunther.
        // Taking focus would activate the app and make this window key; hiding a key
        // window hands key status to the next window in the app, so the main window
        // would jump in front of whatever the user was typing in. The window is also
        // declared not focusable, so it never becomes the key or main window.
        unsafe {
            let _: () = msg_send![native, orderFrontRegardless];
            alpha(native, 1.0, seconds);
        }
        Ok(())
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

    #[cfg(test)]
    mod tests {
        use super::*;

        fn screen(x: f64, y: f64, width: f64, height: f64) -> Screen {
            let bounds = NSRect::new(NSPoint::new(x, y), NSSize::new(width, height));
            Screen {
                bounds,
                visible: bounds,
            }
        }

        #[test]
        fn follows_the_pointer_across_displays_with_negative_origins() {
            let screens = [
                screen(0.0, 0.0, 1710.0, 1107.0),
                screen(-340.0, 1107.0, 2560.0, 1440.0),
            ];
            let size = NSSize::new(480.0, 100.0);
            for (pointer, expected_screen) in [
                (NSPoint::new(500.0, 500.0), 0),
                (NSPoint::new(-100.0, 1500.0), 1),
            ] {
                let origin = panel_origin(pointer, &screens, size).unwrap();
                let bounds = screens[expected_screen].visible;
                assert!(origin.x >= bounds.origin.x && origin.y >= bounds.origin.y);
                assert!(origin.x + size.width <= bounds.origin.x + bounds.size.width);
                assert!(origin.y + size.height <= bounds.origin.y + bounds.size.height);
            }
        }

        #[test]
        fn respects_usable_area_and_selects_by_the_full_screen_frame() {
            let mut lower = screen(0.0, 0.0, 1000.0, 700.0);
            lower.visible = NSRect::new(NSPoint::new(60.0, 50.0), NSSize::new(940.0, 620.0));
            let screens = [lower, screen(0.0, 700.0, 1000.0, 700.0)];
            let origin = panel_origin(
                NSPoint::new(500.0, 690.0),
                &screens,
                NSSize::new(480.0, 100.0),
            )
            .unwrap();
            assert_eq!(origin, NSPoint::new(290.0, 126.0));
        }

        #[test]
        fn clamps_clearance_on_short_screens_and_handles_display_removal() {
            let screens = [screen(-800.0, -600.0, 800.0, 140.0)];
            let origin = panel_origin(
                NSPoint::new(100.0, 100.0),
                &screens,
                NSSize::new(480.0, 100.0),
            )
            .unwrap();
            assert_eq!(origin, NSPoint::new(-640.0, -560.0));
            assert!(panel_origin(NSPoint::new(0.0, 0.0), &[], NSSize::new(480.0, 100.0)).is_none());
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
        log::info!("Voice-input request {}: showing window", request.serial);
        native_window::open(&window)?;
    } else {
        let delay = native_window::close(&window);
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
                        if let Err(error) = window.hide() {
                            log::error!(
                                "Voice-input request {}: could not hide window: {error}",
                                request.serial
                            );
                        } else {
                            log::info!("Voice-input request {}: window hidden", request.serial);
                        }
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
    native_window::prepare(&window);
    let shortcut = Shortcut::new(Some(Modifiers::ALT), Code::Space);
    if let Err(error) = app
        .global_shortcut()
        .on_shortcut(shortcut, |app, _, event| {
            log::info!("Voice-input shortcut: {:?}", event.state());
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
            log::info!(
                "Voice-input request {}: listening={}",
                request.serial,
                request.listening
            );
            if let Err(error) = present_request(app, request) {
                log::error!("Voice-input shortcut failed: {error}");
            }
        })
    {
        log::error!("Could not register Option+Space: {error}");
        app.dialog().message("Option+Space could not be registered for voice input. Another application may already be using it.").title("Voice input shortcut unavailable").show(|_| {});
    } else {
        log::info!("Registered Option+Space for voice input");
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
