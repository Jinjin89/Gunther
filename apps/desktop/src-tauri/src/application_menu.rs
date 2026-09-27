use tauri::{menu::Menu, AppHandle, Runtime};

pub const QUIT_MENU_ID: &str = "quit-gunther";

/// Keep Tauri's standard editing/window menus, but own the macOS Quit action.
/// A predefined Quit sends AppKit's `terminate:` directly and does not pass
/// through Tauri's cancellable `RunEvent::ExitRequested` in our runtime.
pub fn build<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<Menu<R>> {
    let menu = Menu::default(app)?;

    #[cfg(target_os = "macos")]
    {
        use tauri::menu::{AboutMetadata, MenuItem, PredefinedMenuItem, Submenu};

        let quit = MenuItem::with_id(app, QUIT_MENU_ID, "Quit Gunther", true, Some("CmdOrCtrl+Q"))?;
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
                &PredefinedMenuItem::separator(app)?,
                &PredefinedMenuItem::services(app, None)?,
                &PredefinedMenuItem::separator(app)?,
                &PredefinedMenuItem::hide(app, None)?,
                &PredefinedMenuItem::hide_others(app, None)?,
                &PredefinedMenuItem::show_all(app, None)?,
                &PredefinedMenuItem::separator(app)?,
                &quit,
            ],
        )?;

        // The first submenu in Tauri's macOS default menu is the application
        // menu. Remove it (including its native Quit), not just its shortcut.
        menu.remove_at(0)?;
        menu.prepend(&application)?;
    }

    Ok(menu)
}
