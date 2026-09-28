use tauri::{
    menu::{
        AboutMetadata, Menu, MenuItem, PredefinedMenuItem, Submenu, HELP_SUBMENU_ID,
        WINDOW_SUBMENU_ID,
    },
    AppHandle, Runtime,
};

use crate::capture_shell::{
    MENU_NEW_NOTE, MENU_NEW_RECORDING, MENU_OPEN_CAPTURE, MENU_SEARCH, MENU_SETTINGS,
    MENU_SHORTCUTS, MENU_TOGGLE_THEME,
};

pub const QUIT_MENU_ID: &str = "quit-gunther";

/// Gunther's menu bar. The same shortcuts work inside the app; listing them
/// here makes them discoverable and lets them work from the Capture window.
///
/// Gunther owns Quit: a predefined Quit sends AppKit's `terminate:` directly
/// and does not pass through Tauri's cancellable `RunEvent::ExitRequested`, so
/// the app menu uses its own Quit item that respects an active recording.
pub fn build<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<Menu<R>> {
    let item = |id: &str, label: &str, accelerator: Option<&str>| {
        MenuItem::with_id(app, id, label, true, accelerator)
    };
    let separator = || PredefinedMenuItem::separator(app);

    let application = Submenu::with_id_and_items(
        app,
        "gunther-application",
        "Gunther",
        true,
        &[
            &PredefinedMenuItem::about(
                app,
                None,
                Some(AboutMetadata {
                    name: Some("Gunther".into()),
                    version: Some(app.package_info().version.to_string()),
                    ..Default::default()
                }),
            )?,
            &separator()?,
            &item(MENU_SETTINGS, "Settings…", Some("CmdOrCtrl+,"))?,
            &separator()?,
            &PredefinedMenuItem::services(app, None)?,
            &separator()?,
            &PredefinedMenuItem::hide(app, None)?,
            &PredefinedMenuItem::hide_others(app, None)?,
            &PredefinedMenuItem::show_all(app, None)?,
            &separator()?,
            &item(QUIT_MENU_ID, "Quit Gunther", Some("CmdOrCtrl+Q"))?,
        ],
    )?;
    let file = Submenu::with_items(
        app,
        "File",
        true,
        &[
            &item(MENU_NEW_NOTE, "New Note", Some("CmdOrCtrl+N"))?,
            &item(MENU_OPEN_CAPTURE, "Capture…", Some("CmdOrCtrl+Shift+C"))?,
            &item(
                MENU_NEW_RECORDING,
                "New Recording",
                Some("CmdOrCtrl+Shift+R"),
            )?,
            &separator()?,
            &item(MENU_SEARCH, "Search…", Some("CmdOrCtrl+K"))?,
            &separator()?,
            &PredefinedMenuItem::close_window(app, None)?,
        ],
    )?;
    let edit = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &separator()?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ],
    )?;
    let view = Submenu::with_items(
        app,
        "View",
        true,
        &[
            &item(MENU_SHORTCUTS, "Keyboard Shortcuts", Some("CmdOrCtrl+/"))?,
            &item(
                MENU_TOGGLE_THEME,
                "Switch Light and Dark",
                Some("CmdOrCtrl+Shift+L"),
            )?,
            &separator()?,
            &PredefinedMenuItem::fullscreen(app, None)?,
        ],
    )?;
    let window = Submenu::with_id_and_items(
        app,
        WINDOW_SUBMENU_ID,
        "Window",
        true,
        &[
            &PredefinedMenuItem::minimize(app, None)?,
            &PredefinedMenuItem::maximize(app, None)?,
            &separator()?,
            &PredefinedMenuItem::close_window(app, None)?,
        ],
    )?;
    let help = Submenu::with_id_and_items(
        app,
        HELP_SUBMENU_ID,
        "Help",
        true,
        &[&item(MENU_SHORTCUTS, "Keyboard Shortcuts", None)?],
    )?;
    Menu::with_items(app, &[&application, &file, &edit, &view, &window, &help])
}
