import { useRef } from "react";

/** Pointer and keyboard resizing for workspace panels. */
export function ResizeHandle({ label, orientation = "vertical", value, min, max, onChange, onReset, controls, onDragging }: {
  label: string;
  orientation?: "vertical" | "horizontal";
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
  onReset: () => void;
  controls: string;
  onDragging: (dragging: boolean) => void;
}) {
  const drag = useRef<{ id: number; origin: number; value: number } | null>(null);
  const clamp = (next: number) => Math.max(min, Math.min(max, next));
  return <div className={`aw-resize-handle is-${orientation}`} role="separator" tabIndex={0} aria-label={label} aria-orientation={orientation} aria-controls={controls}
    aria-valuemin={Math.round(min)} aria-valuemax={Math.round(max)} aria-valuenow={Math.round(value)} aria-valuetext={`${Math.round(value)} pixels`} title="Drag to resize · double-click to reset"
    onPointerDown={(event) => {
      if (event.button !== 0) return;
      event.preventDefault(); event.currentTarget.focus();
      drag.current = { id: event.pointerId, origin: orientation === "vertical" ? event.clientX : event.clientY, value };
      event.currentTarget.setPointerCapture(event.pointerId); onDragging(true);
    }}
    onPointerMove={(event) => {
      const start = drag.current;
      if (!start || start.id !== event.pointerId) return;
      onChange(clamp(start.value + (orientation === "vertical" ? event.clientX : event.clientY) - start.origin));
    }}
    onPointerUp={(event) => { drag.current = null; onDragging(false); if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
    onPointerCancel={() => { drag.current = null; onDragging(false); }}
    onLostPointerCapture={() => { drag.current = null; onDragging(false); }}
    onDoubleClick={onReset}
    onKeyDown={(event) => {
      const decrease = orientation === "vertical" ? "ArrowLeft" : "ArrowUp";
      const increase = orientation === "vertical" ? "ArrowRight" : "ArrowDown";
      if (event.key === decrease || event.key === increase) { event.preventDefault(); onChange(clamp(value + (event.key === increase ? 1 : -1) * (event.shiftKey ? 50 : 20))); }
      else if (event.key === "Home" || event.key === "End") { event.preventDefault(); onChange(event.key === "Home" ? min : max); }
      else if (event.key === "Escape" && drag.current) { onChange(drag.current.value); const pointerId = drag.current.id; drag.current = null; onDragging(false); if (event.currentTarget.hasPointerCapture(pointerId)) event.currentTarget.releasePointerCapture(pointerId); }
    }}><span aria-hidden="true" /></div>;
}
