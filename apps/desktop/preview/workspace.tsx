/** Local UI preview: production components with in-memory sample data. */
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import type { Artifact, BuildOutputRequest, KnowledgeSession } from "@gunther/contracts";
import { knowledgeApi } from "../src/api";
import type { AtlasMode } from "../src/atlas";
import { AtlasTitlebar, AtlasRail } from "../src/components/AtlasChrome";
import { KnowledgeBaseWorkspace } from "../src/pages/KnowledgeBaseWorkspace";
import { makeBase, makeSource } from "../src/test/fixtures";
import { OutputsPage } from "../src/outputs/OutputsPage";
import { LibraryWorkspaceNavigation } from "../src/components/LibraryWorkspaceNavigation";
import "../src/atlas.css";
import "../src/styles.css";
import "../src/design/tokens.css";
import "../src/design/primitives.css";
import "../src/design/shell.css";
import "../src/design/activity.css";
import "../src/design/home.css";
import "../src/design/pages.css";
import "../src/design/prose.css";
import "../src/design/item.css";
import "../src/design/shortcuts.css";
import "../src/design/trash.css";
import "../src/design/legacy.css";
import "../src/design/services.css";
import "../src/design/models.css";
import "../src/design/agent.css";
import "../src/design/typography.css";

// An unmocked action must never reach the user's real library or model services.
window.fetch = async () => new Response(JSON.stringify({ detail: "This action is unavailable in the sample preview." }), {
  status: 503, headers: { "Content-Type": "application/json" },
});
document.documentElement.dataset.theme = "light";
document.documentElement.dataset.runtime = "web";

const base = makeBase({ id: "workspace_preview", title: "Leadership", eyebrow: "Personal knowledge", description: "界面预览 · 示例数据", color: "green", sourceCount: 3 });
const sources = ["L1-Intro", "L3-领导类型、领导力", "L2-领导类型、领导力"].map((title, index) => makeSource({ id: `preview_source_${index}`, title, kind: "recording" }));
const contents = {
  sections: [{ id: "course", parentId: null, title: "领导力和执行力课程", description: "", position: 0, version: 1, passageCount: 0 }],
  placements: sources.map((source, position) => ({ sourceId: source.id, sectionId: "course", position })),
  sources: sources.map((source, index) => ({ ...source, durationSeconds: [478, 4909, 5192][index], authors: null, year: null, pages: null, mediaType: null, host: null, processing: "ready" })),
};
const report: Artifact = {
  conversationId: "review_study", conversationTitle: "课堂复习与讨论", clientRequestId: "preview_report_request_2", request: "整理一份领导力学习笔记。",
  id: "preview_report_2", workspaceId: "preview_workspace", knowledgeBaseId: base.id,
  lineageId: "preview_report", versionNumber: 2, supersedesArtifactId: "preview_report_1",
  format: "report", audience: "student", title: "领导力学习笔记",
  contentHash: "a".repeat(64), manifestHash: "b".repeat(64), acceptedUnitIds: [], unitCount: 0,
  createdAt: "2026-10-13T01:00:00.000Z", kind: "report", style: "overview", origin: "build",
  content: "# 领导力学习笔记\n\n_课堂复习与讨论_\n\n把课程材料整理为可复习、可追问的阅读路径。\n\n## 阅读顺序\n\n1. 从导论建立问题清单。\n2. 对照两段课程，记录类型与情境。\n3. 回到原始转写确认定义和例子。\n\n## 继续追问\n\n哪些观点有明确例子？哪些仍需要更多材料支持？\n\n这是界面预览的示例正文。",
  unitSnapshots: [],
  provenance: { schemaVersion: 2, generator: "gunther.output-agents.v1", workspaceId: "preview_workspace", knowledgeBaseId: base.id, knowledgeBaseQuestion: "", acceptedOnly: false, acceptedUnitIds: [], revisionIds: [] },
  brief: "课堂复习与讨论",
  outline: [{ heading: "阅读顺序", goal: "形成问题清单" }, { heading: "继续追问", goal: "保留待核验的问题" }],
  citations: [], scope: { mode: "library", sourceIds: [], unitIds: [], sessionIds: [] },
  inputs: { sources: 3, units: 0, sessions: 0 }, modelLabel: "Sample preview",
  sections: [{ index: 0, heading: "阅读顺序", checked: true, issues: [] }, { index: 1, heading: "继续追问", checked: true, issues: [] }],
};
const earlier: Artifact = { ...report, id: "preview_report_1", clientRequestId: "preview_report_request_1", request: "整理课程阅读顺序。", versionNumber: 1, supersedesArtifactId: null, title: "学习笔记初稿" };
const deck: Artifact = { ...report, id: "preview_slides_1", clientRequestId: "preview_slides_request", request: "再做一份课堂分享的幻灯片。", lineageId: "preview_slides", versionNumber: 1, supersedesArtifactId: null, kind: "slides", format: "slides", style: null, title: "课堂分享", content: "# 领导力与执行力\n\n从课程到问题，再回到证据。\n\n---\n\n## 阅读路径\n\n- 导论\n- 类型比较\n- 回到证据", outline: [], sections: [] };
const artifacts = [report, deck, earlier];
const conversations: KnowledgeSession[] = [];
const findArtifact = (id: string) => {
  const artifact = artifacts.find((item) => item.id === id);
  if (!artifact) throw new Error("Sample artifact not found.");
  return artifact;
};
const saveVersion = (source: Artifact, changes: Partial<Artifact>) => {
  const next = { ...source, ...changes, id: crypto.randomUUID(), versionNumber: source.versionNumber + 1, supersedesArtifactId: source.id, createdAt: new Date().toISOString() };
  artifacts.unshift(next);
  return next;
};

