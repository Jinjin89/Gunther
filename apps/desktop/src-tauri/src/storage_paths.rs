//! Installation-independent settings and a self-contained local workspace.

use std::path::{Path, PathBuf};
use tauri::Manager;

pub struct StoragePaths {
    pub config: PathBuf,
    pub library: PathBuf,
    pub data: PathBuf,
    pub legacy: PathBuf,
}

pub fn for_home(home: &Path, legacy: &Path, development: bool) -> Result<StoragePaths, String> {
    let name = if development {
        "gunther-dev"
    } else {
        "gunther"
    };
    let config = home.join(".config").join(name);
    crate::create_private_dir(&config).map_err(|error| error.to_string())?;
    // Only the location is needed before the backend migrates the other settings.
    let choice = config.join("config.json");
    if !choice.exists() && !development {
        let old_choice = legacy.join("library-location.json");
        if old_choice.is_file() && !old_choice.is_symlink() {
            let text = std::fs::read_to_string(old_choice).map_err(|error| error.to_string())?;
            let value: serde_json::Value = serde_json::from_str(&text)
                .map_err(|error| format!("The previous library location is unreadable: {error}"))?;
            if value.get("libraryRoot").is_some() {
                let mut file =
                    crate::secure_open(&choice, false).map_err(|error| error.to_string())?;
                std::io::Write::write_all(&mut file, text.as_bytes())
                    .map_err(|error| error.to_string())?;
                file.sync_all().map_err(|error| error.to_string())?;
            }
        }
    }
    let library = crate::library_location::chosen_root(&config)?.unwrap_or_else(|| {
        home.join(if development {
            "Gunther-dev"
        } else {
            "Gunther"
        })
    });
    if library.starts_with(&config) || config.starts_with(&library) {
        return Err("Keep Gunther's configuration and library folders separate.".into());
    }
    Ok(StoragePaths {
        data: library.join(".gunther"),
        config,
        library,
        legacy: legacy.to_path_buf(),
    })
}

pub fn resolve(app: &tauri::AppHandle) -> Result<StoragePaths, String> {
    let home = app.path().home_dir().map_err(|error| error.to_string())?;
    let legacy = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?;
    for_home(&home, &legacy, cfg!(debug_assertions))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reinstall_and_settings_loss_reopen_the_same_workspace() {
        let home = std::env::temp_dir().join(format!("gunther-paths-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home);
        let legacy = home.join("Application Support");
        let first = for_home(&home, &legacy, false).unwrap();
        std::fs::create_dir_all(&first.data).unwrap();
        std::fs::write(first.data.join("gunther.sqlite"), b"existing work").unwrap();
        std::fs::remove_dir_all(&first.config).unwrap();
        let reopened = for_home(&home, &legacy, false).unwrap();
        assert_eq!(first.data, reopened.data);
        assert_eq!(
            std::fs::read(reopened.data.join("gunther.sqlite")).unwrap(),
            b"existing work"
        );
        std::fs::create_dir_all(&legacy).unwrap();
        std::fs::write(
            legacy.join("library-location.json"),
            format!(
                "{{\"libraryRoot\":{}}}",
                serde_json::to_string(&home.join("Elsewhere")).unwrap()
            ),
        )
        .unwrap();
        let migrated = for_home(&home, &legacy, false).unwrap();
        assert_eq!(migrated.library, home.join("Elsewhere"));
        assert!(legacy.join("library-location.json").exists());
        assert_ne!(for_home(&home, &legacy, true).unwrap().data, migrated.data);
        let _ = std::fs::remove_dir_all(home);
    }
}
