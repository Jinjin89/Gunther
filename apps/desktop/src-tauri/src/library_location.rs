//! Where the libraries live, chosen in Settings rather than in an environment variable.
//!
//! The backend keeps reading `LIBRARY_ROOT` from its environment; the app owns the
//! choice. It is remembered in the app's data folder, handed to the bundled backend
//! when it starts, and changing it moves the folder while the backend is stopped.

use std::fs;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

const CHOICE_FILE_NAME: &str = "library-location.json";

#[derive(Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct Choice {
    library_root: Option<PathBuf>,
}

/// The folder chosen in Settings, if any; the backend's own default applies otherwise.
pub fn chosen_root(data_dir: &Path) -> Option<PathBuf> {
    let text = fs::read_to_string(data_dir.join(CHOICE_FILE_NAME)).ok()?;
    let choice: Choice = serde_json::from_str(&text).ok()?;
    choice.library_root.filter(|path| path.is_absolute())
}

pub fn save_choice(data_dir: &Path, root: &Path) -> Result<(), String> {
    let text = serde_json::to_string_pretty(&Choice {
        library_root: Some(root.to_path_buf()),
    })
    .map_err(|error| error.to_string())?;
    let temporary = data_dir.join(format!("{CHOICE_FILE_NAME}.tmp"));
    fs::write(&temporary, text)
        .and_then(|()| fs::rename(&temporary, data_dir.join(CHOICE_FILE_NAME)))
        .map_err(|error| format!("The new location could not be remembered: {error}"))
}

fn is_empty_dir(path: &Path) -> bool {
    fs::read_dir(path)
        .map(|mut entries| entries.next().is_none())
        .unwrap_or(false)
}

/// Where the libraries go when `picked` is chosen: the folder itself if it is empty or
/// new, otherwise a folder inside it named like the current one, so nothing is mixed in.
pub fn destination(from: &Path, picked: &Path) -> PathBuf {
    if picked.is_dir() && !is_empty_dir(picked) {
        let name = from
            .file_name()
            .map(|name| name.to_os_string())
            .unwrap_or_else(|| "Gunther".into());
        return picked.join(name);
    }
    picked.to_path_buf()
}

/// Why a folder cannot hold the libraries, in words for Settings; None when it can.
pub fn problem_with(from: &Path, to: &Path, data_dir: &Path) -> Option<String> {
    if !to.is_absolute() {
        return Some("Choose a folder by its full path.".into());
    }
    if to == from {
        return Some("Your libraries are already there.".into());
    }
    if to.starts_with(from) {
        return Some("The new place cannot be inside the current library folder.".into());
    }
    if from.starts_with(to) {
        return Some("The new place cannot contain the current library folder.".into());
    }
    if to.starts_with(data_dir) {
        return Some("Choose a folder outside Gunther's private data.".into());
    }
    if to.exists() && !(to.is_dir() && is_empty_dir(to)) {
        return Some(
            "That folder is not empty. Choose an empty folder, or a new name, so nothing of yours is mixed in."
                .into(),
        );
    }
    None
}

fn copy_tree(from: &Path, to: &Path) -> std::io::Result<()> {
    fs::create_dir_all(to)?;
    for entry in fs::read_dir(from)? {
        let entry = entry?;
        let kind = entry.file_type()?;
        let target = to.join(entry.file_name());
        if kind.is_dir() {
            copy_tree(&entry.path(), &target)?;
        } else if kind.is_symlink() {
            #[cfg(unix)]
            std::os::unix::fs::symlink(fs::read_link(entry.path())?, &target)?;
        } else {
            fs::copy(entry.path(), &target)?;
        }
    }
    Ok(())
}

