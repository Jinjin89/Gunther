//! Opt-in macOS smoke check for dictation panel placement, visibility, alpha and non-key window state.
//! Drives the production toggle and completion paths with blank content.
#![allow(dead_code)]

#[cfg(target_os = "macos")]
#[allow(unused_imports)]
#[path = "../src/dictation_shell.rs"]
mod dictation_shell;

#[cfg(target_os = "macos")]
mod check {
    use crate::dictation_shell::{
        self, native_window, placement, CompleteStatus, DictationAction, DictationOutcome,
        DictationRequest,
    };
    use objc2::{
        msg_send,
        runtime::{AnyClass, AnyObject},
    };
    use objc2_foundation::{NSPoint, NSRect};
    use std::{
        sync::{
            atomic::{AtomicBool, Ordering},
            Arc,
        },
        time::Duration,
    };
    use tauri::Manager;
    use tauri_plugin_global_shortcut::ShortcutState;

    fn toggle(app: &tauri::AppHandle) {
        dictation_shell::handle_shortcut(app, ShortcutState::Pressed);
        dictation_shell::handle_shortcut(app, ShortcutState::Released);
    }

    fn panel(app: &tauri::AppHandle) -> tauri::WebviewWindow {
        app.get_webview_window(dictation_shell::WINDOW_LABEL)
            .unwrap()
    }

    fn request(app: &tauri::AppHandle) -> DictationRequest {
        dictation_shell::dictation_request(app.clone(), panel(app)).unwrap()
    }

    fn expected_visible_area() -> placement::Rect {
        let (pointer, screens) = native_window::displays().unwrap();
        placement::target_screen(pointer, &screens)
            .expect("a usable display must exist for the pointer")
            .visible
    }

    fn later(app: tauri::AppHandle, task: impl FnOnce(tauri::AppHandle) + Send + 'static) {
        std::thread::spawn(move || {
            // Both native fades finish within 120 ms; allow the event loop one second to settle.
            std::thread::sleep(Duration::from_secs(1));
            let handle = app.clone();
            app.run_on_main_thread(move || task(handle)).unwrap();
        });
    }

    fn fits_axis(origin: f64, size: f64, area_origin: f64, area_size: f64) -> bool {
        if size > area_size {
            origin == area_origin
        } else {
            origin >= area_origin && origin + size <= area_origin + area_size
        }
    }

    fn placed_in(frame: NSRect, area: placement::Rect) -> bool {
        fits_axis(
            frame.origin.x,
            frame.size.width,
            area.origin.x,
            area.size.width,
        ) && fits_axis(
            frame.origin.y,
            frame.size.height,
            area.origin.y,
            area.size.height,
        )
    }

    fn intersects(frame: NSRect, area: placement::Rect) -> bool {
        frame.origin.x < area.origin.x + area.size.width
            && frame.origin.x + frame.size.width > area.origin.x
            && frame.origin.y < area.origin.y + area.size.height
            && frame.origin.y + frame.size.height > area.origin.y
    }

    fn move_offscreen(app: &tauri::AppHandle) {
        let window = panel(app);
        let (_, screens) = native_window::displays().unwrap();
        let right = screens
            .iter()
            .map(|screen| screen.bounds.origin.x + screen.bounds.size.width)
            .reduce(f64::max)
            .unwrap();
        let top = screens
            .iter()
            .map(|screen| screen.bounds.origin.y + screen.bounds.size.height)
            .reduce(f64::max)
            .unwrap();
        unsafe {
            let native = window.ns_window().unwrap().cast::<AnyObject>();
            let frame: NSRect = msg_send![native, frame];
            let target = NSPoint::new(right + frame.size.width, top + frame.size.height);
            let _: () = msg_send![native, setFrameOrigin: target];
            let moved: NSRect = msg_send![native, frame];
            assert!(
                !screens
                    .iter()
                    .any(|screen| intersects(moved, screen.bounds)),
                "off-screen displacement still overlaps a display: {moved:?}"
            );
        }
    }

