//! macOS keeps Gunther's sound off the Mac's built-in speakers until Gunther may use the
//! microphone; earphones play either way. Gunther is entitled to record, and for such an
//! app macOS checks that permission when it plays through a device it treats as having an
//! input. The web view plays from a helper process that cannot show the question, so it is
//! never asked and the speakers stay silent. The web layer asks here before the first
//! sound; asking does not turn the microphone on.

/// "granted", "denied", "restricted" or "undetermined", after asking if macOS has not asked
/// yet. Other systems need no permission to play sound.
#[tauri::command(async)]
pub fn ask_microphone_access() -> String {
    #[cfg(target_os = "macos")]
    let access = mac::ask();
    #[cfg(not(target_os = "macos"))]
    let access = "granted";
    access.to_string()
}

#[cfg(target_os = "macos")]
mod mac {
    use std::sync::mpsc;
    use std::time::Duration;

    use block2::RcBlock;
    use objc2::msg_send;
    use objc2::runtime::{AnyClass, Bool};
    use objc2_foundation::NSString;

    #[link(name = "AVFoundation", kind = "framework")]
    extern "C" {
        static AVMediaTypeAudio: &'static NSString;
    }

    // AVAuthorizationStatus
    const NOT_DETERMINED: isize = 0;
    const RESTRICTED: isize = 1;
    const DENIED: isize = 2;
    const AUTHORIZED: isize = 3;

    /// Time to read the question; the answer is read again either way.
    const ANSWER_WAIT: Duration = Duration::from_secs(300);

    fn status(device: &AnyClass) -> isize {
        unsafe { msg_send![device, authorizationStatusForMediaType: AVMediaTypeAudio] }
    }

    pub fn ask() -> &'static str {
        let Some(device) = AnyClass::get(c"AVCaptureDevice") else {
            return "undetermined";
        };
        if status(device) == NOT_DETERMINED {
            let (answered, answer) = mpsc::channel();
            let handler = RcBlock::new(move |_granted: Bool| {
                let _ = answered.send(());
            });
            unsafe {
                let _: () = msg_send![
                    device,
                    requestAccessForMediaType: AVMediaTypeAudio,
                    completionHandler: &*handler
                ];
            }
            let _ = answer.recv_timeout(ANSWER_WAIT);
        }
        match status(device) {
            AUTHORIZED => "granted",
            DENIED => "denied",
            RESTRICTED => "restricted",
            _ => "undetermined",
        }
    }
}
