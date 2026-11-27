//! Independent short voice input with a visible finish and a native pasteboard write.

use std::sync::{Mutex, MutexGuard};
use tauri::async_runtime::{channel, Sender};
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
                (
                    Point {
                        x: -100.0,
                        y: 1500.0,
                    },
                    1,
                ),
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
                target_screen(
                    Point {
                        x: 1050.0,
                        y: 350.0
                    },
                    &[left, right]
                ),
                Some(left)
            );
            assert_eq!(
                target_screen(
                    Point {
                        x: 1150.0,
                        y: 350.0
                    },
                    &[left, right]
                ),
                Some(right)
            );
            // Far outer edge falls back to the screen it borders.
            assert_eq!(
                target_screen(
                    Point {
                        x: 2200.0,
                        y: 700.0
                    },
                    &[left, right]
                ),
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
            assert_eq!(
                origin,
                Point {
                    x: -640.0,
                    y: -560.0
                }
            );

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

    pub fn reduced_motion() -> bool {
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

    pub fn open(window: &WebviewWindow, reposition: bool) -> Result<(), String> {
        let native = window.ns_window().map_err(|error| error.to_string())?;
        let native = native.cast::<AnyObject>();
        let visible = window.is_visible().map_err(|error| error.to_string())?;
        let seconds = if reduced_motion() { 0.0 } else { 0.12 };
        unsafe {
            if reposition || !visible {
                position(native)?;
            }
            if !visible {
                alpha(native, if seconds > 0.0 { 0.0 } else { 1.0 }, 0.0);
            }
        }
        if !visible {
            window.show().map_err(|error| error.to_string())?;
        }
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

    pub fn restore_alpha(window: &WebviewWindow) {
        if let Ok(native) = window.ns_window() {
            unsafe {
                alpha(native.cast::<AnyObject>(), 1.0, 0.0);
            }
        }
    }

    pub fn close(window: &WebviewWindow) -> Result<Duration, String> {
        let native = window.ns_window().map_err(|error| error.to_string())?;
        if reduced_motion() {
            return Ok(Duration::ZERO);
        }
        unsafe {
            alpha(native.cast::<AnyObject>(), 0.0, 0.10);
        }
        Ok(Duration::from_millis(120))
    }
}

#[derive(Default)]
pub struct DictationShell(Mutex<Requests>);

impl DictationShell {
    fn lock(&self) -> Result<MutexGuard<'_, Requests>, String> {
        self.0.lock().map_err(|error| error.to_string())
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq, serde::Serialize)]
#[serde(tag = "action", rename_all = "snake_case")]
pub enum DictationAction {
    #[default]
    Idle,
    Start,
    Stop,
    Outcome {
        outcome: VisibleOutcome,
    },
}

#[derive(Clone, Debug, Default, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DictationRequest {
    pub revision: u64,
    pub take_id: u64,
    #[serde(flatten)]
    pub action: DictationAction,
}

#[derive(Clone, Copy, serde::Serialize)]
#[serde(rename_all = "snake_case")]
enum DictationEventAction {
    Idle,
    Start,
    Stop,
    Outcome,
}

#[derive(Clone, Copy, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct DictationEvent {
    revision: u64,
    take_id: u64,
    action: DictationEventAction,
}

impl DictationRequest {
    fn notification(&self) -> DictationEvent {
        let action = match self.action {
            DictationAction::Idle => DictationEventAction::Idle,
            DictationAction::Start => DictationEventAction::Start,
            DictationAction::Stop => DictationEventAction::Stop,
            DictationAction::Outcome { .. } => DictationEventAction::Outcome,
        };
        DictationEvent {
            revision: self.revision,
            take_id: self.take_id,
            action,
        }
    }
}

#[derive(serde::Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum DictationOutcome {
    Completed { text: String },
    NoSpeech,
    Cancelled,
    Error { text: String, message: String },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
enum VisibleOutcomeKind {
    NoSpeech,
    Error,
    CopyFailed,
    CopiedPresentationFailed,
    InvalidText,
}

#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct VisibleOutcome {
    kind: VisibleOutcomeKind,
    copied: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<String>,
}

impl VisibleOutcome {
    fn failure(kind: VisibleOutcomeKind, text: Option<String>, message: String) -> Self {
        Self {
            kind,
            copied: kind == VisibleOutcomeKind::CopiedPresentationFailed,
            text,
            message: Some(message),
        }
    }

    fn presentation_failed(copied: bool, message: &str) -> Self {
        if copied {
            Self::failure(
                VisibleOutcomeKind::CopiedPresentationFailed,
                None,
                format!("已复制到剪贴板，但{message}。"),
            )
        } else {
            Self::failure(VisibleOutcomeKind::Error, None, format!("{message}。"))
        }
    }
}

struct VisibleTake {
    take_id: u64,
    revision: u64,
    outcome: VisibleOutcome,
}

