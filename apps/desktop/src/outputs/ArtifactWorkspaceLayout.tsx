import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { persistentStorage } from "../persistentStorage";
import { ResizeHandle } from "./ResizeHandle";
import "./workspace.css";

function readNumber(key: string, fallback: number, min: number, max: number) {
  try { const saved = Number(persistentStorage.getItem(key)); return Number.isFinite(saved) && saved >= min && saved <= max ? saved : fallback; } catch { return fallback; }
}
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

export function usePreviewControls(storageKey: string) {
  const read = () => { try { return persistentStorage.getItem(storageKey) === "expanded"; } catch { return false; } };
  const [saved, setSaved] = useState(() => ({ key: storageKey, value: read() }));
  const expanded = saved.key === storageKey ? saved.value : read();
  useEffect(() => { if (saved.key !== storageKey) setSaved({ key: storageKey, value: read() }); }, [storageKey, saved.key]);
  useEffect(() => { if (saved.key !== storageKey) return; try { persistentStorage.setItem(storageKey, expanded ? "expanded" : "compact"); } catch { /* The current layout remains usable. */ } }, [storageKey, saved.key, expanded]);
  const setExpanded = useCallback((value: boolean) => setSaved({ key: storageKey, value }), [storageKey]);
  return [expanded, setExpanded] as const;
}

/** Resize preferences are independent of conversations and saved artifact versions. */
export function ArtifactWorkspaceLayout({ storageKey, sources, conversations, chat, preview, focused, onFocus, controlsHeight }: {
  storageKey: string; sources: ReactNode; conversations: ReactNode; chat: ReactNode; preview: ReactNode;
  focused: boolean; onFocus: (value: boolean) => void;
  controlsHeight: number;
}) {
  const [sourceShare, setSourceShare] = useState(() => readNumber(`${storageKey}:split`, .7, .15, .9));
  const [sourceWidth, setSourceWidth] = useState(() => readNumber(`${storageKey}:sources`, 230, 180, 380));
  const [chatShare, setChatShare] = useState(() => readNumber(`${storageKey}:chat`, .42, .15, .8));
  const [width, setWidth] = useState(1100);
  const [leftHeight, setLeftHeight] = useState(800);
  const [rightHeight, setRightHeight] = useState(800);
  const [dragging, setDragging] = useState<"row" | "column" | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const left = useRef<HTMLElement>(null);
  const right = useRef<HTMLElement>(null);
  useEffect(() => {
    const measure = () => {
      if (root.current) setWidth(root.current.getBoundingClientRect().width || 1100);
      const lh = left.current?.getBoundingClientRect().height ?? 0;
      const rh = right.current?.getBoundingClientRect().height ?? 0;
      if (lh > 0) setLeftHeight(lh);
      if (rh > 0) setRightHeight(rh);
    };
    measure();
    if (typeof ResizeObserver === "undefined") { window.addEventListener("resize", measure); return () => window.removeEventListener("resize", measure); }
    const observer = new ResizeObserver(measure);
    [root.current, left.current, right.current].forEach((element) => { if (element) observer.observe(element); });
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    try {
      persistentStorage.setItem(`${storageKey}:split`, String(sourceShare)); persistentStorage.setItem(`${storageKey}:sources`, String(sourceWidth)); persistentStorage.setItem(`${storageKey}:chat`, String(chatShare));
    } catch { /* Changing sizes does not depend on storage being available. */ }
  }, [sourceShare, sourceWidth, chatShare, storageKey]);
  useEffect(() => {
    if (!focused) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); onFocus(false); } };
    window.addEventListener("keydown", escape); return () => window.removeEventListener("keydown", escape);
  }, [focused, onFocus]);
  const sourceMax = Math.max(180, Math.min(380, width - 18 - 280 - 360));
  const fittedSource = clamp(sourceWidth, 180, sourceMax);
  const panes = Math.max(640, width - fittedSource - 18);
  const fittedChat = clamp(chatShare, 280 / panes, 1 - 360 / panes);
  const splitSpace = Math.max(1, leftHeight - 9);
  const topMin = Math.min(200, splitSpace * .6);
  const topMax = splitSpace - Math.min(128, splitSpace * .38);
  const topHeight = clamp(sourceShare * splitSpace, topMin, topMax);
  const controlsMax = Math.max(120, Math.min(480, rightHeight - 213));
  const fittedControls = clamp(controlsHeight, 120, controlsMax);
  const style = { "--aw-left-width": `${fittedSource}px`, "--aw-chat-share": `${fittedChat}fr`, "--aw-preview-share": `${1 - fittedChat}fr`, "--aw-controls-height": `${fittedControls}px` } as CSSProperties;
  return <div ref={root} style={style} className={`aw-workspace${width < 850 ? " is-narrow" : ""}${width < 550 ? " is-tiny" : ""}${focused ? " is-preview-focused" : ""}${dragging === "row" ? " is-resizing" : dragging === "column" ? " is-resizing-width" : ""}`}>
    <aside ref={left} className="aw-left" id="artifact-context-panel" aria-label="Sources and conversations" style={{ "--aw-source-share": `${topHeight / splitSpace}fr`, "--aw-conversation-share": `${1 - topHeight / splitSpace}fr` } as CSSProperties}>
      <div className="aw-source-region">{sources}</div>
      <ResizeHandle label="Resize Sources and Conversations" orientation="horizontal" value={topHeight} min={topMin} max={topMax} controls="artifact-context-panel" onChange={(value) => setSourceShare(value / splitSpace)} onReset={() => setSourceShare(.7)} onDragging={(value) => setDragging(value ? "row" : null)} />
      {conversations}
    </aside>
    <div className="aw-source-width-divider"><ResizeHandle label="Resize sources panel width" value={fittedSource} min={180} max={sourceMax} controls="artifact-context-panel" onChange={setSourceWidth} onReset={() => setSourceWidth(230)} onDragging={(value) => setDragging(value ? "column" : null)} /></div>
    <section className="aw-chat" id="artifact-chat-panel" aria-label="Artifact conversation">{chat}</section>
    <div className="aw-preview-width-divider"><ResizeHandle label="Resize conversation and preview widths" value={panes * fittedChat} min={280} max={panes - 360} controls="artifact-chat-panel artifact-preview-panel" onChange={(value) => setChatShare(value / panes)} onReset={() => setChatShare(.42)} onDragging={(value) => setDragging(value ? "column" : null)} /></div>
    <section ref={right} className="aw-right" id="artifact-preview-panel" aria-label="Conversation artifacts">{preview}</section>
  </div>;
}

export function PreviewHeightHandle({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [max, setMax] = useState(480);
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    const panel = ref.current?.parentElement;
    if (!panel) return;
    const measure = () => setMax(Math.max(120, Math.min(480, panel.clientHeight - 213)));
    measure(); if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure); observer.observe(panel); return () => observer.disconnect();
  }, []);
  return <div ref={ref} className={`aw-controls-height-divider${dragging ? " is-dragging" : ""}`}><ResizeHandle label="Resize preview controls height" orientation="horizontal" value={clamp(value, 120, max)} min={120} max={max} controls="artifact-preview-controls" onChange={onChange} onReset={() => onChange(380)} onDragging={setDragging} /></div>;
}
