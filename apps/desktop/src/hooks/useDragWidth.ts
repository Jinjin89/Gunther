import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

interface DragWidthOptions {
  /** Where the chosen width is remembered on this device. */
  storageKey: string;
  min: number;
  /** The widest it may be right now (it depends on the window). */
  max: () => number;
  /** Used until a width has been chosen, and on double-click. */
  fallback: number;
  /** An element whose size changing (besides the window) changes how wide the panel may be. */
  observe?: () => Element | null;
}

/**
 * The width of a panel docked to the right: dragged by its left edge (leftwards
 * widens), moved by the arrow keys, reset by a double-click, and remembered.
 * Spread `handle` on the edge element.
 */
export function useDragWidth({ storageKey, min, max, fallback, observe }: DragWidthOptions) {
  const clamp = useCallback((value: number) => Math.min(Math.max(value, min), Math.max(min, max())), [min, max]);
  const [width, setWidth] = useState(() => {
    const stored = Number(window.localStorage.getItem(storageKey));
    return Number.isFinite(stored) && stored > 0 ? clamp(stored) : fallback;
  });
  const drag = useRef<{ startX: number; startWidth: number } | null>(null);
  // The room changes with the window: the chosen width stays as chosen, but what is shown is
  // kept within what fits now, so a narrowed window never leaves the panel's content outside it.
  const [, setRoom] = useState(0);
  const observed = useRef(observe);
  observed.current = observe;
  useEffect(() => {
    const refresh = () => setRoom((count) => count + 1);
    window.addEventListener("resize", refresh);
    const target = observed.current?.() ?? null;
    const watcher = target && typeof ResizeObserver !== "undefined" ? new ResizeObserver(refresh) : null;
    if (target) watcher?.observe(target);
    return () => {
      window.removeEventListener("resize", refresh);
      watcher?.disconnect();
    };
  }, []);
  const shown = clamp(width);
  const commit = useCallback((next: number) => {
    const value = clamp(next);
    setWidth(value);
    try {
      window.localStorage.setItem(storageKey, String(value));
    } catch {
      // The width still holds until the app closes.
    }
  }, [clamp, storageKey]);
  const handle = {
    onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
      drag.current = { startX: event.clientX, startWidth: shown };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
      if (drag.current) setWidth(clamp(drag.current.startWidth + drag.current.startX - event.clientX));
    },
    onPointerUp: (event: PointerEvent<HTMLDivElement>) => {
      if (!drag.current) return;
      commit(drag.current.startWidth + drag.current.startX - event.clientX);
      drag.current = null;
    },
    onDoubleClick: () => commit(fallback),
    onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "ArrowLeft") { event.preventDefault(); commit(shown + 24); }
      if (event.key === "ArrowRight") { event.preventDefault(); commit(shown - 24); }
    },
  };
  return { width: shown, handle };
}