Object.assign(knowledgeApi, {
  modelMenu: async () => ({ models: [{ ref: "preview/sample", provider: "Preview", label: "Sample", display: "Sample preview", vision: false, levels: [] }], default: { model: "preview/sample", effort: "high" }, efforts: [] }),
  sources: async () => sources,
  knowledgeUnits: async () => [],
  sessions: async () => conversations,
  topics: async () => [],
  contents: async () => contents,
  artifacts: async () => [...artifacts],
  artifact: async (_base: string, id: string) => findArtifact(id),
  followOutputBuild: async () => null,
  health: async () => ({ webSearchMode: "off" }),
  buildOutputStream: async (_base: string, request: BuildOutputRequest) => {
    const original = request.supersedesArtifactId ? findArtifact(request.supersedesArtifactId) : request.kind === "slides" ? deck : report;
    const changes = {
      kind: request.kind, format: request.kind, audience: request.audience,
      style: request.kind === "report" ? request.style ?? "overview" : null,
      title: request.title || (request.kind === "slides" ? "课堂分享 · 示例" : "学习笔记 · 示例"),
      brief: request.brief ?? "", scope: request.scope, conversationId: request.conversationId ?? null, conversationTitle: request.conversationTitle ?? null, clientRequestId: request.clientRequestId, request: request.brief ?? null, origin: "build" as const,
      inputs: { sources: request.scope.mode === "library" ? sources.length : request.scope.sourceIds.length, units: 0, sessions: request.scope.sessionIds.length },
    };
    if (request.supersedesArtifactId) return saveVersion(original, changes);
    const next = { ...original, ...changes, id: crypto.randomUUID(), lineageId: crypto.randomUUID(), versionNumber: 1, supersedesArtifactId: null, createdAt: new Date().toISOString() };
    artifacts.unshift(next);
    return next;
  },
  editOutput: async (_base: string, id: string, request: { content: string }) => saveVersion(findArtifact(id), { content: request.content, origin: "edit" }),
  reviseOutputStream: async (_base: string, id: string, request: { instruction: string; conversationId?: string; conversationTitle?: string; clientRequestId: string }) => {
    const original = findArtifact(id);
    return saveVersion(original, { content: `${original.content}\n\n> 示例修订：${request.instruction}`, origin: "revise", conversationId: request.conversationId ?? original.conversationId ?? null, conversationTitle: request.conversationTitle ?? original.conversationTitle ?? null, clientRequestId: request.clientRequestId, request: request.instruction });
  },
  checkOutput: async (_base: string, id: string) => findArtifact(id),
  createSession: async (_base: string, request: { focusChapterId?: string | null; selectedSourceIds: string[] }) => {
    const session: KnowledgeSession = { id: crypto.randomUUID(), knowledgeBaseId: base.id, title: "New conversation", summary: "", focusChapterId: request.focusChapterId ?? null, selectedSourceIds: request.selectedSourceIds, pinned: false, archived: false, parentSessionId: null, branchedFromMessageId: null, messageCount: 0, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), messages: [] };
    conversations.unshift(session);
    return session;
  },
  session: async (id: string) => conversations.find((item) => item.id === id),
  updateSession: async (id: string, patch: Partial<KnowledgeSession>) => {
    const session = conversations.find((item) => item.id === id)!;
    Object.assign(session, patch);
    return session;
  },
  followAnswer: async () => null,
});

