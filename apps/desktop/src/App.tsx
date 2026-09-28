import { Check } from "lucide-react";
import type { CreateKnowledgeBaseInput, CreateSourceInput, NotebookNote } from "@gunther/contracts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getKnowledgeBase, type AtlasMode, type KnowledgeBase, type KnowledgeChapter } from "./atlas";
import { metadataToBase } from "./atlasMetadata";
import { AtlasPage, AtlasRail, AtlasTitlebar, type AtlasView } from "./components/AtlasChrome";
import { AccountPageV2, CaptureSheet, CreateKnowledgeBaseSheet, EvidenceDrawer, SettingsPageV2, type CaptureKind } from "./components/AtlasUtilities";
import { AtlasLibraryPage } from "./pages/AtlasLibraryPage";
import { HomePage, type HomeCaptureKind } from "./pages/HomePage";
import { InboxPageV3 } from "./pages/InboxPage";
import { KnowledgeBaseWorkspace } from "./pages/KnowledgeBaseWorkspace";
import { NotebookPage } from "./pages/NotebookPage";
import { knowledgeApi } from "./api";
import { loadStoredCaptures, removeStoredCapture } from "./localCaptureQueue";
import { isTauriRuntime, listenForCaptureSaved, listenForOpenSearch, openCaptureWindow } from "./capture/captureBridge";
import { persistAssetCapture, persistTextCapture } from "./capture/capturePersistence";
import { applyTheme, readThemePreference, resolveTheme, THEME_STORAGE_KEY, watchSystemTheme, type ThemePreference } from "./design/theme";
import "./atlas.css";

type RecordingContext = "lecture" | "meeting" | "memo";

const WORKSPACE_ID_STORAGE_KEY = "gunther:workspace-id";
const PROFILE_STORAGE_KEY = "gunther:profile";
const DEFAULT_PROFILE_NAME = "Knowledge explorer";
const DEFAULT_KNOWLEDGE_BASE_MODE: AtlasMode = "ask";

/** The local profile name, or an empty string while it is still the placeholder. */
const readProfileName = () => {
  try {
    const profile = JSON.parse(window.localStorage.getItem(PROFILE_STORAGE_KEY) ?? "null") as { name?: unknown } | null;
    const name = typeof profile?.name === "string" ? profile.name.trim() : "";
    return name === DEFAULT_PROFILE_NAME ? "" : name;
  } catch {
    return "";
  }
};

let storedCaptureRecovery: Promise<number> | null = null;

const recoverStoredCaptures = (workspaceId: string | null) => {
  if (!storedCaptureRecovery) {
    storedCaptureRecovery = (async () => {
      if (!workspaceId) return 0;
      const bootstrap = await knowledgeApi.workspaceBootstrap();
      if (bootstrap.workspaceId !== workspaceId) return 0;
      const captures = loadStoredCaptures();
      let recovered = 0;
      for (const capture of captures.filter((candidate) => candidate.workspaceId === workspaceId)) {
        try {
          if (capture.kind === "note") {
            const note = await knowledgeApi.createNote({
              title: capture.title || "Untitled note",
              content: capture.content,
              clientCaptureId: capture.id,
            }, workspaceId);
            // The editable Inbox note is already durable. Filing is a second,
            // recoverable action and must never duplicate the original thought.
            removeStoredCapture(capture.id);
            if (capture.baseId) await knowledgeApi.fileNote(note.id, capture.baseId, workspaceId).catch(() => undefined);
          } else if (capture.kind === "link" && capture.url) {
            await knowledgeApi.captureWeb({
              url: capture.url,
              ...(capture.title.trim() ? { title: capture.title.trim() } : {}),
              ...(capture.content.trim() ? { notes: capture.content.trim() } : {}),
              ...(capture.baseId ? { knowledgeBaseId: capture.baseId } : {}),
              clientCaptureId: capture.id,
            }, workspaceId);
          } else {
            await knowledgeApi.importSource({
              title: capture.title || "Untitled source",
              content: capture.content,
              kind: capture.kind as CreateSourceInput["kind"],
              ...(capture.baseId ? { knowledgeBaseId: capture.baseId } : {}),
            }, workspaceId);
          }
          removeStoredCapture(capture.id);
          recovered += 1;
        } catch {
          // Keep the local retry copy untouched until the service is available.
        }
      }
      return recovered;
    })().finally(() => {
      storedCaptureRecovery = null;
    });
  }
  return storedCaptureRecovery;
};

