//! Native persistence for preferences, pending text and unacknowledged audio.

use std::collections::BTreeMap;
use std::fs;
use std::io::Write;
use std::path::Path;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::Manager;

#[derive(Default)]
pub struct DesktopStorage(pub Mutex<()>);

fn read_map(path: &Path) -> Result<BTreeMap<String, String>, String> {
    if !path.exists() {
        return Ok(BTreeMap::new());
    }
    if path.is_symlink() || !path.is_file() {
        return Err("Gunther storage must be a regular file.".into());
    }
    serde_json::from_slice(&fs::read(path).map_err(|error| error.to_string())?)
        .map_err(|error| format!("Gunther's saved preferences or drafts are unreadable: {error}"))
}

fn atomic_json(path: &Path, value: &impl Serialize) -> Result<(), String> {
    let parent = path.parent().ok_or("Invalid storage path")?;
    crate::create_private_dir(parent).map_err(|error| error.to_string())?;
    let staging = path.with_extension("json.tmp");
    let mut file = crate::secure_open(&staging, false).map_err(|error| error.to_string())?;
    file.set_len(0).map_err(|error| error.to_string())?;
    file.write_all(&serde_json::to_vec(value).map_err(|error| error.to_string())?)
        .map_err(|error| error.to_string())?;
    file.sync_all().map_err(|error| error.to_string())?;
    fs::rename(staging, path).map_err(|error| error.to_string())
}

fn workspace_entry(key: &str) -> bool {
    [
        "gunther:local-captures",
        "gunther:recording",
        "gunther:ask-draft:",
        "gunther:workspace-id",
        "gunther:active-session:",
        "gunther:open-source:",
        "gunther:selected-message:",
        "gunther:home-session",
    ]
    .iter()
    .any(|prefix| key.starts_with(prefix))
}

#[tauri::command]
pub fn desktop_storage_load(
    app: tauri::AppHandle,
    legacy: BTreeMap<String, String>,
) -> Result<BTreeMap<String, String>, String> {
    let state = app.state::<DesktopStorage>();
    let _lock = state.0.lock().map_err(|error| error.to_string())?;
    let paths = crate::storage_paths::resolve(&app)?;
    let preferences = paths.config.join("preferences.json");
    let drafts = paths.data.join("pending").join("web-storage.json");
    let mut settings = read_map(&preferences)?;
    let mut pending = read_map(&drafts)?;
    let import_settings = !preferences.exists();
    let import_pending = !drafts.exists();
    if import_settings || import_pending {
        for (key, value) in legacy {
            if !key.starts_with("gunther:") {
                continue;
            }
            if workspace_entry(&key) {
                if import_pending {
                    pending.insert(key, value);
                }
            } else if import_settings {
                settings.insert(key, value);
            }
        }
        atomic_json(&preferences, &settings)?;
        atomic_json(&drafts, &pending)?;
    }
    settings.extend(pending);
    Ok(settings)
}

#[tauri::command]
pub fn desktop_storage_set(
    app: tauri::AppHandle,
    key: String,
    value: Option<String>,
) -> Result<(), String> {
    if !key.starts_with("gunther:") {
        return Err("Invalid Gunther storage key".into());
    }
    let state = app.state::<DesktopStorage>();
    let _lock = state.0.lock().map_err(|error| error.to_string())?;
    let paths = crate::storage_paths::resolve(&app)?;
    let path = if workspace_entry(&key) {
        paths.data.join("pending").join("web-storage.json")
    } else {
        paths.config.join("preferences.json")
    };
    let mut entries = read_map(&path)?;
    match value {
        Some(value) => {
            entries.insert(key, value);
        }
        None => {
            entries.remove(&key);
        }
    }
    atomic_json(&path, &entries)
}

#[derive(Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SpoolChunk {
    pub session_id: String,
    pub sequence: u64,
    pub bytes: Vec<u8>,
    pub mime_type: String,
    pub checksum: String,
    pub workspace_id: String,
    pub source: String,
    pub created_at: String,
}

fn session_directory(data: &Path, session: &str) -> Result<std::path::PathBuf, String> {
    if session.is_empty()
        || session.len() > 160
        || !session
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return Err("Invalid recording session".into());
    }
    Ok(data.join("pending").join("recordings").join(session))
}

fn read_chunk(path: &Path) -> Result<SpoolChunk, String> {
    if path.is_symlink() || !path.is_file() {
        return Err("Invalid recording spool file".into());
    }
    serde_json::from_slice(&fs::read(path).map_err(|error| error.to_string())?)
        .map_err(|error| error.to_string())
}

