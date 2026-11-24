import { PanelLeft, PanelRight } from "lucide-react";
import { useId, type ReactNode, type RefObject } from "react";
import { useDragWidth } from "../hooks/useDragWidth";

const columns = {
  sources: { label: "Library", side: "left", icon: PanelLeft, width: 260 },
  studio: { label: "Studio", side: "right", icon: PanelRight, width: 264 },
} as const;

export function LibraryColumn({ column, workspace, collapsed, slideOver = false, onToggle, children }: {
  column: keyof typeof columns;
  workspace: RefObject<HTMLDivElement | null>;
  collapsed: boolean;
  slideOver?: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const { label, side, icon: Icon, width: fallback } = columns[column];
  const min = column === "sources" ? 200 : 180;
  const id = useId();
  // Keep Studio's existing share cap; Library has its own fixed width limits.
  const max = () => column === "sources" ? 360 : Math.max(min, Math.min(380, Math.floor((workspace.current?.clientWidth || window.innerWidth) * 0.32)));
  const { width, handle } = useDragWidth({
    storageKey: `gunther:library-${column}-width`, min, max, fallback, side,
    observe: () => workspace.current,
  });
  const toggle = `${collapsed ? "Expand" : "Collapse"} ${label}`;

  return <div className={`library-column library-${column}${collapsed ? " is-collapsed" : ""}${slideOver ? " is-narrow" : ""}`} style={collapsed || slideOver ? undefined : { width }}>
    <div className="library-column-panel">
      <header className="library-column-heading">
        {!collapsed && <h2>{label}</h2>}
        <button type="button" className="gx-icon-button" aria-label={toggle} title={toggle}
          aria-controls={id} aria-expanded={!collapsed} onClick={onToggle}><Icon size={16} /></button>
      </header>
      <div className="library-column-body" id={id} hidden={collapsed}>{children}</div>
      {!collapsed && !slideOver && <div className="library-column-resize" role="separator" tabIndex={0} aria-orientation="vertical"
        aria-label={`Resize ${label}`} aria-valuemin={min} aria-valuemax={max()} aria-valuenow={width}
        title="Drag to resize. Double-click to reset." {...handle} />}
    </div>
  </div>;
}
