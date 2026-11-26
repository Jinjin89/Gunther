//! Independent short voice input; stopping hides it while final text finishes in the background.

use std::sync::Mutex;
#[cfg(target_os = "macos")]
use tauri::Emitter;
use tauri::{Manager, WebviewWindow};
use tauri_plugin_clipboard_manager::ClipboardExt;
#[cfg(target_os = "macos")]
use tauri_plugin_dialog::DialogExt;
#[cfg(any(target_os = "macos", test))]
use tauri_plugin_global_shortcut::ShortcutState;
#[cfg(target_os = "macos")]
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};

pub const WINDOW_LABEL: &str = "dictation";
#[cfg(target_os = "macos")]
const REQUEST_EVENT: &str = "gunther://dictation-request";

#[cfg(any(target_os = "macos", test))]
pub(crate) mod placement {
    #[derive(Clone, Copy, Debug, PartialEq)]
    pub struct Point {
        pub x: f64,
        pub y: f64,
    }

    #[derive(Clone, Copy, Debug, PartialEq)]
    pub struct Size {
        pub width: f64,
        pub height: f64,
    }

    #[derive(Clone, Copy, Debug, PartialEq)]
    pub struct Rect {
        pub origin: Point,
        pub size: Size,
    }

    impl Rect {
        fn contains(self, point: Point) -> bool {
            point.x >= self.origin.x
                && point.x < self.origin.x + self.size.width
                && point.y >= self.origin.y
                && point.y < self.origin.y + self.size.height
        }

        fn squared_distance(self, point: Point) -> f64 {
            let dx = (self.origin.x - point.x)
                .max(point.x - self.origin.x - self.size.width)
                .max(0.0);
            let dy = (self.origin.y - point.y)
                .max(point.y - self.origin.y - self.size.height)
                .max(0.0);
            dx * dx + dy * dy
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq)]
    pub struct Screen {
        pub bounds: Rect,
        pub visible: Rect,
    }

    /// Select the display under `pointer` using half-open screen bounds so shared edges do not
    /// depend on enumeration order, falling back to the nearest display for outer edges or gaps.
    pub fn target_screen(pointer: Point, screens: &[Screen]) -> Option<Screen> {
        screens
            .iter()
            .copied()
            .find(|screen| screen.bounds.contains(pointer))
            .or_else(|| {
                screens.iter().copied().min_by(|a, b| {
                    a.bounds
                        .squared_distance(pointer)
                        .total_cmp(&b.bounds.squared_distance(pointer))
                })
            })
    }

