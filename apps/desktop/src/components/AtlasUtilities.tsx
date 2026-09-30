import type { CreateKnowledgeBaseInput, RecordingSession } from "@gunther/contracts";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent, type DragEvent, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent } from "react";
import {
  ArrowRight,
  Bookmark,
  FileUp,
  Globe,
  ImagePlus,
  LoaderCircle,
  Mic,
  NotebookPen,
  Search,
  Trash2,
  BookOpen,
  Camera,
  Check,
  ChevronRight,
  CircleAlert,
  Copy,
  FileText,
  Laptop,
  Maximize2,
  Mic2,
  Minimize2,
  Pause,
  Play,
  Settings2,
  ShieldCheck,
  Smartphone,
  Square,
  Table2,
  RefreshCw,
  X,
} from "lucide-react";
import { knowledgeBases, type KnowledgeBase, type KnowledgeChapter } from "../atlas";
import { knowledgeApi } from "../api";
import type { ThemePreference } from "../design/theme";
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
import { BrandMark } from "../design/BrandMark";
import { LibraryPicker } from "../items/LibraryPicker";
import { parseDelimitedTable } from "../items/sourceContent";
import { comboKeys, formatCombo, useEscape, useShortcut, withShortcut } from "../shortcuts/shortcuts";
import { LibraryFolderSettings } from "./LibraryFolderSettings";
import { SemanticSearchSetting } from "./SemanticSearchSetting";
import { ServiceSettings } from "./ServiceSettings";
import { TypographySettings } from "./TypographySettings";
import { ModelSettings } from "../models/ModelSettings";
import { SpeechSettings } from "../models/SpeechSettings";
import { TtsSettings } from "../speech/TtsSettings";
import { CAPTURE_CONTROL_DOM_EVENT, CAPTURE_SWITCH_DOM_EVENT, recorderOwnsCapture, type CaptureControl, type CaptureKind, type CaptureLaunchRequest, type RecordingContext } from "../capture/captureTypes";
import { getMenuBarMode, isTauriRuntime, setMenuBarMode, type MenuBarMode } from "../capture/captureBridge";

export type { CaptureKind, RecordingContext } from "../capture/captureTypes";

type CaptureSheetSurface = "overlay" | "window";

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

const defaultRecordingTitle = (context: RecordingContext) => `${context === "lecture" ? "Lecture" : context === "meeting" ? "Meeting" : "Voice memo"} · ${new Date().toLocaleDateString()}`;

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

const captureKinds: Array<{ id: CaptureKind; label: string; icon: typeof FileText; tone: string }> = [
  { id: "note", label: "Note", icon: NotebookPen, tone: "clay" },
  { id: "link", label: "Web page", icon: Globe, tone: "amber" },
  { id: "file", label: "Document", icon: FileText, tone: "blue" },
  { id: "image", label: "Photo", icon: Camera, tone: "violet" },
  { id: "recording", label: "Recording", icon: Mic, tone: "rose" },
  { id: "table", label: "Table", icon: Table2, tone: "green" },
];

