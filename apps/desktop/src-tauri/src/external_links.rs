//! External links open in the user's own browser or mail app.
//!
//! WebKit asks the app to create a window for `target="_blank"` links; Gunther
//! registers no such handler, so the web layer hands those links here instead.
//! Only http(s) and mailto are accepted, and never as a command-line option.

/// True for a link Gunther may hand to the operating system.
fn is_openable(url: &str) -> bool {
    let lower = url.to_ascii_lowercase();
    (lower.starts_with("https://") || lower.starts_with("http://") || lower.starts_with("mailto:"))
        && url.len() <= 4_096
        && !url
            .chars()
            .any(|character| character.is_control() || character.is_whitespace())
}

#[tauri::command]
pub fn open_external_url(url: String) -> Result<(), String> {
    let url = url.trim();
    if !is_openable(url) {
        return Err("Only web and email links can be opened.".into());
    }
    #[cfg(target_os = "macos")]
    let mut command = std::process::Command::new("/usr/bin/open");
    #[cfg(target_os = "windows")]
    let mut command = {
        let mut command = std::process::Command::new("rundll32");
        command.arg("url.dll,FileProtocolHandler");
        command
    };
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let mut command = std::process::Command::new("xdg-open");
    command
        .arg(url)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("The link could not be opened: {error}"))
}

#[cfg(test)]
mod tests {
    use super::is_openable;

    #[test]
    fn only_web_and_mail_links_are_opened() {
        assert!(is_openable("https://example.com/a?b=1"));
        assert!(is_openable("http://example.com"));
        assert!(is_openable("mailto:someone@example.com"));
        assert!(!is_openable("file:///etc/passwd"));
        assert!(!is_openable("javascript:alert(1)"));
        assert!(!is_openable("-a Calculator"));
        assert!(!is_openable("https://example.com/ with space"));
        assert!(!is_openable("https://example.com/\nsecond"));
    }
}
