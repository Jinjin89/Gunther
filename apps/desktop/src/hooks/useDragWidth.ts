import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { persistentStorage } from "../persistentStorage";

interface DragWidthOptions {
  storageKey: string;
  min: number;
  /** Use a callback when the caller's limit depends directly on the window. */
  max: number | (() => number);
  fallback: number;
  side?: "left" | "right";
}

export function useDragWidth({ storageKey, min, max, fallback, side = "right" }: DragWidthOptions) {
  const [chosenWidth, setChosenWidth] = useState<number | null>(() => {
    const stored = Number(persistentStorage.getItem(storageKey));
    return Number.isFinite(stored) && stored > 0 ? stored : null;
  });
  const [preview, setPreview] = useState<number | null>(null);
  const drag = useRef<{ pointerId: number; startX: number; startWidth: number } | null>(null);
  const [, setRoom] = useState(0);
  const windowLimit = typeof max === "function";
  useEffect(() => {
    if (!windowLimit) return;
    const refresh = () => setRoom((count) => count + 1);
    window.addEventListener("resize", refresh);
    return () => window.removeEventListener("resize", refresh);
  }, [windowLimit]);

  const limit = Math.max(min, windowLimit ? max() : max);
  const clamp = (value: number) => Math.min(Math.max(value, min), limit);
  const width = clamp(preview ?? chosenWidth ?? fallback);
  const direction = side === "left" ? 1 : -1;
  const commit = (next: number | null) => {
    const value = next === null ? null : clamp(next);
    setChosenWidth(value);
    try {
      if (value === null) persistentStorage.removeItem(storageKey);
      else persistentStorage.setItem(storageKey, String(value));
    } catch {
      // The width still holds until the app closes.
    }
  };
  const clearDrag = useCallback(() => {
    if (!drag.current) return;
    drag.current = null;
    setPreview(null);
  }, []);
  // A dock can lose its resize grip when it becomes a rail or overlay.
  const ref = useCallback((node: HTMLDivElement | null) => {
    if (!node) clearDrag();
  }, [clearDrag]);
  const draggedWidth = (event: PointerEvent<HTMLDivElement>) => {
    const start = drag.current;
    if (!start || start.pointerId !== event.pointerId) return null;
    return start.startWidth + direction * (event.clientX - start.startX);
  };
  const cancel = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId === event.pointerId) clearDrag();
  };
  const handle = {
    ref,
    onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
      if (event.button !== 0 || drag.current) return;
      drag.current = { pointerId: event.pointerId, startX: event.clientX, startWidth: width };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
      const next = draggedWidth(event);
      if (next !== null) setPreview(next);
    },
    onPointerUp: (event: PointerEvent<HTMLDivElement>) => {
      const next = draggedWidth(event);
      if (next === null) return;
      const moved = event.clientX !== drag.current?.startX;
      clearDrag();
      if (moved) commit(next);
    },
    onPointerCancel: cancel,
    onLostPointerCapture: cancel,
    onDoubleClick: () => { clearDrag(); commit(null); },
    onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      clearDrag();
      commit(width + direction * (event.key === "ArrowRight" ? 24 : -24));
    },
  };
  return { width, chosenWidth, min, max: limit, handle };
}
