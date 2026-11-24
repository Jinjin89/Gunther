import { persistentStorage } from "../persistentStorage";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

interface DragWidthOptions {
  /** Where the chosen width is remembered on this device. */
  storageKey: string;
  min: number;
  /** The widest it may be right now (it depends on the window). */
  max: () => number;
  /** Used until a width has been chosen, and on double-click. */
  fallback: number;
  /** Temporarily widen the visible panel without overwriting its saved width. */
  visibleMin?: number | undefined;
  /** Which side of the workspace the panel is docked to. */
  side?: "left" | "right";
  /** An element whose size changing (besides the window) changes how wide the panel may be. */
  observe?: () => Element | null;
}

/**
 * A docked panel's width, set by dragging the inner edge or using its arrow
 * keys. Double-click resets it.
 * Spread `handle` on the edge element.
 */
export function useDragWidth({ storageKey, min, max, fallback, visibleMin, side = "right", observe }: DragWidthOptions) {
  const clamp = useCallback((value: number) => Math.min(Math.max(value, min), Math.max(min, max())), [min, max]);
  const [width, setWidth] = useState(() => {
    const stored = Number(persistentStorage.getItem(storageKey));
    return Number.isFinite(stored) && stored > 0 ? stored : fallback;
  });
  const drag = useRef<{ pointerId: number; startX: number; startWidth: number; chosenWidth: number } | null>(null);
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
  const shown = clamp(Math.max(width, visibleMin ?? min));
  const direction = side === "left" ? 1 : -1;
  const commit = useCallback((next: number) => {
    const value = clamp(next);
    setWidth(value);
    try {
      persistentStorage.setItem(storageKey, String(value));
    } catch {
      // The width still holds until the app closes.
    }
  }, [clamp, storageKey]);
  const cancel = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    setWidth(drag.current.chosenWidth);
    drag.current = null;
  };
  const handle = {
    onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
      if (event.button !== 0 || drag.current) return;
      drag.current = { pointerId: event.pointerId, startX: event.clientX, startWidth: shown, chosenWidth: width };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
      if (drag.current?.pointerId !== event.pointerId) return;
      setWidth(clamp(drag.current.startWidth + direction * (event.clientX - drag.current.startX)));
    },
    onPointerUp: (event: PointerEvent<HTMLDivElement>) => {
      if (drag.current?.pointerId !== event.pointerId) return;
      commit(drag.current.startWidth + direction * (event.clientX - drag.current.startX));
      drag.current = null;
    },
    onPointerCancel: cancel,
    onLostPointerCapture: cancel,
    onDoubleClick: () => commit(fallback),
    onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "ArrowLeft") { event.preventDefault(); commit(shown - direction * 24); }
      if (event.key === "ArrowRight") { event.preventDefault(); commit(shown + direction * 24); }
    },
  };
  return { width: shown, handle };
}
