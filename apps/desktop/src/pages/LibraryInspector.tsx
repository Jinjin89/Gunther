import { BookOpen, PanelRight, X } from "lucide-react";
import type { ReactNode, RefObject } from "react";
import { ColumnResize, type ColumnLayout } from "./LibraryColumn";

export type InspectorTab = "evidence" | "studio";

const tabs = [
  { id: "evidence", label: "Evidence", Icon: BookOpen },
  { id: "studio", label: "Studio", Icon: PanelRight },
] as const;

export function InspectorRail({ panelId, expanded, running, onToggle }: {
  panelId: string; expanded: InspectorTab | null; running: boolean; onToggle: (tab: InspectorTab) => void;
}) {
  return <div className="inspector-rail">
    {tabs.map(({ id, label, Icon }) => <button key={id} type="button" className="gx-icon-button" data-panel-focus={id}
      aria-label={expanded === id ? "Collapse Inspector" : `Expand ${label}`} aria-expanded={expanded === id}
      aria-controls={panelId} title={expanded === id ? "Collapse Inspector" : label} onClick={() => onToggle(id)}>
      <Icon size={16} />{id === "studio" && running && <i className="inspector-running" />}
    </button>)}
  </div>;
}

export function LibraryInspector({ columnRef, panelId, layout, rail, onCollapse, tab, onTab, evidenceCount,
  briefUpdating, running, evidence, studio }: {
  columnRef: RefObject<HTMLElement | null>;
  panelId: string;
  layout: ColumnLayout;
  rail: ReactNode;
  onCollapse: (() => void) | undefined;
  tab: InspectorTab;
  onTab: (tab: InspectorTab) => void;
  evidenceCount: number;
  briefUpdating: boolean;
  running: boolean;
  evidence: ReactNode;
  studio: ReactNode;
}) {
  const { collapsed, slideOver, track, size } = layout;
  return <aside ref={columnRef} aria-label="Inspector" data-panel-focus={tab}
    className={`library-column library-inspector${collapsed ? " is-collapsed" : ""}${slideOver ? " is-overlay" : ""}`} style={{ width: track }}>
    {collapsed && rail}
    <div className="inspector-panel" id={panelId} hidden={collapsed}>
      <header className="inspector-heading">
        <div className="inspector-tabs" role="tablist" aria-label="Inspector">
          <button type="button" role="tab" data-panel-focus="evidence" aria-selected={tab === "evidence"} aria-label={`Evidence (${evidenceCount})`}
            aria-describedby={briefUpdating ? `${panelId}-brief-update` : undefined} onClick={() => onTab("evidence")}>
            Evidence <span className="inspector-tab-count">{evidenceCount}</span>
            {briefUpdating && <span className="inspector-brief-updating" title="Brief updating" aria-hidden="true" />}
          </button>
          <button type="button" role="tab" data-panel-focus="studio" aria-selected={tab === "studio"} onClick={() => onTab("studio")}>Studio{running && <i className="inspector-running" title="Build running" />}</button>
        </div>
        {briefUpdating && <span className="gx-sr-only" id={`${panelId}-brief-update`}>Brief updating</span>}
        {onCollapse && <button type="button" className="gx-icon-button" aria-label="Collapse Inspector" aria-expanded={true}
          aria-controls={panelId} title="Collapse Inspector" onClick={onCollapse}><X size={16} /></button>}
      </header>
      <div className="inspector-content" role="tabpanel" aria-label="Evidence" hidden={tab !== "evidence"}>{evidence}</div>
      <div className="inspector-content" role="tabpanel" aria-label="Studio" hidden={tab !== "studio"}>{studio}</div>
      {!collapsed && !slideOver && <ColumnResize name="Inspector" size={size} />}
    </div>
  </aside>;
}