    pub fn panel_origin(pointer: Point, screens: &[Screen], size: Size) -> Option<Point> {
        let frame = target_screen(pointer, screens)?.visible;
        Some(Point {
            x: frame.origin.x + ((frame.size.width - size.width) / 2.0).max(0.0),
            // Keep the existing 76-point clearance, within the usable screen area.
            y: frame.origin.y + 76.0_f64.min((frame.size.height - size.height).max(0.0)),
        })
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        fn rect(x: f64, y: f64, width: f64, height: f64) -> Rect {
            Rect {
                origin: Point { x, y },
                size: Size { width, height },
            }
        }

        fn screen(x: f64, y: f64, width: f64, height: f64) -> Screen {
            let bounds = rect(x, y, width, height);
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
            let size = Size {
                width: 480.0,
                height: 100.0,
            };
            for (pointer, expected_screen) in [
                (Point { x: 500.0, y: 500.0 }, 0),
                (Point { x: -100.0, y: 1500.0 }, 1),
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
            lower.visible = rect(60.0, 50.0, 940.0, 620.0);
            let screens = [lower, screen(0.0, 700.0, 1000.0, 700.0)];
            let origin = panel_origin(
                Point { x: 500.0, y: 690.0 },
                &screens,
                Size {
                    width: 480.0,
                    height: 100.0,
                },
            )
            .unwrap();
            assert_eq!(origin, Point { x: 290.0, y: 126.0 });
        }

        #[test]
        fn shared_edge_prefers_half_open_containment_and_gaps_use_nearest_display() {
            let lower = screen(0.0, 0.0, 1000.0, 700.0);
            let upper = screen(0.0, 700.0, 1000.0, 700.0);
            let edge = Point { x: 500.0, y: 700.0 };
            assert_eq!(target_screen(edge, &[lower, upper]), Some(upper));
            assert_eq!(target_screen(edge, &[upper, lower]), Some(upper));

            let left = screen(0.0, 0.0, 1000.0, 700.0);
            let right = screen(1200.0, 0.0, 1000.0, 700.0);
            assert_eq!(
                target_screen(Point { x: 1050.0, y: 350.0 }, &[left, right]),
                Some(left)
            );
            assert_eq!(
                target_screen(Point { x: 1150.0, y: 350.0 }, &[left, right]),
                Some(right)
            );
            // Far outer edge falls back to the screen it borders.
            assert_eq!(
                target_screen(Point { x: 2200.0, y: 700.0 }, &[left, right]),
                Some(right)
            );
        }

        #[test]
        fn clamps_clearance_oversized_panels_and_empty_displays() {
            let screens = [screen(-800.0, -600.0, 800.0, 140.0)];
            let origin = panel_origin(
                Point { x: 100.0, y: 100.0 },
                &screens,
                Size {
                    width: 480.0,
                    height: 100.0,
                },
            )
            .unwrap();
            assert_eq!(origin, Point { x: -640.0, y: -560.0 });

            let tiny = [screen(10.0, 20.0, 300.0, 80.0)];
            let anchored = panel_origin(
                Point { x: 50.0, y: 50.0 },
                &tiny,
                Size {
                    width: 480.0,
                    height: 100.0,
                },
            )
            .unwrap();
            assert_eq!(anchored, Point { x: 10.0, y: 20.0 });

            assert!(panel_origin(
                Point { x: 0.0, y: 0.0 },
                &[],
                Size {
                    width: 480.0,
                    height: 100.0,
                }
            )
            .is_none());
        }
    }
}

#[cfg(target_os = "macos")]
pub(crate) mod native_window {
    use super::placement::{panel_origin, Point, Rect, Screen, Size};
    use objc2::{
        msg_send,
        runtime::{AnyClass, AnyObject, Bool},
    };
    use objc2_foundation::{NSPoint, NSRect};
    use std::time::Duration;
    use tauri::WebviewWindow;

    fn from_ns_rect(rect: NSRect) -> Rect {
        Rect {
            origin: Point {
                x: rect.origin.x,
                y: rect.origin.y,
            },
            size: Size {
                width: rect.size.width,
                height: rect.size.height,
            },
        }
    }

    pub fn displays() -> Result<(Point, Vec<Screen>), String> {
        let screen_class = AnyClass::get(c"NSScreen").ok_or("macOS displays are unavailable.")?;
        let event_class =
            AnyClass::get(c"NSEvent").ok_or("The pointer position is unavailable.")?;
        unsafe {
            let pointer: NSPoint = msg_send![event_class, mouseLocation];
            let displays: *mut AnyObject = msg_send![screen_class, screens];
            if displays.is_null() {
                return Err("No display is available for voice input.".into());
            }
            let count: usize = msg_send![displays, count];
            let screens = (0..count)
                .map(|index| {
                    let screen: *mut AnyObject = msg_send![displays, objectAtIndex: index];
                    let bounds: NSRect = msg_send![screen, frame];
                    let visible: NSRect = msg_send![screen, visibleFrame];
                    Screen {
                        bounds: from_ns_rect(bounds),
                        visible: from_ns_rect(visible),
                    }
                })
                .collect();
            Ok((
                Point {
                    x: pointer.x,
                    y: pointer.y,
                },
                screens,
            ))
        }
    }

    unsafe fn position(native: *mut AnyObject) -> Result<(), String> {
        let (pointer, screens) = displays()?;
        let frame: NSRect = msg_send![native, frame];
        let origin = panel_origin(pointer, &screens, from_ns_rect(frame).size)
            .ok_or("No display is available for voice input.")?;
        // AppKit screen frames, mouse position and window frame all use logical points.
        // A physical position would be converted using the window's *previous* display
        // scale, which can put it off screen when the destination has another scale.
        let _: () = msg_send![native, setFrameOrigin: NSPoint::new(origin.x, origin.y)];
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
}

#[derive(Default)]
pub struct DictationShell(Mutex<Requests>);

#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct DictationRequest {
    serial: u64,
    listening: bool,
}

#[cfg(any(target_os = "macos", test))]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Transition {
    Start(u64),
    Stop(u64),
}

