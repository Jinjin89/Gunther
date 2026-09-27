import { useCallback, useEffect, useRef, useState } from "react";
import { CircleAlert, LoaderCircle, RotateCcw } from "lucide-react";
import { knowledgeApi } from "../api";
import type { KnowledgeBase } from "../atlas";
import { metadataToBase } from "../atlasMetadata";
import { CaptureSheet } from "../components/AtlasUtilities";
import type { RecorderSnapshot } from "../components/LectureRecorder";
import {
  emitCaptureSaved,
  emitOpenSearch,
  hideCaptureWindow,
  listenForCaptureControl,
  listenForCaptureRequest,
  takeCaptureLaunchRequest,
  updateCaptureStatus,
  showMainWindow,
} from "./captureBridge";
import { persistAssetCapture, persistTextCapture } from "./capturePersistence";
import {
  CAPTURE_CONTROL_DOM_EVENT,
  type CaptureControl,
  type CaptureLaunchRequest,
  type CaptureRuntimeStatus,
} from "./captureTypes";
import "../atlas.css";

const EMPTY_SNAPSHOT: RecorderSnapshot = {
  phase: "idle",
  seconds: 0,
  persistence: "idle",
  transcriptWords: 0,
  transcriptionLabel: "Live transcript",
  markedMoments: 0,
};

const DEFAULT_REQUEST: Required<Pick<CaptureLaunchRequest, "recordingContext">> & CaptureLaunchRequest = {
  kind: null,
  recordingContext: "lecture",
  targetBaseId: null,
  source: "capture-window",
};

function requestOwnsCapture(status: CaptureRuntimeStatus): boolean {
  return status.hasUnreviewedWork
    || status.phase === "requesting"
    || status.phase === "importing"
    || status.phase === "recording"
    || status.phase === "paused"
    || status.phase === "stopped";
}