export default function App() {
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [basesReady, setBasesReady] = useState(false);
  const [view, setView] = useState<AtlasView>("home");
  const [activeBaseId, setActiveBaseId] = useState("single-cell-annotation");
  const [selectedChapterId, setSelectedChapterId] = useState("context");
  const [mode, setMode] = useState<AtlasMode>(DEFAULT_KNOWLEDGE_BASE_MODE);
  const [captureOpen, setCaptureOpen] = useState(false);
  const [captureRecordingActive, setCaptureRecordingActive] = useState(false);
  const [captureKind, setCaptureKind] = useState<CaptureKind | null>(null);
  const [captureTargetBaseId, setCaptureTargetBaseId] = useState<string | null>(null);
  const [recordingContext, setRecordingContext] = useState<RecordingContext>("lecture");
  const [createBaseOpen, setCreateBaseOpen] = useState(false);
  const [editingBaseId, setEditingBaseId] = useState<string | null>(null);
  const [evidenceChapter, setEvidenceChapter] = useState<KnowledgeChapter | null>(null);
  const [themePreference, setThemePreference] = useState<ThemePreference>(readThemePreference);
  const [, setSystemThemeVersion] = useState(0);
  const theme = resolveTheme(themePreference);
  const [toast, setToast] = useState<string | null>(null);
  const [focusNoteId, setFocusNoteId] = useState<string | null>(null);
  const [inboxCount, setInboxCount] = useState(0);
  const [searchFocusRequest, setSearchFocusRequest] = useState(0);
  const [serviceOnline, setServiceOnline] = useState(true);
  const checkServiceRef = useRef<() => void>(() => undefined);
  const [profileName, setProfileName] = useState(readProfileName);
  // A persisted id is useful diagnostics, but it is never trusted as the
  // active destination until this launch has verified it with the backend.
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [captureWorkspaceId, setCaptureWorkspaceId] = useState<string | null>(null);
  const toastTimer = useRef<number | null>(null);

  const activeBase = useMemo(() => bases.find((base) => base.id === activeBaseId) ?? getKnowledgeBase(activeBaseId), [activeBaseId, bases]);
  const editingBase = editingBaseId ? bases.find((base) => base.id === editingBaseId) : undefined;
  const pendingCount = inboxCount;

  const refreshWorkspaceId = useCallback(async () => {
    const bootstrap = await knowledgeApi.workspaceBootstrap();
    window.localStorage.setItem(WORKSPACE_ID_STORAGE_KEY, bootstrap.workspaceId);
    setWorkspaceId(bootstrap.workspaceId);
    return bootstrap.workspaceId;
  }, []);

  const refreshKnowledgeBases = useCallback(async () => {
    const metadata = await knowledgeApi.knowledgeBases();
    setBases(metadata.map(metadataToBase));
  }, []);

  const notify = useCallback((message: string) => {
    setToast(message);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 2800);
  }, []);

  const openCapture = useCallback((kind: CaptureKind | null = null, context: RecordingContext = "lecture", targetBaseId: string | null = null) => {
    if (isTauriRuntime()) {
      void openCaptureWindow({
        kind,
        recordingContext: context,
        targetBaseId,
        source: view,
      }).catch((reason: unknown) => {
        notify(reason instanceof Error ? `Capture could not open: ${reason.message}` : "Capture could not open.");
      });
      return;
    }
    if (captureOpen && captureRecordingActive) {
      window.dispatchEvent(new CustomEvent("gunther:expand-capture"));
      notify("Your preserved recording session is already open.");
      return;
    }
    setCaptureRecordingActive(false);
    const lastVerifiedWorkspaceId = workspaceId;
    setCaptureWorkspaceId(null);
    void refreshWorkspaceId()
      .then(setCaptureWorkspaceId)
      .catch(() => setCaptureWorkspaceId(lastVerifiedWorkspaceId));
    setCaptureKind(kind);
    setCaptureTargetBaseId(targetBaseId);
    setRecordingContext(context);
    setCaptureOpen(true);
  }, [captureOpen, captureRecordingActive, notify, refreshWorkspaceId, view, workspaceId]);

  const closeCapture = useCallback((force = false) => {
    if (captureRecordingActive && !force) {
      window.dispatchEvent(new CustomEvent("gunther:minimize-capture"));
      notify("Recording continues in the background. Open the dock whenever you need it.");
      return;
    }
    setCaptureOpen(false);
  }, [captureRecordingActive, notify]);

  const openSearch = useCallback(() => {
    setView("home");
    setSearchFocusRequest((current) => current + 1);
  }, []);

  const openQuickNote = useCallback(async () => {
    setView("notebook");
    try {
      const created = await knowledgeApi.createNote();
      setFocusNoteId(created.id);
      notify("New note ready. No library required.");
    } catch (reason) {
      notify(reason instanceof Error ? `Note not created: ${reason.message}` : "A new note could not be created.");
    }
  }, [notify]);

  const openBase = useCallback((id: string, chapterId?: string, initialMode: AtlasMode = DEFAULT_KNOWLEDGE_BASE_MODE) => {
    const base = bases.find((item) => item.id === id) ?? getKnowledgeBase(id);
    setActiveBaseId(base.id);
    setSelectedChapterId(chapterId ?? base.chapters[0]!.id);
    setMode(initialMode);
    setView("base");
  }, [bases]);

  // A question asked from Home lands in the library's Ask composer, ready to send.
  const askBase = useCallback((id: string, question: string) => {
    window.localStorage.setItem(`gunther:ask-draft:${id}`, question);
    openBase(id, undefined, "ask");
  }, [openBase]);

  const exportBase = useCallback(async () => {
    notify("Preparing a complete local workbook…");
    try {
      const boundWorkspaceId = await refreshWorkspaceId();
      const [metadataItems, sourceSummaries, knowledgeUnits, sessionSummaries, proposals, artifactSummaries] = await Promise.all([
        knowledgeApi.knowledgeBases(),
        knowledgeApi.sources(activeBase.id),
        knowledgeApi.knowledgeUnits(activeBase.id),
        knowledgeApi.sessions(activeBase.id, true),
        knowledgeApi.proposals(activeBase.id),
        knowledgeApi.artifacts(activeBase.id, boundWorkspaceId),
      ]);
      const [indexedSources, sessions, artifacts] = await Promise.all([
        Promise.all(sourceSummaries.map((source) => knowledgeApi.source(source.id))),
        Promise.all(sessionSummaries.map((session) => knowledgeApi.session(session.id))),
        Promise.all(artifactSummaries.map((artifact) => knowledgeApi.artifact(activeBase.id, artifact.id, boundWorkspaceId))),
      ]);
      const payload = {
        format: "gunther-local-workbook-v3",
        exportedAt: new Date().toISOString(),
        ownerProfile: (() => { try { return JSON.parse(window.localStorage.getItem("gunther:profile") ?? "null") as unknown; } catch { return null; } })(),
        knowledgeBase: metadataItems.find((item) => item.id === activeBase.id) ?? activeBase,
        curatedFieldView: activeBase,
        indexedSources,
        knowledgeUnits,
        sessions,
        proposals,
        artifacts,
        reviewModel: "durable-unified-inbox",
      };
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${activeBase.id}.gunther.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
      const plural = (count: number, singular: string) => `${count} ${singular}${count === 1 ? "" : "s"}`;
      notify(`Exported ${plural(indexedSources.length, "source")}, ${plural(sessions.length, "session")}, ${plural(knowledgeUnits.length, "knowledge unit")}, and ${plural(artifacts.length, "Output version")}.`);
    } catch (reason) {
      notify(reason instanceof Error ? `Export failed: ${reason.message}` : "The complete workbook could not be exported.");
    }
  }, [activeBase, notify, refreshWorkspaceId]);

  useEffect(() => {
    void refreshKnowledgeBases().catch(() => undefined).finally(() => setBasesReady(true));
  }, [refreshKnowledgeBases]);
  useEffect(() => {
    const refreshSourceCounts = () => void refreshKnowledgeBases().catch(() => undefined);
    window.addEventListener("gunther:sources-updated", refreshSourceCounts);
    return () => window.removeEventListener("gunther:sources-updated", refreshSourceCounts);
  }, [refreshKnowledgeBases]);
  useEffect(() => {
    const refreshInboxCount = () => void knowledgeApi.inbox().then((items) => setInboxCount(items.filter((item) => item.state !== "held").length)).catch(() => undefined);
    refreshInboxCount();
    window.addEventListener("gunther:inbox-updated", refreshInboxCount);
    return () => window.removeEventListener("gunther:inbox-updated", refreshInboxCount);
  }, []);
  useEffect(() => {
    let active = true;
    const recover = () => void refreshWorkspaceId().then((currentWorkspaceId) => {
      if (active) setServiceOnline(true);
      return recoverStoredCaptures(currentWorkspaceId);
    }).then((recovered) => {
      if (!active || !recovered) return;
      window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
      window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
      notify(`${recovered} locally preserved capture${recovered === 1 ? "" : "s"} recovered.`);
    }).catch(() => {
      // The local service is unreachable; captures stay queued on this device.
      if (active) setServiceOnline(false);
    });
    checkServiceRef.current = recover;
    const recoverWhenVisible = () => {
      if (document.visibilityState === "visible") recover();
    };
    recover();
    const interval = window.setInterval(recover, 30_000);
    window.addEventListener("online", recover);
    document.addEventListener("visibilitychange", recoverWhenVisible);
    return () => {
      active = false;
      window.clearInterval(interval);
      window.removeEventListener("online", recover);
      document.removeEventListener("visibilitychange", recoverWhenVisible);
    };
  }, [notify, refreshWorkspaceId]);
  // While the service is away, check back often so the app recovers quickly.
  useEffect(() => {
    if (serviceOnline) return undefined;
    const interval = window.setInterval(() => checkServiceRef.current(), 5_000);
    return () => window.clearInterval(interval);
  }, [serviceOnline]);
  useEffect(() => {
    applyTheme(theme);
    window.localStorage.setItem(THEME_STORAGE_KEY, themePreference);
  }, [theme, themePreference]);
  useEffect(() => themePreference === "system" ? watchSystemTheme(() => setSystemThemeVersion((current) => current + 1)) : undefined, [themePreference]);
  useEffect(() => {
    const onProposal = () => window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
    window.addEventListener("gunther:proposal-created", onProposal);
    return () => window.removeEventListener("gunther:proposal-created", onProposal);
  }, []);
  useEffect(() => {
    if (!isTauriRuntime()) return undefined;
    let disposed = false;
    const unlisteners: Array<() => void> = [];
    void Promise.all([
      listenForCaptureSaved(({ message }) => {
        void refreshKnowledgeBases().catch(() => undefined);
        window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
        window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
        notify(message);
      }),
      listenForOpenSearch(openSearch),
    ]).then((dispose) => {
      if (disposed) dispose.forEach((unlisten) => unlisten());
      else unlisteners.push(...dispose);
    }).catch(() => undefined);
    return () => {
      disposed = true;
      unlisteners.forEach((unlisten) => unlisten());
    };
  }, [notify, openSearch, refreshKnowledgeBases]);
  useEffect(() => () => { if (toastTimer.current) window.clearTimeout(toastTimer.current); }, []);
  useEffect(() => { setProfileName(readProfileName()); }, [view]);
  useEffect(() => {
    document.querySelector<HTMLElement>(".atlas-workspace")?.scrollTo({ top: 0 });
  }, [view, activeBaseId, selectedChapterId, mode]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (captureOpen) closeCapture();
        else if (createBaseOpen) setCreateBaseOpen(false);
        else if (editingBaseId) setEditingBaseId(null);
        else if (evidenceChapter) setEvidenceChapter(null);
        return;
      }
      const typing = event.target instanceof Element && Boolean(event.target.closest("input, textarea, select, [contenteditable='true']"));
      if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey && !event.altKey && !captureOpen && !createBaseOpen && !editingBaseId) {
        event.preventDefault();
        openSearch();
        return;
      }
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key.toLowerCase() === "k") { event.preventDefault(); openSearch(); }
      if (event.key.toLowerCase() === "n") {
        event.preventDefault();
        if (view === "base" && mode === "ask") window.dispatchEvent(new CustomEvent("gunther:new-session"));
        else void openQuickNote();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [captureOpen, closeCapture, createBaseOpen, editingBaseId, evidenceChapter, mode, openQuickNote, openSearch, view]);

  const path = view === "base"
    ? [{ label: "Libraries", onClick: () => setView("library") }, { label: activeBase.title }]
    : [{ label: ({ home: "Home", library: "Libraries", notebook: "Notebook", inbox: "Inbox", settings: "Settings", account: "Account" } as const)[view] }];

  return (
    <div className="app-shell atlas-app-shell">
      <AtlasTitlebar path={path} theme={theme} serviceOnline={serviceOnline} showSearch={view !== "home"} onHome={() => setView("home")} onSearch={openSearch} onCapture={() => openCapture()} onTheme={() => setThemePreference(theme === "light" ? "dark" : "light")} />
      <div className="atlas-app-body">
        <AtlasRail active={view} inboxCount={pendingCount} bases={bases} basesReady={basesReady} activeBaseId={activeBaseId} profileName={profileName} onNavigate={setView} onOpenBase={(id) => openBase(id)} onCreateBase={() => setCreateBaseOpen(true)} />
        <main className="atlas-workspace" id="main-content">
          {view === "home" && <AtlasPage className="gx-page-home"><HomePage bases={bases} basesReady={basesReady} inboxCount={pendingCount} profileName={profileName} focusRequest={searchFocusRequest} onCapture={(kind?: HomeCaptureKind) => openCapture(kind ?? null)} onOpenBase={(id, initialMode) => openBase(id, undefined, initialMode)} onOpenChapter={openBase} onAskBase={askBase} onOpenNote={(id) => { setFocusNoteId(id); setView("notebook"); }} onOpenLibraries={() => setView("library")} onCreateBase={() => setCreateBaseOpen(true)} onOpenInbox={() => setView("inbox")} onNotify={notify} resolveWorkspaceId={refreshWorkspaceId} /></AtlasPage>}
          {view === "library" && <AtlasPage><AtlasLibraryPage bases={bases} loading={!basesReady} onOpen={openBase} onAdd={() => openCapture()} onCreateBase={() => setCreateBaseOpen(true)} /></AtlasPage>}
          {view === "base" && <KnowledgeBaseWorkspace base={activeBase} workspaceId={workspaceId} mode={mode} selectedChapterId={selectedChapterId} onMode={setMode} onChapter={setSelectedChapterId} onBack={() => setView("library")} onEvidence={setEvidenceChapter} onAdd={(kind) => openCapture(kind ?? null, "lecture", activeBase.id)} onRecord={(context) => openCapture("recording", context, activeBase.id)} onExport={exportBase} onEdit={() => setEditingBaseId(activeBase.id)} onNotify={notify} />}
          {view === "notebook" && <NotebookPage bases={bases} focusNoteId={focusNoteId} onFocused={() => setFocusNoteId(null)} onNotify={notify} onFiled={(note: NotebookNote, baseId) => {
            const base = bases.find((item) => item.id === baseId) ?? getKnowledgeBase(baseId);
            void refreshKnowledgeBases().catch(() => undefined);
            window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
            window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
            notify(`Filed “${note.title}” into ${base.title}.`);
          }} />}
          {view === "inbox" && <AtlasPage><InboxPageV3 bases={bases} onOpenBase={openBase} onOpenNote={(id) => { setFocusNoteId(id); setView("notebook"); }} onCapture={() => openCapture()} onCountChange={setInboxCount} onNotify={notify} /></AtlasPage>}
          {view === "settings" && <AtlasPage><SettingsPageV2 theme={themePreference} onTheme={setThemePreference} onNotify={notify} /></AtlasPage>}
          {view === "account" && <AtlasPage><AccountPageV2 bases={bases} /></AtlasPage>}
        </main>
      </div>

      <CaptureSheet open={captureOpen} bases={bases} workspaceId={captureWorkspaceId} resolveWorkspaceId={refreshWorkspaceId} baseId={captureTargetBaseId} initialKind={captureKind} initialRecordingContext={recordingContext} onRecordingState={setCaptureRecordingActive} onClose={closeCapture} onSearch={openSearch} onCaptured={async (title, baseId, content, kind, captureId, url, boundWorkspaceId) => {
        const result = await persistTextCapture({ title, baseId, content, kind, captureId, url, workspaceId: boundWorkspaceId });
        await refreshKnowledgeBases().catch(() => undefined);
        window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
        window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
        setCaptureRecordingActive(false);
        setCaptureOpen(false);
        notify(result.message);
      }} onAssetCaptured={async (file, title, baseId, kind, notes, boundWorkspaceId) => {
        const result = await persistAssetCapture({ file, title, baseId, kind, notes, workspaceId: boundWorkspaceId });
        await refreshKnowledgeBases().catch(() => undefined);
        window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
        window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
        setCaptureRecordingActive(false);
        setCaptureOpen(false);
        notify(result.message);
      }} />
      <CreateKnowledgeBaseSheet open={createBaseOpen} onClose={() => setCreateBaseOpen(false)} onSave={async (payload: CreateKnowledgeBaseInput) => {
        const metadata = await knowledgeApi.createKnowledgeBase(payload);
        const created = metadataToBase(metadata);
        setBases((current) => [created, ...current.filter((base) => base.id !== created.id)]);
        setCreateBaseOpen(false);
        setActiveBaseId(created.id);
        setSelectedChapterId(created.chapters[0]!.id);
        setMode(DEFAULT_KNOWLEDGE_BASE_MODE);
        setView("base");
        notify(`Created “${created.title}”. Capture a source or file one from Inbox.`);
      }} />
      <CreateKnowledgeBaseSheet open={Boolean(editingBase)} base={editingBase} onClose={() => setEditingBaseId(null)} onSave={async (payload: CreateKnowledgeBaseInput) => {
        if (!editingBase) return;
        const metadata = await knowledgeApi.updateKnowledgeBase(editingBase.id, payload);
        setBases((current) => current.map((base) => base.id === editingBase.id ? {
          ...base,
          title: metadata.title,
          eyebrow: metadata.eyebrow,
          subtitle: metadata.subtitle,
          question: metadata.question,
          description: metadata.description,
          color: metadata.color,
          status: metadata.status,
          updated: "Today",
        } : base));
        setEditingBaseId(null);
        notify(`Updated “${metadata.title}” without changing its sessions or revisions.`);
      }} />
      <EvidenceDrawer base={activeBase} chapter={evidenceChapter} onClose={() => setEvidenceChapter(null)} />
      {toast && <div className="atlas-toast" role="status"><Check size={14} />{toast}</div>}
    </div>
  );
}