#[derive(Default)]
struct Requests {
    #[cfg(any(target_os = "macos", test))]
    next_serial: u64,
    published_serial: u64,
    #[cfg(any(target_os = "macos", test))]
    pending_start: Option<u64>,
    #[cfg(any(target_os = "macos", test))]
    pending_stop: Option<u64>,
    #[cfg(any(target_os = "macos", test))]
    pressed: bool,
    listening: bool,
    #[cfg(any(target_os = "macos", test))]
    closing: bool,
}

impl Requests {
    #[cfg(any(target_os = "macos", test))]
    fn next_serial(&mut self) -> u64 {
        self.next_serial = self.next_serial.saturating_add(1);
        self.next_serial
    }

    #[cfg(any(target_os = "macos", test))]
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

    #[cfg(any(target_os = "macos", test))]
    fn toggle(&mut self, visible: bool) -> Transition {
        if self.listening || self.pending_start.is_some() || (visible && !self.closing) {
            Transition::Stop(self.begin_stop())
        } else {
            Transition::Start(self.begin_start())
        }
    }

    #[cfg(any(target_os = "macos", test))]
    fn begin_start(&mut self) -> u64 {
        let serial = self.next_serial();
        self.pending_start = Some(serial);
        self.pending_stop = None;
        self.closing = false;
        serial
    }

    #[cfg(any(target_os = "macos", test))]
    fn is_pending_start(&self, serial: u64) -> bool {
        self.pending_start == Some(serial)
    }

    /// Expose a start to `dictation_request` only after the panel opened and start was delivered.
    #[cfg(any(target_os = "macos", test))]
    fn commit_start(&mut self, serial: u64) -> Option<DictationRequest> {
        if self.pending_start != Some(serial) {
            return None;
        }
        self.pending_start = None;
        self.published_serial = serial;
        self.listening = true;
        self.closing = false;
        Some(self.current())
    }

    #[cfg(any(target_os = "macos", test))]
    fn abort_start(&mut self, serial: u64) -> bool {
        if self.pending_start == Some(serial) {
            self.pending_start = None;
            true
        } else {
            false
        }
    }

    #[cfg(any(target_os = "macos", test))]
    fn begin_stop(&mut self) -> u64 {
        let serial = self.next_serial();
        self.pending_start = None;
        self.pending_stop = Some(serial);
        self.closing = true;
        serial
    }

    #[cfg(any(target_os = "macos", test))]
    fn commit_stop(&mut self, serial: u64) -> bool {
        if self.pending_stop != Some(serial) {
            return false;
        }
        self.pending_stop = None;
        self.published_serial = serial;
        self.listening = false;
        self.closing = true;
        true
    }

    /// Roll back a stop whose event never reached the webview so the next press retries stop.
    #[cfg(any(target_os = "macos", test))]
    fn abort_stop(&mut self, serial: u64) -> bool {
        if self.pending_stop == Some(serial) {
            self.pending_stop = None;
            self.closing = false;
            true
        } else {
            false
        }
    }

    /// Clear `closing` if scheduling the native close/hide failed, keeping a visible panel stoppable.
    #[cfg(any(target_os = "macos", test))]
    fn cancel_close(&mut self, serial: u64) {
        if self.is_current_stop(serial) {
            self.closing = false;
        }
    }

    #[cfg(any(target_os = "macos", test))]
    fn is_current_stop(&self, serial: u64) -> bool {
        self.published_serial == serial
            && self.pending_start.is_none()
            && self.pending_stop.is_none()
            && !self.listening
    }

    #[cfg(any(target_os = "macos", test))]
    fn should_hide(&mut self, serial: u64) -> bool {
        let hide = self.is_current_stop(serial);
        if hide {
            self.closing = false;
        }
        hide
    }

    fn current(&self) -> DictationRequest {
        DictationRequest {
            serial: self.published_serial,
            listening: self.listening,
        }
    }

    fn finish(&mut self, serial: u64) -> bool {
        if self.published_serial == serial || !self.listening {
            self.listening = false;
            true
        } else {
            false
        }
    }
}

#[cfg(target_os = "macos")]
fn hide_window(app: &tauri::AppHandle, serial: u64) {
    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        if let Err(error) = window.hide() {
            log::error!("Voice-input request {serial}: could not hide window: {error}");
        }
    }
}

