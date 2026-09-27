import type { CreateKnowledgeBaseInput, KnowledgeSearchResult, RecordingSession, SearchScope, WebSearchResult } from "@gunther/contracts";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent, type MouseEvent as ReactMouseEvent } from "react";
import {
  ArrowRight,
  Bookmark,
  BookOpen,
  Camera,
  Check,
  ChevronRight,
  CircleAlert,
  Copy,
  FileText,
  Globe2,
  Laptop,
  Link2,
  Maximize2,
  MessageSquareText,
  Mic2,
  Minimize2,
  Paperclip,
  Pause,
  Play,
  Search,
  Settings2,
  ShieldCheck,
  Smartphone,
  Sparkles,
  StickyNote,
  Square,
  Table2,
  RefreshCw,
  WifiOff,
  X,
} from "lucide-react";
import { knowledgeBases, type AtlasMode, type KnowledgeBase, type KnowledgeChapter } from "../atlas";
import { knowledgeApi } from "../api";
import { appendStoredCapture } from "../localCaptureQueue";
import {
  DEFAULT_DEVICE_SCOPES,
  buildPairingCopyText,
  findDevicePairedForSession,
  formatAbsoluteTime,
  formatDeviceLastUsed,
  formatPairingExpiry,
  isPairingExpired,
  mobileConnectionFromGateway,
  sortPairedDevices,
  type DevicePairingSession,
  type MobileGatewayStatus,
  type PairedDevice,
} from "../devicePairing";
import { LectureRecorder, loadRecordingDrafts, recordingDraftsForWorkspace, removeRecordingDraft, type LectureRecorderHandle, type RecorderSnapshot, type RecordingDraft } from "./LectureRecorder";
import { CAPTURE_CONTROL_DOM_EVENT, type CaptureControl, type CaptureKind, type RecordingContext } from "../capture/captureTypes";

export type { CaptureKind, RecordingContext } from "../capture/captureTypes";

export type CaptureSheetSurface = "overlay" | "window";

interface CaptureSheetProps {
  open: boolean;
  bases: KnowledgeBase[];
  workspaceId?: string | null;
  resolveWorkspaceId?: () => Promise<string | null>;
  baseId?: string | null;
  chapterId?: string | undefined;
  initialKind?: CaptureKind | null | undefined;
  initialRecordingContext?: RecordingContext | undefined;
  surface?: CaptureSheetSurface;
  onRecordingState?: (active: boolean) => void;
  onRecorderSnapshot?: (snapshot: RecorderSnapshot, title: string, hasUnreviewedWork: boolean) => void;
  onHide?: () => void;
  onClose: (force?: boolean) => void;
  onSearch?: () => void;
  onCaptured: (title: string, baseId: string | null, content: string, kind: CaptureKind, captureId: string, url?: string, workspaceId?: string | null) => void | Promise<void>;
  onAssetCaptured: (file: File, title: string, baseId: string | null, kind: "file" | "image", notes: string, workspaceId?: string | null) => Promise<void>;
}

const recordingContexts: Array<{ id: RecordingContext; label: string; description: string }> = [
  { id: "lecture", label: "Course / lecture", description: "Class, seminar, or teaching session" },
  { id: "meeting", label: "Meeting", description: "Discussion, decisions, and action items" },
  { id: "memo", label: "Voice memo", description: "A quick thought captured in your own words" },
];

const recordingTitle = (context: RecordingContext) => `${context === "lecture" ? "Lecture" : context === "meeting" ? "Meeting" : "Voice memo"} · ${new Date().toLocaleDateString()}`;

const normalizedWebUrl = (value: string): string | null => {
  const raw = value.trim();
  if (!raw) return null;
  try {
    const parsed = new URL(/^[A-Za-z][A-Za-z0-9+.-]*:/.test(raw) ? raw : `https://${raw}`);
    if ((parsed.protocol !== "http:" && parsed.protocol !== "https:") || !parsed.hostname || parsed.username || parsed.password) return null;
    parsed.hash = "";
    return parsed.toString();
  } catch {
    return null;
  }
};

const emptyRecorderSnapshot: RecorderSnapshot = { phase: "idle", seconds: 0, persistence: "idle", transcriptWords: 0, transcriptionLabel: "Live transcript", markedMoments: 0 };

const formatRecorderTime = (seconds: number) => {
  const hours = Math.floor(seconds / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60).toString().padStart(2, "0");
  const remainder = (seconds % 60).toString().padStart(2, "0");
  return hours ? `${hours.toString().padStart(2, "0")}:${minutes}:${remainder}` : `${minutes}:${remainder}`;
};

const captureKinds: Array<{ id: CaptureKind; label: string; description: string; icon: typeof FileText; tone: string }> = [
  { id: "note", label: "Quick note", description: "Write a thought or paste text", icon: FileText, tone: "clay" },
  { id: "file", label: "Document", description: "Import a paper, file, or dataset", icon: Paperclip, tone: "blue" },
  { id: "image", label: "Photo or scan", description: "Keep a page, board, or diagram", icon: Camera, tone: "violet" },
  { id: "link", label: "Web page", description: "Save a link with context", icon: Link2, tone: "sand" },
  { id: "recording", label: "Recording", description: "Record live or import audio", icon: Mic2, tone: "green" },
  { id: "table", label: "Table or data", description: "Paste structured rows", icon: Table2, tone: "web" },
];