impl VisibleTake {
    fn request(&self) -> DictationRequest {
        DictationRequest {
            revision: self.revision,
            take_id: self.take_id,
            action: DictationAction::Outcome {
                outcome: self.outcome.clone(),
            },
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CompleteStatus {
    Applied,
    Ignored,
    CopyFailed,
    InvalidText,
    PresentationFailed,
}

#[derive(Debug, PartialEq, Eq, serde::Serialize)]
pub struct CompleteResult {
    pub status: CompleteStatus,
    pub copied: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    request: Option<DictationRequest>,
}

impl CompleteResult {
    fn new(status: CompleteStatus, copied: bool) -> Self {
        Self {
            status,
            copied,
            request: None,
        }
    }

    fn with_request(mut self, request: Option<DictationRequest>) -> Self {
        self.request = request;
        self
    }
}

type CompletionResult = Result<CompleteResult, String>;
type CompletionReply = Sender<CompletionResult>;

struct StagedCompletion {
    outcome: DictationOutcome,
    reply: CompletionReply,
}

impl StagedCompletion {
    fn ignore(self, copied: bool) {
        let _ = self
            .reply
            .try_send(Ok(CompleteResult::new(CompleteStatus::Ignored, copied)));
    }
}

enum CompletionPresentation {
    StartNext { revision: u64, copied: bool },
    Hide { revision: u64, copied: bool },
}

enum CompletionStep {
    Write(String),
    Present(CompletionPresentation),
    Settled(CompleteResult),
}

struct CompletionWork {
    take_id: u64,
    reply: CompletionReply,
    step: CompletionStep,
}

#[cfg(any(target_os = "macos", test))]
enum Transition {
    Start(u64),
    Stop(DictationRequest),
    Dismiss {
        revision: u64,
        outcome: VisibleOutcome,
    },
}

#[derive(Default)]
enum Phase {
    #[default]
    Idle,
    Opening {
        take_id: u64,
        revision: u64,
        delivering: bool,
        staged_completion: Option<StagedCompletion>,
        previous_outcome: Option<VisibleTake>,
    },
    #[cfg_attr(not(any(target_os = "macos", test)), allow(dead_code))]
    Listening {
        take_id: u64,
    },
    #[cfg_attr(not(any(target_os = "macos", test)), allow(dead_code))]
    Stopping {
        take_id: u64,
        revision: u64,
        queued_start: bool,
        staged_completion: Option<StagedCompletion>,
        previous_outcome: Option<VisibleTake>,
    },
    Finishing {
        take_id: u64,
        queued_start: bool,
        writing_clipboard: bool,
    },
    Outcome(VisibleTake),
    Closing {
        take_id: u64,
        close_revision: u64,
        copied: bool,
    },
}

#[derive(Default)]
struct Requests {
    revision: u64,
    published: DictationRequest,
    phase: Phase,
    #[cfg(any(target_os = "macos", test))]
    pressed: bool,
}

const MAX_CLIPBOARD_BYTES: usize = 200_000;

impl Requests {
    fn next_revision(&mut self) -> u64 {
        self.revision += 1;
        self.revision
    }

    fn current(&self) -> DictationRequest {
        let visible = match &self.phase {
            Phase::Outcome(visible) => Some(visible),
            Phase::Opening {
                previous_outcome, ..
            }
            | Phase::Stopping {
                previous_outcome, ..
            } => previous_outcome.as_ref(),
            _ => None,
        };
        visible.map_or_else(|| self.published.clone(), VisibleTake::request)
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
    fn toggle(&mut self) -> Option<Transition> {
        match &mut self.phase {
            Phase::Idle | Phase::Closing { .. } | Phase::Outcome(_) => {
                Some(Transition::Start(self.begin_start()))
            }
            Phase::Opening { take_id, .. } | Phase::Listening { take_id } => {
                let take_id = *take_id;
                Some(Transition::Stop(self.begin_stop(take_id)))
            }
            Phase::Stopping { queued_start, .. } | Phase::Finishing { queued_start, .. } => {
                *queued_start = !*queued_start;
                None
            }
        }
    }

    #[cfg(any(target_os = "macos", test))]
    fn stop(&mut self) -> Option<Transition> {
        match &mut self.phase {
            Phase::Opening { take_id, .. } | Phase::Listening { take_id } => {
                let take_id = *take_id;
                Some(Transition::Stop(self.begin_stop(take_id)))
            }
            Phase::Outcome(_) => {
                let Phase::Outcome(visible) = std::mem::take(&mut self.phase) else {
                    unreachable!();
                };
                Some(Transition::Dismiss {
                    revision: self.begin_close(visible.take_id, visible.outcome.copied),
                    outcome: visible.outcome,
                })
            }
            Phase::Stopping { queued_start, .. } | Phase::Finishing { queued_start, .. } => {
                *queued_start = false;
                None
            }
            Phase::Idle | Phase::Closing { .. } => None,
        }
    }

    #[cfg(any(target_os = "macos", test))]
    fn stop_for(&mut self, take_id: u64, revision: u64) -> Option<Transition> {
        let displayed = match &self.phase {
            Phase::Opening {
                take_id, revision, ..
            }
            | Phase::Stopping {
                take_id, revision, ..
            } => (*take_id, *revision),
            Phase::Listening { take_id } | Phase::Finishing { take_id, .. } => {
                (*take_id, self.published.revision)
            }
            Phase::Outcome(visible) => (visible.take_id, visible.revision),
            Phase::Idle | Phase::Closing { .. } => return None,
        };
        if displayed == (take_id, revision) {
            self.stop()
        } else {
            None
        }
    }

    fn begin_start(&mut self) -> u64 {
        let revision = self.next_revision();
        let previous_outcome = match std::mem::take(&mut self.phase) {
            Phase::Outcome(visible) => Some(visible),
            _ => None,
        };
        self.phase = Phase::Opening {
            take_id: revision,
            revision,
            delivering: false,
            staged_completion: None,
            previous_outcome,
        };
        revision
    }

    fn pending_start(&self, revision: u64) -> Option<DictationRequest> {
        match self.phase {
            Phase::Opening {
                take_id,
                revision: pending,
                ..
            } if pending == revision => Some(DictationRequest {
                revision,
                take_id,
                action: DictationAction::Start,
            }),
            _ => None,
        }
    }

    fn abort_start(&mut self, revision: u64) -> Option<DictationRequest> {
        let request = self.pending_start(revision)?;
        let Phase::Opening {
            staged_completion,
            previous_outcome,
            ..
        } = std::mem::take(&mut self.phase)
        else {
            unreachable!();
        };

        if let Some(previous) = previous_outcome {
            self.show_outcome(previous.take_id, previous.outcome);
        } else if staged_completion.is_some() {
            // A renderer that already saw Start needs a later revision to discard it.
            let revision = self.next_revision();
            self.publish_idle(request.take_id, revision);
        }
        if let Some(completion) = staged_completion {
            completion.ignore(false);
        }
        Some(self.current())
    }

    #[cfg(any(target_os = "macos", test))]
    fn begin_stop(&mut self, take_id: u64) -> DictationRequest {
        let revision = self.next_revision();
        let (previous_outcome, staged_completion) = match std::mem::take(&mut self.phase) {
            Phase::Opening {
                previous_outcome,
                staged_completion,
                ..
            } => (previous_outcome, staged_completion),
            _ => (None, None),
        };
        self.phase = Phase::Stopping {
            take_id,
            revision,
            queued_start: false,
            staged_completion,
            previous_outcome,
        };
        DictationRequest {
            revision,
            take_id,
            action: DictationAction::Stop,
        }
    }

    fn publish_idle(&mut self, take_id: u64, revision: u64) {
        self.published = DictationRequest {
            revision,
            take_id,
            action: DictationAction::Idle,
        };
    }

    fn show_outcome(&mut self, take_id: u64, outcome: VisibleOutcome) -> DictationRequest {
        let visible = VisibleTake {
            take_id,
            revision: self.next_revision(),
            outcome,
        };
        let request = visible.request();
        self.phase = Phase::Outcome(visible);
        request
    }

    fn begin_close(&mut self, take_id: u64, copied: bool) -> u64 {
        let close_revision = self.next_revision();
        self.publish_idle(take_id, close_revision);
        self.phase = Phase::Closing {
            take_id,
            close_revision,
            copied,
        };
        close_revision
    }

    fn is_closing(&self, revision: u64) -> bool {
        matches!(self.phase, Phase::Closing { close_revision, .. } if close_revision == revision)
    }

    fn finish_close(&mut self, revision: u64) -> Option<DictationRequest> {
        if !self.is_closing(revision) {
            return None;
        }
        self.phase = Phase::Idle;
        Some(self.current())
    }

    fn fail_close(
        &mut self,
        revision: u64,
        previous: Option<VisibleOutcome>,
    ) -> Option<DictationRequest> {
        let Phase::Closing {
            take_id,
            close_revision,
            copied,
        } = self.phase
        else {
            return None;
        };
        if revision != close_revision {
            return None;
        }
        let outcome = previous
            .unwrap_or_else(|| VisibleOutcome::presentation_failed(copied, "无法自动收起窗口"));
        Some(self.show_outcome(take_id, outcome))
    }

    fn finish_take(
        &mut self,
        take_id: u64,
        queued_start: bool,
        copied: bool,
    ) -> CompletionPresentation {
        if queued_start {
            let revision = self.next_revision();
            self.publish_idle(take_id, revision);
            CompletionPresentation::StartNext {
                revision: self.begin_start(),
                copied,
            }
        } else {
            CompletionPresentation::Hide {
                revision: self.begin_close(take_id, copied),
                copied,
            }
        }
    }

    fn accept_completion(
        &mut self,
        take_id: u64,
        completion: StagedCompletion,
    ) -> Option<CompletionWork> {
        let queued_start = match &mut self.phase {
            Phase::Opening {
                take_id: active,
                delivering: true,
                staged_completion,
                ..
            }
            | Phase::Stopping {
                take_id: active,
                staged_completion,
                ..
            } if *active == take_id && staged_completion.is_none() => {
                *staged_completion = Some(completion);
                return None;
            }
            Phase::Listening { take_id: active } if *active == take_id => false,
            Phase::Finishing {
                take_id: active,
                queued_start,
                writing_clipboard: false,
            } if *active == take_id => *queued_start,
            Phase::Outcome(visible)
                if visible.take_id == take_id
                    && visible.outcome.kind == VisibleOutcomeKind::CopyFailed
                    && matches!(
                        &completion.outcome,
                        DictationOutcome::Completed { text }
                            if visible.outcome.text.as_ref() == Some(text)
                    ) =>
            {
                false
            }
            Phase::Outcome(visible) => {
                completion.ignore(visible.take_id == take_id && visible.outcome.copied);
                return None;
            }
            Phase::Closing {
                take_id: active,
                copied,
                ..
            } => {
                completion.ignore(*active == take_id && *copied);
                return None;
            }
            _ => {
                completion.ignore(false);
                return None;
            }
        };

        let StagedCompletion { outcome, reply } = completion;
        let step = match outcome {
            DictationOutcome::Completed { text } => {
                let invalid = if text.len() > MAX_CLIPBOARD_BYTES {
                    Some(format!("文字超过 {MAX_CLIPBOARD_BYTES} 字节，无法复制。"))
                } else if text.trim().is_empty() {
                    Some("没有可复制的语音输入文字。".into())
                } else {
                    None
                };
                if let Some(message) = invalid {
                    let outcome = VisibleOutcome::failure(
                        VisibleOutcomeKind::InvalidText,
                        Some(text),
                        message,
                    );
                    CompletionStep::Settled(self.outcome_result(
                        take_id,
                        CompleteStatus::InvalidText,
                        outcome,
                    ))
                } else {
                    // A renderer mounting during a write sees a stopped take, including for copy retries.
                    if !matches!(self.phase, Phase::Finishing { .. }) {
                        self.published = DictationRequest {
                            revision: self.next_revision(),
                            take_id,
                            action: DictationAction::Stop,
                        };
                    }
                    self.phase = Phase::Finishing {
                        take_id,
                        queued_start,
                        writing_clipboard: true,
                    };
                    CompletionStep::Write(text)
                }
            }
            DictationOutcome::NoSpeech => {
                let outcome = VisibleOutcome {
                    kind: VisibleOutcomeKind::NoSpeech,
                    copied: false,
                    text: None,
                    message: None,
                };
                CompletionStep::Settled(self.outcome_result(
                    take_id,
                    CompleteStatus::Applied,
                    outcome,
                ))
            }
            DictationOutcome::Error { text, message } => {
                let outcome =
                    VisibleOutcome::failure(VisibleOutcomeKind::Error, Some(text), message);
                CompletionStep::Settled(self.outcome_result(
                    take_id,
                    CompleteStatus::Applied,
                    outcome,
                ))
            }
            DictationOutcome::Cancelled => {
                CompletionStep::Present(self.finish_take(take_id, queued_start, false))
            }
        };
        Some(CompletionWork {
            take_id,
            reply,
            step,
        })
    }

    fn outcome_result(
        &mut self,
        take_id: u64,
        status: CompleteStatus,
        outcome: VisibleOutcome,
    ) -> CompleteResult {
        CompleteResult::new(status, outcome.copied)
            .with_request(Some(self.show_outcome(take_id, outcome)))
    }

    fn finish_copy(
        &mut self,
        take_id: u64,
        text: String,
        result: Result<(), String>,
    ) -> Result<CompletionPresentation, CompleteResult> {
        let Phase::Finishing {
            queued_start,
            writing_clipboard: true,
            ..
        } = self.phase
        else {
            unreachable!("a clipboard reservation keeps its take in Finishing");
        };
        match result {
            Ok(()) => Ok(self.finish_take(take_id, queued_start, true)),
            Err(_) => {
                let outcome = VisibleOutcome::failure(
                    VisibleOutcomeKind::CopyFailed,
                    Some(text),
                    "无法完成复制，文字已保留。".into(),
                );
                Err(self.outcome_result(take_id, CompleteStatus::CopyFailed, outcome))
            }
        }
    }
}

#[cfg(any(target_os = "macos", test))]
fn run_start(
    shell: &DictationShell,
    revision: u64,
    open: impl FnOnce() -> Result<(), String>,
    emit: impl FnOnce(DictationRequest) -> Result<(), String>,
) -> Result<Option<CompletionWork>, String> {
    let Some(request) = shell.lock()?.pending_start(revision) else {
        return Ok(None);
    };
    open()?;
    {
        let mut requests = shell.lock()?;
        match &mut requests.phase {
            Phase::Opening {
                revision: pending,
                delivering,
                ..
            } if *pending == revision => {
                *delivering = true;
            }
            _ => return Ok(None),
        }
    }
    emit(request.clone())?;
    let mut requests = shell.lock()?;
    if requests.pending_start(revision).is_none() {
        return Ok(None);
    }
    let Phase::Opening {
        staged_completion, ..
    } = std::mem::take(&mut requests.phase)
    else {
        unreachable!();
    };
    let take_id = request.take_id;
    requests.phase = Phase::Listening { take_id };
    requests.published = request;
    Ok(staged_completion.and_then(|completion| requests.accept_completion(take_id, completion)))
}

#[cfg(any(target_os = "macos", test))]
fn run_stop(
    shell: &DictationShell,
    request: DictationRequest,
    emit: impl FnOnce(DictationRequest) -> Result<(), String>,
    hide_unstarted: impl FnOnce(),
) -> Result<Option<CompletionWork>, String> {
    let delivered = emit(request.clone());
    let mut requests = shell.lock()?;
    let Phase::Stopping {
        take_id,
        revision,
        queued_start,
        staged_completion,
        previous_outcome,
    } = &mut requests.phase
    else {
        return Ok(None);
    };
    if *revision != request.revision {
        return Ok(None);
    }
    let (take_id, queued_start) = (*take_id, *queued_start);
    let staged = staged_completion.take();
    let previous = previous_outcome.take();
    if let Err(error) = delivered {
        let listening = requests.published.action == DictationAction::Start
            && requests.published.take_id == take_id;
        requests.phase = if let Some(previous) = previous {
            Phase::Outcome(previous)
        } else if listening {
            Phase::Listening { take_id }
        } else {
            Phase::Idle
        };
        let unstarted = matches!(requests.phase, Phase::Idle);
        if let Some(completion) = staged {
            completion.ignore(false);
        }
        drop(requests);
        if unstarted {
            hide_unstarted();
        }
        return Err(error);
    }
    requests.published = request;
    requests.phase = Phase::Finishing {
        take_id,
        queued_start,
        writing_clipboard: false,
    };
    // Settle a staged outcome in the same transition that commits Stop.
    Ok(staged.and_then(|completion| requests.accept_completion(take_id, completion)))
}

#[cfg(any(target_os = "macos", test))]
fn report_close_failure(
    shell: &DictationShell,
    revision: u64,
    previous: Option<VisibleOutcome>,
    publish: impl FnOnce(DictationRequest),
) -> Result<(), String> {
    let request = shell.lock()?.fail_close(revision, previous);
    if let Some(request) = request {
        publish(request);
    }
    Ok(())
}

fn run_completion(
    shell: &DictationShell,
    work: CompletionWork,
    write_clipboard: impl FnOnce(&str) -> Result<(), String>,
    start_next: impl FnOnce(u64) -> Result<(), String>,
    schedule_hide: impl FnOnce(u64) -> Result<(), String>,
    publish: impl FnOnce(&DictationRequest),
) {
    let CompletionWork {
        take_id,
        reply,
        step,
    } = work;
    let result = (|| {
        let presentation = match step {
            CompletionStep::Write(text) => {
                let result = write_clipboard(&text);
                match shell.lock()?.finish_copy(take_id, text, result) {
                    Ok(presentation) => presentation,
                    Err(result) => return Ok(result),
                }
            }
            CompletionStep::Present(presentation) => presentation,
            CompletionStep::Settled(result) => return Ok(result),
        };
        let (copied, presented) = match presentation {
            CompletionPresentation::StartNext { revision, copied } => {
                (copied, start_next(revision))
            }
            CompletionPresentation::Hide { revision, copied } => (copied, schedule_hide(revision)),
        };
        if presented.is_ok() {
            return Ok(CompleteResult::new(CompleteStatus::Applied, copied));
        }

        let mut requests = shell.lock()?;
        let restored = match presentation {
            CompletionPresentation::StartNext { revision, .. } => {
                requests.abort_start(revision).map(|_| {
                    requests.show_outcome(
                        take_id,
                        VisibleOutcome::presentation_failed(copied, "无法开始下一次语音输入"),
                    )
                })
            }
            CompletionPresentation::Hide { revision, .. } => requests.fail_close(revision, None),
        };
        Ok(CompleteResult::new(CompleteStatus::PresentationFailed, copied).with_request(restored))
    })();
    if let Ok(CompleteResult {
        request: Some(request),
        ..
    }) = &result
    {
        publish(request);
    }
    let _ = reply.try_send(result);
}

fn voice_panel(app: &tauri::AppHandle) -> Result<WebviewWindow, String> {
    app.get_webview_window(WINDOW_LABEL)
        .ok_or_else(|| "Voice input is unavailable.".into())
}

#[cfg(target_os = "macos")]
fn on_main(
    app: &tauri::AppHandle,
    work: impl FnOnce(&tauri::AppHandle) -> Result<(), String> + Send + 'static,
) -> Result<(), String> {
    if objc2::MainThreadMarker::new().is_some() {
        return work(app);
    }
    let (send, receive) = std::sync::mpsc::sync_channel(1);
    let handle = app.clone();
    app.run_on_main_thread(move || {
        let _ = send.send(work(&handle));
    })
    .map_err(|error| error.to_string())?;
    receive.recv().map_err(|error| error.to_string())?
}

#[cfg(target_os = "macos")]
fn emit_notification(app: &tauri::AppHandle, notification: DictationEvent) -> Result<(), String> {
    app.emit_to(WINDOW_LABEL, REQUEST_EVENT, notification)
        .map_err(|error| error.to_string())
}

fn publish_panel_state(app: &tauri::AppHandle, notification: DictationEvent) {
    #[cfg(target_os = "macos")]
    if emit_notification(app, notification).is_err() {
        log::error!("Could not publish the voice-input panel state");
    }
    #[cfg(not(target_os = "macos"))]
    let _ = (app, notification);
}

#[cfg(target_os = "macos")]
fn present_start(app: &tauri::AppHandle, revision: u64, reposition: bool) -> Result<(), String> {
    let work = run_start(
        &app.state::<DictationShell>(),
        revision,
        || native_window::open(&voice_panel(app)?, reposition),
        |request| emit_notification(app, request.notification()),
    )?;
    dispatch_completion(app, work);
    Ok(())
}

#[cfg(target_os = "macos")]
fn hide_unstarted(app: &tauri::AppHandle) {
    if let Err(error) =
        voice_panel(app).and_then(|window| window.hide().map_err(|error| error.to_string()))
    {
        log::error!("Could not hide the unstarted voice-input window: {error}");
    }
}

fn hide_panel(app: &tauri::AppHandle, revision: u64) -> Result<(), String> {
    let shell = app.state::<DictationShell>();
    if !shell.lock()?.is_closing(revision) {
        return Ok(());
    }
    let window = voice_panel(app)?;
    if let Err(error) = window.hide() {
        #[cfg(target_os = "macos")]
        native_window::restore_alpha(&window);
        return Err(error.to_string());
    }
    let idle = shell.lock()?.finish_close(revision);
    if let Some(request) = idle {
        publish_panel_state(app, request.notification());
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn schedule_close(app: &tauri::AppHandle, revision: u64, animate: bool) -> Result<(), String> {
    on_main(app, move |app| {
        let shell = app.state::<DictationShell>();
        if !shell.lock()?.is_closing(revision) {
            return Ok(());
        }
        if !animate {
            return hide_panel(app, revision);
        }
        let window = voice_panel(app)?;
        let delay = native_window::close(&window)?;
        if delay.is_zero() {
            return hide_panel(app, revision);
        }
        let handle = app.clone();
        if let Err(error) = std::thread::Builder::new().spawn(move || {
            std::thread::sleep(delay);
            let result = on_main(&handle, move |app| hide_panel(app, revision));
            if let Err(error) = result {
                if let Err(error) = report_close_failure(
                    &handle.state::<DictationShell>(),
                    revision,
                    None,
                    |request| publish_panel_state(&handle, request.notification()),
                ) {
                    log::error!("Could not recover the voice-input panel: {error}");
                }
                log::error!("Could not hide the voice-input window: {error}");
            }
        }) {
            native_window::restore_alpha(&window);
            return Err(error.to_string());
        }
        Ok(())
    })
}

fn present_completion(app: &tauri::AppHandle, work: CompletionWork) {
    run_completion(
        &app.state::<DictationShell>(),
        work,
        |text| {
            app.clipboard()
                .write_text(text)
                .map_err(|error| error.to_string())
        },
        |revision| {
            #[cfg(target_os = "macos")]
            return on_main(app, move |app| present_start(app, revision, false)).inspect_err(
                |error| log::error!("Could not start the next voice-input take: {error}"),
            );
            #[cfg(not(target_os = "macos"))]
            {
                let _ = revision;
                Err("Voice input is unavailable.".into())
            }
        },
        |revision| {
            #[cfg(target_os = "macos")]
            return schedule_close(app, revision, true).inspect_err(|error| {
                log::error!("Could not schedule the voice-input window to close: {error}")
            });
            #[cfg(not(target_os = "macos"))]
            hide_panel(app, revision)
        },
        |request| publish_panel_state(app, request.notification()),
    );
}

fn dispatch_completion(app: &tauri::AppHandle, work: Option<CompletionWork>) {
    if let Some(work) = work {
        let handle = app.clone();
        tauri::async_runtime::spawn_blocking(move || present_completion(&handle, work));
    }
}

#[cfg(target_os = "macos")]
fn present_transition(app: &tauri::AppHandle, transition: Transition) -> Result<(), String> {
    let shell = app.state::<DictationShell>();
    match transition {
        Transition::Start(revision) => {
            if let Err(error) = present_start(app, revision, true) {
                let restored = shell.lock()?.abort_start(revision);
                if let Some(request) = restored {
                    if request.action == DictationAction::Idle {
                        hide_unstarted(app);
                    }
                    publish_panel_state(app, request.notification());
                }
                return Err(error);
            }
        }
        Transition::Stop(request) => {
            let work = run_stop(
                &shell,
                request,
                |request| emit_notification(app, request.notification()),
                || hide_unstarted(app),
            )?;
            dispatch_completion(app, work);
        }
        Transition::Dismiss { revision, outcome } => {
            if let Err(error) = schedule_close(app, revision, false) {
                report_close_failure(&shell, revision, Some(outcome), |request| {
                    publish_panel_state(app, request.notification());
                })?;
                return Err(error);
            }
        }
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
    if let Err(error) = on_main(app, move |app| {
        let shell = app.state::<DictationShell>();
        let transition = {
            let mut requests = shell.lock()?;
            if !requests.key(state) {
                return Ok(());
            }
            requests.toggle()
        };
        if let Some(transition) = transition {
            present_transition(app, transition)?;
        }
        Ok(())
    }) {
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
pub fn dictation_request(
    app: tauri::AppHandle,
    window: WebviewWindow,
) -> Result<DictationRequest, String> {
    voice_window(&window)?;
    Ok(app.state::<DictationShell>().lock()?.current())
}

fn voice_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != WINDOW_LABEL {
        return Err("This command belongs to the voice-input window.".into());
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn stop_on_main(app: &tauri::AppHandle, target: Option<(u64, u64)>) -> Result<(), String> {
    on_main(app, move |app| {
        let shell = app.state::<DictationShell>();
        let transition = {
            let mut requests = shell.lock()?;
            match target {
                Some((take_id, revision)) => requests.stop_for(take_id, revision),
                None => requests.stop(),
            }
        };
        if let Some(transition) = transition {
            present_transition(app, transition)?;
        }
        Ok(())
    })
}

#[cfg(target_os = "macos")]
pub fn stop(app: &tauri::AppHandle) -> Result<(), String> {
    stop_on_main(app, None)
}

#[tauri::command]
pub fn stop_dictation_window(
    app: tauri::AppHandle,
    window: WebviewWindow,
    take_id: u64,
    revision: u64,
) -> Result<(), String> {
    voice_window(&window)?;
    #[cfg(target_os = "macos")]
    return stop_on_main(&app, Some((take_id, revision)));
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (app, take_id, revision);
        Ok(())
    }
}

#[tauri::command]
pub async fn complete_dictation(
    app: tauri::AppHandle,
    window: WebviewWindow,
    take_id: u64,
    outcome: DictationOutcome,
) -> Result<CompleteResult, String> {
    voice_window(&window)?;
    settle_take(&app, take_id, outcome).await
}

pub(crate) async fn settle_take(
    app: &tauri::AppHandle,
    take_id: u64,
    outcome: DictationOutcome,
) -> CompletionResult {
    let (reply, mut result) = channel(1);
    let work = app
        .state::<DictationShell>()
        .lock()?
        .accept_completion(take_id, StagedCompletion { outcome, reply });
    dispatch_completion(app, work);
    result
        .recv()
        .await
        .ok_or_else(|| "Voice input could not finish.".to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{cell::Cell, sync::mpsc};
    use tauri::async_runtime::Receiver;

    fn current(shell: &DictationShell) -> DictationRequest {
        shell.lock().unwrap().current()
    }

    fn start_revision(shell: &DictationShell) -> u64 {
        let Some(Transition::Start(revision)) = shell.lock().unwrap().toggle() else {
            panic!("expected Start");
        };
        revision
    }

    fn start(shell: &DictationShell) -> DictationRequest {
        let revision = start_revision(shell);
        assert!(run_start(shell, revision, || Ok(()), |_| Ok(()))
            .unwrap()
            .is_none());
        current(shell)
    }

    fn stop_request(shell: &DictationShell, shortcut: bool) -> DictationRequest {
        let mut requests = shell.lock().unwrap();
        let transition = if shortcut {
            requests.toggle()
        } else {
            requests.stop()
        };
        let Some(Transition::Stop(request)) = transition else {
            panic!("expected Stop");
        };
        request
    }

    fn stop(shell: &DictationShell, shortcut: bool) -> DictationRequest {
        let request = stop_request(shell, shortcut);
        assert!(run_stop(
            shell,
            request.clone(),
            |_| Ok(()),
            || panic!("unexpected hide")
        )
        .unwrap()
        .is_none());
        request
    }

    fn dismiss(shell: &DictationShell) {
        let Some(Transition::Dismiss { revision, .. }) = shell.lock().unwrap().stop() else {
            panic!("expected a visible outcome to dismiss");
        };
        assert!(shell.lock().unwrap().finish_close(revision).is_some());
        assert_eq!(current(shell).action, DictationAction::Idle);
    }

    fn submit(
        shell: &DictationShell,
        take_id: u64,
        outcome: DictationOutcome,
    ) -> (Option<CompletionWork>, Receiver<CompletionResult>) {
        let (reply, result) = channel(1);
        let work = shell
            .lock()
            .unwrap()
            .accept_completion(take_id, StagedCompletion { outcome, reply });
        (work, result)
    }

    fn complete(
        shell: &DictationShell,
        take_id: u64,
        outcome: DictationOutcome,
        write: impl FnOnce(&str) -> Result<(), String>,
        next: impl FnOnce(u64) -> Result<(), String>,
        hide: impl FnOnce(u64) -> Result<(), String>,
    ) -> CompleteResult {
        let (work, mut result) = submit(shell, take_id, outcome);
        if let Some(work) = work {
            run_completion(shell, work, write, next, hide, |_| {});
        }
        result.blocking_recv().unwrap().unwrap()
    }

    fn complete_without_effects(
        shell: &DictationShell,
        take_id: u64,
        outcome: DictationOutcome,
    ) -> CompleteResult {
        complete(
            shell,
            take_id,
            outcome,
            no_write,
            no_presentation,
            no_presentation,
        )
    }

    fn completed(text: &str) -> DictationOutcome {
        DictationOutcome::Completed { text: text.into() }
    }

    fn asr_error() -> DictationOutcome {
        DictationOutcome::Error {
            text: "partial text".into(),
            message: "Provider disconnected".into(),
        }
    }

    fn no_write(_: &str) -> Result<(), String> {
        panic!("unexpected clipboard write");
    }

    fn no_presentation(_: u64) -> Result<(), String> {
        panic!("unexpected native window update");
    }

    fn assert_result(result: &CompleteResult, status: CompleteStatus, copied: bool) {
        assert_eq!((result.status, result.copied), (status, copied));
    }

    fn visible(request: &DictationRequest, kind: VisibleOutcomeKind) -> &VisibleOutcome {
        let DictationAction::Outcome { outcome } = &request.action else {
            panic!("expected Outcome");
        };
        assert_eq!(outcome.kind, kind);
        outcome
    }

    #[test]
    fn held_keys_do_not_toggle_and_stop_publishes_the_same_take_at_a_new_revision() {
        let shell = DictationShell::default();
        assert_eq!(
            serde_json::to_value(current(&shell)).unwrap(),
            serde_json::json!({ "revision": 0, "takeId": 0, "action": "idle" })
        );
        assert!(shell.lock().unwrap().key(ShortcutState::Pressed));
        let first = start(&shell);
        assert_eq!(
            serde_json::to_value(&first).unwrap(),
            serde_json::json!({
                "revision": first.revision, "takeId": first.take_id, "action": "start"
            })
        );
        assert_eq!(first.revision, first.take_id);
        assert!(!shell.lock().unwrap().key(ShortcutState::Pressed));
        assert!(!shell.lock().unwrap().key(ShortcutState::Released));
        assert!(shell.lock().unwrap().key(ShortcutState::Pressed));
        let stopped = stop(&shell, true);
        assert_eq!(stopped.action, DictationAction::Stop);
        assert_eq!(stopped.take_id, first.take_id);
        assert!(stopped.revision > first.revision);
        assert!(matches!(
            shell.lock().unwrap().phase,
            Phase::Finishing {
                writing_clipboard: false,
                ..
            }
        ));
    }

    #[test]
    fn failed_open_and_failed_start_delivery_leave_the_snapshot_idle() {
        let shell = DictationShell::default();
        for open_succeeds in [false, true] {
            let revision = start_revision(&shell);
            let result = run_start(
                &shell,
                revision,
                || {
                    assert_eq!(current(&shell), DictationRequest::default());
                    if open_succeeds {
                        Ok(())
                    } else {
                        Err("no display".into())
                    }
                },
                |request| {
                    assert!(open_succeeds);
                    assert_eq!(request.take_id, revision);
                    assert_eq!(current(&shell), DictationRequest::default());
                    Err("webview unavailable".into())
                },
            );
            assert!(result.is_err());
            assert_eq!(
                shell.lock().unwrap().abort_start(revision),
                Some(DictationRequest::default())
            );
        }
        assert!(start(&shell).revision > 2);
    }

    #[test]
    fn an_error_racing_start_is_settled_after_commit_or_discarded_on_rollback() {
        for delivered in [true, false] {
            let shell = DictationShell::default();
            let revision = start_revision(&shell);
            let mut reply = None;
            let result = run_start(
                &shell,
                revision,
                || Ok(()),
                |request| {
                    assert_eq!(current(&shell), DictationRequest::default());
                    let (work, mut pending) = submit(&shell, request.take_id, asr_error());
                    assert!(work.is_none());
                    assert!(pending.try_recv().is_err());
                    reply = Some(pending);
                    if delivered {
                        Ok(())
                    } else {
                        Err("delivery failed".into())
                    }
                },
            );
            let mut reply = reply.unwrap();
            assert!(reply.try_recv().is_err());
            if delivered {
                let work = result
                    .unwrap()
                    .expect("committed Start settles its staged error");
                let mut published = None;
                run_completion(
                    &shell,
                    work,
                    no_write,
                    no_presentation,
                    no_presentation,
                    |request| {
                        assert_eq!(*request, current(&shell));
                        published = Some(request.clone());
                    },
                );
                assert_result(
                    &reply.blocking_recv().unwrap().unwrap(),
                    CompleteStatus::Applied,
                    false,
                );
                let published = published.unwrap();
                assert_eq!(published.take_id, revision);
                assert_eq!(
                    visible(&published, VisibleOutcomeKind::Error)
                        .text
                        .as_deref(),
                    Some("partial text")
                );
            } else {
                assert!(result.is_err());
                let restored = shell.lock().unwrap().abort_start(revision).unwrap();
                assert_eq!(restored.action, DictationAction::Idle);
                assert!(restored.revision > revision);
                assert_result(
                    &reply.blocking_recv().unwrap().unwrap(),
                    CompleteStatus::Ignored,
                    false,
                );
                assert_result(
                    &complete_without_effects(&shell, revision, asr_error()),
                    CompleteStatus::Ignored,
                    false,
                );
                assert!(start(&shell).revision > restored.revision);
            }
        }
    }

    #[test]
    fn stop_can_preempt_an_open_that_has_not_published_a_start() {
        let shell = DictationShell::default();
        let revision = start_revision(&shell);
        let stopped = stop(&shell, false);
        assert_eq!(stopped.take_id, revision);
        assert_eq!(current(&shell), stopped);
        assert!(run_start(
            &shell,
            revision,
            || panic!("a superseded Start must not open the panel"),
            |_| panic!("a superseded Start must not be emitted"),
        )
        .unwrap()
        .is_none());
        let result = complete(
            &shell,
            stopped.take_id,
            DictationOutcome::Cancelled,
            no_write,
            no_presentation,
            |revision| {
                assert!(shell.lock().unwrap().finish_close(revision).is_some());
                Ok(())
            },
        );
        assert_result(&result, CompleteStatus::Applied, false);
    }

    #[test]
    fn a_completion_during_stop_publication_waits_to_write_until_stop_commits() {
        let shell = DictationShell::default();
        let first = start(&shell);
        let request = stop_request(&shell, true);
        let mut result = None;
        let work = run_stop(
            &shell,
            request.clone(),
            |_| {
                assert_eq!(current(&shell), first);
                let (work, mut reply) = submit(&shell, first.take_id, completed("final text"));
                assert!(work.is_none());
                assert!(reply.try_recv().is_err());
                result = Some(reply);
                Ok(())
            },
            || panic!("unexpected hide"),
        )
        .unwrap()
        .unwrap();
        let mut result = result.unwrap();
        assert!(result.try_recv().is_err());
        run_completion(
            &shell,
            work,
            |text| {
                assert_eq!(text, "final text");
                assert_eq!(current(&shell), request);
                Ok(())
            },
            no_presentation,
            |_| Ok(()),
            |_| {},
        );
        assert_result(
            &result.blocking_recv().unwrap().unwrap(),
            CompleteStatus::Applied,
            true,
        );
    }

    #[test]
    fn a_staged_asr_error_can_be_resubmitted_after_failed_then_successful_stop() {
        let shell = DictationShell::default();
        let first = start(&shell);
        let request = stop_request(&shell, true);
        let mut result = None;
        let delivery = run_stop(
            &shell,
            request,
            |_| {
                let (work, reply) = submit(&shell, first.take_id, asr_error());
                assert!(work.is_none());
                result = Some(reply);
                Err("webview unavailable".into())
            },
            || panic!("failed Stop must not hide an active take"),
        );
        assert!(matches!(delivery, Err(ref error) if error == "webview unavailable"));
        let ignored = result.unwrap().blocking_recv().unwrap().unwrap();
        assert_result(&ignored, CompleteStatus::Ignored, false);
        assert_eq!(current(&shell), first);
        assert_eq!(stop(&shell, false).take_id, first.take_id);

        let settled = complete_without_effects(&shell, first.take_id, asr_error());
        assert_result(&settled, CompleteStatus::Applied, false);
        assert_eq!(settled.request, Some(current(&shell)));
        visible(&current(&shell), VisibleOutcomeKind::Error);
        assert!(start(&shell).take_id > first.take_id);
    }

    #[test]
    fn every_stop_entry_point_keeps_the_panel_visible_during_a_pending_write() {
        for shortcut in [true, false] {
            let shell = DictationShell::default();
            let first = start(&shell);
            stop(&shell, shortcut);
            let (writing, started) = mpsc::channel();
            let (release, written) = mpsc::channel();
            let (hiding, hidden) = mpsc::channel();
            std::thread::scope(|scope| {
                let shell = &shell;
                let worker = scope.spawn(move || {
                    complete(
                        shell,
                        first.take_id,
                        completed("final text"),
                        |text| {
                            assert_eq!(text, "final text");
                            writing.send(()).unwrap();
                            written.recv().unwrap();
                            Ok(())
                        },
                        no_presentation,
                        |revision| {
                            assert!(shell.lock().unwrap().is_closing(revision));
                            hiding.send(()).unwrap();
                            Ok(())
                        },
                    )
                });

                started.recv().unwrap();
                assert!(matches!(
                    shell.lock().unwrap().phase,
                    Phase::Finishing {
                        writing_clipboard: true,
                        ..
                    }
                ));
                assert!(hidden.try_recv().is_err());
                let duplicate =
                    complete_without_effects(shell, first.take_id, completed("duplicate"));
                assert_result(&duplicate, CompleteStatus::Ignored, false);
                assert!(shell.lock().unwrap().toggle().is_none());
                assert!(shell.lock().unwrap().stop().is_none());
                assert!(hidden.try_recv().is_err());
                release.send(()).unwrap();
                assert_result(&worker.join().unwrap(), CompleteStatus::Applied, true);
                assert_eq!(hidden.try_recv(), Ok(()));
            });
        }
    }

    #[test]
    fn successful_copy_starts_the_queued_take_without_hiding_and_rejects_stale_completions() {
        let shell = DictationShell::default();
        let first = start(&shell);
        stop(&shell, true);
        assert!(shell.lock().unwrap().toggle().is_none());
        let wrote = Cell::new(false);

        let result = complete(
            &shell,
            first.take_id,
            completed("first take"),
            |_| {
                wrote.set(true);
                Ok(())
            },
            |revision| {
                assert!(wrote.get());
                let stale = complete_without_effects(&shell, first.take_id, completed("stale"));
                assert_result(&stale, CompleteStatus::Ignored, false);
                run_start(&shell, revision, || Ok(()), |_| Ok(()))
                    .map(|work| assert!(work.is_none()))
            },
            no_presentation,
        );
        assert_result(&result, CompleteStatus::Applied, true);

        let next = current(&shell);
        assert_eq!(next.action, DictationAction::Start);
        assert!(next.take_id > first.take_id);
        let stale = complete_without_effects(&shell, first.take_id, DictationOutcome::Cancelled);
        assert_result(&stale, CompleteStatus::Ignored, false);
        assert_eq!(current(&shell), next);
    }

    #[test]
    fn cancelled_takes_follow_the_final_queued_intent() {
        for queued in [true, false] {
            let shell = DictationShell::default();
            let first = start(&shell);
            stop(&shell, true);
            assert!(shell.lock().unwrap().toggle().is_none());
            if !queued {
                assert!(shell.lock().unwrap().toggle().is_none());
            }
            let result = complete(
                &shell,
                first.take_id,
                DictationOutcome::Cancelled,
                no_write,
                |revision| {
                    assert!(queued);
                    run_start(&shell, revision, || Ok(()), |_| Ok(()))
                        .map(|work| assert!(work.is_none()))
                },
                |revision| {
                    assert!(!queued);
                    assert!(shell.lock().unwrap().finish_close(revision).is_some());
                    Ok(())
                },
            );
            assert_result(&result, CompleteStatus::Applied, false);
            assert_eq!(
                current(&shell).action,
                if queued {
                    DictationAction::Start
                } else {
                    DictationAction::Idle
                }
            );
        }
    }

    #[test]
    fn no_speech_and_errors_publish_visible_snapshots_until_dismissed() {
        for (kind, outcome) in [
            (VisibleOutcomeKind::NoSpeech, DictationOutcome::NoSpeech),
            (VisibleOutcomeKind::Error, asr_error()),
        ] {
            let shell = DictationShell::default();
            let first = start(&shell);
            let stopped = stop(&shell, false);
            assert!(shell.lock().unwrap().toggle().is_none());
            let result = complete_without_effects(&shell, first.take_id, outcome);
            assert_result(&result, CompleteStatus::Applied, false);
            let request = current(&shell);
            assert_eq!(result.request, Some(request.clone()));
            assert!(request.revision > stopped.revision);
            assert_eq!(request.take_id, first.take_id);
            assert!(!visible(&request, kind).copied);
            if kind == VisibleOutcomeKind::NoSpeech {
                assert_eq!(
                    serde_json::to_value(request).unwrap()["outcome"],
                    serde_json::json!({ "kind": "no_speech", "copied": false })
                );
            }
            let stale = complete_without_effects(&shell, first.take_id, completed("stale result"));
            assert_result(&stale, CompleteStatus::Ignored, false);
            dismiss(&shell);
        }
    }

    #[test]
    fn outcome_notification_carries_only_identity_while_snapshot_keeps_details() {
        let shell = DictationShell::default();
        let take = start(&shell);
        let result = complete_without_effects(&shell, take.take_id, asr_error());
        let snapshot = current(&shell);

        assert_eq!(
            serde_json::to_value(snapshot.notification()).unwrap(),
            serde_json::json!({
                "revision": snapshot.revision,
                "takeId": take.take_id,
                "action": "outcome",
            }),
        );
        let detail = serde_json::json!({
            "kind": "error",
            "copied": false,
            "text": "partial text",
            "message": "Provider disconnected",
        });
        assert_eq!(serde_json::to_value(snapshot).unwrap()["outcome"], detail);
        let reply = serde_json::to_value(result).unwrap();
        assert_eq!(reply["request"]["outcome"], detail);
        assert!(reply.get("message").is_none());
    }

    #[test]
    fn stale_panel_stop_leaves_the_new_take_and_its_queued_restart_untouched() {
        let shell = DictationShell::default();
        let first = start(&shell);
        complete_without_effects(&shell, first.take_id, DictationOutcome::NoSpeech);
        let outcome = current(&shell);
        let revision = start_revision(&shell);
        assert!(shell
            .lock()
            .unwrap()
            .stop_for(outcome.take_id, outcome.revision)
            .is_none());
        assert!(run_start(&shell, revision, || Ok(()), |_| Ok(()))
            .unwrap()
            .is_none());
        let second = current(&shell);
        assert!(shell
            .lock()
            .unwrap()
            .stop_for(outcome.take_id, outcome.revision)
            .is_none());
        assert_eq!(current(&shell), second);

        let Some(Transition::Stop(stopped)) = shell
            .lock()
            .unwrap()
            .stop_for(second.take_id, second.revision)
        else {
            panic!("the displayed take must be stoppable");
        };
        assert!(run_stop(
            &shell,
            stopped.clone(),
            |_| Ok(()),
            || panic!("unexpected hide")
        )
        .unwrap()
        .is_none());
        let mut requests = shell.lock().unwrap();
        assert!(requests.toggle().is_none());
        assert!(requests
            .stop_for(outcome.take_id, outcome.revision)
            .is_none());
        assert!(requests.stop_for(second.take_id, second.revision).is_none());
        assert!(matches!(
            requests.phase,
            Phase::Finishing {
                queued_start: true,
                ..
            }
        ));
        assert!(requests
            .stop_for(stopped.take_id, stopped.revision)
            .is_none());
        assert!(matches!(
            requests.phase,
            Phase::Finishing {
                queued_start: false,
                ..
            }
        ));
    }

    #[test]
    fn copy_retry_requires_the_retained_bytes() {
        let shell = DictationShell::default();
        let take = start(&shell);
        let failed = complete(
            &shell,
            take.take_id,
            completed("retry this text"),
            |_| Err("pasteboard unavailable".into()),
            no_presentation,
            no_presentation,
        );
        assert_result(&failed, CompleteStatus::CopyFailed, false);
        let retained = current(&shell);
        let mismatched = complete_without_effects(&shell, take.take_id, completed("replacement"));
        assert_result(&mismatched, CompleteStatus::Ignored, false);
        assert_eq!(current(&shell), retained);
    }

    #[test]
    fn failed_copy_remains_retryable_after_failed_open_or_start_delivery() {
        let shell = DictationShell::default();
        let first = start(&shell);
        stop(&shell, false);
        assert!(shell.lock().unwrap().toggle().is_none());
        let text = "keep this text";
        let failed = complete(
            &shell,
            first.take_id,
            completed(text),
            |_| Err("pasteboard unavailable".into()),
            no_presentation,
            no_presentation,
        );
        assert_result(&failed, CompleteStatus::CopyFailed, false);
        let retained = current(&shell);
        assert_eq!(failed.request, Some(retained.clone()));
        assert_eq!(
            visible(&retained, VisibleOutcomeKind::CopyFailed)
                .text
                .as_deref(),
            Some(text)
        );

        let Some(Transition::Dismiss { revision, outcome }) = shell.lock().unwrap().stop() else {
            panic!("expected the failed copy to be dismissible");
        };
        assert_eq!(current(&shell).action, DictationAction::Idle);
        report_close_failure(&shell, revision, Some(outcome), |request| {
            assert_eq!(request.take_id, first.take_id);
            assert_eq!(request.action, retained.action);
        })
        .unwrap();

        for open_succeeds in [false, true] {
            let revision = start_revision(&shell);
            assert_eq!(current(&shell).action, retained.action);
            let failed_start = run_start(
                &shell,
                revision,
                || {
                    if open_succeeds {
                        Ok(())
                    } else {
                        Err("no display".into())
                    }
                },
                |_| Err("start delivery failed".into()),
            );
            assert!(failed_start.is_err());
            let restored = shell.lock().unwrap().abort_start(revision).unwrap();
            assert_eq!(restored.take_id, first.take_id);
            assert_eq!(restored.action, retained.action);
            assert_eq!(current(&shell), restored);
        }

        let wrote = Cell::new(false);
        let retry = complete(
            &shell,
            first.take_id,
            completed(text),
            |actual| {
                assert_eq!(actual, text);
                wrote.set(true);
                Ok(())
            },
            no_presentation,
            |revision| {
                assert!(wrote.get());
                assert!(shell.lock().unwrap().finish_close(revision).is_some());
                Ok(())
            },
        );
        assert_result(&retry, CompleteStatus::Applied, true);
        assert_eq!(current(&shell).action, DictationAction::Idle);
    }

    #[test]
    fn synchronous_hide_failure_reports_copy_and_dismissal_keeps_one_write() {
        let shell = DictationShell::default();
        let first = start(&shell);
        stop(&shell, false);
        let writes = Cell::new(0);
        let result = complete(
            &shell,
            first.take_id,
            completed("single copy"),
            |_| {
                writes.set(writes.get() + 1);
                Ok(())
            },
            no_presentation,
            |_| Err("hide failed".into()),
        );
        assert_result(&result, CompleteStatus::PresentationFailed, true);
        let snapshot = current(&shell);
        assert_eq!(result.request, Some(snapshot.clone()));
        assert_eq!(snapshot.take_id, first.take_id);
        let outcome = visible(&snapshot, VisibleOutcomeKind::CopiedPresentationFailed);
        assert!(outcome.copied);
        assert!(outcome.text.is_none());
        assert!(outcome.message.as_ref().unwrap().contains("已复制到剪贴板"));
        let duplicate = complete_without_effects(&shell, first.take_id, completed("single copy"));
        assert_result(&duplicate, CompleteStatus::Ignored, true);
        dismiss(&shell);
        assert_eq!(writes.get(), 1);
    }

    #[test]
    fn delayed_hide_failure_publishes_a_copied_outcome_that_can_dismiss_or_restart() {
        for restart in [false, true] {
            let shell = DictationShell::default();
            let first = start(&shell);
            stop(&shell, true);
            let mut close_revision = None;
            let writes = Cell::new(0);
            let result = complete(
                &shell,
                first.take_id,
                completed("single copy"),
                |_| {
                    writes.set(writes.get() + 1);
                    Ok(())
                },
                no_presentation,
                |revision| {
                    close_revision = Some(revision);
                    Ok(())
                },
            );
            assert_result(&result, CompleteStatus::Applied, true);
            assert!(result.request.is_none());

            let close_revision = close_revision.unwrap();
            let mut published = None;
            report_close_failure(&shell, close_revision, None, |request| {
                assert_eq!(request, current(&shell));
                published = Some(request);
            })
            .unwrap();
            let published = published.unwrap();
            assert_eq!(published.take_id, first.take_id);
            assert!(published.revision > close_revision);
            assert!(visible(&published, VisibleOutcomeKind::CopiedPresentationFailed).copied);
            if restart {
                assert!(start(&shell).take_id > first.take_id);
            } else {
                dismiss(&shell);
            }
            assert_result(
                &complete_without_effects(&shell, first.take_id, completed("duplicate")),
                CompleteStatus::Ignored,
                false,
            );
            assert_eq!(writes.get(), 1);
        }
    }

    #[test]
    fn failed_queued_start_returns_presentation_failure_after_one_write() {
        let shell = DictationShell::default();
        let first = start(&shell);
        stop(&shell, false);
        assert!(shell.lock().unwrap().toggle().is_none());
        let writes = Cell::new(0);
        let result = complete(
            &shell,
            first.take_id,
            completed("already copied"),
            |_| {
                writes.set(writes.get() + 1);
                Ok(())
            },
            |_| Err("start dispatch failed".into()),
            no_presentation,
        );
        assert_result(&result, CompleteStatus::PresentationFailed, true);
        let outcome = current(&shell);
        assert_eq!(result.request, Some(outcome.clone()));
        assert_eq!(outcome.take_id, first.take_id);
        assert!(visible(&outcome, VisibleOutcomeKind::CopiedPresentationFailed).copied);
        let duplicate =
            complete_without_effects(&shell, first.take_id, completed("already copied"));
        assert_result(&duplicate, CompleteStatus::Ignored, true);
        assert_eq!(writes.get(), 1);
    }

    #[test]
    fn starting_during_close_invalidates_the_old_fade_and_hide_failure() {
        let shell = DictationShell::default();
        let first = start(&shell);
        stop(&shell, true);
        let mut closing = None;
        let result = complete(
            &shell,
            first.take_id,
            DictationOutcome::Cancelled,
            no_write,
            no_presentation,
            |revision| {
                closing = Some(revision);
                Ok(())
            },
        );
        assert_result(&result, CompleteStatus::Applied, false);
        let closing = closing.unwrap();
        assert!(shell.lock().unwrap().is_closing(closing));
        let next = start(&shell);
        assert!(next.revision > closing);
        assert!(!shell.lock().unwrap().is_closing(closing));
        assert!(shell.lock().unwrap().finish_close(closing).is_none());
        report_close_failure(&shell, closing, None, |_| panic!("stale close failure")).unwrap();
        assert_eq!(current(&shell), next);
    }

    #[test]
    fn invalid_text_is_retained_without_a_retry_or_clipboard_write() {
        let shell = DictationShell::default();
        let exact = "é".repeat(MAX_CLIPBOARD_BYTES / 2);
        for invalid in [" ".to_string(), format!("{exact}x")] {
            let take = start(&shell);
            let invalid_result =
                complete_without_effects(&shell, take.take_id, completed(&invalid));
            assert_result(&invalid_result, CompleteStatus::InvalidText, false);
            let request = current(&shell);
            let outcome = visible(&request, VisibleOutcomeKind::InvalidText);
            assert_eq!(outcome.text.as_deref(), Some(invalid.as_str()));
            assert_eq!(invalid_result.request, Some(request));
            let retry = complete_without_effects(&shell, take.take_id, completed(&exact));
            assert_result(&retry, CompleteStatus::Ignored, false);
            dismiss(&shell);
        }

        let take = start(&shell);
        let result = complete(
            &shell,
            take.take_id,
            completed(&exact),
            |text| {
                assert_eq!(text.len(), MAX_CLIPBOARD_BYTES);
                Ok(())
            },
            no_presentation,
            |_| Ok(()),
        );
        assert_result(&result, CompleteStatus::Applied, true);
    }
}