#[cfg(any(target_os = "macos", test))]
fn run_start(
    shell: &DictationShell,
    serial: u64,
    open: impl FnOnce() -> Result<(), String>,
    emit: impl FnOnce(DictationRequest) -> Result<(), String>,
    hide: impl FnOnce(),
) {
    let pending = shell
        .0
        .lock()
        .map(|r| r.is_pending_start(serial))
        .unwrap_or(false);
    if !pending {
        return;
    }
    if let Err(error) = open() {
        log::error!("Voice-input window could not open: {error}");
        let rolled_back = shell
            .0
            .lock()
            .map(|mut r| r.abort_start(serial))
            .unwrap_or(false);
        if rolled_back {
            hide();
        }
        return;
    }
    let still_pending = shell
        .0
        .lock()
        .map(|r| r.is_pending_start(serial))
        .unwrap_or(false);
    if !still_pending {
        let idle = shell
            .0
            .lock()
            .map(|r| !r.listening && r.pending_start.is_none())
            .unwrap_or(false);
        if idle {
            hide();
        }
        return;
    }
    let request = DictationRequest {
        serial,
        listening: true,
    };
    if let Err(error) = emit(request) {
        log::error!("Voice-input start could not be delivered: {error}");
        let rolled_back = shell
            .0
            .lock()
            .map(|mut r| r.abort_start(serial))
            .unwrap_or(false);
        if rolled_back {
            hide();
        }
        return;
    }
    let committed = shell
        .0
        .lock()
        .ok()
        .and_then(|mut r| r.commit_start(serial))
        .is_some();
    if !committed {
        let idle = shell
            .0
            .lock()
            .map(|r| !r.listening && r.pending_start.is_none())
            .unwrap_or(false);
        if idle {
            hide();
        }
    }
}