function WorkspacePreview() {
  const [mode, setMode] = useState<AtlasMode>("outputs");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [notice, setNotice] = useState("");
  const [chapter, setChapter] = useState("");
  const [previewFocused, setPreviewFocused] = useState(false);
  const [windowWidth, setWindowWidth] = useState(window.innerWidth);
  const [sidebarPreference, setSidebarPreference] = useState<"expanded" | "collapsed" | null>(() => {
    try { const saved = window.localStorage.getItem("gunther:preview:sidebar"); return saved === "expanded" || saved === "collapsed" ? saved : null; } catch { return null; }
  });
  useEffect(() => {
    const resized = () => setWindowWidth(window.innerWidth);
    window.addEventListener("resize", resized); return () => window.removeEventListener("resize", resized);
  }, []);
  useEffect(() => {
    try { if (sidebarPreference) window.localStorage.setItem("gunther:preview:sidebar", sidebarPreference); else window.localStorage.removeItem("gunther:preview:sidebar"); } catch { /* The toggle remains usable without storage. */ }
  }, [sidebarPreference]);
  const sidebarCollapsed = previewFocused || (sidebarPreference ? sidebarPreference === "collapsed" : mode === "outputs" || windowWidth < 1100);
  const navigate = (next: AtlasMode) => { setPreviewFocused(false); setMode(next); };
  const inform = () => setNotice("Sample preview · no changes to your library");
  return <div className={`app-shell atlas-app-shell has-adaptive-sidebar${mode === "outputs" ? " conversation-preview-shell" : ""}${sidebarCollapsed ? " is-sidebar-collapsed" : " is-sidebar-expanded"}`}>
    <AtlasTitlebar path={[{ label: "Libraries", onClick: () => navigate("contents") }, { label: base.title }]} theme={theme} navigation={<LibraryWorkspaceNavigation mode={mode} onMode={navigate} onEdit={inform} onExport={inform} />} status={<span className="gx-save-state">Sample library</span>} sidebarCollapsed={sidebarCollapsed} onHome={() => navigate("contents")} onSearch={inform} onCapture={inform} onSidebar={() => { setPreviewFocused(false); setSidebarPreference(sidebarCollapsed ? "expanded" : "collapsed"); }}
      onTheme={() => { const next = theme === "light" ? "dark" : "light"; document.documentElement.dataset.theme = next; setTheme(next); }} />
    <div className="atlas-app-body">
      <AtlasRail active="base" inboxCount={0} bases={[base, makeBase({ id: "preview_biology", title: "Biology", color: "blue" })]} activeBaseId={base.id} profileName="UI review" onNavigate={() => navigate("contents")} onOpenBase={() => navigate("contents")} onSidebarAuto={() => setSidebarPreference(null)} sidebarAutomatic={sidebarPreference === null} />
      <main className="atlas-workspace">
        {mode === "outputs" ? <OutputsPage base={base} workspaceId="preview_workspace" onNotify={setNotice} onAdd={inform} focused={previewFocused} onFocus={setPreviewFocused} allowVoiceInput={false} /> : <KnowledgeBaseWorkspace showHeader={false} base={base} workspaceId="preview_workspace" mode={mode} selectedChapterId={chapter} onMode={navigate} onChapter={setChapter} onBack={() => navigate("contents")} onEvidence={inform} onAdd={inform} onRecord={inform} onExport={inform} onEdit={inform} onNotify={setNotice} onOpenSource={inform} />}
      </main>
    </div>
    {notice && <div className="gx-toast" role="status" onClick={() => setNotice("")}>{notice}</div>}
  </div>;
}

createRoot(document.getElementById("root")!).render(<WorkspacePreview />);
