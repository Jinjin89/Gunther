# Capture

Desktop Capture is available from the app, shortcuts and the menu bar. Choose Note, Web page, Document, Photo, Recording or Table. Each kind keeps its input while you switch. Paste a link or table to suggest a matching kind, or drop a file to attach it. Choose Inbox or a library when saving. See [keyboard shortcuts](DESIGN_SYSTEM.md#keyboard).

## Save and read

Both desktop and phone submit documents and photos through `POST /api/captures/assets`. The receipt identifies the preserved source and queued reading. The backend checks the capture identity, bytes and target before acknowledging it. Exact saved client identity or an exact capture fingerprint can reuse a capture. Ambiguous captures get their own source. Web reading and web capture share public URL, redirect and decode checks. A captured web response stays available with its provenance.

The worker reads the original and publishes the result as described in [Library](LIBRARY.md#readings-and-provenance). Analysis extracts assertions from eligible text captures, keeps reviewed claims through Retry, and reports missing credentials or failure. File capture uses the same file-reading, indexing and summary jobs on both clients. Capture summaries are separate model jobs, with key points citing the captured reading.

Activity shows concurrent work, progress, failed jobs and the source or conversation that a job belongs to. Open a row to return to it. Stop interrupts work that can be stopped. Dismissing completed Activity leaves the source or result in place. [Activity routes](../apps/backend/gunther/activity_api.py)

## Web pages and PDFs

Choose Web page and paste a public PDF URL to save the original and queue a reading. Web capture accepts PDFs up to 64 MB. A page that declares a PDF through `citation_pdf_url` or an alternate PDF link also imports the original when Gunther can fetch it. The capture records the URL you entered and the final PDF URL.

From the Library Inspector, choose **+ Add to library** on a cited web page or a web result under **Also searched**. **Add all cited** captures the missing cited pages in order. [Library guide](LIBRARY.md#library-chat-and-studio)

Capture checks transfer framing and rejects partial PDF responses. If the linked PDF is unavailable or its transfer fails, capture saves the web page and records a notice on the receipt. If the page itself is unavailable, the capture returns an error. Ask requests text formats when it reads a public page.

Saving either recorded URL in the same library reuses the existing source. An exact retry with the same request ID returns the source and any fallback notice, even after restart. Changing the request while reusing that ID returns a conflict. Moving the source to Trash lets you capture it again with a fresh request.

## Recordings

- Desktop keeps unacknowledged audio in a durable spool and sends numbered, hashed chunks to a recording session. The server ledger records the accepted prefix. An import stages its audio and verifies the manifest before retrying an outstanding suffix. Transcript edits, moments and the chosen destination are stored in a revisioned checkpoint. The desktop draft reader also restores saved text when older transcript state is incomplete. [Draft storage](../apps/desktop/src/services/recordingDrafts.ts) · [Audio spool](../apps/desktop/src/services/recordingSpool.ts)
- Finish opens Review. Save recording keeps the audio and the transcript available when saved. Use Transcribe again from the source to process stored audio. Recording duration follows the sample clock and can be updated with a measurement of the saved audio.
- The source API resolves the audio ID, duration and pending measurement status. A current recording header takes precedence, including a header with no ID. For older shapes, an above-transcript reference comes before a final standalone reference. Transcribe again writes the resolved ID into the current header. [Recording metadata](../apps/backend/gunther/recording_metadata.py)
- On mobile, frontmost recording writes a durable WAV while live transcription runs. After interruption or restart the saved audio can be recovered locally and uploaded. In-app minimize keeps the recorder available inside the app. Plan for foreground use on phones.

## Live transcript v2

The authorized socket is `/api/recordings/live?protocol=2`. Desktop recording and dictation send 16 kHz PCM, and the phone sends 24 kHz PCM. The backend resamples as needed for the provider. Qwen can use a live task, and local SenseVoice and compatible HTTP transcription use bounded pieces. See [Transcription settings](SETTINGS.md#transcription).

Recording clients stamp `input_audio_buffer.append` frames with recording offsets. The first stamp sets the clock on each connection. Sample count advances it, with sub-frame stamp differences treated as jitter. The clock excludes paused time and retains real gaps. Pause and Stop send pending PCM, commit the boundary and use the acknowledged flush. Reconnect replays retained frames at their original positions. A known lost segment stays visible after Stop.

Desktop dictation starts at offset zero and advances the clock by sample count using unstamped appends. Stop waits for the flush acknowledgment at the committed sample boundary. Connection loss fails the take and keeps any transcript preview in the panel.

`transcript.piece` carries text, time and final sentence pieces. `transcript.gap` marks a gap, and `transcript.flushed` acknowledges a commit. The clients show time-aligned paragraphs. When available, Analysis groups the transcript, and Listen again can correct saved time ranges using the recording's live audio copy. These are separate from saving the recording. [Server codec](../apps/backend/gunther/realtime.py) · [Desktop projection](../apps/desktop/src/services/liveTranscript.ts) · [Mobile transport](../apps/mobile/lib/data/services/live_transcription_service.dart)

## Dictation and menu bar

Use the microphone in a supported desktop text field to insert a transcript. Ask's composer also supports its dictation shortcut. Dictation uses its own transcription job. [Desktop shortcut registry](../apps/desktop/src/shortcuts/shortcuts.ts)

On macOS, press Option+Space to open the dictation panel while your current app keeps focus. The panel shows **正在启动…** while the microphone starts and **正在听…** once it receives audio. It opens on the display under the pointer, above the usable lower edge, and works across displays with different scales.

Press Option+Space again or click Stop to release the microphone. The panel keeps the preview visible under **正在整理…** while transcription and the clipboard write finish. The panel closes after the clipboard write succeeds. You can then paste into the app that still has focus. Press Option+Space during finishing to queue a new take in the same panel after the copy. Press it again to withdraw that request.

While the panel says **正在启动…**, Stop cancels the take if microphone permission or audio context startup is pending. The panel closes immediately or opens the queued take. Once the capture graph is wired, Stop commits even if zero audio blocks have arrived. The panel shows **未识别到语音** if no speech was heard.

Desktop dictation commits all blocks delivered by its 1,024-frame ScriptProcessor. Stop disconnects the processor immediately and can leave an in-flight block undelivered. One block spans 21.3 ms at 48 kHz, 23.2 ms at 44.1 kHz, or 64 ms at 16 kHz. Device buffering and delayed main-thread callbacks can add to that interval.

No speech, capture or transcription failure, and clipboard write failure each clear a queued restart and keep the panel visible until you dismiss it or start again. A retryable clipboard failure keeps the text available and **重试复制** retries the write. If the copy succeeds but the window fails to close or start the next take, the panel confirms the copy and offers dismissal.

The menu-bar G has idle, draft, preparing, recording, paused, review and attention states. Recording shows a red light with elapsed time, and pause uses amber bars. The menu offers the actions for the current state, including Pause, Resume, Mark Moment, Finish and Show Recorder. Settings can show the item only during work. A preference change applies after it is saved. Hiding Capture keeps an active recording going. Quit checks for unsaved or active capture work. [Tray states SVG](CAPTURE_TRAY_STATES.svg) · [Native capture shell](../apps/desktop/src-tauri/src/capture_shell.rs)

## Mobile capture

The phone's v3 outbox stages originals in app-owned storage, checks size and hash and writes its manifest atomically. Saved older entries migrate after file and manifest checks. A pending capture binds to its verified workspace and connection profile before its first upload. Switching profiles leaves that binding intact. Network errors and interrupted uploads are retryable. The app cleans up staged originals after the server acknowledges receipt.

Android and iOS share ingress hand off text, links, photos and files through the outbox. The native queue acknowledges a share after the durable entry is committed. An unassigned share can wait for pairing. Use the pending capture or maintenance view when a share or upload needs attention. [Pairing and recovery](OPERATIONS.md#recovery)