    fn assert_visible(app: &tauri::AppHandle, expected: placement::Rect) {
        let window = panel(app);
        assert!(window.is_visible().unwrap());
        unsafe {
            let native = window.ns_window().unwrap().cast::<AnyObject>();
            let frame: NSRect = msg_send![native, frame];
            let alpha: f64 = msg_send![native, alphaValue];
            assert_eq!(alpha, 1.0, "the open panel must be opaque");
            assert!(
                placed_in(frame, expected),
                "voice panel {frame:?} is outside the pointer display's usable area {expected:?}"
            );
            let class = AnyClass::get(c"NSApplication").unwrap();
            let application: *mut AnyObject = msg_send![class, sharedApplication];
            let key: *mut AnyObject = msg_send![application, keyWindow];
            assert_ne!(key, native, "voice panel must not become keyWindow");
        }
    }

    fn assert_hidden(app: &tauri::AppHandle) {
        assert!(!panel(app).is_visible().unwrap());
        assert_eq!(request(app).action, DictationAction::Idle);
    }

    pub fn run() {
        let complete = Arc::new(AtomicBool::new(false));
        let finished = complete.clone();
        let mut context = tauri::generate_context!();
        context.config_mut().identifier = "com.gunther.dictation-window-smoke".into();
        context
            .config_mut()
            .app
            .windows
            .retain(|w| w.label == dictation_shell::WINDOW_LABEL);
        context.config_mut().app.windows[0].url =
            tauri::WebviewUrl::External("about:blank".parse().unwrap());
        let app = tauri::Builder::default()
            .manage(dictation_shell::DictationShell::default())
            .setup(move |app| {
                dictation_shell::prepare_window(app)?;
                later(app.handle().clone(), move |app| {
                    move_offscreen(&app);
                    let first_area = expected_visible_area();
                    let reduced_motion = native_window::reduced_motion();
                    toggle(&app);
                    if reduced_motion {
                        assert_visible(&app, first_area);
                    }
                    later(app, move |app| {
                        assert_visible(&app, first_area);
                        let first = request(&app);
                        assert_eq!(first.action, DictationAction::Start);
                        println!("PASS: toggle shows an opaque non-key panel on the pointer's display");
                        toggle(&app);
                        later(app, move |app| {
                            assert_visible(&app, first_area);
                            let stopped = request(&app);
                            assert_eq!(stopped.action, DictationAction::Stop);
                            assert_eq!(stopped.take_id, first.take_id);
                            assert!(stopped.revision > first.revision);
                            println!("PASS: Stop leaves the same opaque non-key panel visible");
                            tauri::async_runtime::spawn(async move {
                                let completed = dictation_shell::settle_take(
                                    &app, first.take_id, DictationOutcome::Cancelled,
                                ).await.unwrap();
                                assert_eq!(completed.status, CompleteStatus::Applied);
                                assert!(!completed.copied);
                                if reduced_motion {
                                    let handle = app.clone();
                                    app.run_on_main_thread(move || assert_hidden(&handle)).unwrap();
                                }
                                later(app, move |app| {
                                    assert_hidden(&app);
                                    println!("PASS: completion hides the panel");
                                    move_offscreen(&app);
                                    let second_area = expected_visible_area();
                                    toggle(&app);
                                    later(app, move |app| {
                                        assert_visible(&app, second_area);
                                        let second = request(&app);
                                        assert_eq!(second.action, DictationAction::Start);
                                        assert!(second.take_id > first.take_id);
                                        dictation_shell::stop_dictation_window(
                                            app.clone(), panel(&app), stopped.take_id, stopped.revision,
                                        ).unwrap();
                                        assert_eq!(request(&app), second);
                                        assert_visible(&app, second_area);
                                        println!("PASS: reopening recovers an off-screen panel onto the pointer's display");
                                        finished.store(true, Ordering::SeqCst);
                                        app.exit(0);
                                    });
                                });
                            });
                        });
                    });
                });
                Ok(())
            })
            .build(context)
            .expect("native dictation fixture must start");
        std::thread::spawn(|| {
            // Bound a stalled event loop; normal checks take five one-second intervals.
            std::thread::sleep(Duration::from_secs(15));
            eprintln!("FAIL: native dictation window check timed out");
            std::process::exit(1);
        });
        assert_eq!(app.run_return(|_, _| {}), 0);
        assert!(complete.load(Ordering::SeqCst));
    }
}

#[cfg(target_os = "macos")]
fn main() {
    check::run();
}
#[cfg(not(target_os = "macos"))]
fn main() {
    println!("SKIP: native dictation placement targets macOS");
}
