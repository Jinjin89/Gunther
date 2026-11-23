import { PanelLeft, PanelRight } from "lucide-react";
import { useId, type ReactNode, type RefObject } from "react";
import { useDragWidth } from "../hooks/useDragWidth";

const columns = {
  sources: { label: "Sources", side: "left", icon: PanelLeft, width: 252 },
  studio: { label: "Studio", side: "right", icon: PanelRight, width: 264 },
} as const;
const MIN_WIDTH = 180;

export function LibraryColumn({ column, workspace, collapsed, onToggle, children }: {
  column: keyof typeof columns;
  workspace: RefObject<HTMLDivElement | null>;
  collapsed: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const { label, side, icon: Icon, width: fallback } = columns[column];
  const id = useId();
  // Cap each column's share so two open columns leave space for the middle.
  const max = () => Math.max(MIN_WIDTH, Math.min(380, Math.floor((workspace.current?.clientWidth || window.innerWidth) * 0.32)));
  const { width, handle } = useDragWidth({
    storageKey: `gunther:library-${column}-width`, min: MIN_WIDTH, max, fallback, side,
    observe: () => workspace.current,
  });
  const toggle = `${collapsed ? "Expand" : "Collapse"} ${label}`;

  return <div className={`library-column library-${column}${collapsed ? " is-collapsed" : ""}`} style={collapsed ? undefined : { width }}>
    <header className="library-column-heading">
      {!collapsed && <h2>{label}</h2>}
      <button type="button" className="gx-icon-button" aria-label={toggle} title={toggle}
        aria-controls={id} aria-expanded={!collapsed} onClick={onToggle}><Icon size={16} /></button>
    </header>
    <div className="library-column-body" id={id} hidden={collapsed}>{children}</div>
    {!collapsed && <div className="library-column-resize" role="separator" tabIndex={0} aria-orientation="vertical"
      aria-label={`Resize ${label}`} aria-valuemin={MIN_WIDTH} aria-valuemax={max()} aria-valuenow={width}
      title="Drag to resize. Double-click to reset." {...handle} />}
  </div>;
}
