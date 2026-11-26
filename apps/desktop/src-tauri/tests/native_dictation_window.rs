//! Real native shortcut dispatch and panel placement; blank content, no backend or microphone.
#![allow(dead_code)]

#[cfg(target_os = "macos")]
#[allow(unused_imports)]
#[path = "../src/dictation_shell.rs"]
mod dictation_shell;

#[cfg(target_os = "macos")]
mod check {
    use crate::dictation_shell;
    use objc2::{
        msg_send,
        runtime::{AnyClass, AnyObject},
    };
    use objc2_foundation::{NSPoint, NSRect};
    use std::{
        ffi::c_void,
        sync::{
            atomic::{AtomicBool, Ordering},
            Arc,
        },
        time::Duration,
    };
    use tauri::Manager;
    use tauri_plugin_global_shortcut::{Code, Modifiers, Shortcut};

    #[repr(C)]
    struct HotKeyId {
        signature: u32,
        id: u32,
    }
    #[link(name = "Carbon", kind = "framework")]
    extern "C" {
        fn GetApplicationEventTarget() -> *mut c_void;
        fn CreateEvent(
            allocator: *const c_void,
            class: u32,
            kind: u32,
            time: f64,
            attributes: u32,
            event: *mut *mut c_void,
        ) -> i32;
        fn SetEventParameter(
            event: *mut c_void,
            name: u32,
            kind: u32,
            size: usize,
            data: *const c_void,
        ) -> i32;
        fn SendEventToEventTarget(event: *mut c_void, target: *mut c_void) -> i32;
        fn ReleaseEvent(event: *mut c_void);
    }

    fn press() {
        let id = HotKeyId {
            signature: u32::from_be_bytes(*b"htrs"),
            id: Shortcut::new(Some(Modifiers::ALT), Code::Space).id(),
        };
        // Deliver Carbon messages only to this test process, without synthesizing OS keys.
        for kind in [5, 6] {
            unsafe {
                let mut event = std::ptr::null_mut();
                assert_eq!(
                    CreateEvent(
                        std::ptr::null(),
                        u32::from_be_bytes(*b"keyb"),
                        kind,
                        0.0,
                        0,
                        &mut event
                    ),
                    0
                );
                assert_eq!(
                    SetEventParameter(
                        event,
                        u32::from_be_bytes(*b"----"),
                        u32::from_be_bytes(*b"hkid"),
                        std::mem::size_of::<HotKeyId>(),
                        (&id as *const HotKeyId).cast()
                    ),
                    0
                );
                assert_eq!(
                    SendEventToEventTarget(event, GetApplicationEventTarget()),
                    0
                );
                ReleaseEvent(event);
            }
        }
    }

    fn later(app: tauri::AppHandle, task: impl FnOnce(tauri::AppHandle) + Send + 'static) {
        std::thread::spawn(move || {
            // Both native fades finish within 120 ms; allow the event loop one second to settle.
            std::thread::sleep(Duration::from_secs(1));
            let handle = app.clone();
            app.run_on_main_thread(move || task(handle)).unwrap();
        });
    }

    unsafe fn visible_frames() -> Vec<NSRect> {
        let class = AnyClass::get(c"NSScreen").unwrap();
        let screens: *mut AnyObject = msg_send![class, screens];
        let count: usize = msg_send![screens, count];
        (0..count)
            .map(|i| {
                let screen: *mut AnyObject = msg_send![screens, objectAtIndex: i];
                msg_send![screen, visibleFrame]
            })
            .collect()
    }

    fn move_offscreen(app: &tauri::AppHandle) {
        let window = app
            .get_webview_window(dictation_shell::WINDOW_LABEL)
            .unwrap();
        unsafe {
            let native = window.ns_window().unwrap().cast::<AnyObject>();
            let frame: NSRect = msg_send![native, frame];
            let frames = visible_frames();
            let right = frames
                .iter()
                .map(|r| r.origin.x + r.size.width)
                .reduce(f64::max)
                .unwrap();
            let top = frames
                .iter()
                .map(|r| r.origin.y + r.size.height)
                .reduce(f64::max)
                .unwrap();
            let _: () = msg_send![native, setFrameOrigin: NSPoint::new(right + frame.size.width, top + frame.size.height)];
        }
    }

    fn assert_visible(app: &tauri::AppHandle) {
        let window = app
            .get_webview_window(dictation_shell::WINDOW_LABEL)
            .unwrap();
        assert!(window.is_visible().unwrap());
        unsafe {
            let native = window.ns_window().unwrap().cast::<AnyObject>();
            let frame: NSRect = msg_send![native, frame];
            let alpha: f64 = msg_send![native, alphaValue];
            assert_eq!(alpha, 1.0, "the open panel must be opaque");
            assert!(
                visible_frames().iter().any(|r| {
                    frame.origin.x >= r.origin.x
                        && frame.origin.y >= r.origin.y
                        && frame.origin.x + frame.size.width <= r.origin.x + r.size.width
                        && frame.origin.y + frame.size.height <= r.origin.y + r.size.height
                }),
                "voice panel is outside the usable displays: {frame:?}"
            );
            let class = AnyClass::get(c"NSApplication").unwrap();
            let application: *mut AnyObject = msg_send![class, sharedApplication];
            let key: *mut AnyObject = msg_send![application, keyWindow];
            assert_ne!(key, native, "voice input must not take keyboard focus");
        }
        let request =
            serde_json::to_value(dictation_shell::dictation_request(app.clone()).unwrap()).unwrap();
        assert_eq!(request["listening"], true);
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
            .plugin(tauri_plugin_global_shortcut::Builder::new().build())
            .plugin(tauri_plugin_dialog::init())
            .manage(dictation_shell::DictationShell::default())
            .setup(move |app| {
                dictation_shell::setup(app)?;
                later(app.handle().clone(), move |app| {
                    move_offscreen(&app);
                    press();
                    later(app, move |app| {
                        assert_visible(&app);
                        println!("PASS: native shortcut shows an opaque panel inside a usable display without taking focus");
                        press();
                        later(app, move |app| {
                            assert!(!app.get_webview_window(dictation_shell::WINDOW_LABEL).unwrap().is_visible().unwrap());
                            move_offscreen(&app);
                            press();
                            later(app, move |app| {
                                assert_visible(&app);
                                println!("PASS: reopening recovers an off-screen panel");
                                dictation_shell::stop(&app).unwrap();
                                finished.store(true, Ordering::SeqCst);
                                app.exit(0);
                            });
                        });
                    });
                });
                Ok(())
            }).build(context).expect("native dictation fixture must start");
        std::thread::spawn(|| {
            // Bound a stalled event loop; normal checks take four one-second intervals.
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
