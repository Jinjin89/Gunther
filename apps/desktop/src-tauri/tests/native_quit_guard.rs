//! Opt-in macOS integration check: real native menus/windows, no microphone,
//! backend, user workspace, or synthetic operating-system key presses.
#![allow(dead_code)]

#[cfg(target_os = "macos")]
#[path = "../src/application_menu.rs"]
mod application_menu;
#[cfg(target_os = "macos")]
#[allow(unused_imports)]
#[path = "../src/capture_shell.rs"]
mod capture_shell;

#[cfg(target_os = "macos")]
fn main() {
    use std::sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc,
    };
    use tauri::{menu::MenuItemKind, Listener, WebviewUrl, WebviewWindowBuilder};

    fn check_menu(items: Vec<MenuItemKind<tauri::Wry>>) -> usize {
        let mut guarded_quit_items = 0;
        for item in items {
            match item {
                MenuItemKind::Submenu(submenu) => {
                    guarded_quit_items += check_menu(submenu.items().unwrap());
                }
                MenuItemKind::MenuItem(item) => {
                    if item.text().unwrap().starts_with("Quit ") {
                        assert_eq!(item.id().as_ref(), application_menu::QUIT_MENU_ID);
                        guarded_quit_items += 1;
                    }
                }
                MenuItemKind::Predefined(item) => {
                    assert!(
                        !item.text().unwrap().starts_with("Quit "),
                        "A native predefined Quit bypasses the recording guard",
                    );
                }
                _ => {}
            }
        }
        guarded_quit_items
    }

    let requested_exit = Arc::new(AtomicBool::new(false));
    let exited = Arc::new(AtomicBool::new(false));
    let exit_seen = exited.clone();
    let mut context = tauri::generate_context!();
    context.config_mut().identifier = "com.gunther.quit-guard-smoke".into();
    context.config_mut().app.windows.clear();

    let app = tauri::Builder::default()
        .manage(capture_shell::CaptureShell::default())
        .menu(application_menu::build)
        .on_menu_event(|app, event| {
            capture_shell::handle_menu_event(app, event.id().as_ref());
        })
        .setup(|app| {
            assert_eq!(check_menu(app.menu().unwrap().items()?), 1);
            println!("PASS: exactly one custom application Quit; no native Quit bypass");

            let capture = WebviewWindowBuilder::new(
                app,
                capture_shell::CAPTURE_WINDOW_LABEL,
                WebviewUrl::External("about:blank".parse()?),
            )
            .title("Gunther — disposable quit-guard check")
            .visible(false)
            .build()?;
            capture_shell::setup_tray(app)?;
            let blocked_events = Arc::new(AtomicUsize::new(0));
            let event_count = blocked_events.clone();
            app.listen_any(capture_shell::CAPTURE_CONTROL_EVENT, move |event| {
                if event.payload() == "\"quit-blocked\"" {
                    event_count.fetch_add(1, Ordering::SeqCst);
                }
            });

            let mut cases: Vec<_> = [
                "requesting",
                "importing",
                "recording",
                "paused",
                "finalizing",
                "stopped",
                "review",
                "review-ready",
            ]
            .into_iter()
            .map(|phase| capture_shell::CaptureStatus {
                phase: phase.into(),
                ..Default::default()
            })
            .collect();
            for persistence in ["saving", "recovering"] {
                cases.push(capture_shell::CaptureStatus {
                    persistence: Some(persistence.into()),
                    ..Default::default()
                });
            }
            cases.push(capture_shell::CaptureStatus {
                has_unreviewed_work: Some(true),
                ..Default::default()
            });

            for (index, status) in cases.into_iter().enumerate() {
                let label = format!("{} / {:?}", status.phase, status.persistence);
                capture_shell::update_capture_status(app.handle().clone(), status)?;
                capture.hide()?;
                assert!(!capture.is_visible()?);
                capture_shell::handle_menu_event(app.handle(), application_menu::QUIT_MENU_ID);
                assert!(capture.is_visible()?, "{label}: hidden Capture must reopen");
                assert!(
                    capture_shell::blocks_exit(app.handle()),
                    "{label}: guard was released"
                );
                assert_eq!(blocked_events.load(Ordering::SeqCst), index + 1);
                println!("PASS: Quit preserves {label} and reveals Capture");
            }

            capture_shell::update_capture_status(app.handle().clone(), Default::default())?;
            assert!(!capture_shell::blocks_exit(app.handle()));
            capture_shell::handle_menu_event(app.handle(), application_menu::QUIT_MENU_ID);
            Ok(())
        })
        .build(context)
        .expect("native quit-guard fixture must start");

    let quit_seen = requested_exit.clone();
    let exit_code = app.run_return(move |app, event| match event {
        tauri::RunEvent::ExitRequested { code, .. } => {
            assert!(!capture_shell::blocks_exit(app));
            assert_eq!(code, Some(0));
            quit_seen.store(true, Ordering::SeqCst);
        }
        tauri::RunEvent::Exit => exit_seen.store(true, Ordering::SeqCst),
        _ => {}
    });
    assert_eq!(exit_code, 0);
    assert!(requested_exit.load(Ordering::SeqCst));
    assert!(exited.load(Ordering::SeqCst));
    println!("PASS: a clean, idle app can still quit normally");
}

#[cfg(not(target_os = "macos"))]
fn main() {
    println!("SKIP: this native regression check targets macOS Cmd+Q");
}