const FILE_ACCEPT = ".pdf,.docx,.epub,.txt,.md,.markdown,.csv,.tsv,.json,.html,.htm,application/pdf,text/*";
const fileExtension = (name: string) => (name.match(/\.([a-z0-9]{1,5})$/i)?.[1] ?? "file").toUpperCase();
const formatFileSize = (bytes: number) => bytes >= 1_048_576 ? `${(bytes / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
const hostLabel = (url: string) => {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
};

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
  // A recording is its own session beside the other types: it keeps its own
  // title, text and library, and keeps running while a note, file or link is
  // captured in the other tabs.
  const [recordingContext, setRecordingContext] = useState<RecordingContext>("lecture");
  const [recordingTitleText, setRecordingTitleText] = useState("");
  const [recordingContent, setRecordingContent] = useState("");
  const [recordingBaseId, setRecordingBaseId] = useState(baseId ?? "");
  const [recorderKey, setRecorderKey] = useState(0);
  const [otherKind, setOtherKind] = useState<Exclude<CaptureKind, "recording">>("note");
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [recordingActive, setRecordingActive] = useState(false);
  const [recordingMinimized, setRecordingMinimized] = useState(false);
  const [recorderSnapshot, setRecorderSnapshot] = useState<RecorderSnapshot>(emptyRecorderSnapshot);
  const [recordingDraftId, setRecordingDraftId] = useState<string | null>(null);
  const [recordingDrafts, setRecordingDrafts] = useState<RecordingDraft[]>([]);
  const [dragging, setDragging] = useState(false);
  const [pasteHint, setPasteHint] = useState<{ kind: "link" | "table"; value: string } | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const shortcutActions = useRef({ submit: () => undefined as void, switchKind: (_: CaptureKind) => undefined as void, hide: () => undefined as void, launch: (_: CaptureLaunchRequest) => undefined as void, showRecorder: () => undefined as void });
  const [interruptedRecordings, setInterruptedRecordings] = useState<RecordingSession[]>([]);
  const [recoveringRecordingId, setRecoveringRecordingId] = useState<string | null>(null);
  const recorderRef = useRef<LectureRecorderHandle>(null);
  const captureFormRef = useRef<HTMLFormElement>(null);
  const targetBase = bases.find((base) => base.id === targetBaseId);
  const recordingBase = bases.find((base) => base.id === recordingBaseId);
  const recordingTargetLabel = recordingBase?.title ?? "Inbox · organize later";
  const chapter = targetBase?.chapters.find((item) => item.id === chapterId);
  const recoverableRecordingDrafts = useMemo(
    () => recordingDraftsForWorkspace(recordingDrafts, workspaceId),
    [recordingDrafts, workspaceId],
  );
  // Text, a link or a file waiting in the non-recording tabs, whichever tab is showing.
  const hasUnreviewedWork = Boolean(title.trim() || content.trim() || linkUrl.trim() || selectedFile);
  useEffect(() => {
    if (!open) return;
    setTargetBaseId(baseId ?? "");
    // Capture opens ready to write; every other type is one keystroke away.
    setKind(initialKind ?? "note");
    setPasteHint(null);
    setDragging(false);
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
    setRecordingBaseId(baseId ?? "");
    setRecordingContent("");
    setRecordingTitleText(initialKind === "recording" ? defaultRecordingTitle(initialRecordingContext) : "");
    setSavedNotice(null);
    onRecordingState?.(false);
    setTitle("");
    setContent("");
  }, [baseId, initialKind, initialRecordingContext, onRecordingState, open]);
  useEffect(() => {
    if (kind && kind !== "recording") setOtherKind(kind);
  }, [kind]);
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
  const updateLectureContent = useCallback((value: string) => setRecordingContent(value), []);
  const updateRecordingDraft = useCallback((draft: RecordingDraft | null) => {
    setRecordingDraftId(draft?.id ?? null);
    setRecordingDrafts(loadRecordingDrafts());
  }, []);
  useEffect(() => {
    const expand = () => shortcutActions.current.showRecorder();
    const minimize = () => setRecordingMinimized(true);
    window.addEventListener("gunther:expand-capture", expand);
    window.addEventListener("gunther:minimize-capture", minimize);
    return () => {
      window.removeEventListener("gunther:expand-capture", expand);
      window.removeEventListener("gunther:minimize-capture", minimize);
    };
  }, []);
  useEffect(() => {
    // The menu bar names the recording while there is one.
    onRecorderSnapshot?.(recorderSnapshot, recorderOwnsCapture(recorderSnapshot) ? recordingTitleText : title, hasUnreviewedWork);
  }, [hasUnreviewedWork, onRecorderSnapshot, recorderSnapshot, recordingTitleText, title]);
  useEffect(() => {
    const launch = (event: Event) => shortcutActions.current.launch((event as CustomEvent<CaptureLaunchRequest>).detail);
    window.addEventListener(CAPTURE_SWITCH_DOM_EVENT, launch);
    return () => window.removeEventListener(CAPTURE_SWITCH_DOM_EVENT, launch);
  }, []);
  useEffect(() => {
    if (surface !== "window") return undefined;
    const control = (event: Event) => {
      const action = (event as CustomEvent<CaptureControl>).detail;
      if (action === "pause") recorderRef.current?.pause();
      else if (action === "resume") recorderRef.current?.resume();
      else if (action === "mark") recorderRef.current?.markMoment();
      else if (action === "finish") recorderRef.current?.stop();
      else if (action === "show") shortcutActions.current.showRecorder();
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
  // A photo keeps a local thumbnail while it waits to be saved.
  useEffect(() => {
    if (!selectedFile || !selectedFile.type.startsWith("image/")) {
      setPreviewUrl(null);
      return undefined;
    }
    const url = URL.createObjectURL(selectedFile);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [selectedFile]);
  useShortcut("mod+enter", () => shortcutActions.current.submit(), { enabled: open, allowInInputs: true, allowInModal: true });
  useShortcut(["mod+1", "mod+2", "mod+3", "mod+4", "mod+5", "mod+6"], (event) => {
    const index = Number(event.code.replace(/\D/g, "") || event.key) - 1;
    const next = captureKinds[index];
    if (next) shortcutActions.current.switchKind(next.id);
  }, { enabled: open, allowInInputs: true, allowInModal: true });
  // In its own window, Esc hides Capture to the menu bar; nothing is lost.
  useEscape(() => shortcutActions.current.hide(), open && surface === "window");
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
      setRecordingTitleText(recovered.title);
      setRecordingContext(recovered.recordingContext);
      if (bases.some((base) => base.id === recovered.knowledgeBaseId)) setRecordingBaseId(recovered.knowledgeBaseId);
      setRecordingDraftId(recovered.id);
      recorderRef.current?.restore(recovered);
      setInterruptedRecordings((current) => current.filter((item) => item.id !== recording.id));
    } catch (reason) {
      if (reason instanceof Error && reason.message.toLowerCase().includes("not found")) {
        // Recordings created before the lifecycle ledger are still downloadable by asset id.
        setRecordingTitleText(draft.title);
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
    knowledgeBaseId: recording.knowledgeBaseId ?? recordingBaseId,
    workspaceId,
    updatedAt: recording.checkpointedAt ?? recording.updatedAt,
  });

  /** Start the next recording afresh; whatever the other tabs hold stays. */
  const resetRecordingSession = () => {
    setRecorderKey((value) => value + 1);
    setRecordingActive(false);
    setRecordingMinimized(false);
    setRecorderSnapshot(emptyRecorderSnapshot);
    setRecordingDraftId(null);
    setRecordingDrafts(loadRecordingDrafts());
    setRecordingContent("");
    setRecordingTitleText("");
    onRecordingState?.(false);
    setKind(otherKind);
  };

  const saveCapture = async (saving: CaptureKind | null) => {
    if (!saving) return;
    const isRecording = saving === "recording";
    const saveTitle = isRecording ? recordingTitleText : title;
    const saveText = isRecording ? recordingContent : content;
    const saveBaseId = isRecording ? recordingBaseId : targetBaseId;
    const normalizedUrl = saving === "link" ? normalizedWebUrl(linkUrl) : null;
    if (saving === "link" && !normalizedUrl) {
      setFileError("Enter a public http or https web page without embedded credentials.");
      return;
    }
    const assetFile = (saving === "file" || saving === "image") ? selectedFile : null;
    if (saving !== "link" && !saveText.trim() && !assetFile) return;
    // Captured by value: a recording that ends while this saves is left alone.
    const recordingContinues = !isRecording && recordingActive;
    setWorking(true);
    setFileError(null);
    setSavedNotice(null);
    try {
      const boundWorkspaceId = workspaceId ?? await resolveWorkspaceId?.().catch(() => null) ?? null;
      if (!boundWorkspaceId) {
        throw new Error("Gunther could not verify this local workspace yet. Keep this window open and retry when the knowledge service reconnects.");
      }
      if (assetFile && (saving === "file" || saving === "image")) {
        await onAssetCaptured(assetFile, title || assetFile.name, targetBaseId || null, saving, content.trim(), boundWorkspaceId);
      } else {
        const captureId = `cap-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        appendStoredCapture({
          id: captureId,
          title: saveTitle,
          content: saveText,
          kind: saving,
          baseId: saveBaseId,
          workspaceId: boundWorkspaceId,
          ...(normalizedUrl ? { url: normalizedUrl } : {}),
          createdAt: new Date().toISOString(),
        });
        await onCaptured(saving === "link" ? saveTitle : saveTitle || "Untitled source", saveBaseId || null, saveText.trim(), saving, captureId, normalizedUrl ?? undefined, boundWorkspaceId);
      }
      // When the parent keeps Capture open, clear only what was just saved.
      if (isRecording) {
        if (recordingDraftId) removeRecordingDraft(recordingDraftId);
        resetRecordingSession();
      } else {
        clearCaptureInput();
        setPasteHint(null);
        if (recordingContinues) {
          setKind("recording");
          setSavedNotice(`Saved “${saveTitle.trim() || "your capture"}”. The recording kept going.`);
          if (surface === "overlay") setRecordingMinimized(true);
        }
      }
    } catch (reason) {
      setFileError(reason instanceof Error ? reason.message : "This source could not be preserved. It remains open so you can retry.");
    } finally {
      setWorking(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void saveCapture(kind);
  };

  const recorderIsLive = recorderSnapshot.phase === "recording" || recorderSnapshot.phase === "paused";
  const recorderStatus = recorderSnapshot.phase === "recording" ? "Recording" : recorderSnapshot.phase === "paused" ? "Paused" : recorderSnapshot.phase === "stopped" ? "Ready to file" : recorderSnapshot.phase === "importing" ? "Importing audio" : "Preparing";
  const canSubmit = kind === "link"
    ? Boolean(normalizedWebUrl(linkUrl))
    : kind === "recording"
      ? Boolean(recordingContent.trim())
      : Boolean(content.trim() || ((kind === "file" || kind === "image") && selectedFile));
  const confirmDiscard = () => !hasUnreviewedWork || window.confirm("Discard this unsaved Capture? This cannot be undone.");
  const clearCaptureInput = () => {
    setTitle("");
    setContent("");
    setLinkUrl("");
    setFileName("");
    setFileError(null);
    setSelectedFile(null);
  };
  const discardCapture = () => {
    if (!confirmDiscard()) return;
    clearCaptureInput();
    setPasteHint(null);
    if (kind === null) setKind("note");
  };
  const switchCaptureKind = (nextKind: CaptureKind) => {
    if (nextKind === kind) return;
    // The recording tab holds nothing of the others, so visiting it loses nothing.
    const keepsFile = nextKind === "recording" || nextKind === "file" || (nextKind === "image" && selectedFile?.type.startsWith("image/"));
    const losesFile = Boolean(selectedFile) && !keepsFile;
    if (losesFile && !window.confirm("Switch type and drop the chosen file? This cannot be undone.")) return;
    if (losesFile) {
      setSelectedFile(null);
      setFileName("");
    }
    if (nextKind === "recording" && !recordingActive && !recordingTitleText.trim()) {
      setRecordingTitleText(defaultRecordingTitle(recordingContext));
    }
    setFileError(null);
    setPasteHint(null);
    setSavedNotice(null);
    setKind(nextKind);
  };
  const showRecorder = () => {
    setRecordingMinimized(false);
    if (recordingActive) {
      setSavedNotice(null);
      setKind("recording");
    }
  };
  /** Another entry point asked for Capture while this one is busy. */
  const applyLaunch = (request: CaptureLaunchRequest) => {
    const requested = request.kind ?? "note";
    setRecordingMinimized(false);
    setSavedNotice(null);
    if (requested === "recording") {
      if (!recordingActive) {
        const context = request.recordingContext ?? recordingContext;
        setRecordingContext(context);
        setRecordingTitleText(defaultRecordingTitle(context));
        if (request.targetBaseId !== undefined) setRecordingBaseId(request.targetBaseId ?? "");
      }
      setKind("recording");
      return;
    }
    if (hasUnreviewedWork) {
      // Unsaved text is never replaced: show it, and let the person decide.
      setKind(otherKind);
      return;
    }
    if (request.targetBaseId !== undefined) setTargetBaseId(request.targetBaseId ?? "");
    setFileError(null);
    setPasteHint(null);
    setKind(requested);
  };
  const acceptFile = (file: File, targetKind: "file" | "image") => {
    setFileError(null);
    if (targetKind === "image" && !file.type.startsWith("image/")) {
      setFileError("Choose an image from your camera or photo library.");
      return;
    }
    if (file.size > 512 * 1024 * 1024) {
      setFileError(`Choose ${targetKind === "image" ? "an image" : "a file"} smaller than 512 MB.`);
      return;
    }
    if (!file.size) {
      setFileError("The selected file is empty.");
      return;
    }
    if (kind !== targetKind) setKind(targetKind);
    setSavedNotice(null);
    setSelectedFile(file);
    setFileName(file.name);
    if (!title.trim() || title === selectedFile?.name.replace(/\.[^.]+$/, "")) setTitle(file.name.replace(/\.[^.]+$/, ""));
  };
  const removeFile = () => {
    if (selectedFile && title === selectedFile.name.replace(/\.[^.]+$/, "")) setTitle("");
    setSelectedFile(null);
    setFileName("");
  };
  const onDropFile = (event: DragEvent<HTMLFormElement>) => {
    if (!event.dataTransfer?.types.includes("Files")) return;
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) acceptFile(file, file.type.startsWith("image/") ? "image" : "file");
  };
  const onPasteText = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    if (kind !== "note" || content.trim()) return;
    const pasted = event.clipboardData.getData("text/plain").trim();
    const url = /^\S+$/.test(pasted) ? normalizedWebUrl(pasted) : null;
    if (url && /^(https?:\/\/|www\.)/i.test(pasted)) setPasteHint({ kind: "link", value: url });
    else if (parseDelimitedTable(pasted) && pasted.includes("\n")) setPasteHint({ kind: "table", value: pasted });
  };
  const applyPasteHint = () => {
    if (!pasteHint) return;
    if (pasteHint.kind === "link") {
      setLinkUrl(pasteHint.value);
      setContent("");
      setKind("link");
    } else {
      setKind("table");
    }
    setPasteHint(null);
  };
  const keepDraftAndClose = () => {
    if (hasUnreviewedWork) {
      // The recording stays a draft; the unsaved note beside it stays open.
      resetRecordingSession();
      if (surface === "window") onHide?.();
      return;
    }
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
  const activeKind = captureKinds.find((item) => item.id === kind) ?? captureKinds[0]!;
  const detectedTable = kind === "table" && content.trim() ? parseDelimitedTable(content) : null;
  const linkPreview = kind === "link" ? normalizedWebUrl(linkUrl) : null;
  const statusLine = kind === "recording"
    ? recorderIsLive ? `${recorderStatus} · ${formatRecorderTime(recorderSnapshot.seconds)}` : recorderSnapshot.phase === "stopped" ? "Review the recording, then save it" : "Recording keeps going when this window is hidden"
    : recorderIsLive ? `${recorderStatus} in the background · ${formatRecorderTime(recorderSnapshot.seconds)}`
      : targetBase ? `Saving to ${targetBase.title}` : "Saved to Inbox until you choose a home";
  const submitLabel = working ? "Saving…" : ({ note: "Save note", link: "Save web page", file: "Save document", image: "Save photo", table: "Save table", recording: "Save recording" } as const)[activeKind.id];
  const onKindKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const index = captureKinds.findIndex((item) => item.id === activeKind.id);
    const next = captureKinds[(index + (event.key === "ArrowRight" ? 1 : captureKinds.length - 1)) % captureKinds.length]!;
    switchCaptureKind(next.id);
    window.requestAnimationFrame(() => document.getElementById(`capture-kind-${next.id}`)?.focus());
  };

  const captureForm = <form
    ref={captureFormRef}
    className={`gx-capture ${surface === "window" ? "is-window" : "is-overlay"} kind-${activeKind.id} ${dragging ? "is-dragging" : ""}`}
    onMouseDown={(event) => event.stopPropagation()}
    onSubmit={submit}
    onDragOver={(event) => {
      if (!event.dataTransfer?.types.includes("Files")) return;
      event.preventDefault();
      setDragging(true);
    }}
    onDragLeave={(event) => { if (event.currentTarget === event.target) setDragging(false); }}
    onDrop={onDropFile}
    aria-label="Capture"
  >
    <header className="gx-capture-head" role="none" data-tauri-drag-region={surface === "window" ? true : undefined} onMouseDown={startWindowDrag}>
      <span className="gx-capture-brand" data-tauri-drag-region={surface === "window" ? true : undefined}>
        <BrandMark size={17} busy={recorderSnapshot.phase === "recording" || working} />
        <strong data-tauri-drag-region={surface === "window" ? true : undefined}>Capture</strong>
      </span>
      <span className={`gx-capture-status ${recorderIsLive ? "is-live" : ""}`} role="status" data-tauri-drag-region={surface === "window" ? true : undefined}>{recorderIsLive && <i />}{statusLine}</span>
      <span className="gx-capture-head-actions" data-no-drag>
        {onSearch && <button type="button" className="gx-icon-button" onClick={() => { if (surface === "overlay") onClose(true); onSearch(); }} aria-label="Search your knowledge" title={withShortcut("Search your knowledge", "search")}><Search size={15} /></button>}
        {surface === "window"
          ? <button type="button" className="gx-capture-hide" onClick={keepInBackground} title={`${hideActionLabel}  Esc`} aria-label={hideActionLabel}><Minimize2 size={14} /><span>{hideActionLabel}</span></button>
          : <>
            {recordingActive
              ? <button type="button" className="gx-icon-button" onClick={keepInBackground} title="Keep recording in the background" aria-label="Minimize recording"><Minimize2 size={15} /></button>
              : <button type="button" className="gx-icon-button" onClick={closeCaptureSurface} title="Close  Esc" aria-label="Close capture"><X size={16} /></button>}
          </>}
      </span>
    </header>

    <div className="gx-capture-kinds" role="tablist" aria-label="Capture type" onKeyDown={onKindKeyDown}>
      {captureKinds.map(({ id, label, icon: Icon, tone }, index) => (
        <button
          type="button"
          role="tab"
          id={`capture-kind-${id}`}
          key={id}
          aria-selected={activeKind.id === id}
          aria-controls="capture-panel"
          tabIndex={activeKind.id === id ? 0 : -1}
          className={`gx-capture-kind tone-${tone} ${activeKind.id === id ? "is-active" : ""}`}
          onClick={() => switchCaptureKind(id)}
          title={`${label}  ${formatCombo(`mod+${index + 1}`)}`}
        >
          <Icon size={15} />
          <span>{label}</span>
          {id === "recording" && recordingActive && activeKind.id !== "recording" && <em className={`gx-capture-kind-live ${recorderIsLive && recorderSnapshot.phase === "recording" ? "is-live" : ""}`} aria-label={`${recorderStatus} ${formatRecorderTime(recorderSnapshot.seconds)}`}><i />{recorderSnapshot.phase === "stopped" ? "Review" : formatRecorderTime(recorderSnapshot.seconds)}</em>}
        </button>
      ))}
    </div>

    <div className="gx-capture-body" id="capture-panel" role="tabpanel" aria-labelledby={`capture-kind-${activeKind.id}`}>
      {kind === "recording" && <div className="gx-capture-context" role="radiogroup" aria-label="Recording type">
        {recordingContexts.map((context) => <button type="button" role="radio" aria-checked={recordingContext === context.id} key={context.id} className={recordingContext === context.id ? "is-active" : ""} disabled={recordingActive} onClick={() => { setRecordingContext(context.id); setRecordingTitleText(defaultRecordingTitle(context.id)); }} title={context.description}>{context.label}</button>)}
      </div>}
      {kind === "recording" && recorderSnapshot.phase === "idle" && (recoverableRecordingDrafts.length > 0 || interruptedRecordings.length > 0) && <div className="gx-capture-drafts">
        <header><strong>Continue a preserved session</strong><em>{recoverableRecordingDrafts.length + interruptedRecordings.length} saved</em></header>
        {recoverableRecordingDrafts.slice(0, 3).map((draft) => <button type="button" key={draft.id} disabled={recoveringRecordingId === draft.recording.id} onClick={() => void restoreRecording(draft)}><Mic2 size={14} /><span><strong>{draft.title}</strong><small>{formatRecorderTime(draft.seconds)} · {draft.transcript.trim() ? `${draft.transcript.trim().split(/\s+/).length} words` : "audio preserved"}</small></span><em>{recoveringRecordingId === draft.recording.id ? "Recovering…" : new Date(draft.updatedAt).toLocaleDateString()}</em><ChevronRight size={14} /></button>)}
        {interruptedRecordings.slice(0, Math.max(0, 3 - recoverableRecordingDrafts.length)).map((recording) => <button type="button" key={recording.id} disabled={recoveringRecordingId === recording.id} onClick={() => void restoreInterruptedRecording(recording)}><CircleAlert size={14} /><span><strong>{recording.title}</strong><small>Interrupted · {recording.sizeBytes >= 1_048_576 ? `${(recording.sizeBytes / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(recording.sizeBytes / 1024))} KB`} safe</small></span><em>{recoveringRecordingId === recording.id ? "Recovering…" : "Recover"}</em><ChevronRight size={14} /></button>)}
      </div>}

      {(kind === "file" || kind === "image") && (selectedFile ? (
        <div className={`gx-capture-file ${kind === "image" ? "is-image" : ""}`}>
          {kind === "image" && previewUrl ? <img src={previewUrl} alt="" /> : <span className="gx-capture-file-mark"><FileText size={20} /><em>{fileExtension(selectedFile.name)}</em></span>}
          <span><strong title={fileName}>{fileName}</strong><small>{formatFileSize(selectedFile.size)} · {selectedFile.type || "file"} · stays exactly as it is</small></span>
          <label className="gx-btn gx-btn-quiet gx-btn-sm"><input type="file" className="gx-sr-only" accept={kind === "image" ? "image/*" : FILE_ACCEPT} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) acceptFile(file, kind); }} />Replace</label>
          <button type="button" className="gx-icon-button" onClick={removeFile} aria-label="Remove file" title="Remove"><X size={14} /></button>
        </div>
      ) : (
        <label className="gx-capture-drop">
          <input type="file" className="gx-sr-only" accept={kind === "image" ? "image/*" : FILE_ACCEPT} capture={kind === "image" ? "environment" : undefined} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) acceptFile(file, kind); }} aria-label={kind === "image" ? "Choose a photo" : "Choose a file"} />
          <span className="gx-capture-drop-icon">{kind === "image" ? <ImagePlus size={22} /> : <FileUp size={22} />}</span>
          <strong>{kind === "image" ? "Drop a photo, or choose one" : "Drop a file, or choose one"}</strong>
          <small>{kind === "image" ? "Pages, whiteboards, diagrams, screenshots · text is recognised" : "PDF · DOCX · EPUB · Markdown · text · CSV · up to 512 MB"}</small>
        </label>
      ))}

      {kind === "link" && <div className="gx-capture-url">
        <Globe size={16} />
        <input type="text" inputMode="url" autoCapitalize="none" autoCorrect="off" spellCheck={false} value={linkUrl} onChange={(event) => { setLinkUrl(event.target.value); setFileError(null); }} placeholder="Paste a link — https://…" maxLength={2_048} aria-label="Web page URL" autoFocus />
        {linkPreview && <span className="gx-capture-url-ok"><Check size={13} />{hostLabel(linkPreview)}</span>}
      </div>}
      {kind === "link" && <p className="gx-capture-note">{linkPreview ? "Gunther keeps an unchangeable snapshot of this page, even if it changes or disappears later." : "Any public http or https page. The page itself is fetched and kept on this device."}</p>}

      {fileError && <p className="gx-capture-error" role="alert"><CircleAlert size={13} />{fileError}</p>}
      {savedNotice && <p className="gx-capture-note is-ok" role="status"><Check size={13} />{savedNotice}</p>}

      {kind !== null && kind !== "recording" && <input className="gx-capture-title" value={title} onChange={(event) => setTitle(event.target.value)} placeholder={kind === "link" ? "Title (optional — the page title is used otherwise)" : kind === "table" ? "What is this table?" : kind === "image" ? "What is this image? (optional)" : kind === "file" ? "Title (optional)" : "Title (optional)"} maxLength={160} aria-label="Title" />}
      {kind === "recording" && <input className="gx-capture-title" value={recordingTitleText} onChange={(event) => setRecordingTitleText(event.target.value)} placeholder={`${recordingContext === "lecture" ? "Course" : recordingContext === "meeting" ? "Meeting" : "Voice memo"} title`} maxLength={160} aria-label="Title" />}

      {(kind === "link" || kind === "file" || kind === "image") && <label className="gx-capture-label" htmlFor="capture-content">{kind === "file" && !selectedFile ? "Or paste the text itself" : "Why you’re saving it"}<span>optional</span></label>}
      {/* Once started, the recorder stays mounted (and recording) while other tabs are in use. */}
      {(kind === "recording" || recordingActive) && <div className="gx-capture-recorder" hidden={kind !== "recording"}>
        <LectureRecorder key={recorderKey} ref={recorderRef} title={recordingTitleText} knowledgeBaseId={recordingBaseId} workspaceId={workspaceId} recordingContext={recordingContext} onKnowledgeContent={updateLectureContent} onTitleChange={setRecordingTitleText} onStatusChange={setRecorderSnapshot} onDraftChange={updateRecordingDraft} onActiveChange={(active) => { setRecordingActive(active); onRecordingState?.(active); }} />
      </div>}
      {kind !== null && kind !== "recording" && <textarea
        id="capture-content"
        className={`gx-capture-text ${kind === "table" ? "is-data" : ""} ${kind === "note" ? "is-note" : ""}`}
        value={content}
        onChange={(event) => { setContent(event.target.value); if (pasteHint && !event.target.value.trim()) setPasteHint(null); }}
        onPaste={onPasteText}
        placeholder={kind === "link" ? "Why are you saving this page? What should Gunther pay attention to?" : kind === "table" ? "Paste rows from a spreadsheet, including the header row…" : kind === "image" ? "Add context: what does it show, and why does it matter?" : kind === "file" ? selectedFile ? "Add context: what should Gunther pay attention to?" : "…or paste the text itself here" : "Write the thought as it comes. Markdown works — # heading, - list, - [ ] task."}
        rows={kind === "note" ? 10 : kind === "table" ? 9 : 4}
        maxLength={1_000_000}
        aria-label="Source content"
        autoFocus={kind === "note" || kind === "table"}
      />}
      {pasteHint && <div className="gx-capture-hint" role="status">
        {pasteHint.kind === "link" ? <Globe size={14} /> : <Table2 size={14} />}
        <span>{pasteHint.kind === "link" ? <>That looks like a link. Save <strong>{hostLabel(pasteHint.value)}</strong> as a web page with a snapshot?</> : "That looks like a table. Save it as rows and columns?"}</span>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={applyPasteHint}>{pasteHint.kind === "link" ? "Save as web page" : "Save as table"}</button>
        <button type="button" className="gx-icon-button" onClick={() => setPasteHint(null)} aria-label="Keep as a note" title="Keep as a note"><X size={13} /></button>
      </div>}
      {kind === "table" && content.trim() && <p className={`gx-capture-note ${detectedTable ? "is-ok" : ""}`}>{detectedTable ? <><Check size={13} />{detectedTable.rows.length} {detectedTable.rows.length === 1 ? "row" : "rows"} · {detectedTable.header.length} columns detected — {detectedTable.header.slice(0, 4).join(", ")}{detectedTable.header.length > 4 ? "…" : ""}</> : "Keep one row per line and separate columns with tabs or commas."}</p>}
    </div>

    <footer className="gx-capture-foot">
      <div className="gx-capture-destination" data-no-drag>
        <span>Save to</span>
        {kind === "recording"
          ? <LibraryPicker bases={bases} value={recordingBaseId} onChange={setRecordingBaseId} noneLabel="Inbox" disabled={recordingActive} label="Save to" placement="above" />
          : <LibraryPicker bases={bases} value={targetBaseId} onChange={setTargetBaseId} noneLabel="Inbox" label="Save to" placement="above" />}
        {chapter && kind !== "recording" && <em>→ {chapter.title}</em>}
      </div>
      <span className="gx-capture-foot-fill" />
      {hasUnreviewedWork && kind !== "recording" && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={discardCapture} aria-label="Discard this capture" title="Discard this capture"><Trash2 size={13} />Discard</button>}
      {kind === "recording" && recorderSnapshot.phase === "stopped" && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={keepDraftAndClose}>Keep as draft</button>}
      {kind === "recording" && recorderIsLive && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={keepInBackground}><Minimize2 size={13} />Keep in background</button>}
      <button className="gx-btn gx-btn-primary gx-capture-save" disabled={working || !canSubmit} aria-label={submitLabel}>
        {working ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}
        <span>{submitLabel}</span>
        <span className="gx-keys" aria-hidden="true">{comboKeys("mod+enter").map((key) => <kbd key={key}>{key}</kbd>)}</span>
      </button>
    </footer>
    {dragging && <div className="gx-capture-dropping" aria-hidden="true"><FileUp size={26} /><strong>Drop to capture</strong><small>The original stays exactly as it is</small></div>}
  </form>;

  shortcutActions.current = {
    submit: () => { if (canSubmit && !working) captureFormRef.current?.requestSubmit(); },
    switchKind: switchCaptureKind,
    hide: closeCaptureSurface,
    launch: applyLaunch,
    showRecorder,
  };

  const recordingDock = recordingActive && recordingMinimized && <aside className={`recording-session-dock is-${recorderSnapshot.phase}`} aria-label="Background recording session">
      <button type="button" className="recording-dock-summary" onClick={showRecorder}>
        <span className={`recording-dock-pulse ${recorderIsLive ? "is-live" : ""}`}><Mic2 size={15} /></span>
        <span><small>{recorderStatus} · {recorderSnapshot.transcriptionLabel}</small><strong>{recordingTitleText}</strong><em>{recordingTargetLabel} · {recorderSnapshot.transcriptWords ? `${recorderSnapshot.transcriptWords} words` : recorderSnapshot.persistence === "saved" ? "preserved locally" : "saving locally"}{recorderSnapshot.markedMoments ? ` · ${recorderSnapshot.markedMoments} marked` : ""}</em></span>
      </button>
      <strong className="recording-dock-time">{formatRecorderTime(recorderSnapshot.seconds)}</strong>
      <div className="recording-dock-controls">
        {recorderIsLive && <button type="button" onClick={() => recorderRef.current?.markMoment()} aria-label="Mark important moment"><Bookmark size={14} /></button>}
        {recorderSnapshot.phase === "recording" && <button type="button" onClick={() => recorderRef.current?.pause()} aria-label="Pause recording"><Pause size={14} /></button>}
        {recorderSnapshot.phase === "paused" && <button type="button" onClick={() => recorderRef.current?.resume()} aria-label="Resume recording"><Play size={14} /></button>}
        {recorderIsLive && <button type="button" className="is-finish" onClick={() => recorderRef.current?.stop()}><Square size={12} />Finish</button>}
        {recorderSnapshot.phase === "stopped" && <button type="button" onClick={keepDraftAndClose}>Keep draft</button>}
        {recorderSnapshot.phase === "stopped" && <button type="button" className="is-save" disabled={working || !recordingContent.trim()} onClick={() => void saveCapture("recording")}>Save to knowledge</button>}
        <button type="button" onClick={showRecorder} aria-label="Expand recording"><Maximize2 size={15} /></button>
      </div>
    </aside>;

  if (surface === "window") {
    return <main className="capture-window-surface">{captureForm}</main>;
  }

  return <>
    <section className={`atlas-overlay ${recordingMinimized ? "is-capture-minimized" : ""}`} onMouseDown={recordingActive ? undefined : closeCaptureSurface} role="dialog" aria-modal="true" aria-label={kind === "recording" ? "Recording workspace" : "Capture something"}>
      {captureForm}
    </section>
    {recordingDock}
  </>;
}

