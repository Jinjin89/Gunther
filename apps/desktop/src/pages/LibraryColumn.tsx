import { PanelLeft } from "lucide-react";
import { useId, type ReactNode, type RefObject } from "react";
import { useDragWidth } from "../hooks/useDragWidth";

export function LibraryColumn({ workspace, columnRef, collapsed, slideOver = false, onToggle, children }: {
  workspace: RefObject<HTMLDivElement | null>;
  columnRef: RefObject<HTMLDivElement | null>;
  collapsed: boolean;
  slideOver?: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const id = useId();
  const max = () => 360;
  const { width, handle } = useDragWidth({
    storageKey: "gunther:library-sources-width", min: 200, max, fallback: 260, side: "left",
    observe: () => workspace.current,
  });
  const toggle = `${collapsed ? "Expand" : "Collapse"} Library`;

  return <div ref={columnRef} className={`library-column library-sources${collapsed ? " is-collapsed" : ""}${slideOver ? " is-narrow" : ""}`} style={collapsed || slideOver ? undefined : { width }}>
    <div className="library-column-panel">
      <header className="library-column-heading">
        {!collapsed && <h2>Library</h2>}
        <button type="button" className="gx-icon-button" aria-label={toggle} title={toggle}
          aria-controls={id} aria-expanded={!collapsed} onClick={onToggle}><PanelLeft size={16} /></button>
      </header>
      <div className="library-column-body" id={id} hidden={collapsed}>{children}</div>
      {!collapsed && !slideOver && <div className="library-column-resize" role="separator" tabIndex={0} aria-orientation="vertical"
        aria-label="Resize Library" aria-valuemin={200} aria-valuemax={max()} aria-valuenow={width}
        title="Drag to resize. Double-click to reset." {...handle} />}
    </div>
  </div>;
}