#[cfg(target_os = "macos")]
fn present_start(app: &tauri::AppHandle, serial: u64) -> Result<(), String> {
    let handle = app.clone();
    if let Err(error) = app.run_on_main_thread(move || {
        let shell = handle.state::<DictationShell>();
        run_start(
            &shell,
            serial,
            || {
                handle
                    .get_webview_window(WINDOW_LABEL)
                    .ok_or_else(|| "Voice input is unavailable.".to_string())
                    .and_then(|window| native_window::open(&window))
            },
            |request| {
                handle
                    .emit_to(WINDOW_LABEL, REQUEST_EVENT, request)
                    .map_err(|error| error.to_string())
            },
            || hide_window(&handle, serial),
        );
    }) {
        if let Ok(mut requests) = app.state::<DictationShell>().0.lock() {
            requests.abort_start(serial);
        }
        return Err(error.to_string());
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn present_stop(app: &tauri::AppHandle, serial: u64) -> Result<(), String> {
    let request = DictationRequest {
        serial,
        listening: false,
    };
    // Stop capture immediately; display motion is independent of the audio/clipboard work.
    if let Err(error) = app.emit_to(WINDOW_LABEL, REQUEST_EVENT, request) {
        if let Ok(mut requests) = app.state::<DictationShell>().0.lock() {
            requests.abort_stop(serial);
        }
        return Err(error.to_string());
    }
    let committed = app
        .state::<DictationShell>()
        .0
        .lock()
        .map(|mut r| r.commit_stop(serial))
        .unwrap_or(false);
    if !committed {
        return Ok(());
    }
    let handle = app.clone();
    if let Err(error) = app.run_on_main_thread(move || {
        let current = handle
            .state::<DictationShell>()
            .0
            .lock()
            .map(|r| r.is_current_stop(serial))
            .unwrap_or(false);
        if !current {
            return;
        }
        let Some(window) = handle.get_webview_window(WINDOW_LABEL) else {
            if let Ok(mut requests) = handle.state::<DictationShell>().0.lock() {
                requests.cancel_close(serial);
            }
            log::error!("Voice-input window could not update: Voice input is unavailable.");
            return;
        };
        let delay = native_window::close(&window);
        let ui_handle = handle.clone();
        std::thread::spawn(move || {
            std::thread::sleep(delay);
            let hide_handle = ui_handle.clone();
            if let Err(error) = ui_handle.run_on_main_thread(move || {
                let should_hide = hide_handle
                    .state::<DictationShell>()
                    .0
                    .lock()
                    .map(|mut r| r.should_hide(serial))
                    .unwrap_or(false);
                if should_hide {
                    hide_window(&hide_handle, serial);
                }
            }) {
                log::error!("Voice-input request {serial}: could not schedule window hide: {error}");
                if let Ok(mut requests) = ui_handle.state::<DictationShell>().0.lock() {
                    requests.cancel_close(serial);
                }
            }
        });
    }) {
        if let Ok(mut requests) = app.state::<DictationShell>().0.lock() {
            requests.cancel_close(serial);
        }
        return Err(error.to_string());
    }
    Ok(())
}

#[cfg(target_os = "macos")]
pub fn prepare_window(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let config = app
        .config()
        .app
        .windows
        .iter()
        .find(|c| c.label == WINDOW_LABEL)
        .ok_or("Voice-input window configuration is missing.")?;
    let window = tauri::WebviewWindowBuilder::from_config(app.handle(), config)?.build()?;
    native_window::prepare(&window);
    Ok(())
}

#[cfg(target_os = "macos")]
pub fn handle_shortcut(app: &tauri::AppHandle, state: ShortcutState) {
    let visible = app
        .get_webview_window(WINDOW_LABEL)
        .and_then(|w| w.is_visible().ok())
        .unwrap_or(false);
    let transition = {
        let shell = app.state::<DictationShell>();
        let Ok(mut requests) = shell.0.lock() else {
            return;
        };
        if !requests.key(state) {
            return;
        }
        requests.toggle(visible)
    };
    let result = match transition {
        Transition::Start(serial) => present_start(app, serial),
        Transition::Stop(serial) => present_stop(app, serial),
    };
    if let Err(error) = result {
        log::error!("Voice-input shortcut failed: {error}");
    }
}

#[cfg(target_os = "macos")]
pub fn setup(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    prepare_window(app)?;
    let shortcut = Shortcut::new(Some(Modifiers::ALT), Code::Space);
    if let Err(error) = app
        .global_shortcut()
        .on_shortcut(shortcut, |app, _, event| {
            handle_shortcut(app, event.state());
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
    let serial = app
        .state::<DictationShell>()
        .0
        .lock()
        .map_err(|e| e.to_string())?
        .begin_stop();
    present_stop(app, serial)
}

#[tauri::command]
pub fn stop_dictation_window(app: tauri::AppHandle, window: WebviewWindow) -> Result<(), String> {
    voice_window(&window)?;
    #[cfg(target_os = "macos")]
    return stop(&app);
    #[cfg(not(target_os = "macos"))]
    {
        let _ = app;
        window.hide().map_err(|e| e.to_string())
    }
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
        let Transition::Start(serial) = state.toggle(false) else {
            panic!("first press must start voice input");
        };
        assert!(state.commit_start(serial).unwrap().listening);
        assert!(!state.key(ShortcutState::Pressed));
        assert!(!state.key(ShortcutState::Released));
        assert!(state.key(ShortcutState::Pressed));
        let Transition::Stop(stop_serial) = state.toggle(true) else {
            panic!("second press must stop voice input");
        };
        assert!(state.commit_stop(stop_serial));
        assert!(!state.current().listening);
        assert_eq!(state.current().serial, 2);
    }

    #[test]
    fn an_older_completion_must_not_hide_a_new_take() {
        let mut state = Requests::default();
        let first_serial = state.begin_start();
        let first = state.commit_start(first_serial).unwrap();
        let stop_serial = state.begin_stop();
        assert!(state.commit_stop(stop_serial));
        let next_serial = state.begin_start();
        let next = state.commit_start(next_serial).unwrap();
        assert!(!state.finish(first.serial));
        assert!(state.listening);
        assert!(state.finish(next.serial));
        assert!(!state.listening);
    }

    #[test]
    fn a_new_open_interrupts_the_previous_close_motion() {
        let mut state = Requests::default();
        let first = state.begin_start();
        state.commit_start(first).unwrap();
        let close_serial = state.begin_stop();
        assert!(state.commit_stop(close_serial));
        assert!(state.closing);
        let Transition::Start(pending) = state.toggle(true) else {
            panic!("pressing during close fade must reopen");
        };
        assert!(!state.closing);
        assert!(!state.should_hide(close_serial));
        let open = state.commit_start(pending).unwrap();
        assert!(open.serial > close_serial);
        assert!(!state.should_hide(close_serial));
        assert!(!state.finish(close_serial));
        assert!(state.listening);
    }

    #[test]
    fn failed_open_or_start_delivery_publishes_no_start_and_retry_succeeds() {
        let shell = DictationShell::default();
        let idle = DictationRequest {
            serial: 0,
            listening: false,
        };

        // 1. Failing open never calls emit, hides any partial panel, and publishes no start.
        let Transition::Start(first) = shell.0.lock().unwrap().toggle(false) else {
            panic!("first press must schedule an open");
        };
        let mut emitted = false;
        let mut hidden = 0;
        run_start(
            &shell,
            first,
            || Err("no display".into()),
            |_| {
                emitted = true;
                Ok(())
            },
            || hidden += 1,
        );
        assert!(!emitted);
        assert_eq!(hidden, 1);
        assert_eq!(shell.0.lock().unwrap().current(), idle);

        // 2. Successful open with failing emit keeps the start unreadable during and after failure.
        let Transition::Start(second) = shell.0.lock().unwrap().toggle(false) else {
            panic!("retry after failed open must schedule a new open");
        };
        assert!(second > first);
        let mut seen_during_emit = None;
        run_start(
            &shell,
            second,
            || {
                assert_eq!(shell.0.lock().unwrap().current(), idle);
                Ok(())
            },
            |request| {
                assert_eq!(
                    request,
                    DictationRequest {
                        serial: second,
                        listening: true,
                    }
                );
                seen_during_emit = Some(shell.0.lock().unwrap().current());
                Err("webview unavailable".into())
            },
            || hidden += 1,
        );
        assert_eq!(seen_during_emit, Some(idle.clone()));
        assert_eq!(hidden, 2);
        assert_eq!(shell.0.lock().unwrap().current(), idle);

        // 3. Subsequent retry commits only after emit succeeds and does not hide the panel.
        let Transition::Start(third) = shell.0.lock().unwrap().toggle(false) else {
            panic!("retry after failed delivery must schedule a new open");
        };
        assert!(third > second);
        let mut seen_during_ok_emit = None;
        run_start(
            &shell,
            third,
            || Ok(()),
            |request| {
                assert_eq!(
                    request,
                    DictationRequest {
                        serial: third,
                        listening: true,
                    }
                );
                seen_during_ok_emit = Some(shell.0.lock().unwrap().current());
                Ok(())
            },
            || hidden += 1,
        );
        assert_eq!(seen_during_ok_emit, Some(idle));
        assert_eq!(hidden, 2);
        assert_eq!(
            shell.0.lock().unwrap().current(),
            DictationRequest {
                serial: third,
                listening: true,
            }
        );
    }

    #[test]
    fn stale_open_failure_leaves_a_newer_take_alone() {
        let mut state = Requests::default();
        let first = state.begin_start();
        let stop_serial = state.begin_stop();
        assert!(state.commit_stop(stop_serial));
        let second = state.begin_start();
        assert!(!state.abort_start(first));
        assert!(state.is_pending_start(second));
        let published = state.commit_start(second).unwrap();
        assert!(!state.abort_start(first));
        assert_eq!(state.current(), published);
        assert!(state.listening);
    }

    #[test]
    fn failed_stop_delivery_or_close_dispatch_keeps_stop_retryable() {
        let mut state = Requests::default();
        let start = state.begin_start();
        state.commit_start(start).unwrap();

        // Stop event fails to deliver: listening remains true, closing resets, next press retries stop.
        let Transition::Stop(first_stop) = state.toggle(true) else {
            panic!("pressing while listening must attempt stop");
        };
        assert!(state.abort_stop(first_stop));
        assert!(state.listening);
        assert!(!state.closing);
        let Transition::Stop(second_stop) = state.toggle(true) else {
            panic!("next press after failed stop delivery must retry stop, not start");
        };
        assert!(second_stop > first_stop);

        // Stop event delivers, but scheduling native close/hide fails while panel is still visible.
        assert!(state.commit_stop(second_stop));
        state.cancel_close(second_stop);
        assert!(!state.listening);
        assert!(!state.closing);
        let Transition::Stop(third_stop) = state.toggle(true) else {
            panic!("visible panel after failed close dispatch must still stop/hide on next press");
        };
        assert!(third_stop > second_stop);
        assert!(state.commit_stop(third_stop));
        assert!(state.should_hide(third_stop));
        assert!(!state.closing);
    }
}