fn store_chunk(data: &Path, chunk: &SpoolChunk) -> Result<(), String> {
    let directory = session_directory(data, &chunk.session_id)?;
    let path = directory.join(format!("{:012}.json", chunk.sequence));
    if path.exists() {
        let previous = read_chunk(&path)?;
        if previous.bytes != chunk.bytes
            || previous.workspace_id != chunk.workspace_id
            || previous.source != chunk.source
            || previous.mime_type != chunk.mime_type
        {
            return Err(
                "A recording chunk already exists with different content or ownership.".into(),
            );
        }
        return Ok(());
    }
    atomic_json(&path, chunk)
}

#[tauri::command]
pub fn recording_spool_put(app: tauri::AppHandle, chunk: SpoolChunk) -> Result<(), String> {
    if chunk.workspace_id.is_empty()
        || chunk.bytes.is_empty()
        || !matches!(chunk.source.as_str(), "live" | "import")
    {
        return Err("A recording chunk requires bytes and workspace ownership.".into());
    }
    let state = app.state::<DesktopStorage>();
    let _lock = state.0.lock().map_err(|error| error.to_string())?;
    store_chunk(&crate::storage_paths::resolve(&app)?.data, &chunk)
}

#[tauri::command]
pub fn recording_spool_list(
    app: tauri::AppHandle,
    session_id: String,
) -> Result<Vec<SpoolChunk>, String> {
    let state = app.state::<DesktopStorage>();
    let _lock = state.0.lock().map_err(|error| error.to_string())?;
    let directory = session_directory(&crate::storage_paths::resolve(&app)?.data, &session_id)?;
    if !directory.exists() {
        return Ok(Vec::new());
    }
    if directory.is_symlink() || !directory.is_dir() {
        return Err("Invalid recording spool directory".into());
    }
    let mut chunks = Vec::new();
    for entry in fs::read_dir(directory).map_err(|error| error.to_string())? {
        let path = entry.map_err(|error| error.to_string())?.path();
        if path
            .extension()
            .is_some_and(|extension| extension == "json")
        {
            let chunk = read_chunk(&path)?;
            if chunk.session_id != session_id {
                return Err("Recording session mismatch".into());
            }
            chunks.push(chunk);
        }
    }
    chunks.sort_by_key(|chunk| chunk.sequence);
    Ok(chunks)
}

#[tauri::command]
pub fn recording_spool_delete(
    app: tauri::AppHandle,
    session_id: String,
    sequence: u64,
) -> Result<(), String> {
    let state = app.state::<DesktopStorage>();
    let _lock = state.0.lock().map_err(|error| error.to_string())?;
    let directory = session_directory(&crate::storage_paths::resolve(&app)?.data, &session_id)?;
    let path = directory.join(format!("{sequence:012}.json"));
    if path.exists() {
        read_chunk(&path)?;
        fs::remove_file(path).map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub fn recording_spool_probe(app: tauri::AppHandle) -> Result<(), String> {
    let state = app.state::<DesktopStorage>();
    let _lock = state.0.lock().map_err(|error| error.to_string())?;
    let path = crate::storage_paths::resolve(&app)?
        .data
        .join("pending")
        .join("probe.json");
    atomic_json(&path, &true)?;
    fs::remove_file(path).map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn persisted_recording_chunks_reject_changed_bytes_and_foreign_workspaces() {
        let data = std::env::temp_dir().join(format!("gunther-spool-{}", std::process::id()));
        let _ = fs::remove_dir_all(&data);
        let mut chunk = SpoolChunk {
            session_id: "rec_1".into(),
            sequence: 0,
            bytes: vec![1, 2, 3],
            mime_type: "audio/webm".into(),
            checksum: "test".into(),
            workspace_id: "wsp_1".into(),
            source: "live".into(),
            created_at: "today".into(),
        };
        store_chunk(&data, &chunk).unwrap();
        store_chunk(&data, &chunk).unwrap();
        chunk.bytes.reverse();
        assert!(store_chunk(&data, &chunk).is_err());
        chunk.bytes.reverse();
        chunk.workspace_id = "wsp_other".into();
        assert!(store_chunk(&data, &chunk).is_err());
        assert!(session_directory(&data, "../elsewhere").is_err());
        assert_eq!(
            read_chunk(&data.join("pending/recordings/rec_1/000000000000.json"))
                .unwrap()
                .bytes,
            vec![1, 2, 3]
        );
        let _ = fs::remove_dir_all(data);
    }
}