export default function CaptureWindowApp() {
  const [request, setRequest] = useState<CaptureLaunchRequest>(DEFAULT_REQUEST);
  const [generation, setGeneration] = useState(0);
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<CaptureRuntimeStatus>({
    ...EMPTY_SNAPSHOT,
    title: "Capture something",
    hasUnreviewedWork: false,
  });
  const runtimeStatusRef = useRef(runtimeStatus);

  const refreshWorkspace = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [bootstrap, metadata] = await Promise.all([
        knowledgeApi.workspaceBootstrap(),
        knowledgeApi.knowledgeBases(),
      ]);
      setWorkspaceId(bootstrap.workspaceId);
      setBases(metadata.map(metadataToBase));
      return bootstrap.workspaceId;
    } catch (reason) {
      setWorkspaceId(null);
      setLoadError(reason instanceof Error ? reason.message : "Gunther could not verify the local workspace.");
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const applyRequest = useCallback((next: CaptureLaunchRequest | null) => {
    if (!next || requestOwnsCapture(runtimeStatusRef.current)) return;
    setRequest({
      kind: next.kind ?? null,
      recordingContext: next.recordingContext ?? "lecture",
      targetBaseId: next.targetBaseId ?? null,
      source: next.source ?? "capture-window",
    });
    setGeneration((value) => value + 1);
  }, []);

  useEffect(() => {
    void refreshWorkspace();
  }, [refreshWorkspace]);

  useEffect(() => {
    const syncTheme = () => {
      document.documentElement.dataset.theme = window.localStorage.getItem("gunther:v3-theme") === "dark"
        ? "dark"
        : "light";
    };
    syncTheme();
    window.addEventListener("storage", syncTheme);
    return () => window.removeEventListener("storage", syncTheme);
  }, []);

  useEffect(() => {
    let disposed = false;
    let receivedLiveRequest = false;
    const unlisteners: Array<() => void> = [];
    void (async () => {
      // Register first, then consume the pending slot. A second open request
      // arriving during startup is therefore observed by either the listener or
      // the pending command and can never fall between those two mechanisms.
      const [unlistenRequest, unlistenControl] = await Promise.all([
        listenForCaptureRequest((next) => {
          receivedLiveRequest = true;
          applyRequest(next);
        }),
        listenForCaptureControl((control: CaptureControl) => {
          window.dispatchEvent(new CustomEvent(CAPTURE_CONTROL_DOM_EVENT, { detail: control }));
        }),
      ]);
      if (disposed) {
        unlistenRequest();
        unlistenControl();
        return;
      }
      unlisteners.push(unlistenRequest, unlistenControl);
      const pending = await takeCaptureLaunchRequest();
      if (disposed) return;
      // Rust stores the request before emitting it. If the live listener saw
      // that emission, `take` is only clearing the fallback slot and must not
      // remount the Capture surface a second time.
      if (!receivedLiveRequest) applyRequest(pending);
    })().catch(() => {
      // The visible startup state remains usable while the native bridge retries on the next launch.
    });
    return () => {
      disposed = true;
      unlisteners.forEach((unlisten) => unlisten());
    };
  }, [applyRequest]);

  useEffect(() => {
    runtimeStatusRef.current = runtimeStatus;
    void updateCaptureStatus(runtimeStatus).catch(() => undefined);
  }, [runtimeStatus]);

  const reportSnapshot = useCallback((snapshot: RecorderSnapshot, title: string, hasUnreviewedWork: boolean) => {
    const nextStatus = { ...snapshot, title: title.trim() || "Untitled capture", hasUnreviewedWork };
    // Keep the synchronous guard ahead of React's effect flush so a launch
    // request arriving in the same frame cannot replace a recorder that has
    // just entered requesting/recording/review.
    runtimeStatusRef.current = nextStatus;
    setRuntimeStatus(nextStatus);
  }, []);

  const finishCapture = useCallback(async (message: string) => {
    const idleStatus = { ...EMPTY_SNAPSHOT, title: "Capture something", hasUnreviewedWork: false };
    // Persistence has completed. Release this singleton synchronously before
    // yielding so a later launch can safely claim it without being overwritten
    // by delayed cleanup from the saved Capture.
    setRequest(DEFAULT_REQUEST);
    setGeneration((value) => value + 1);
    runtimeStatusRef.current = idleStatus;
    setRuntimeStatus(idleStatus);
    await hideCaptureWindow().catch(() => undefined);
    await refreshWorkspace();
    await emitCaptureSaved(message).catch(() => undefined);
  }, [refreshWorkspace]);

  const releaseCaptureOwnership = useCallback(async () => {
    const idleStatus = { ...EMPTY_SNAPSHOT, title: "Capture something", hasUnreviewedWork: false };
    setRequest(DEFAULT_REQUEST);
    setGeneration((value) => value + 1);
    runtimeStatusRef.current = idleStatus;
    setRuntimeStatus(idleStatus);
    await hideCaptureWindow().catch(() => undefined);
  }, []);

  const openSearchInMainWindow = useCallback(async () => {
    const mainVisible = await showMainWindow().then(() => true).catch(() => false);
    if (!mainVisible) return;
    const routed = await emitOpenSearch().then(() => true).catch(() => false);
    if (!routed) return;
    await hideCaptureWindow().catch(() => undefined);
  }, []);

  if (loading && !workspaceId) {
    return <main className="capture-window-bootstrap" role="status"><LoaderCircle className="spin" size={20} /><span><strong>Opening Capture</strong><small>Verifying your private local workspace…</small></span></main>;
  }

  if (loadError && !workspaceId) {
    return <main className="capture-window-bootstrap is-error" role="alert"><CircleAlert size={20} /><span><strong>Capture is waiting for Gunther</strong><small>{loadError}</small></span><button type="button" onClick={() => void refreshWorkspace()}><RotateCcw size={14} />Retry</button></main>;
  }

  return <CaptureSheet
    key={generation}
    open
    surface="window"
    bases={bases}
    workspaceId={workspaceId}
    resolveWorkspaceId={refreshWorkspace}
    baseId={request.targetBaseId ?? null}
    initialKind={request.kind ?? null}
    initialRecordingContext={request.recordingContext ?? "lecture"}
    onRecorderSnapshot={reportSnapshot}
    onHide={() => { void hideCaptureWindow().catch(() => undefined); }}
    onClose={(force = false) => {
      if (force) void releaseCaptureOwnership();
      else void hideCaptureWindow().catch(() => undefined);
    }}
    onSearch={() => { void openSearchInMainWindow(); }}
    onCaptured={async (title, baseId, content, kind, captureId, url, boundWorkspaceId) => {
      const result = await persistTextCapture({
        title,
        baseId,
        content,
        kind,
        captureId,
        url,
        workspaceId: boundWorkspaceId,
      });
      await finishCapture(result.message);
    }}
    onAssetCaptured={async (file, title, baseId, kind, notes, boundWorkspaceId) => {
      const result = await persistAssetCapture({
        file,
        title,
        baseId,
        kind,
        notes,
        workspaceId: boundWorkspaceId,
      });
      await finishCapture(result.message);
    }}
  />;
}
