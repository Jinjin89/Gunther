use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::Path;

fn require_regular_if_exists(path: &Path) -> io::Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() => Ok(()),
        Ok(_) => Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "private file path must be a regular file",
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

fn private_write_options() -> OpenOptions {
    let mut options = OpenOptions::new();
    options.write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    options
}

#[cfg(unix)]
fn set_private_mode(file: &File) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    file.set_permissions(fs::Permissions::from_mode(0o600))
}

#[cfg(not(unix))]
fn set_private_mode(_: &File) -> io::Result<()> {
    Ok(())
}

pub(crate) fn secure_open(path: &Path, append: bool) -> io::Result<File> {
    require_regular_if_exists(path)?;
    let file = private_write_options()
        .create(true)
        .append(append)
        .open(path)?;
    set_private_mode(&file)?;
    Ok(file)
}

pub(crate) fn write_private_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Invalid storage path"))?;
    crate::create_private_dir(parent)?;
    require_regular_if_exists(path)?;

    let mut nonce = [0; 8];
    getrandom::getrandom(&mut nonce)
        .map_err(|error| io::Error::other(format!("OS randomness unavailable: {error}")))?;
    let staging = parent.join(format!(".gunther-write-{:016x}", u64::from_ne_bytes(nonce)));
    let mut file = private_write_options().create_new(true).open(&staging)?;
    let prepared = set_private_mode(&file)
        .and_then(|()| file.write_all(contents))
        .and_then(|()| file.sync_all());
    drop(file);
    let result = prepared.and_then(|()| fs::rename(&staging, path));
    if result.is_err() {
        let _ = fs::remove_file(&staging);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replaces_private_contents_without_reusing_an_old_staging_path() {
        let root =
            std::env::temp_dir().join(format!("gunther-private-write-{}", std::process::id()));
        let destination = root.join("settings.json");
        write_private_atomic(&destination, b"old settings").unwrap();
        let old_staging = root.join("settings.json.tmp");
        fs::create_dir(&old_staging).unwrap();

        write_private_atomic(&destination, b"new settings").unwrap();
        assert_eq!(fs::read(&destination).unwrap(), b"new settings");
        assert!(old_staging.is_dir());
        assert_eq!(fs::read_dir(&root).unwrap().count(), 2);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(destination).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
        fs::remove_dir_all(root).unwrap();
    }
}