/// Move the library folder. A rename when both are on one disk; otherwise copy
/// everything, and only then remove the original, so a failure leaves it whole.
pub fn move_library(from: &Path, to: &Path) -> Result<(), String> {
    if let Some(parent) = to.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("The new folder could not be made: {error}"))?;
    }
    if !from.exists() {
        return fs::create_dir_all(to)
            .map_err(|error| format!("The new folder could not be made: {error}"));
    }
    if to.exists() {
        // An empty folder chosen in the picker: rename replaces it on Unix, not everywhere.
        let _ = fs::remove_dir(to);
    }
    match fs::rename(from, to) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == ErrorKind::CrossesDevices => {
            copy_tree(from, to).map_err(|error| {
                let _ = fs::remove_dir_all(to);
                format!("Your libraries could not be copied there, so nothing was moved: {error}")
            })?;
            fs::remove_dir_all(from).map_err(|error| {
                format!(
                    "Your libraries were copied, but the old folder could not be removed: {error}"
                )
            })
        }
        Err(error) => Err(format!("Your libraries could not be moved there: {error}")),
    }
}

#[cfg(test)]
mod tests {
    use super::{chosen_root, destination, move_library, problem_with, save_choice};
    use std::fs;
    use std::path::PathBuf;

    fn scratch(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("gunther-library-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("scratch folder");
        dir
    }

    #[test]
    fn the_choice_is_remembered_in_the_data_folder() {
        let data = scratch("choice");
        assert_eq!(chosen_root(&data), None);
        save_choice(&data, &PathBuf::from("/Users/me/Documents/Gunther")).expect("saved");
        assert_eq!(
            chosen_root(&data),
            Some(PathBuf::from("/Users/me/Documents/Gunther"))
        );
        fs::write(
            data.join("library-location.json"),
            "{\"libraryRoot\":\"relative\"}",
        )
        .unwrap();
        assert_eq!(chosen_root(&data), None);
        let _ = fs::remove_dir_all(&data);
    }

    #[test]
    fn unsafe_places_are_refused() {
        let root = scratch("refuse");
        let from = root.join("Gunther");
        let data = root.join("data");
        fs::create_dir_all(&from).unwrap();
        fs::create_dir_all(root.join("full")).unwrap();
        fs::write(root.join("full").join("mine.txt"), "x").unwrap();
        assert!(problem_with(&from, &from, &data).is_some());
        assert!(problem_with(&from, &from.join("inner"), &data).is_some());
        assert!(problem_with(&from, &root, &data).is_some());
        assert!(problem_with(&from, &data.join("x"), &data).is_some());
        assert!(problem_with(&from, &root.join("full"), &data).is_some());
        assert!(problem_with(&from, &PathBuf::from("relative"), &data).is_some());
        fs::create_dir_all(root.join("empty")).unwrap();
        assert_eq!(problem_with(&from, &root.join("empty"), &data), None);
        assert_eq!(
            problem_with(&from, &root.join("new").join("Gunther"), &data),
            None
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_folder_with_files_gets_a_gunther_folder_inside() {
        let root = scratch("destination");
        let from = root.join("Gunther");
        fs::create_dir_all(root.join("Documents")).unwrap();
        fs::write(root.join("Documents").join("taxes.pdf"), "x").unwrap();
        fs::create_dir_all(root.join("Empty")).unwrap();
        assert_eq!(
            destination(&from, &root.join("Documents")),
            root.join("Documents").join("Gunther")
        );
        assert_eq!(destination(&from, &root.join("Empty")), root.join("Empty"));
        assert_eq!(destination(&from, &root.join("New")), root.join("New"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn moving_keeps_every_file() {
        let root = scratch("move");
        let from = root.join("Gunther");
        fs::create_dir_all(from.join("Cells").join("sources")).unwrap();
        fs::write(from.join("Cells").join("sources").join("a.md"), "CD3D").unwrap();
        fs::create_dir_all(root.join("empty")).unwrap();
        move_library(&from, &root.join("empty")).expect("moved");
        assert!(!from.exists());
        assert_eq!(
            fs::read_to_string(
                root.join("empty")
                    .join("Cells")
                    .join("sources")
                    .join("a.md")
            )
            .unwrap(),
            "CD3D"
        );
        // Nothing there yet: the new place is simply made.
        move_library(&root.join("missing"), &root.join("fresh")).expect("made");
        assert!(root.join("fresh").is_dir());
        let _ = fs::remove_dir_all(&root);
    }
}
