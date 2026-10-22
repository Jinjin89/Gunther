import type { Artifact, ModelMenu, OutputScope } from "@gunther/contracts";
import { ArrowUp, Check, ChevronDown, ChevronRight, Download, FileText, ListChecks, LoaderCircle, Maximize2, MessageSquare, Minimize2, PanelsTopLeft, Plus, Presentation, SlidersHorizontal, Sparkles } from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { BrandMark } from "../src/design/BrandMark";
import { AgentPlanPreview, type PreviewAgentPlan } from "./AgentPlanPreview";
import { ResizeHandle } from "./ResizeHandle";
import { MicGlyph } from "../src/components/Dictation";
import { ModelPicker } from "../src/models/ModelPicker";
import type { AskChoice } from "../src/models/askModel";
import { MaterialSelectionPanel } from "../src/outputs/MaterialSelectionPanel";
import { ReportView } from "../src/outputs/ReportView";
import { LazySlidesView } from "../src/outputs/LazySlides";
import { OutlineEditor } from "../src/outputs/OutlineEditor";
import { PrintRoot } from "../src/outputs/PrintRoot";
import type { ScopeCounts } from "../src/outputs/ScopePicker";
import "../src/outputs/outputs.css";
import "./conversation-workbench.css";

// These relationships and changes are sample data for reviewing the design.
interface PreviewArtifact { id: string; format: "pdf" | "slides"; versions: Artifact[] }
interface Message { id: string; role: "user" | "assistant"; text: string }
interface Conversation {
  id: string; title: string; scope: OutputScope; messages: Message[];
  artifacts: PreviewArtifact[]; selectedArtifact: string | null; draft: string; choice: AskChoice;
}
const wholeLibrary: OutputScope = { mode: "library", sourceIds: [], unitIds: [], sessionIds: [] };
const message = (role: Message["role"], text: string): Message => ({ id: crypto.randomUUID(), role, text });
const defaultChoice = { model: "deepseek/deepseek-flash", effort: "high" } satisfies AskChoice;
const previewLevels: ModelMenu["efforts"] = [{ id: "low", label: "Low" }, { id: "high", label: "High" }, { id: "max", label: "Max" }];
const previewModelMenu: ModelMenu = {
  models: [
    { ref: "deepseek/deepseek-flash", provider: "DeepSeek", label: "Flash", display: "DeepSeek · Flash", vision: false, levels: previewLevels },
    { ref: "glm/glm-5.3", provider: "GLM", label: "5.3", display: "GLM · 5.3", vision: false, levels: previewLevels },
    { ref: "kimi/kimi-k2.7-code", provider: "Kimi", label: "K2.7 Code", display: "Kimi · K2.7 Code", vision: false, levels: [] },
  ], default: defaultChoice, efforts: previewLevels,
};
const SPLITTER_HEIGHT = 9;
const DEFAULT_SOURCE_SHARE = .7;
const previewPlan = (target: string, slides: boolean, step = 0): PreviewAgentPlan => ({
  target, step, state: "running", steps: [
    { title: "Read selected sources", detail: "Finding relevant passages in the selected material." },
    { title: "Prepare the outline", detail: "Organizing the main ideas and reading order." },
    { title: slides ? "Build the slide deck" : "Write the document", detail: `Updating “${target}”.` },
    { title: "Check sources & save", detail: "Checking the result before saving the new version." },
  ],
});
function splitBounds(height: number) {
  const available = Math.max(1, height - SPLITTER_HEIGHT);
  return { available, min: Math.min(200 / available, .6), max: 1 - Math.min(128 / available, .38) };
}
function clampSplit(share: number, height: number) {
  const { min, max } = splitBounds(height);
  return Math.max(min, Math.min(max, share));
}

function readLayoutNumber(key: string, fallback: number, min: number, max: number) {
  try { const saved = Number(window.localStorage.getItem(key)); return Number.isFinite(saved) && saved >= min && saved <= max ? saved : fallback; } catch { return fallback; }
}

