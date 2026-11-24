import { BookOpen, PanelRight, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { useDragWidth } from "../hooks/useDragWidth";
import { useEscape } from "../shortcuts/shortcuts";

export type InspectorTab = "evidence" | "studio";

export function LibraryInspector({ workspace, screenWidth, collapsed, onCollapse, tab, onTab, evidenceCount,
  running, detailOpen, onBack, originalPdf, evidence, studio }: {
  workspace: RefObject<HTMLDivElement | null>;
  screenWidth: number;
  collapsed: boolean;
  onCollapse: () => void;
  tab: InspectorTab;
  onTab: (tab: InspectorTab) => void;
  evidenceCount: number;
  running: boolean;
  detailOpen: boolean;
  onBack: () => void;
  originalPdf: boolean;
  evidence: ReactNode;
  studio: ReactNode;
}) {
  const narrow = screenWidth < 1_080;
  const root = useRef<HTMLElement>(null);
  const panelId = useId();
  const viewingPdf = tab === "evidence" && detailOpen && originalPdf;
  const [manualPdfWidth, setManualPdfWidth] = useState(false);
  useEffect(() => { if (!viewingPdf) setManualPdfWidth(false); }, [viewingPdf]);
  const max = () => viewingPdf ? Math.max(280, Math.floor((workspace.current?.clientWidth || window.innerWidth) * 0.5)) : 440;
  const fallback = Math.round(Math.max(336, Math.min(380, 336 + (screenWidth - 1_280) * 44 / 448)));
  const { width, handle } = useDragWidth({ storageKey: "gunther:library-studio-width", min: 280, max, fallback,
    visibleMin: viewingPdf && !manualPdfWidth ? 460 : undefined, side: "right", observe: () => workspace.current });

  useEffect(() => {
    if (!narrow || collapsed) return;
    const away = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) onCollapse();
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [narrow, collapsed, onCollapse]);
  useEscape(() => {
    if (tab === "evidence" && detailOpen) onBack();
    else onCollapse();
  }, !collapsed && ((tab === "evidence" && detailOpen) || narrow));

  return <aside ref={root} aria-label="Inspector" className={`library-column library-inspector${collapsed ? " is-collapsed" : ""}${narrow ? " is-narrow" : ""}`}
    style={collapsed || narrow ? undefined : { width }}>
    <div className="inspector-rail" hidden={!collapsed}>
      <button type="button" className="gx-icon-button" aria-label="Expand Evidence" aria-expanded={false} aria-controls={panelId} title="Evidence" onClick={() => onTab("evidence")}><BookOpen size={16} /></button>
      <button type="button" className="gx-icon-button" aria-label="Expand Studio" aria-expanded={false} aria-controls={panelId} title="Studio" onClick={() => onTab("studio")}><PanelRight size={16} />{running && <i className="inspector-running" />}</button>
    </div>
    <div className="inspector-panel" id={panelId} hidden={collapsed}>
      <header className="inspector-heading">
        <div className="inspector-tabs" role="tablist" aria-label="Inspector">
          <button type="button" role="tab" aria-selected={tab === "evidence"} onClick={() => onTab("evidence")}>Evidence ({evidenceCount})</button>
          <button type="button" role="tab" aria-selected={tab === "studio"} onClick={() => onTab("studio")}>Studio{running && <i className="inspector-running" title="Build running" />}</button>
        </div>
        <button type="button" className="gx-icon-button" aria-label="Collapse Inspector" aria-expanded={true} aria-controls={panelId} title="Collapse Inspector" onClick={onCollapse}><X size={16} /></button>
      </header>
      <div className="inspector-content" role="tabpanel" aria-label="Evidence" hidden={tab !== "evidence"}>{evidence}</div>
      <div className="inspector-content" role="tabpanel" aria-label="Studio" hidden={tab !== "studio"}>{studio}</div>
      {!narrow && <div className="library-column-resize" role="separator" tabIndex={0} aria-orientation="vertical"
        aria-label="Resize Inspector" aria-valuemin={280} aria-valuemax={max()} aria-valuenow={width}
        title="Drag to resize. Double-click to reset." {...handle}
        onPointerMove={(event) => { if (viewingPdf && event.buttons) setManualPdfWidth(true); handle.onPointerMove(event); }}
        onKeyDown={(event) => { if (viewingPdf && (event.key === "ArrowLeft" || event.key === "ArrowRight")) setManualPdfWidth(true); handle.onKeyDown(event); }}
        onDoubleClick={() => { setManualPdfWidth(false); handle.onDoubleClick(); }} />}
    </div>
  </aside>;
}