export function CaptureSheet({ open, bases, workspaceId = null, resolveWorkspaceId, baseId = null, chapterId, initialKind = null, initialRecordingContext = "lecture", surface = "overlay", onRecordingState, onRecorderSnapshot, onHide, onClose, onSearch, onCaptured, onAssetCaptured }: CaptureSheetProps) {
  const [kind, setKind] = useState<CaptureKind | null>(null);
  const [targetBaseId, setTargetBaseId] = useState(baseId ?? "");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [linkUrl, setLinkUrl] = useState("");
  const [working, setWorking] = useState(false);
  const [fileName, setFileName] = useState("");
  const [fileError, setFileError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [recordingContext, setRecordingContext] = useState<RecordingContext>("lecture");
  const [recordingActive, setRecordingActive] = useState(false);
  const [recordingMinimized, setRecordingMinimized] = useState(false);
  const [recorderSnapshot, setRecorderSnapshot] = useState<RecorderSnapshot>(emptyRecorderSnapshot);
  const [recordingDraftId, setRecordingDraftId] = useState<string | null>(null);
  const [recordingDrafts, setRecordingDrafts] = useState<RecordingDraft[]>([]);
  const [interruptedRecordings, setInterruptedRecordings] = useState<RecordingSession[]>([]);
  const [recoveringRecordingId, setRecoveringRecordingId] = useState<string | null>(null);
  const recorderRef = useRef<LectureRecorderHandle>(null);
  const captureFormRef = useRef<HTMLFormElement>(null);
  const targetBase = bases.find((base) => base.id === targetBaseId);
  const targetLabel = targetBase?.title ?? "Inbox · organize later";
  const chapter = targetBase?.chapters.find((item) => item.id === chapterId);
  const recoverableRecordingDrafts = useMemo(
    () => recordingDraftsForWorkspace(recordingDrafts, workspaceId),
    [recordingDrafts, workspaceId],
  );
  const hasUnreviewedWork = kind !== null
    && kind !== "recording"
    && Boolean(title.trim() || content.trim() || linkUrl.trim() || selectedFile);
  useEffect(() => {
    if (!open) return;
    setTargetBaseId(baseId ?? "");
    setKind(initialKind);
    setRecordingContext(initialRecordingContext);
    setFileName("");
    setFileError(null);
    setSelectedFile(null);
    setLinkUrl("");
    setRecordingActive(false);
    setRecordingMinimized(false);
    setRecorderSnapshot(emptyRecorderSnapshot);
    setRecordingDraftId(null);
    setRecordingDrafts(loadRecordingDrafts());
    onRecordingState?.(false);
    if (initialKind === "recording") {
      setTitle(recordingTitle(initialRecordingContext));
      setContent("");
    } else {
      setTitle("");
      setContent("");
    }
  }, [baseId, initialKind, initialRecordingContext, onRecordingState, open]);
  useEffect(() => {
    if (!open || kind !== "recording" || !workspaceId) {
      setInterruptedRecordings([]);
      return;
    }
    let active = true;
    void knowledgeApi.recordings(workspaceId).then((recordings) => {
      if (!active) return;
      const locallyTracked = new Set(
        loadRecordingDrafts()
          .filter((draft) => draft.workspaceId === workspaceId)
          .map((draft) => draft.recording.id),
      );
      setInterruptedRecordings(recordings.filter((recording) => recording.status === "capturing" && recording.sizeBytes > 0 && !locallyTracked.has(recording.id)));
    }).catch(() => {
      if (active) setInterruptedRecordings([]);
    });
    return () => { active = false; };
  }, [kind, open, workspaceId]);
  const updateLectureContent = useCallback((value: string) => setContent(value), []);
  const updateRecordingDraft = useCallback((draft: RecordingDraft | null) => {
    setRecordingDraftId(draft?.id ?? null);
    setRecordingDrafts(loadRecordingDrafts());
  }, []);
  useEffect(() => {
    const expand = () => setRecordingMinimized(false);
    const minimize = () => setRecordingMinimized(true);
    window.addEventListener("gunther:expand-capture", expand);
    window.addEventListener("gunther:minimize-capture", minimize);
    return () => {
      window.removeEventListener("gunther:expand-capture", expand);
      window.removeEventListener("gunther:minimize-capture", minimize);
    };
  }, []);
  useEffect(() => {
    onRecorderSnapshot?.(recorderSnapshot, title, hasUnreviewedWork);
  }, [hasUnreviewedWork, onRecorderSnapshot, recorderSnapshot, title]);
  useEffect(() => {
    if (surface !== "window") return undefined;
    const control = (event: Event) => {
      const action = (event as CustomEvent<CaptureControl>).detail;
      if (action === "pause") recorderRef.current?.pause();
      else if (action === "resume") recorderRef.current?.resume();
      else if (action === "mark") recorderRef.current?.markMoment();
      else if (action === "finish") recorderRef.current?.stop();
      else if (action === "show") setRecordingMinimized(false);
      else if (action === "quit-blocked") {
        setFileError(
          recorderSnapshot.phase === "stopped"
            ? "This recording is ready to review. Save it or keep it as a draft before quitting Gunther."
            : recorderSnapshot.phase === "requesting" || recorderSnapshot.phase === "importing" || recorderSnapshot.phase === "recording" || recorderSnapshot.phase === "paused"
              ? "Recording is active. Finish it, or leave Gunther running safely in the menu bar."
              : "This Capture has unsaved work. Save it or discard it before quitting Gunther.",
        );
      }
    };
    window.addEventListener(CAPTURE_CONTROL_DOM_EVENT, control);
    return () => window.removeEventListener(CAPTURE_CONTROL_DOM_EVENT, control);
  }, [hasUnreviewedWork, recorderSnapshot.phase, surface]);
  if (!open) return null;

  const restoreRecording = async (draft: RecordingDraft) => {
    setRecoveringRecordingId(draft.recording.id);
    setFileError(null);
    try {
      if (!workspaceId || !draft.workspaceId || draft.workspaceId !== workspaceId) {
        throw new Error("This recording belongs to another or an older unbound workspace. Reopen its original workspace to continue it.");
      }
      const recording = await knowledgeApi.recordingMetadata(draft.recording.id, workspaceId);
      const recovered = { ...draft, recording, updatedAt: new Date().toISOString() };
      setTitle(recovered.title);
      setRecordingContext(recovered.recordingContext);
      if (bases.some((base) => base.id === recovered.knowledgeBaseId)) setTargetBaseId(recovered.knowledgeBaseId);
      setRecordingDraftId(recovered.id);
      recorderRef.current?.restore(recovered);
      setInterruptedRecordings((current) => current.filter((item) => item.id !== recording.id));
    } catch (reason) {
      if (reason instanceof Error && reason.message.toLowerCase().includes("not found")) {
        // Recordings created before the lifecycle ledger are still downloadable by asset id.
        setTitle(draft.title);
        setRecordingContext(draft.recordingContext);
        setRecordingDraftId(draft.id);
        recorderRef.current?.restore(draft);
      } else {
        setFileError(reason instanceof Error ? `This recording could not be recovered: ${reason.message}` : "This recording could not be recovered.");
      }
    } finally {
      setRecoveringRecordingId(null);
    }
  };

  const restoreInterruptedRecording = (recording: RecordingSession) => restoreRecording({
    id: recording.id,
    title: recording.title,
    transcript: recording.transcript,
    summary: null,
    seconds: recording.durationSeconds,
    recording,
    moments: recording.moments,
    recordingContext: recording.recordingContext,
    knowledgeBaseId: recording.knowledgeBaseId ?? targetBaseId,
    workspaceId,
    updatedAt: recording.checkpointedAt ?? recording.updatedAt,
  });

  const ingestFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setFileError(null);
    if (kind === "image") {
      if (!file.type.startsWith("image/")) {
        setFileError("Choose an image from your camera or photo library.");
        return;
      }
      if (file.size > 512 * 1024 * 1024) {
        setFileError("Choose an image smaller than 512 MB.");
        return;
      }
      setSelectedFile(file);
      setFileName(file.name);
      setTitle(file.name.replace(/\.[^.]+$/, ""));
      setContent("");
      return;
    }
    if (file.size > 512 * 1024 * 1024) {
      setFileError("Choose a file smaller than 512 MB.");
      return;
    }
    if (!file.size) {
      setFileError("The selected file is empty.");
      return;
    }
    setSelectedFile(file);
    setFileName(file.name);
    setTitle(file.name.replace(/\.[^.]+$/, ""));
    setContent("");
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const normalizedUrl = kind === "link" ? normalizedWebUrl(linkUrl) : null;
    if (kind === "link" && !normalizedUrl) {
      setFileError("Enter a public http or https web page without embedded credentials.");
      return;
    }
    if (kind !== "link" && !content.trim() && !selectedFile) return;
    setWorking(true);
    setFileError(null);
    try {
      const boundWorkspaceId = workspaceId ?? await resolveWorkspaceId?.().catch(() => null) ?? null;
      if (!boundWorkspaceId) {
        throw new Error("Gunther could not verify this local workspace yet. Keep this window open and retry when the knowledge service reconnects.");
      }
      if ((kind === "file" || kind === "image") && selectedFile) {
        await onAssetCaptured(selectedFile, title || selectedFile.name, targetBaseId || null, kind, content.trim(), boundWorkspaceId);
        return;
      }
      if (!kind) return;
      const captureId = `cap-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      appendStoredCapture({
        id: captureId,
        title,
        content,
        kind,
        baseId: targetBaseId,
        workspaceId: boundWorkspaceId,
        ...(normalizedUrl ? { url: normalizedUrl } : {}),
        createdAt: new Date().toISOString(),
      });
      await onCaptured(kind === "link" ? title : title || "Untitled source", targetBaseId || null, content.trim(), kind, captureId, normalizedUrl ?? undefined, boundWorkspaceId);
      if (kind === "recording" && recordingDraftId) removeRecordingDraft(recordingDraftId);
    } catch (reason) {
      setFileError(reason instanceof Error ? reason.message : "This source could not be preserved. It remains open so you can retry.");
    } finally {
      setWorking(false);
    }
  };

  const recorderIsLive = recorderSnapshot.phase === "recording" || recorderSnapshot.phase === "paused";
  const recorderStatus = recorderSnapshot.phase === "recording" ? "Recording" : recorderSnapshot.phase === "paused" ? "Paused" : recorderSnapshot.phase === "stopped" ? "Ready to file" : recorderSnapshot.phase === "importing" ? "Importing audio" : "Preparing";
  const canSubmit = kind === "link" ? Boolean(normalizedWebUrl(linkUrl)) : Boolean(content.trim() || selectedFile);
  const confirmDiscard = () => !hasUnreviewedWork || window.confirm("Discard this unsaved Capture? This cannot be undone.");
  const clearCaptureInput = () => {
    setTitle("");
    setContent("");
    setLinkUrl("");
    setFileName("");
    setFileError(null);
    setSelectedFile(null);
  };
  const returnToCaptureOptions = () => {
    if (!confirmDiscard()) return;
    clearCaptureInput();
    setKind(null);
  };
  const switchCaptureKind = (nextKind: CaptureKind) => {
    if (nextKind === kind || !confirmDiscard()) return;
    clearCaptureInput();
    setKind(nextKind);
    if (nextKind === "recording") setTitle(recordingTitle(recordingContext));
  };
  const keepDraftAndClose = () => {
    setRecordingActive(false);
    setRecordingMinimized(false);
    onRecordingState?.(false);
    onClose(true);
  };
  const keepInBackground = () => {
    if (surface === "window") {
      onHide?.();
      return;
    }
    setRecordingMinimized(true);
  };
  const closeCaptureSurface = () => {
    if (surface === "window") {
      onHide?.();
      return;
    }
    onClose();
  };
  const startWindowDrag = (event: ReactMouseEvent<HTMLElement>) => {
    if (surface !== "window" || event.button !== 0 || !("__TAURI_INTERNALS__" in window)) return;
    const target = event.target as HTMLElement;
    if (target.closest("button, a, input, select, textarea, [data-no-drag]")) return;
    event.preventDefault();
    void getCurrentWindow().startDragging();
  };
  const hideActionLabel = recorderSnapshot.phase === "stopped" ? "Hide review" : "Hide to menu bar";

  const captureForm = <form ref={captureFormRef} className={`capture-sheet ${surface === "window" ? "is-window-surface" : ""} ${kind === "recording" ? "is-recording-capture" : ""} ${kind === null ? "is-capture-launcher" : ""}`} onMouseDown={(event) => event.stopPropagation()} onSubmit={submit} role={surface === "overlay" ? "dialog" : undefined} aria-modal={surface === "overlay" ? true : undefined} aria-label={kind === "recording" ? "Recording workspace" : "Capture something"}>
        <header data-tauri-drag-region={surface === "window" ? true : undefined} onMouseDown={startWindowDrag}><div data-tauri-drag-region={surface === "window" ? true : undefined}><span className="capture-spark"><Sparkles size={16} /></span><span data-tauri-drag-region={surface === "window" ? true : undefined}><small data-tauri-drag-region={surface === "window" ? true : undefined}>{kind === null ? "One place for every source" : kind === "recording" ? recorderSnapshot.phase === "stopped" ? "Review before saving" : "Persistent recording session" : targetBase ? `Saving to ${targetBase.title}` : "Capture now · organize later"}</small><strong data-tauri-drag-region={surface === "window" ? true : undefined}>{kind === null ? "Capture something" : kind === "recording" ? title || "Record audio" : captureKinds.find((item) => item.id === kind)?.label ?? "New source"}</strong></span></div><div className="capture-header-actions" data-no-drag>{kind !== null && !recordingActive && <button type="button" onClick={returnToCaptureOptions} title="Back to capture options" aria-label="Back to capture options"><ChevronRight className="capture-back-icon" size={16} /></button>}{surface === "window" ? <button type="button" className="capture-hide-to-menu" onClick={keepInBackground} title={hideActionLabel} aria-label={hideActionLabel}><Minimize2 size={15} /><span>{hideActionLabel}</span></button> : <>{kind === "recording" && recordingActive && <button type="button" onClick={keepInBackground} title="Keep recording in the background" aria-label="Minimize recording"><Minimize2 size={16} /></button>}<button type="button" onClick={closeCaptureSurface} disabled={recordingActive} title={recordingActive ? "Save or keep this session as a draft before closing" : undefined} aria-label="Close capture"><X size={17} /></button></>}</div></header>
        <div className="capture-target-row"><label>Destination</label><select value={targetBaseId} disabled={recordingActive} onChange={(event) => setTargetBaseId(event.target.value)}><option value="">Inbox · organize later</option>{bases.map((base) => <option value={base.id} key={base.id}>{base.title}</option>)}</select>{chapter && <span>→ {chapter.title}</span>}<small>{targetBase ? "You can change this later" : "No knowledge base required"}</small></div>
        {kind === null && <div className="capture-launcher-grid">{captureKinds.map(({ id, label, description, icon: Icon, tone }) => <button type="button" key={id} onClick={() => { setKind(id); setFileError(null); setFileName(""); setSelectedFile(null); setLinkUrl(""); if (id === "recording") setTitle(recordingTitle(recordingContext)); }}><span className={`capture-action-icon tone-${tone}`}><Icon size={20} /></span><span><strong>{label}</strong><small>{description}</small></span><ArrowRight size={14} /></button>)}{onSearch && <button type="button" onClick={() => { if (surface === "overlay") onClose(true); onSearch(); }}><span className="capture-action-icon tone-web"><Globe2 size={20} /></span><span><strong>Web search</strong><small>Research, compare, then save</small></span><ArrowRight size={14} /></button>}</div>}
        {kind !== null && <div className="capture-kind-row">{captureKinds.map(({ id, label, icon: Icon }) => <button type="button" key={id} className={kind === id ? "is-active" : ""} disabled={recordingActive} onClick={() => switchCaptureKind(id)}><Icon size={14} />{label}</button>)}</div>}
        {kind === "recording" && <div className="recording-context-row" aria-label="Recording type">{recordingContexts.map((context) => <button type="button" key={context.id} className={recordingContext === context.id ? "is-active" : ""} disabled={recordingActive} onClick={() => { setRecordingContext(context.id); setTitle(recordingTitle(context.id)); }}><span>{context.label}</span><small>{context.description}</small></button>)}</div>}
        {kind === "recording" && recorderSnapshot.phase === "idle" && (recoverableRecordingDrafts.length > 0 || interruptedRecordings.length > 0) && <div className="recording-draft-tray"><header><span><strong>Continue a preserved session</strong><small>Recover the audio first; organize it only when you are ready</small></span><em>{recoverableRecordingDrafts.length + interruptedRecordings.length} saved</em></header><div>{recoverableRecordingDrafts.slice(0, 3).map((draft) => <button type="button" key={draft.id} disabled={recoveringRecordingId === draft.recording.id} onClick={() => void restoreRecording(draft)}><span><strong>{draft.title}</strong><small>{formatRecorderTime(draft.seconds)} · {draft.transcript.trim() ? `${draft.transcript.trim().split(/\s+/).length} words` : "audio preserved"}</small></span><em>{recoveringRecordingId === draft.recording.id ? "Recovering…" : new Date(draft.updatedAt).toLocaleDateString()}</em><ChevronRight size={14} /></button>)}{interruptedRecordings.slice(0, Math.max(0, 3 - recoverableRecordingDrafts.length)).map((recording) => <button type="button" key={recording.id} disabled={recoveringRecordingId === recording.id} onClick={() => void restoreInterruptedRecording(recording)}><span><strong>{recording.title}</strong><small>Interrupted session · {recording.sizeBytes >= 1_048_576 ? `${(recording.sizeBytes / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(recording.sizeBytes / 1024))} KB`} safe</small></span><em>{recoveringRecordingId === recording.id ? "Recovering…" : "Recover"}</em><ChevronRight size={14} /></button>)}</div></div>}
        {(kind === "file" || kind === "image") && <label className="capture-file-picker"><input type="file" accept={kind === "image" ? "image/*" : ".pdf,.docx,.epub,.txt,.md,.markdown,.csv,.tsv,.json,.html,.htm,application/pdf,text/*"} capture={kind === "image" ? "environment" : undefined} onChange={(event) => void ingestFile(event)} /><span>{kind === "image" ? <Camera size={14} /> : <Paperclip size={14} />}<strong>{fileName || (kind === "image" ? "Choose or take a photo" : "Choose a local file")}</strong><small>{kind === "image" ? "Original photo stays in the local asset store" : "PDF · DOCX · EPUB · text · data · up to 512 MB"}</small></span><ArrowRight size={13} /></label>}
        {fileError && <p className="capture-file-error" role="alert"><CircleAlert size={12} />{fileError}</p>}
        {kind === "link" && <input className="capture-title-input capture-url-input" type="text" inputMode="url" autoCapitalize="none" autoCorrect="off" spellCheck={false} value={linkUrl} onChange={(event) => { setLinkUrl(event.target.value); setFileError(null); }} placeholder="https://example.org/article" maxLength={2_048} aria-label="Web page URL" />}
        {kind !== null && <input className="capture-title-input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder={kind === "recording" ? `${recordingContext === "lecture" ? "Course" : recordingContext === "meeting" ? "Meeting" : "Voice memo"} title` : kind === "link" ? "Optional title · otherwise use the page title" : kind === "table" ? "Dataset or table title" : kind === "image" ? "What is this image?" : "Source title"} maxLength={160} aria-label="Title" />}
        {kind !== null && (kind === "recording" ? <LectureRecorder ref={recorderRef} title={title} knowledgeBaseId={targetBaseId} workspaceId={workspaceId} recordingContext={recordingContext} onKnowledgeContent={updateLectureContent} onTitleChange={setTitle} onStatusChange={setRecorderSnapshot} onDraftChange={updateRecordingDraft} onActiveChange={(active) => { setRecordingActive(active); onRecordingState?.(active); }} /> : <textarea value={content} onChange={(event) => setContent(event.target.value)} placeholder={kind === "link" ? "Optional: why are you saving this page, and what should Gunther pay attention to?" : kind === "table" ? "Paste rows from a spreadsheet, including the header…" : kind === "image" ? selectedFile ? "Optional: what does this image contain, and why does it matter?" : "Choose a photo above, then add any context here…" : kind === "file" ? selectedFile ? "Optional: what should Gunther pay attention to in this file?" : "Choose a file above, or paste its text here…" : "Write a thought, observation, or passage…"} rows={kind === "link" ? 5 : 9} maxLength={1_000_000} aria-label="Source content" />)}
        {kind !== null && <div className="capture-promise"><span><Check size={12} />Original stays local</span><span><Check size={12} />Organize anytime</span><span><Check size={12} />You approve knowledge</span></div>}
        {kind !== null && <footer><span>{kind === "recording" ? <>Audio, transcript, and summary stay together in <strong>{targetLabel}</strong>.</> : kind === "note" ? <>This stays editable in <strong>{targetLabel}</strong>{targetBase ? " and becomes a source when filed" : "; choose its home later"}.</> : kind === "link" ? <>Gunther will preserve an immutable page snapshot, extracted text, and capture provenance in <strong>{targetLabel}</strong>.</> : selectedFile ? <>The immutable original and extracted text will stay together in <strong>{targetLabel}</strong>.</> : <>This source will be preserved in <strong>{targetLabel}</strong>. AI suggestions stay separate until you review them.</>}</span><div className="capture-footer-actions">{kind === "recording" && recorderSnapshot.phase === "stopped" && <button type="button" className="quiet-button" onClick={keepDraftAndClose}>Keep as draft</button>}{kind === "recording" && recorderIsLive && <button type="button" className="quiet-button" onClick={keepInBackground}><Minimize2 size={14} />Keep in background</button>}<button className="primary-button" disabled={working || !canSubmit}>{working ? "Preserving…" : kind === "recording" ? "Save recording" : kind === "note" ? "Save note" : kind === "link" ? "Capture page" : "Save source"}<ArrowRight size={14} /></button></div></footer>}
      </form>

  const recordingDock = kind === "recording" && recordingMinimized && <aside className={`recording-session-dock is-${recorderSnapshot.phase}`} aria-label="Background recording session">
      <button type="button" className="recording-dock-summary" onClick={() => setRecordingMinimized(false)}>
        <span className={`recording-dock-pulse ${recorderIsLive ? "is-live" : ""}`}><Mic2 size={15} /></span>
        <span><small>{recorderStatus} · {recorderSnapshot.transcriptionLabel}</small><strong>{title}</strong><em>{targetLabel} · {recorderSnapshot.transcriptWords ? `${recorderSnapshot.transcriptWords} words` : recorderSnapshot.persistence === "saved" ? "preserved locally" : "saving locally"}{recorderSnapshot.markedMoments ? ` · ${recorderSnapshot.markedMoments} marked` : ""}</em></span>
      </button>
      <strong className="recording-dock-time">{formatRecorderTime(recorderSnapshot.seconds)}</strong>
      <div className="recording-dock-controls">
        {recorderIsLive && <button type="button" onClick={() => recorderRef.current?.markMoment()} aria-label="Mark important moment"><Bookmark size={14} /></button>}
        {recorderSnapshot.phase === "recording" && <button type="button" onClick={() => recorderRef.current?.pause()} aria-label="Pause recording"><Pause size={14} /></button>}
        {recorderSnapshot.phase === "paused" && <button type="button" onClick={() => recorderRef.current?.resume()} aria-label="Resume recording"><Play size={14} /></button>}
        {recorderIsLive && <button type="button" className="is-finish" onClick={() => recorderRef.current?.stop()}><Square size={12} />Finish</button>}
        {recorderSnapshot.phase === "stopped" && <button type="button" onClick={keepDraftAndClose}>Keep draft</button>}
        {recorderSnapshot.phase === "stopped" && <button type="button" className="is-save" disabled={working || !content.trim()} onClick={() => captureFormRef.current?.requestSubmit()}>Save to knowledge</button>}
        <button type="button" onClick={() => setRecordingMinimized(false)} aria-label="Expand recording"><Maximize2 size={15} /></button>
      </div>
    </aside>;

  if (surface === "window") {
    return <main className="capture-window-surface">{captureForm}</main>;
  }

  return <>
    <div className={`atlas-overlay ${recordingMinimized ? "is-capture-minimized" : ""}`} onMouseDown={recordingActive ? undefined : closeCaptureSurface} role="presentation">
      {captureForm}
    </div>
    {recordingDock}
  </>;
}

export function CreateKnowledgeBaseSheet({ open, base, onClose, onSave }: { open: boolean; base?: KnowledgeBase | undefined; onClose: () => void; onSave: (payload: CreateKnowledgeBaseInput) => Promise<void> }) {
  const [title, setTitle] = useState("");
  const [eyebrow, setEyebrow] = useState("Personal knowledge");
  const [question, setQuestion] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState<CreateKnowledgeBaseInput["color"]>("green");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    setTitle(base?.title ?? "");
    setEyebrow(base?.eyebrow ?? "Personal knowledge");
    setQuestion(base?.question ?? "");
    setDescription(base?.description ?? "");
    setColor(base?.color ?? "green");
    setError(null);
  }, [base, open]);
  if (!open) return null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim() || !question.trim() || !description.trim()) return;
    setWorking(true);
    setError(null);
    try {
      await onSave({
        title: title.trim(),
        eyebrow: eyebrow.trim() || "Personal knowledge",
        subtitle: question.trim(),
        question: question.trim(),
        description: description.trim(),
        color,
      });
      setTitle("");
      setQuestion("");
      setDescription("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create the knowledge base");
    } finally {
      setWorking(false);
    }
  };

  return <div className="atlas-overlay" onMouseDown={onClose} role="presentation"><form className="create-base-sheet" onMouseDown={(event) => event.stopPropagation()} onSubmit={(event) => void submit(event)} role="dialog" aria-modal="true" aria-label={base ? "Edit knowledge base" : "Create knowledge base"}>
    <header><span><small>{base ? "Field settings" : "New field"}</small><strong>{base ? "Edit knowledge base" : "Start a knowledge base"}</strong></span><button type="button" onClick={onClose} aria-label="Close knowledge base details dialog"><X size={17} /></button></header>
    <div className="create-base-intro"><span><BookOpen size={18} /></span><div><h2>{base ? "Keep the field boundary explicit." : "Create a lasting home for this subject."}</h2><p>{base ? "Changes update the durable base identity without rewriting its sessions, sources, or accepted revisions." : "A knowledge base is a durable subject boundary—for example, Bioinformatics. You can capture first, then file documents, notes, photos, links, recordings, data, and useful findings here when they belong together."}</p></div></div>
    {!base && <div className="create-base-path" aria-label="Knowledge base setup path"><span><i>1</i><strong>Name the subject</strong><small>Create its boundary</small></span><em /><span><i>2</i><strong>Bring sources</strong><small>Any captured material</small></span><em /><span><i>3</i><strong>Build knowledge</strong><small>Review and connect</small></span></div>}
    <div className="create-base-fields"><label><span>Name</span><input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} placeholder="e.g. Decision Science" maxLength={160} /></label><label><span>Field label</span><input value={eyebrow} onChange={(event) => setEyebrow(event.target.value)} placeholder="e.g. Applied reasoning" maxLength={80} /></label><label className="is-wide"><span>Guiding question</span><textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="What do you want this knowledge base to help you understand?" rows={2} maxLength={1000} /></label><label className="is-wide"><span>Description</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Describe its scope, boundaries, and intended use." rows={3} maxLength={2000} /></label></div>
    <fieldset className="create-base-color"><legend>Accent</legend>{(["green", "blue", "clay"] as const).map((item) => <button type="button" key={item} className={`color-${item} ${color === item ? "is-active" : ""}`} onClick={() => setColor(item)} aria-label={`Use ${item} accent`}><i />{item}{color === item && <Check size={12} />}</button>)}</fieldset>
    {error && <p className="create-base-error" role="alert"><CircleAlert size={13} />{error}</p>}
    <footer><span>{base ? "Session history and accepted knowledge stay unchanged." : "Inbox sources can be filed here at any time."}</span><div><button type="button" className="quiet-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={working || !title.trim() || question.trim().length < 3 || description.trim().length < 3}>{working ? "Saving…" : base ? "Save details" : "Create knowledge base"}<ArrowRight size={14} /></button></div></footer>
  </form></div>;
}

interface EvidenceDrawerProps {
  base: KnowledgeBase;
  chapter: KnowledgeChapter | null;
  onClose: () => void;
}

export function EvidenceDrawer({ base, chapter, onClose }: EvidenceDrawerProps) {
  if (!chapter) return null;
  const sources = base.sources.filter((source) => chapter.sourceIds.includes(source.id));
  return (
    <div className="drawer-scrim" onMouseDown={onClose} role="presentation">
      <aside className="evidence-drawer" onMouseDown={(event) => event.stopPropagation()} aria-label="Chapter evidence">
        <header><div><span className="atlas-eyebrow">Evidence layer</span><h2>{chapter.title}</h2><p>Sources used to shape this chapter. Open a source to inspect the original.</p></div><button onClick={onClose}><X size={18} /></button></header>
        <div className="evidence-policy"><ShieldCheck size={16} /><span><strong>Grounded, not automatic</strong><small>Sources can support a claim. They do not remove the need for biological judgment.</small></span></div>
        <div className="evidence-source-list">
          {sources.map((source, index) => <a href={source.url} target="_blank" rel="noreferrer" key={source.id}>
            <span className="source-index">{String(index + 1).padStart(2, "0")}</span>
            <span><small>{source.kind} · {source.publisher}</small><strong>{source.title}</strong><p>{source.scope}</p></span>
            <ArrowRight size={14} />
          </a>)}
          {sources.length === 0 && <div className="empty-evidence"><BookOpen size={19} /><strong>No grounded sources yet</strong><p>Add a trusted source to turn this outline into living knowledge.</p></div>}
        </div>
        <footer><span><i />{sources.length} linked sources</span><button onClick={onClose}>Done</button></footer>
      </aside>
    </div>
  );
}

export function SearchPage({ bases = knowledgeBases, onOpenBase, onOpenChapter, onOpenNote, onCapture, onNotify, resolveWorkspaceId }: { bases?: KnowledgeBase[]; onOpenBase: (id: string, mode?: AtlasMode) => void; onOpenChapter: (baseId: string, chapterId: string) => void; onOpenNote: (id: string) => void; onCapture: () => void; onNotify: (message: string) => void; resolveWorkspaceId: () => Promise<string> }) {
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [scope, setScope] = useState<SearchScope>("both");
  const [indexedResults, setIndexedResults] = useState<KnowledgeSearchResult[]>([]);
  const [webResult, setWebResult] = useState<WebSearchResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [savingResearch, setSavingResearch] = useState(false);
  const [savedResearchQuery, setSavedResearchQuery] = useState<string | null>(null);
  const searchRequest = useRef(0);
  const curatedResults = useMemo(() => {
    const needle = submittedQuery.trim().toLowerCase();
    if (!needle || scope === "web") return [];
    return bases.flatMap((base) => [
      ...(base.title.toLowerCase().includes(needle) || base.description.toLowerCase().includes(needle) ? [{ type: "base" as const, base, chapter: null }] : []),
      ...base.chapters.filter((chapter) => `${chapter.title} ${chapter.question} ${chapter.summary} ${chapter.topics.map((topic) => `${topic.title} ${topic.markers?.join(" ")}`).join(" ")}`.toLowerCase().includes(needle)).map((chapter) => ({ type: "chapter" as const, base, chapter })),
    ]).slice(0, 10);
  }, [bases, scope, submittedQuery]);

  const performSearch = async (needle: string, selectedScope: SearchScope = scope) => {
    if (!needle) return;
    const requestId = searchRequest.current + 1;
    searchRequest.current = requestId;
    setSubmittedQuery(needle);
    setSearching(true);
    setSearchError(null);
    setIndexedResults([]);
    setWebResult(null);
    const requests: Promise<void>[] = [];
    if (selectedScope !== "web") requests.push(knowledgeApi.search(needle).then((results) => {
      if (searchRequest.current === requestId) setIndexedResults(results);
    }).catch((reason) => {
      if (searchRequest.current === requestId) setSearchError(reason instanceof Error ? reason.message : "Your local index could not be reached.");
    }));
    if (selectedScope !== "knowledge") requests.push(knowledgeApi.webSearch(needle).then((result) => {
      if (searchRequest.current === requestId) setWebResult(result);
    }).catch((reason) => {
      if (searchRequest.current === requestId) setWebResult({ query: needle, answer: "", sources: [], mode: "failed", message: reason instanceof Error ? reason.message : "Online search could not be reached." });
    }));
    await Promise.all(requests);
    if (searchRequest.current === requestId) setSearching(false);
  };

  const search = (event: FormEvent) => {
    event.preventDefault();
    const needle = query.trim();
    if (needle) void performSearch(needle);
  };

  const choosePrompt = (prompt: string) => {
    setQuery(prompt);
    void performSearch(prompt);
  };
  const saveResearch = async () => {
    if (!webResult || webResult.mode !== "openai" || !webResult.answer.trim()) return;
    setSavingResearch(true);
    const references = webResult.sources.map((source, index) => [
      `${index + 1}. ${source.title}`,
      source.url,
      source.snippet?.trim() || null,
    ].filter(Boolean).join("\n")).join("\n\n");
    const content = [
      `Search query: ${webResult.query}`,
      "",
      "Answer captured from web research:",
      webResult.answer,
      references ? "\nReferenced pages:\n" + references : "",
    ].join("\n").trim();
    try {
      const expectedWorkspaceId = await resolveWorkspaceId();
      await knowledgeApi.importSource({
        title: `Web research · ${webResult.query}`.slice(0, 160),
        kind: "link",
        content,
      }, expectedWorkspaceId);
      setSavedResearchQuery(webResult.query);
      window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
      window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
      onNotify("Web research preserved in Inbox with its referenced URLs.");
    } catch (reason) {
      onNotify(reason instanceof Error ? `Research not saved: ${reason.message}` : "This research could not be saved.");
    } finally {
      setSavingResearch(false);
    }
  };
  const totalLocal = curatedResults.length + indexedResults.length;
  const hasResults = Boolean(submittedQuery);
  return <div className={`search-page page-enter ${hasResults ? "has-results" : "is-home"}`}>
    <section className="search-home">
      <div className="search-wordmark"><span>G</span><strong>Gunther</strong><small>Search what you know. Discover what you don’t.</small></div>
      <form className="search-command" onSubmit={search}>
        <Search size={21} />
        <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search your knowledge or ask the web…" aria-label="Search your knowledge or the web" />
        {query && <button type="button" className="clear-search" onClick={() => { setQuery(""); setSubmittedQuery(""); setIndexedResults([]); setWebResult(null); }} aria-label="Clear search"><X size={15} /></button>}
        <button className="submit-search" disabled={!query.trim()} aria-label="Run search">{searching ? <span className="search-spinner" /> : <ArrowRight size={17} />}</button>
      </form>
      <div className="search-scope" aria-label="Search scope">
        {([
          ["knowledge", BookOpen, "My knowledge"],
          ["both", Sparkles, "Knowledge + web"],
          ["web", Globe2, "Web"],
        ] as const).map(([value, Icon, label]) => <button key={value} className={scope === value ? "is-active" : ""} onClick={() => { setScope(value); if (submittedQuery) void performSearch(query.trim() || submittedQuery, value); }}><Icon size={13} />{label}{scope === value && <i />}</button>)}
      </div>
      {!hasResults && <div className="search-starters">
        <div><small>Ask across your world</small>{["What have I learned about uncertainty?", "Compare my notes with the latest research", "Where are the gaps in my knowledge?"].map((item) => <button key={item} onClick={() => choosePrompt(item)}>{item}<ArrowRight size={12} /></button>)}</div>
        <section className="search-home-actions">
          <button className="quick-note-entry" onClick={onCapture}><span><Paperclip size={17} /></span><span><small>Any source</small><strong>Capture now, organize later</strong><em>Note, document, photo, web page, recording, or data</em></span><ChevronRight size={15} /></button>
        </section>
      </div>}
    </section>

    {hasResults && <section className="search-results-stage">
      <header><span><small>Results for</small><h1>“{submittedQuery}”</h1></span><em>{searching ? "Searching two worlds…" : scope === "both" ? `${totalLocal} personal matches · web ${webResult?.mode === "openai" ? "ready" : "checked"}` : scope === "knowledge" ? `${totalLocal} personal matches` : "Online answer"}</em></header>
      {searchError && <p className="search-status-card is-error"><CircleAlert size={14} />{searchError}</p>}
      {scope !== "knowledge" && webResult && <article className={`web-answer is-${webResult.mode}`}>
        <header><span><Globe2 size={15} /><strong>From the web</strong></span><div><small>{webResult.mode === "openai" ? "Live sources" : "Unavailable"}</small>{webResult.mode === "openai" && webResult.answer && <button type="button" className="save-web-research" disabled={savingResearch || savedResearchQuery === webResult.query} onClick={() => void saveResearch()}>{savedResearchQuery === webResult.query ? <Check size={13} /> : <Bookmark size={13} />}{savedResearchQuery === webResult.query ? "Saved to Inbox" : savingResearch ? "Saving…" : "Save research"}</button>}</div></header>
        {webResult.answer ? <p>{webResult.answer}</p> : <div className="web-unavailable"><WifiOff size={18} /><span><strong>{webResult.mode === "not_configured" ? "Online search needs one-time setup" : "The web could not be reached"}</strong><small>{webResult.message ?? "Your personal results remain available below."}</small></span></div>}
        {webResult.sources.length > 0 && <div className="web-sources">{webResult.sources.map((source, index) => <a href={source.url} target="_blank" rel="noreferrer" key={source.url}><i>{index + 1}</i><span><strong>{source.title}</strong><small>{source.url.replace(/^https?:\/\/(www\.)?/, "").split("/")[0]}</small></span><ArrowRight size={12} /></a>)}</div>}
      </article>}
      {scope !== "web" && <div className="personal-results">
        <div className="personal-results-heading"><span><BookOpen size={15} /><strong>Your knowledge</strong></span><small>Notes · accepted knowledge · sources · sessions · chapters</small></div>
        {indexedResults.map((result) => { const base = bases.find((item) => item.id === result.knowledgeBaseId); const Icon = result.kind === "note" ? StickyNote : result.kind === "session" ? MessageSquareText : result.kind === "source" ? FileText : BookOpen; return <button key={`indexed-${result.kind}-${result.id}`} onClick={() => { if (result.kind === "note") { onOpenNote(result.id); return; } if (!result.knowledgeBaseId) return; if (result.kind === "session") { window.localStorage.setItem(`gunther:active-session:${result.knowledgeBaseId}`, result.id); onOpenBase(result.knowledgeBaseId, "ask"); return; } if (result.kind === "source") { window.localStorage.setItem(`gunther:open-source:${result.knowledgeBaseId}`, result.id); onOpenBase(result.knowledgeBaseId, "sources"); return; } if (result.kind === "knowledge_unit" && result.sourceSessionId) { window.localStorage.setItem(`gunther:active-session:${result.knowledgeBaseId}`, result.sourceSessionId); if (result.sourceMessageId) window.localStorage.setItem(`gunther:selected-message:${result.knowledgeBaseId}`, result.sourceMessageId); onOpenBase(result.knowledgeBaseId, "ask"); return; } onOpenBase(result.knowledgeBaseId); }}><span className={`result-icon color-${base?.color ?? "green"}`}><Icon size={16} /></span><span><small>{result.kind === "note" ? "Notebook" : base?.title ?? result.knowledgeBaseId} · {result.kind.replace("_", " ")} · {result.meta}</small><strong>{result.title}</strong><p>{result.snippet}</p></span><ChevronRight size={15} /></button>; })}
        {curatedResults.map((result) => <button key={`${result.type}-${result.base.id}-${result.chapter?.id ?? ""}`} onClick={() => result.chapter ? onOpenChapter(result.base.id, result.chapter.id) : onOpenBase(result.base.id)}><span className={`result-icon color-${result.base.color}`}>{result.type === "base" ? <BookOpen size={16} /> : <FileText size={16} />}</span><span><small>{result.base.title} · {result.type}</small><strong>{result.chapter?.title ?? result.base.title}</strong><p>{result.chapter?.summary ?? result.base.description}</p></span><ChevronRight size={15} /></button>)}
        {!searching && totalLocal === 0 && <p className="no-results">Nothing in your knowledge matches yet. Use the web result as a lead, then save only what you choose to trust.</p>}
      </div>}
    </section>}
  </div>;
}

async function writeClipboard(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }

  const field = document.createElement("textarea");
  field.value = value;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.opacity = "0";
  document.body.append(field);
  field.select();
  const copied = document.execCommand("copy");
  field.remove();
  if (!copied) throw new Error("Clipboard is unavailable");
}

export function SettingsPageV2({ theme, onTheme, onNotify }: { theme: "light" | "dark"; onTheme: (theme: "light" | "dark") => void; onNotify: (message: string) => void }) {
  const [engine, setEngine] = useState<"local" | "deepseek" | "offline">("offline");
  const [webMode, setWebMode] = useState<"openai" | "not_configured">("not_configured");
  const [transcriptionMode, setTranscriptionMode] = useState<"sensevoice_local" | "openai_realtime" | "not_configured">("not_configured");
  const [transcriptionProvider, setTranscriptionProvider] = useState<"sensevoice" | "openai" | "none">("none");
  const [transcriptionProfile, setTranscriptionProfile] = useState({ model: "gpt-live-transcribe", delay: "medium", languages: ["en", "zh-cn"] as string[] });
  const [summaryMode, setSummaryMode] = useState<"local" | "deepseek" | "openai">("local");
  const [checking, setChecking] = useState(true);
  const [devices, setDevices] = useState<PairedDevice[]>([]);
  const [devicesLoading, setDevicesLoading] = useState(true);
  const [devicesError, setDevicesError] = useState<string | null>(null);
  const [gatewayStatus, setGatewayStatus] = useState<MobileGatewayStatus | null>(null);
  const [gatewayLoading, setGatewayLoading] = useState(true);
  const [gatewayError, setGatewayError] = useState<string | null>(null);
  const [pairing, setPairing] = useState<DevicePairingSession | null>(null);
  const [pairingWorking, setPairingWorking] = useState(false);
  const [revokingDeviceId, setRevokingDeviceId] = useState<string | null>(null);
  const [nowMilliseconds, setNowMilliseconds] = useState(() => Date.now());
  const connection = useMemo(() => {
    const current = mobileConnectionFromGateway(gatewayStatus);
    return gatewayError ? { ...current, explanation: gatewayError } : current;
  }, [gatewayError, gatewayStatus]);

  const checkHealth = () => {
    setChecking(true);
    void knowledgeApi.health().then((health) => {
      setEngine(health.extractionMode);
      setWebMode(health.webSearchMode);
      setTranscriptionMode(health.transcriptionMode);
      setTranscriptionProvider(health.transcriptionProvider);
      setTranscriptionProfile({ model: health.transcriptionModel, delay: health.transcriptionDelay, languages: health.transcriptionLanguages });
      setSummaryMode(health.summaryMode);
      onNotify(`Local workbook ready · web ${health.webSearchMode === "openai" ? "connected" : "not configured"} · transcript ${health.transcriptionMode === "sensevoice_local" ? "SenseVoice local" : health.transcriptionMode === "openai_realtime" ? "OpenAI connected" : "local audio only"}.`);
    }).catch(() => {
      setEngine("offline");
      onNotify("The local service is not reachable. Your browser-only view is still available.");
    }).finally(() => setChecking(false));
  };

  const loadDevices = useCallback(async (announce = false) => {
    setDevicesLoading(true);
    setDevicesError(null);
    try {
      const nextDevices = sortPairedDevices(await knowledgeApi.pairedDevices());
      setDevices(nextDevices);
      if (announce) onNotify(`Device access refreshed · ${nextDevices.filter((device) => !device.revokedAt).length} active.`);
    } catch (error) {
      setDevicesError(error instanceof Error ? error.message : "Device access could not be loaded.");
      if (announce) onNotify("Device access could not be refreshed.");
    } finally {
      setDevicesLoading(false);
    }
  }, [onNotify]);

  const loadGateway = useCallback(async (announce = false) => {
    setGatewayLoading(true);
    setGatewayError(null);
    try {
      const status = await knowledgeApi.mobileGatewayStatus();
      setGatewayStatus(status);
      if (announce) onNotify(status.running ? "Secure mobile gateway is ready." : "Secure mobile gateway is not available yet.");
    } catch (error) {
      setGatewayStatus(null);
      setGatewayError(error instanceof Error ? error.message : "Secure mobile gateway status could not be loaded.");
      if (announce) onNotify("Secure mobile gateway status could not be refreshed.");
    } finally {
      setGatewayLoading(false);
    }
  }, [onNotify]);

  useEffect(() => {
    setChecking(true);
    void knowledgeApi.health().then((health) => {
      setEngine(health.extractionMode);
      setWebMode(health.webSearchMode);
      setTranscriptionMode(health.transcriptionMode);
      setTranscriptionProvider(health.transcriptionProvider);
      setTranscriptionProfile({ model: health.transcriptionModel, delay: health.transcriptionDelay, languages: health.transcriptionLanguages });
      setSummaryMode(health.summaryMode);
    }).catch(() => setEngine("offline")).finally(() => setChecking(false));
    void loadDevices();
    void loadGateway();
  }, [loadDevices, loadGateway]);

  useEffect(() => {
    if (!pairing) return;
    setNowMilliseconds(Date.now());
    const timer = window.setInterval(() => setNowMilliseconds(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [pairing]);

  const pairingExpired = pairing ? isPairingExpired(pairing.expiresAt, nowMilliseconds) : false;

  useEffect(() => {
    if (!pairing || pairingExpired) return;
    let stopped = false;
    let requestInFlight = false;
    const checkForPairedDevice = async () => {
      if (requestInFlight) return;
      requestInFlight = true;
      try {
        const nextDevices = sortPairedDevices(await knowledgeApi.pairedDevices());
        if (stopped) return;
        setDevices(nextDevices);
        const pairedDevice = findDevicePairedForSession(pairing, nextDevices);
        if (!pairedDevice) return;
        setPairing(null);
        onNotify(`${pairedDevice.name} is securely connected. You can pair another device now.`);
      } catch {
        // Pairing remains usable until expiry; transient polling failures stay quiet.
      } finally {
        requestInFlight = false;
      }
    };
    const timer = window.setInterval(() => void checkForPairedDevice(), 3_000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [onNotify, pairing, pairingExpired]);

  const activeDeviceCount = devices.filter((device) => !device.revokedAt).length;
  const engineLabel = checking ? "Checking…" : engine === "deepseek" ? "DeepSeek" : engine === "local" ? "Local evidence mode" : "Service offline";
  const engineDescription = engine === "deepseek" ? "DeepSeek is configured; extraction and synthesis fall back locally if the provider fails." : engine === "local" ? "Deterministic local extraction and evidence synthesis are active." : "Reconnect the local service to use indexed sources and sessions.";

  const createPairing = async () => {
    if (connection.kind !== "secure") {
      onNotify("Start the secure mobile gateway before creating a pairing code.");
      return;
    }
    setPairingWorking(true);
    setDevicesError(null);
    try {
      const created = await knowledgeApi.createDevicePairing({
        scopes: [...DEFAULT_DEVICE_SCOPES],
        expiresInSeconds: 120,
      });
      setPairing(created);
      setNowMilliseconds(Date.now());
      onNotify("A one-time pairing code was created. It expires in two minutes.");
    } catch (error) {
      setDevicesError(error instanceof Error ? error.message : "A pairing code could not be created.");
      onNotify("A pairing code could not be created.");
    } finally {
      setPairingWorking(false);
    }
  };

  const copyValue = async (label: string, value: string) => {
    try {
      await writeClipboard(value);
      onNotify(`${label} copied.`);
    } catch {
      onNotify("Copy is unavailable. Select the value and copy it manually.");
    }
  };

  const copyPairingBundle = (currentPairing: DevicePairingSession) => {
    try {
      void copyValue("Pairing details", buildPairingCopyText(currentPairing, connection));
    } catch {
      onNotify("Secure gateway details are incomplete. Refresh before pairing.");
    }
  };

  const revokeDevice = async (device: PairedDevice) => {
    if (!window.confirm(`Revoke access for “${device.name}”? This device will need to pair again.`)) return;
    setRevokingDeviceId(device.id);
    setDevicesError(null);
    try {
      const revoked = await knowledgeApi.revokePairedDevice(device.id);
      setDevices((current) => sortPairedDevices(current.map((item) => item.id === revoked.id ? revoked : item)));
      onNotify(`${device.name} can no longer access this workspace.`);
    } catch (error) {
      setDevicesError(error instanceof Error ? error.message : "Device access could not be revoked.");
      onNotify("Device access could not be revoked.");
    } finally {
      setRevokingDeviceId(null);
    }
  };

  return <div className="utility-page settings-v2 page-enter">
    <header><span className="atlas-eyebrow">Settings</span><h1>A quiet workspace, under your control.</h1><p>Local storage is the default. Online services are visible and optional; AI proposes changes but never silently rewrites accepted knowledge.</p></header>
    <div className="settings-columns">
      <section>
        <div className="setting-heading"><Settings2 size={16} /><span><strong>Appearance</strong><small>Default workspace presentation</small></span></div>
        <label className="setting-row"><span><strong>Theme</strong><small>Applied immediately and remembered on this device.</small></span><select value={theme} onChange={(event) => onTheme(event.target.value as "light" | "dark")}><option value="light">White</option><option value="dark">Dark</option></select></label>
        <div className="setting-row"><span><strong>Home</strong><small>Gunther opens on your capture, Inbox, and recent knowledge overview.</small></span><span className="setting-state">Overview</span></div>
      </section>
      <section>
        <div className="setting-heading"><ShieldCheck size={16} /><span><strong>Knowledge services</strong><small>Storage, search, transcript, and synthesis</small></span></div>
        <div className="setting-row"><span><strong>Local workbook</strong><small>Sources, audio, and accepted revisions stay on this device.</small></span><span className="setting-state"><i />{engine === "offline" && !checking ? "Offline" : "Ready"}</span></div>
        <div className="setting-row"><span><strong>Online research</strong><small>{webMode === "openai" ? "Live, sourced web answers are enabled." : "Add OPENAI_API_KEY to the local backend to enable web answers."}</small></span><span className={`setting-state ${webMode === "not_configured" ? "is-muted" : ""}`}><i />{webMode === "openai" ? "Connected" : "Not configured"}</span></div>
        <div className="setting-row"><span><strong>Live transcript</strong><small>{transcriptionMode === "sensevoice_local" ? `${transcriptionProfile.model} · private on-device STT · speaker labels enabled` : transcriptionMode === "openai_realtime" ? `${transcriptionProfile.languages.join(" + ")} · ${transcriptionProfile.delay} delay · ${transcriptionProfile.model}` : "Audio still records locally; connect SenseVoice or OpenAI for live words."}</small></span><span className={`setting-state ${transcriptionMode === "not_configured" ? "is-muted" : ""}`}><i />{transcriptionProvider === "sensevoice" ? "SenseVoice local" : transcriptionProvider === "openai" ? "OpenAI" : "Local audio"}</span></div>
        <details className="stt-setup"><summary>Live transcription provider</summary><p>Gunther automatically prefers your private SenseVoice service, then falls back to OpenAI when configured.</p><code>STT_PROVIDER=auto<br />SENSEVOICE_URL=http://127.0.0.1:8765<br />SENSEVOICE_SEGMENT_SECONDS=3.2<br />OPENAI_API_KEY=optional-fallback</code></details>
        <div className="setting-row"><span><strong>Lecture summaries</strong><small>{engineDescription}</small></span><span className="setting-state"><i />{summaryMode === "openai" ? "OpenAI" : summaryMode === "deepseek" ? "DeepSeek" : engineLabel}</span></div>
        <button className="settings-action" onClick={checkHealth} disabled={checking}>{checking ? "Checking services…" : "Check all services"} <ArrowRight size={13} /></button>
      </section>
    </div>

    <section className="mobile-connection-settings">
      <div className="setting-heading"><Smartphone size={16} /><span><strong>Mobile connection</strong><small>Pair a phone without exposing your desktop token</small></span></div>
      <div className="mobile-connection-overview">
        <div className={`mobile-address-boundary is-${connection.kind}`}>
          <span><Laptop size={17} /></span>
          <div><small>Phone connection address</small><strong>{gatewayLoading ? "Checking secure gateway…" : connection.displayValue}</strong><p>{connection.explanation}</p>{connection.caFingerprint && <code className="gateway-ca-fingerprint">CA SHA-256 · {connection.caFingerprint}</code>}</div>
        </div>
        <button className="primary-button" type="button" disabled={gatewayLoading || connection.kind !== "secure" || pairingWorking || Boolean(pairing && !pairingExpired)} onClick={() => void createPairing()}>
          {gatewayLoading ? "Checking gateway…" : connection.kind !== "secure" ? "Gateway unavailable" : pairingWorking ? "Creating…" : pairing && !pairingExpired ? "Pairing code active" : pairing ? "Generate a new code" : "Generate 2-minute code"}
          <ArrowRight size={13} />
        </button>
      </div>

      {devicesError && <p className="mobile-connection-error"><CircleAlert size={14} />{devicesError}</p>}

      {pairing && <article className={`pairing-details ${pairingExpired ? "is-expired" : ""}`}>
        <header>
          <span><small>One-time pairing</small><strong>{pairing.workspaceName}</strong></span>
          <em>{formatPairingExpiry(pairing.expiresAt, nowMilliseconds)}</em>
        </header>
        <div className="pairing-field-grid">
          <div className="pairing-field is-wide"><small>Connection address</small><span><code>{connection.displayValue}</code>{connection.connectionAddress && <button type="button" title="Copy connection address" aria-label="Copy connection address" onClick={() => void copyValue("Connection address", connection.connectionAddress ?? "")}><Copy size={13} /></button>}</span></div>
          <div className="pairing-field"><small>Pairing ID</small><span><code title={pairing.pairingId}>{pairing.pairingId}</code><button type="button" title="Copy pairing ID" aria-label="Copy pairing ID" disabled={pairingExpired} onClick={() => void copyValue("Pairing ID", pairing.pairingId)}><Copy size={13} /></button></span></div>
          <div className="pairing-field is-code"><small>Pairing code</small><span><code title={pairing.pairingCode}>{pairing.pairingCode}</code><button type="button" title="Copy pairing code" aria-label="Copy pairing code" disabled={pairingExpired} onClick={() => void copyValue("Pairing code", pairing.pairingCode)}><Copy size={13} /></button></span></div>
          <div className="pairing-field"><small>CA SHA-256</small><span><code title={connection.caFingerprint ?? "Unavailable"}>{connection.caFingerprint ?? "Unavailable"}</code>{connection.caFingerprint && <button type="button" title="Copy CA fingerprint" aria-label="Copy CA fingerprint" disabled={pairingExpired} onClick={() => void copyValue("CA fingerprint", connection.caFingerprint ?? "")}><Copy size={13} /></button>}</span></div>
          <div className="pairing-field"><small>Workspace ID</small><span><code title={pairing.workspaceId}>{pairing.workspaceId}</code><button type="button" title="Copy workspace ID" aria-label="Copy workspace ID" disabled={pairingExpired} onClick={() => void copyValue("Workspace ID", pairing.workspaceId)}><Copy size={13} /></button></span></div>
          <div className="pairing-field"><small>Protocol and expiry</small><span><code>v{connection.protocolVersion ?? pairing.protocolVersion} · {formatAbsoluteTime(pairing.expiresAt)}</code></span></div>
        </div>
        {connection.caCertificatePem && <details className="pairing-ca-certificate"><summary>CA certificate <span>Public trust anchor</span></summary><pre>{connection.caCertificatePem}</pre><button className="quiet-button" type="button" disabled={pairingExpired} onClick={() => void copyValue("CA certificate", connection.caCertificatePem ?? "")}><Copy size={13} />Copy certificate</button></details>}
        <footer>
          <span>{pairingExpired ? "This code can no longer be exchanged. Generate a new one." : "The code is shown once. Share it only with the phone you are pairing."}</span>
          <button className="quiet-button" type="button" disabled={pairingExpired || connection.kind !== "secure"} onClick={() => copyPairingBundle(pairing)}><Copy size={13} />Copy all details</button>
        </footer>
      </article>}

      <div className="paired-devices-heading">
        <span><strong>Paired devices</strong><small>{devicesLoading ? "Loading access…" : `${activeDeviceCount} active · ${devices.length - activeDeviceCount} revoked`}</small></span>
        <button className="quiet-button" type="button" disabled={devicesLoading || gatewayLoading} onClick={() => void Promise.all([loadDevices(true), loadGateway(true)])}><RefreshCw size={13} />Refresh</button>
      </div>
      {devicesLoading ? <div className="paired-devices-empty"><span className="search-spinner" /><p>Checking device access…</p></div> : devices.length === 0 ? <div className="paired-devices-empty"><Smartphone size={20} /><strong>No paired devices</strong><p>Generate a one-time code when your secure mobile gateway is ready.</p></div> : <div className="paired-device-list">
        {devices.map((device) => <article key={device.id} className={device.revokedAt ? "is-revoked" : ""}>
          <span className="paired-device-icon"><Smartphone size={16} /></span>
          <div>
            <span className="paired-device-name"><strong>{device.name}</strong><em>{device.revokedAt ? "Revoked" : "Active"}</em></span>
            <small>{device.platform} · {formatDeviceLastUsed(device.lastUsedAt, nowMilliseconds)} · paired {formatAbsoluteTime(device.createdAt)}</small>
            <p>{device.scopes.includes("transcription:stream") ? "Knowledge access and live transcription" : "Knowledge access"}</p>
          </div>
          {device.revokedAt ? <span className="revoked-at">{formatAbsoluteTime(device.revokedAt)}</span> : <button className="quiet-button is-danger" type="button" disabled={revokingDeviceId === device.id} onClick={() => void revokeDevice(device)}>{revokingDeviceId === device.id ? "Revoking…" : "Revoke"}</button>}
        </article>)}
      </div>}
    </section>
  </div>;
}

export function AccountPageV2({ bases = knowledgeBases }: { bases?: KnowledgeBase[] }) {
  const defaultProfile = { initials: "KE", name: "Knowledge explorer", role: "Local learner", bio: "Building clear, evidence-backed maps of difficult fields." };
  const [profile, setProfile] = useState(() => {
    try { return { ...defaultProfile, ...JSON.parse(window.localStorage.getItem("gunther:profile") ?? "{}") as Partial<typeof defaultProfile> }; } catch { return defaultProfile; }
  });
  const [draftProfile, setDraftProfile] = useState(profile);
  const [editing, setEditing] = useState(false);
  const saveProfile = (event: FormEvent) => {
    event.preventDefault();
    const next = { initials: draftProfile.initials.trim().slice(0, 3).toUpperCase() || "KE", name: draftProfile.name.trim() || "Knowledge explorer", role: draftProfile.role.trim() || "Local learner", bio: draftProfile.bio.trim() || defaultProfile.bio };
    setProfile(next);
    setDraftProfile(next);
    window.localStorage.setItem("gunther:profile", JSON.stringify(next));
    setEditing(false);
  };
  return <div className="utility-page account-v2 page-enter"><header><span className="atlas-eyebrow">Your profile</span><h1>Knowledge belongs to a learner.</h1><p>Your identity is stored locally and travels with complete workbook exports.</p></header><section className={`profile-card-v2 ${editing ? "is-editing" : ""}`}><span className="profile-avatar-v2">{profile.initials}</span>{editing ? <form className="profile-edit-form" onSubmit={saveProfile}><label><span>Initials</span><input autoFocus value={draftProfile.initials} maxLength={3} onChange={(event) => setDraftProfile((current) => ({ ...current, initials: event.target.value }))} /></label><label><span>Name</span><input value={draftProfile.name} maxLength={80} onChange={(event) => setDraftProfile((current) => ({ ...current, name: event.target.value }))} /></label><label><span>Role</span><input value={draftProfile.role} maxLength={80} onChange={(event) => setDraftProfile((current) => ({ ...current, role: event.target.value }))} /></label><label className="is-wide"><span>Bio</span><textarea value={draftProfile.bio} rows={2} maxLength={240} onChange={(event) => setDraftProfile((current) => ({ ...current, bio: event.target.value }))} /></label><div><button type="button" className="quiet-button" onClick={() => { setDraftProfile(profile); setEditing(false); }}>Cancel</button><button className="primary-button"><Check size={13} />Save locally</button></div></form> : <><div><small>{profile.role}</small><h2>{profile.name}</h2><p>{profile.bio}</p></div><button className="quiet-button" onClick={() => setEditing(true)}>Edit profile</button></>}</section><section className="profile-stats-v2"><div><strong>{bases.length}</strong><span>knowledge bases</span></div><div><strong>{bases.reduce((sum, base) => sum + base.chapterCount, 0)}</strong><span>chapters</span></div><div><strong>{bases.reduce((sum, base) => sum + (base.indexedSourceCount ?? 0), 0)}</strong><span>indexed sources</span></div></section></div>;
}