export function CreateKnowledgeBaseSheet({ open, base, onClose, onSave, onTrash }: { open: boolean; base?: KnowledgeBase | undefined; onClose: () => void; onSave: (payload: CreateKnowledgeBaseInput) => Promise<void>; /** Editing only: move the library, and the sources only it holds, to Trash. */ onTrash?: () => void }) {
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
      setError(reason instanceof Error ? reason.message : "Could not create the library");
    } finally {
      setWorking(false);
    }
  };

  return <section className="atlas-overlay" onMouseDown={onClose} role="dialog" aria-modal="true" aria-label={base ? "Edit library" : "Create library"}><form className="create-base-sheet" onMouseDown={(event) => event.stopPropagation()} onSubmit={(event) => void submit(event)}>
    <header role="none"><span><small>{base ? "Library settings" : "New library"}</small><strong>{base ? "Edit library" : "Start a library"}</strong></span><button type="button" onClick={onClose} aria-label="Close library details"><X size={17} /></button></header>
    <div className="create-base-intro"><span><BookOpen size={18} /></span><div><h2>{base ? "Keep the field boundary explicit." : "Create a lasting home for this subject."}</h2><p>{base ? "Changes update the library’s identity without rewriting its sessions, sources, or accepted revisions." : "A library is a durable home for one subject — for example, Bioinformatics. Capture first, then file documents, notes, photos, links, recordings and data here once they belong together."}</p></div></div>
    {!base && <div className="create-base-path" aria-label="Library setup path"><span><i>1</i><strong>Name the subject</strong><small>Create its boundary</small></span><em /><span><i>2</i><strong>Bring sources</strong><small>Any captured material</small></span><em /><span><i>3</i><strong>Build knowledge</strong><small>Review and connect</small></span></div>}
    <div className="create-base-fields"><label><span>Name</span><input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} placeholder="e.g. Decision Science" maxLength={160} /></label><label><span>Field label</span><input value={eyebrow} onChange={(event) => setEyebrow(event.target.value)} placeholder="e.g. Applied reasoning" maxLength={80} /></label><label className="is-wide"><span>Guiding question</span><textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="What do you want this library to help you understand?" rows={2} maxLength={1000} /></label><label className="is-wide"><span>Description</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Describe its scope, boundaries, and intended use." rows={3} maxLength={2000} /></label></div>
    <fieldset className="create-base-color"><legend>Accent</legend>{(["green", "blue", "clay"] as const).map((item) => <button type="button" key={item} className={`color-${item} ${color === item ? "is-active" : ""}`} onClick={() => setColor(item)} aria-label={`Use ${item} accent`}><i />{item}{color === item && <Check size={12} />}</button>)}</fieldset>
    {error && <p className="create-base-error" role="alert"><CircleAlert size={13} />{error}</p>}
    <footer>{base && onTrash ? <button type="button" className="quiet-button gx-sheet-trash" onClick={onTrash}><Trash2 size={13} />Move library to Trash</button> : <span>{base ? "Session history and accepted knowledge stay unchanged." : "Inbox sources can be filed here at any time."}</span>}<div><button type="button" className="quiet-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={working || !title.trim() || question.trim().length < 3 || description.trim().length < 3}>{working ? "Saving…" : base ? "Save details" : "Create library"}<ArrowRight size={14} /></button></div></footer>
  </form></section>;
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

