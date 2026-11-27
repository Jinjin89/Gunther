import type { ReactNode, RefObject } from "react";
import type { useDragWidth } from "../hooks/useDragWidth";

export type ColumnLayout = {
  collapsed: boolean;
  slideOver: boolean;
  track: number;
  size: ReturnType<typeof useDragWidth>;
};

export function ColumnResize({ name, size }: { name: string; size: ColumnLayout["size"] }) {
  return <div className="library-column-resize" role="separator" tabIndex={0} aria-orientation="vertical"
    aria-label={`Resize ${name}`} aria-valuemin={size.min} aria-valuemax={size.max} aria-valuenow={size.width}
    title="Drag to resize. Double-click to reset." {...size.handle} />;
}

export function LibraryColumn({ columnRef, panelId, layout, trigger, children }: {
  columnRef: RefObject<HTMLDivElement | null>;
  panelId: string;
  layout: ColumnLayout;
  trigger: ReactNode;
  children: ReactNode;
}) {
  const { collapsed, slideOver, track, size } = layout;

  return <div ref={columnRef} data-panel-focus="library"
    className={`library-column library-sources${collapsed ? " is-collapsed" : ""}${slideOver ? " is-overlay" : ""}`} style={{ width: track }}>
    <div className="library-column-panel">
      {(!collapsed || trigger) && <header className="library-column-heading">
        {!collapsed && <h2>Library</h2>}
        {trigger}
      </header>}
      <div className="library-column-body" id={panelId} hidden={collapsed}>{children}</div>
      {!collapsed && !slideOver && <ColumnResize name="Library" size={size} />}
    </div>
  </div>;
}