export function ConversationWorkbench({ baseId, samples, onNotice, focused, onFocus }: { baseId: string; samples: Artifact[]; onNotice: (text: string) => void; focused: boolean; onFocus: (focused: boolean) => void }) {
  const report = samples.find((item) => item.kind === "report")!;
  const deck = samples.find((item) => item.kind === "slides")!;
  const questions: Artifact = { ...report, id: "preview_questions", lineageId: "preview_questions", versionNumber: 1, supersedesArtifactId: null, title: "复习问题清单", content: "# 复习问题清单\n\n_课后复习与小组讨论_\n\n## 核心理解\n\n1. 领导力和执行力之间有什么关系？\n2. 不同领导类型分别适用于哪些情境？\n3. 课程中的例子支持了哪些观点？\n\n## 回到证据\n\n挑选一段课程，记录原话、时间位置和自己的理解。", outline: [{ heading: "核心理解", goal: "检验课程理解" }, { heading: "回到证据", goal: "记录可以核验的例子" }] };
  const [conversations, setConversations] = useState<Conversation[]>([
    { id: "study", title: "课堂复习与讨论", scope: wholeLibrary, draft: "", choice: defaultChoice, selectedArtifact: "notes", artifacts: [
      { id: "notes", format: "pdf", versions: samples.filter((item) => item.lineageId === report.lineageId).sort((a, b) => b.versionNumber - a.versionNumber) },
      { id: "slides", format: "slides", versions: [deck] },
      { id: "questions", format: "pdf", versions: [questions] },
    ], messages: [
      message("user", "把这三段课程整理成学习笔记，再做一份课堂分享的幻灯片。"),
      message("assistant", "已整理成学习笔记和课堂分享幻灯片。你可以继续修改其中一个，也可以在这个对话里创建新的成果。"),
      message("user", "再补一份复习问题清单。"),
      message("assistant", "已添加复习问题清单。这个对话现在有 2 份文档和 1 套幻灯片，可在右侧切换预览。"),
    ] },
    { id: "team", title: "团队分享准备", scope: { mode: "selection", sourceIds: ["preview_source_0"], unitIds: [], sessionIds: [] }, draft: "", choice: { model: "glm/glm-5.3", effort: "high" }, selectedArtifact: "team-deck", artifacts: [
      { id: "team-deck", format: "slides", versions: [{ ...deck, id: "team_deck", title: "团队讨论 · Slides", content: "# 让团队一起思考\n\n从课程中挑选可以应用的问题。\n\n---\n\n## 本周的讨论\n\n- 如何对齐目标？\n- 谁需要更多支持？\n- 哪些决定需要明确负责人？" }] },
      { id: "team-brief", format: "pdf", versions: [{ ...questions, id: "team_brief", title: "团队讨论提纲" }] },
    ], messages: [message("user", "把导论整理成适合团队讨论的幻灯片和一份提纲。"), message("assistant", "这次讨论有一套幻灯片和一份文档。可以从右侧选中一个，再告诉我你想改什么。") ] },
  ]);
  const [activeId, setActiveId] = useState("study");
  const [counts, setCounts] = useState<ScopeCounts | null>(null);
  const [action, setAction] = useState<"modify" | "create">("modify");
  const [format, setFormat] = useState<"pdf" | "slides">("pdf");
  const [versionId, setVersionId] = useState<string | null>(null);
  const [settings, setSettings] = useState(false);
  const [audience, setAudience] = useState("Students");
  const [tone, setTone] = useState("Clear & concise");
  const [voicePreview, setVoicePreview] = useState(false);
  const layoutKey = `gunther:preview:panel-layout:${baseId}`;
  const [sourceWidth, setSourceWidth] = useState(() => readLayoutNumber(`${layoutKey}:sources`, 230, 180, 380));
  const [chatShare, setChatShare] = useState(() => readLayoutNumber(`${layoutKey}:chat`, .42, .15, .8));
  const [controlsHeight, setControlsHeight] = useState(() => readLayoutNumber(`${layoutKey}:controls-height`, 320, 120, 480));
  const [controlsExpanded, setControlsExpanded] = useState(() => { try { return window.localStorage.getItem(`${layoutKey}:controls`) === "expanded"; } catch { return false; } });
  const [layoutWidth, setLayoutWidth] = useState(1100);
  const [rightHeight, setRightHeight] = useState(800);
  const [draggingWidth, setDraggingWidth] = useState(false);
  const workspace = useRef<HTMLDivElement>(null);
  const rightPanel = useRef<HTMLElement>(null);
  const outlinePanel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = workspace.current;
    const right = rightPanel.current;
    if (!root || !right) return;
    const measure = () => { setLayoutWidth(root.getBoundingClientRect().width); const height = right.getBoundingClientRect().height; if (height > 0) setRightHeight(height); };
    measure(); const observer = new ResizeObserver(measure); observer.observe(root); observer.observe(right);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    try {
      window.localStorage.setItem(`${layoutKey}:sources`, String(sourceWidth));
      window.localStorage.setItem(`${layoutKey}:chat`, String(chatShare));
      window.localStorage.setItem(`${layoutKey}:controls-height`, String(controlsHeight));
      window.localStorage.setItem(`${layoutKey}:controls`, controlsExpanded ? "expanded" : "compact");
    } catch { /* Layout changes remain usable when storage is unavailable. */ }
  }, [layoutKey, sourceWidth, chatShare, controlsHeight, controlsExpanded]);
  useEffect(() => {
    if (!focused) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); onFocus(false); } };
    window.addEventListener("keydown", escape); return () => window.removeEventListener("keydown", escape);
  }, [focused, onFocus]);
  const sourceMax = Math.max(180, Math.min(380, layoutWidth - 18 - 280 - 360));
  const fittedSourceWidth = Math.max(180, Math.min(sourceMax, sourceWidth));
  const paneWidth = Math.max(640, layoutWidth - fittedSourceWidth - 18);
  const fittedChatShare = Math.max(280 / paneWidth, Math.min(1 - 360 / paneWidth, chatShare));
  const controlsMax = Math.max(120, Math.min(480, rightHeight - 44 - 160 - 9));
  const fittedControlsHeight = Math.max(120, Math.min(controlsMax, controlsHeight));
  const [plans, setPlans] = useState<Record<string, PreviewAgentPlan>>(() => ({ study: previewPlan(report.title, false, 2), team: { ...previewPlan("团队讨论 · Slides", true, 4), state: "complete" } }));
  const jobTimers = useRef(new Map<string, ReturnType<typeof window.setInterval>>());
  useEffect(() => () => { jobTimers.current.forEach((timer) => window.clearInterval(timer)); jobTimers.current.clear(); }, []);
  const splitStorageKey = `gunther:preview:source-conversation-split:${baseId}`;
  const [sourceShare, setSourceShare] = useState(() => {
    try {
      const saved = Number(window.localStorage.getItem(splitStorageKey));
      return Number.isFinite(saved) && saved >= .15 && saved <= .9 ? saved : DEFAULT_SOURCE_SHARE;
    } catch { return DEFAULT_SOURCE_SHARE; }
  });
  const [leftHeight, setLeftHeight] = useState(0);
  const [resizing, setResizing] = useState(false);
  const leftPanel = useRef<HTMLElement>(null);
  const splitDrag = useRef<{ pointerId: number; offset: number; startShare: number } | null>(null);
  useEffect(() => {
    const panel = leftPanel.current;
    if (!panel) return;
    const measure = () => {
      const height = panel.getBoundingClientRect().height;
      if (height < 1) return;
      setLeftHeight(height);
      setSourceShare((share) => clampSplit(share, height));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(panel);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    try { window.localStorage.setItem(splitStorageKey, String(sourceShare)); } catch { /* Resizing works even when browser storage is unavailable. */ }
  }, [sourceShare, splitStorageKey]);
  const splitLimits = splitBounds(leftHeight);
  const input = useRef<HTMLTextAreaElement>(null);
  const conversation = conversations.find((item) => item.id === activeId)!;
  const selected = conversation.artifacts.find((item) => item.id === conversation.selectedArtifact) ?? null;
  const artifact = selected?.versions.find((item) => item.id === versionId) ?? selected?.versions[0] ?? null;
  const chosenCount = conversation.scope.mode === "library" ? counts?.sources ?? 3 : conversation.scope.sourceIds.length;
  const agentPlan = plans[activeId] ?? null;
  const generating = jobTimers.current.has(activeId);
  const advancePlan = (id: string) => setPlans((current) => {
    const plan = current[id];
    if (!plan || plan.state !== "running") return current;
    const step = Math.min(plan.steps.length, plan.step + 1);
    return { ...current, [id]: { ...plan, step, state: step === plan.steps.length ? "complete" : "running" } };
  });
  const stopPlan = (id: string) => {
    const timer = jobTimers.current.get(id);
    if (timer !== undefined) window.clearInterval(timer);
    jobTimers.current.delete(id);
    setPlans((current) => current[id] ? { ...current, [id]: { ...current[id], state: "stopped" } } : current);
  };
  const update = (id: string, change: (item: Conversation) => Conversation) => setConversations((all) => all.map((item) => item.id === id ? change(item) : item));
  const setDraft = (draft: string) => update(activeId, (item) => ({ ...item, draft }));
  const chooseConversation = (item: Conversation) => { setVoicePreview(false); setActiveId(item.id); setVersionId(null); setAction(item.artifacts.length ? "modify" : "create"); setSettings(false); };
  const newConversation = () => {
    const id = crypto.randomUUID();
    setConversations((all) => [{ id, title: "New conversation", scope: wholeLibrary, draft: "", choice: conversation.choice, messages: [], artifacts: [], selectedArtifact: null }, ...all]);
    setVoicePreview(false); setActiveId(id); setAction("create"); setVersionId(null); setSettings(false);
    requestAnimationFrame(() => input.current?.focus());
  };
  const chooseArtifact = (item: PreviewArtifact) => {
    update(activeId, (thread) => ({ ...thread, selectedArtifact: item.id }));
    setVersionId(null); setFormat(item.format); setAction("modify");
  };
  const toggleVoicePreview = () => {
    if (voicePreview) {
      setDraft(`${conversation.draft}${conversation.draft ? "\n" : ""}请把这份材料整理得更简洁。`);
      setVoicePreview(false);
      input.current?.focus();
      onNotice("Sample voice text inserted · this design preview does not record audio");
    } else {
      setVoicePreview(true);
      input.current?.focus();
    }
  };
  const send = (instruction = conversation.draft.trim()) => {
    if (!instruction || voicePreview || generating) return;
    const making = action === "create" || !selected;
    const source = making ? (format === "slides" ? deck : report) : selected.versions[0] ?? report;
    const id = crypto.randomUUID();
    const next: Artifact = {
      ...source, id, lineageId: making ? id : source.lineageId,
      versionNumber: making ? 1 : source.versionNumber + 1,
      supersedesArtifactId: making ? null : source.id,
      title: making ? `${format === "slides" ? "演示幻灯片" : "演示文档"} ${conversation.artifacts.length + 1}` : source.title,
      brief: instruction, scope: conversation.scope, inputs: { sources: chosenCount, units: 0, sessions: 0 },
      modelLabel: previewModelMenu.models.find((model) => model.ref === conversation.choice.model)?.display ?? null,
      content: source.content + (source.kind === "slides" ? `\n\n---\n\n## 演示修改\n\n${instruction}` : `\n\n## 演示修改\n\n${instruction}`),
    };
    const threadId = activeId;
    update(threadId, (item) => ({ ...item, draft: "", title: item.title === "New conversation" ? instruction.slice(0, 28) : item.title, messages: [...item.messages, message("user", instruction)] }));
    setPlans((current) => ({ ...current, [threadId]: previewPlan(next.title, next.kind === "slides") }));
    let step = 0;
    const timer = window.setInterval(() => {
      step += 1;
      setPlans((current) => {
        const plan = current[threadId];
        return plan ? { ...current, [threadId]: { ...plan, step, state: step === plan.steps.length ? "complete" : "running" } } : current;
      });
      if (step < 4) return;
      window.clearInterval(timer);
      jobTimers.current.delete(threadId);
      update(threadId, (item) => ({
        ...item,
        messages: [...item.messages, message("assistant", making ? `演示：已在这个对话中添加「${next.title}」。右侧保留了已有的全部成果。` : `演示：已将「${next.title}」更新为 v${next.versionNumber}。可以在右侧查看，也可以继续修改。`)],
        artifacts: making ? [...item.artifacts, { id, format, versions: [next] }] : item.artifacts.map((entry) => entry.id === selected?.id ? { ...entry, versions: [next, ...entry.versions] } : entry),
        selectedArtifact: making ? id : selected!.id,
      }));
      requestAnimationFrame(() => { const log = document.querySelector(".cw-message-scroll"); if (log) log.scrollTop = log.scrollHeight; });
    }, 1400);
    jobTimers.current.set(threadId, timer);
    setVersionId(null); setAction("modify");
    requestAnimationFrame(() => { const log = document.querySelector(".cw-message-scroll"); if (log) log.scrollTop = log.scrollHeight; });
  };
  const downloadMarkdown = () => {
    if (!artifact) return;
    const url = URL.createObjectURL(new Blob([artifact.content], { type: "text/markdown;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `${artifact.title}.md`; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <div ref={workspace} className={`cw-workspace${resizing ? " is-resizing" : ""}${draggingWidth ? " is-resizing-width" : ""}${focused ? " is-preview-focused" : ""}${layoutWidth < 850 ? " is-narrow" : ""}${layoutWidth < 550 ? " is-tiny" : ""}`} style={{ "--cw-left-width": `${fittedSourceWidth}px`, "--cw-chat-share": `${fittedChatShare}fr`, "--cw-preview-share": `${1 - fittedChatShare}fr` } as CSSProperties}>
    <aside id="preview-context-panel" ref={leftPanel} className="cw-left" aria-label="Sources and conversations" style={{ "--cw-source-share": `${sourceShare}fr`, "--cw-conversation-share": `${1 - sourceShare}fr` } as CSSProperties}>
      <div className="cw-source-region" id="preview-source-panel"><MaterialSelectionPanel baseId={baseId} scope={conversation.scope} onChange={(scope) => update(activeId, (item) => ({ ...item, scope }))} onLoaded={setCounts} disabled={generating} onAdd={() => onNotice("Design preview · sample sources")} /></div>
      <div className="cw-left-splitter" role="separator" tabIndex={0} aria-label="Resize Sources and Conversations" aria-orientation="horizontal" aria-controls="preview-source-panel preview-conversations-panel" aria-valuemin={Math.ceil(splitLimits.min * 100)} aria-valuemax={Math.floor(splitLimits.max * 100)} aria-valuenow={Math.round(sourceShare * 100)} aria-valuetext={`Sources ${Math.round(sourceShare * 100)}%, Conversations ${Math.round((1 - sourceShare) * 100)}%`} title="Drag to resize · double-click to reset"
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          event.preventDefault();
          event.currentTarget.focus();
          const box = event.currentTarget.getBoundingClientRect();
          splitDrag.current = { pointerId: event.pointerId, offset: event.clientY - box.top - box.height / 2, startShare: sourceShare };
          event.currentTarget.setPointerCapture(event.pointerId);
          setResizing(true);
        }}
        onPointerMove={(event) => {
          if (splitDrag.current?.pointerId !== event.pointerId || !leftPanel.current) return;
          const panel = leftPanel.current.getBoundingClientRect();
          const next = (event.clientY - splitDrag.current.offset - panel.top - SPLITTER_HEIGHT / 2) / Math.max(1, panel.height - SPLITTER_HEIGHT);
          setSourceShare(clampSplit(next, panel.height));
        }}
        onPointerUp={(event) => { splitDrag.current = null; setResizing(false); if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
        onPointerCancel={() => { splitDrag.current = null; setResizing(false); }}
        onLostPointerCapture={() => { splitDrag.current = null; setResizing(false); }}
        onDoubleClick={() => setSourceShare(clampSplit(DEFAULT_SOURCE_SHARE, leftHeight))}
        onKeyDown={(event) => {
          const step = (event.shiftKey ? 50 : 20) / splitLimits.available;
          if (event.key === "ArrowUp" || event.key === "ArrowDown") { event.preventDefault(); setSourceShare((share) => clampSplit(share + (event.key === "ArrowDown" ? step : -step), leftHeight)); }
          else if (event.key === "Home" || event.key === "End") { event.preventDefault(); setSourceShare(event.key === "Home" ? splitLimits.min : splitLimits.max); }
          else if (event.key === "Escape" && splitDrag.current) { setSourceShare(splitDrag.current.startShare); const pointerId = splitDrag.current.pointerId; splitDrag.current = null; setResizing(false); if (event.currentTarget.hasPointerCapture(pointerId)) event.currentTarget.releasePointerCapture(pointerId); }
        }}><span className="cw-left-splitter-grip" aria-hidden="true" /></div>
      <section className="cw-conversations" id="preview-conversations-panel" aria-label="Conversations">
        <header><h2>Conversations</h2><button type="button" className="gx-icon-button" aria-label="New conversation" title="New conversation" onClick={newConversation}><Plus size={16} /></button></header>
        <div className="cw-conversation-list">{conversations.map((item) => <button type="button" key={item.id} className={item.id === activeId ? "is-active" : ""} aria-current={item.id === activeId ? "page" : undefined} onClick={() => chooseConversation(item)}><MessageSquare size={15} /><span><strong>{item.title}</strong><small>{item.messages.length ? `${item.messages.length} messages` : "Start a conversation"}</small></span></button>)}</div>
      </section>
    </aside>
    <div className="cw-source-width-divider"><ResizeHandle label="Resize sources panel width" value={fittedSourceWidth} min={180} max={sourceMax} controls="preview-context-panel" onChange={setSourceWidth} onReset={() => setSourceWidth(230)} onDragging={setDraggingWidth} /></div>
    <section id="preview-chat-panel" className="cw-chat" aria-label="Artifact conversation">
      <header className="cw-pane-head"><div><h1>Conversation</h1><p>{conversation.title}</p></div><span className="cw-live-dot" title="Design preview" /></header>
      <div className="cw-message-scroll">
        {conversation.messages.length === 0 && <div className="cw-welcome"><BrandMark size={35} /><h2>What would you like to make?</h2><p>Choose sources, create a document or slides, then refine them here.</p><button type="button" onClick={() => { setDraft("整理一份学习笔记"); input.current?.focus(); }}>Create study notes <ArrowUp size={14} /></button></div>}
        <div className="cw-messages" role="log" aria-label="Conversation messages">{conversation.messages.map((item) => <div className={`cw-message is-${item.role}`} key={item.id}>{item.role === "assistant" && <span className="cw-agent-mark"><BrandMark size={17} /></span>}<div>{item.role === "user" && <small>You</small>}<p>{item.text}</p>{item.role === "assistant" && <small className="cw-message-status"><Check size={11} />Saved in this conversation</small>}</div></div>)}</div>
      </div>
      {settings && <section className="cw-inline-settings" aria-label="Conversation settings"><label>Audience<select value={audience} onChange={(event) => setAudience(event.target.value)}><option>Students</option><option>Researchers</option><option>My team</option></select></label><label>Style<select value={tone} onChange={(event) => setTone(event.target.value)}><option>Clear & concise</option><option>Detailed explanation</option><option>Presentation</option></select></label></section>}
      <form className="cw-composer" onSubmit={(event) => { event.preventDefault(); send(); }}>
        <div className="cw-composer-target"><select aria-label="Conversation action" value={action} onChange={(event) => setAction(event.target.value as "modify" | "create")}><option value="modify" disabled={!selected}>Modify selected artifact</option><option value="create">Create another artifact</option></select>{action === "create" && <select aria-label="New artifact format" value={format} onChange={(event) => setFormat(event.target.value as "pdf" | "slides")}><option value="pdf">PDF document</option><option value="slides">Slides</option></select>}<button type="button" className="gx-icon-button" aria-label="Conversation settings" aria-expanded={settings} onClick={() => setSettings(!settings)}><SlidersHorizontal size={15} /></button></div>
        <div className={`cw-input-box${voicePreview ? " is-voice-preview" : ""}`}>
          <textarea ref={input} aria-label="Conversation message" placeholder={action === "modify" && artifact ? `Tell me what to change in “${artifact.title}”…` : "Describe the document or slides you need…"} value={conversation.draft} onChange={(event) => setDraft(event.target.value)} rows={3} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} />
          <div className="cw-input-context">{action === "modify" && artifact ? <><FileText size={12} />{artifact.title} · v{artifact.versionNumber}</> : <><Sparkles size={12} />{chosenCount} sources · New {format === "pdf" ? "document" : "slides"}</>}</div>
          <footer>
            <div className="cw-model-controls"><ModelPicker menu={previewModelMenu} choice={conversation.choice} onChange={(choice) => update(activeId, (item) => ({ ...item, choice }))} disabled={generating} /></div>
            <div className="cw-input-actions">
              <button type="button" className={`cw-voice-button${voicePreview ? " is-on" : ""}`} aria-label={voicePreview ? "Stop voice input" : "Voice input"} aria-pressed={voicePreview} disabled={generating} title={voicePreview ? "Stop the preview and insert sample text" : "Voice input · design preview"} onClick={toggleVoicePreview}><MicGlyph state={voicePreview ? "listening" : "idle"} size={16} /></button>
              <button type="submit" className="gx-btn gx-btn-primary cw-send-button" aria-label="Send message" disabled={!conversation.draft.trim() || voicePreview || generating}><ArrowUp size={19} /></button>
            </div>
          </footer>
        </div>
        {voicePreview ? <div className="cw-voice-status" role="status"><span className="cw-voice-bars" aria-hidden="true"><i /><i /><i /><i /></span><span>Voice preview · stop to insert sample text</span></div> : <small className="cw-composer-help" role={generating ? "status" : undefined}>{generating ? "Agent is working · follow the plan on the right" : "Enter to send · Shift + Enter for a new line"}</small>}
      </form>
    </section>
    <div className="cw-preview-width-divider"><ResizeHandle label="Resize conversation and preview widths" value={paneWidth * fittedChatShare} min={280} max={paneWidth - 360} controls="preview-chat-panel preview-artifacts-panel" onChange={(value) => setChatShare(value / paneWidth)} onReset={() => setChatShare(.42)} onDragging={setDraggingWidth} /></div>
    <section id="preview-artifacts-panel" ref={rightPanel} className="cw-right" aria-label="Conversation artifacts">
      <div className="cw-preview-toolbar cw-main-preview-toolbar">
        <strong>Preview</strong>
        <select aria-label="Preview artifact" value={selected?.id ?? ""} onChange={(event) => { const next = conversation.artifacts.find((item) => item.id === event.target.value); if (next) chooseArtifact(next); }}>
          {conversation.artifacts.length === 0 && <option value="">No artifacts yet</option>}
          {conversation.artifacts.map((item) => <option key={item.id} value={item.id}>{item.versions[0]!.title} · {item.format === "slides" ? "Slides" : "PDF"}</option>)}
        </select>
        {artifact && selected && <select aria-label="Artifact version" value={artifact.id} onChange={(event) => setVersionId(event.target.value)}>{selected.versions.map((item) => <option key={item.id} value={item.id}>v{item.versionNumber}</option>)}</select>}
        <button type="button" className={`cw-toolbar-details${controlsExpanded ? " is-selected" : ""}`} aria-label={controlsExpanded ? "Hide preview controls" : "Show preview controls"} aria-expanded={controlsExpanded} aria-controls="preview-controls-panel" title="Artifacts, agent plan & outline" onClick={() => setControlsExpanded(!controlsExpanded)}><PanelsTopLeft size={14} /><span>Details</span></button>
        {artifact?.outline.length ? <button type="button" className="gx-icon-button" aria-label="Show outline" title="Outline" onClick={() => { setControlsExpanded(true); requestAnimationFrame(() => outlinePanel.current?.scrollIntoView({ block: "nearest" })); }}><ListChecks size={15} /></button> : null}
        {artifact && <button type="button" className="gx-icon-button" aria-label="Export PDF" title="Export PDF" onClick={() => window.print()}><Download size={15} /></button>}
        <button type="button" className="gx-icon-button" aria-label={focused ? "Exit preview focus" : "Expand preview"} title={focused ? "Return to the conversation · Esc" : "Focus on the preview"} aria-pressed={focused} onClick={() => { if (!focused) setControlsExpanded(false); onFocus(!focused); }}>{focused ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>
      </div>
      <div className="cw-preview-controls" id="preview-controls-panel" hidden={!controlsExpanded} style={{ height: fittedControlsHeight }}>
      <header className="cw-controls-heading"><strong>Artifacts</strong><span>{conversation.artifacts.length} from this conversation</span><button type="button" className="gx-icon-button" aria-label="Collapse details area" title="Hide controls" onClick={() => setControlsExpanded(false)}><ChevronDown size={14} /></button></header>
      <div className="cw-artifact-tray" aria-label="Artifacts in this conversation">{conversation.artifacts.map((item) => <button type="button" key={item.id} className={`cw-artifact-card ${item.id === selected?.id ? "is-active" : ""}`} aria-label={`Preview ${item.versions[0]!.title}`} aria-current={item.id === selected?.id ? "page" : undefined} onClick={() => chooseArtifact(item)}><span className="cw-file-icon">{item.format === "slides" ? <Presentation size={19} /> : <FileText size={19} />}</span><strong>{item.versions[0]!.title}</strong><small>{item.format === "slides" ? "Slides" : "PDF document"}<span>v{item.versions[0]!.versionNumber}</span></small></button>)}{conversation.artifacts.length === 0 && <div className="cw-empty-tray"><FileText size={18} /><span>Documents and slides from this conversation appear here.</span></div>}</div>
      <AgentPlanPreview key={activeId} plan={agentPlan} automatic={generating} onNext={() => { if (!generating) advancePlan(activeId); }} onStop={() => stopPlan(activeId)} onReplay={() => setPlans((current) => ({ ...current, [activeId]: previewPlan(artifact?.title ?? "New artifact", artifact?.kind === "slides") }))} />
      {artifact && <div ref={outlinePanel} className="cw-controls-outline"><OutlineEditor key={artifact.id} outline={artifact.outline} label={artifact.kind === "slides" ? "slides" : "sections"} max={14} busy={generating} onRebuild={(outline) => { setAction("modify"); send(`调整大纲：${outline.map((item) => item.heading).join("、")}`); }} /></div>}
      {artifact && <button type="button" className="cw-controls-download" onClick={downloadMarkdown}><Download size={12} />Download Markdown</button>}
      </div>
      {controlsExpanded ? <div className="cw-controls-height-divider"><ResizeHandle label="Resize preview controls height" orientation="horizontal" value={fittedControlsHeight} min={120} max={controlsMax} controls="preview-controls-panel" onChange={setControlsHeight} onReset={() => setControlsHeight(320)} onDragging={setResizing} /></div>
        : agentPlan?.state === "running" && <button type="button" className="cw-compact-agent" aria-label="Show agent plan" onClick={() => setControlsExpanded(true)}><LoaderCircle className="spin" size={11} /><span>{agentPlan.steps[agentPlan.step]?.title ?? "Working…"}</span><small>{agentPlan.step}/{agentPlan.steps.length}</small><ChevronRight size={12} /></button>}
      {artifact && selected ? <>
        <div className={`cw-preview-scroll ${artifact.kind === "slides" ? "is-slides" : ""}`}>{artifact.kind === "slides" ? <div className="cw-deck"><LazySlidesView artifact={artifact} onCite={() => onNotice("Sample preview · citations are not connected")} /></div> : <article className="cw-paper" aria-label="Document preview"><div className="cw-paper-eyebrow">LEADERSHIP / LEARNING NOTES</div><h2 className="outputs-title">{artifact.title}</h2><p className="cw-paper-meta">For students · {artifact.inputs.sources} sources</p><ReportView artifact={artifact} onCite={() => onNotice("Sample preview · citations are not connected")} /><footer className="cw-paper-foot">Design preview · sample content<span>01</span></footer></article>}</div>
      </> : <div className="cw-preview-empty"><FileText size={30} /><h3>Your artifacts live here.</h3><p>A conversation can hold multiple documents and slide decks. Create them through the conversation, then select one to preview here.</p></div>}
    </section>
    {artifact && <PrintRoot artifact={artifact} />}
  </div>;
}