export function SettingsPageV2({ theme, onTheme, onNotify }: { theme: ThemePreference; onTheme: (theme: ThemePreference) => void; onNotify: (message: string) => void }) {
  const [online, setOnline] = useState<boolean | null>(null);
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
  const [menuBarMode, setMenuBarModeState] = useState<MenuBarMode>("always");
  useEffect(() => {
    if (isTauriRuntime()) void getMenuBarMode().then(setMenuBarModeState).catch(() => undefined);
  }, []);
  const changeMenuBarMode = (mode: MenuBarMode) => {
    setMenuBarModeState(mode);
    void setMenuBarMode(mode)
      .then(() => onNotify(mode === "always" ? "Gunther stays in the menu bar." : "Gunther appears in the menu bar only while something is being captured."))
      .catch(() => onNotify("The menu bar preference could not be saved."));
  };
  const connection = useMemo(() => {
    const current = mobileConnectionFromGateway(gatewayStatus);
    return gatewayError ? { ...current, explanation: gatewayError } : current;
  }, [gatewayError, gatewayStatus]);

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
    void knowledgeApi.health().then(() => setOnline(true)).catch(() => setOnline(false));
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
    <header className="gx-page-header"><div><h1>Settings</h1><p>Local storage is the default. Online services stay visible and optional, and AI never silently rewrites accepted knowledge.</p></div></header>
    <div className="settings-columns">
      <section>
        <div className="setting-heading"><Settings2 size={16} /><span><strong>Appearance</strong><small>Default workspace presentation</small></span></div>
        <label className="setting-row"><span><strong>Theme</strong><small>Applied immediately and remembered on this device.</small></span><select value={theme} aria-label="Theme" onChange={(event) => onTheme(event.target.value as ThemePreference)}><option value="light">Light</option><option value="dark">Dark</option><option value="system">Match system</option></select></label>
        <TypographySettings />
        <div className="setting-row"><span><strong>Home</strong><small>Gunther opens on search. Type @ to scope a search to a library, or press ⌘K from anywhere.</small></span><span className="setting-state">Search</span></div>
        {isTauriRuntime() && <label className="setting-row"><span><strong>Menu bar</strong><small>Gunther’s mark stays in the menu bar for quick capture. Recordings always show there, with their timer, while they run.</small></span><select value={menuBarMode} aria-label="Menu bar" onChange={(event) => changeMenuBarMode(event.target.value as MenuBarMode)}><option value="always">Always show</option><option value="whileCapturing">Only while capturing</option></select></label>}
        <div className="setting-row"><span><strong>Keyboard shortcuts</strong><small>Capture, search and move through Inbox without the mouse. Press {formatCombo("mod+/")} any time.</small></span><button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => window.dispatchEvent(new CustomEvent("gunther:show-shortcuts"))}>Show all</button></div>
      </section>
      <section>
        <div className="setting-heading"><ShieldCheck size={16} /><span><strong>On this device</strong><small>Where your knowledge is kept and searched</small></span></div>
        <div className="setting-row"><span><strong>Local workbook</strong><small>Sources, audio, and accepted revisions stay on this device.</small></span><span className={`setting-state ${online === false ? "is-muted" : ""}`}><i />{online === null ? "Checking" : online ? "Ready" : "Offline"}</span></div>
        <SemanticSearchSetting />
      </section>
    </div>

    <ModelSettings onNotify={onNotify} />

    <SpeechSettings onNotify={onNotify} />

    <TtsSettings onNotify={onNotify} />

    <ServiceSettings onNotify={onNotify} />

    <LibraryFolderSettings onNotify={onNotify} onCopy={(label, value) => void copyValue(label, value)} />

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
      {devicesLoading ? <div className="paired-devices-empty"><span className="gx-spinner" aria-hidden="true" /><p>Checking device access…</p></div> : devices.length === 0 ? <div className="paired-devices-empty"><Smartphone size={20} /><strong>No paired devices</strong><p>Generate a one-time code when your secure mobile gateway is ready.</p></div> : <div className="paired-device-list">
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
  return <div className="utility-page account-v2 page-enter"><header className="gx-page-header"><div><h1>Account</h1><p>Your profile is stored on this device and travels with complete workbook exports.</p></div></header><section className={`profile-card-v2 ${editing ? "is-editing" : ""}`}><span className="profile-avatar-v2">{profile.initials}</span>{editing ? <form className="profile-edit-form" onSubmit={saveProfile}><label><span>Initials</span><input autoFocus value={draftProfile.initials} maxLength={3} onChange={(event) => setDraftProfile((current) => ({ ...current, initials: event.target.value }))} /></label><label><span>Name</span><input value={draftProfile.name} maxLength={80} onChange={(event) => setDraftProfile((current) => ({ ...current, name: event.target.value }))} /></label><label><span>Role</span><input value={draftProfile.role} maxLength={80} onChange={(event) => setDraftProfile((current) => ({ ...current, role: event.target.value }))} /></label><label className="is-wide"><span>Bio</span><textarea value={draftProfile.bio} rows={2} maxLength={240} onChange={(event) => setDraftProfile((current) => ({ ...current, bio: event.target.value }))} /></label><div><button type="button" className="quiet-button" onClick={() => { setDraftProfile(profile); setEditing(false); }}>Cancel</button><button className="primary-button"><Check size={13} />Save locally</button></div></form> : <><div><small>{profile.role}</small><h2>{profile.name}</h2><p>{profile.bio}</p></div><button className="quiet-button" onClick={() => setEditing(true)}>Edit profile</button></>}</section><section className="profile-stats-v2"><div><strong>{bases.length}</strong><span>libraries</span></div><div><strong>{bases.reduce((sum, base) => sum + base.chapterCount, 0)}</strong><span>chapters</span></div><div><strong>{bases.reduce((sum, base) => sum + (base.indexedSourceCount ?? 0), 0)}</strong><span>indexed sources</span></div></section></div>;
}
