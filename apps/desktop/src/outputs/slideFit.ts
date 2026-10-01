import { useSyncExternalStore } from "react";

/**
 * How far a slide's type is scaled down so that its text fits the slide (1 is as designed).
 *
 * A slide is drawn on a fixed 1280 × 720 canvas, so how much room its text needs depends only
 * on the text. The fit is worked out where the slide is first laid out (the deck or its
 * thumbnail) and kept here by the slide's text, so the deck, the thumbnails and the printed
 * page, which is never laid out on screen, all draw the same slide.
 */
const SMALLEST = 0.55;
const KEPT = 400;

const fits = new Map<string, number>();
const watchers = new Set<() => void>();

/**
 * The scale for a slide whose text takes `used` of the space `room` leaves it (both as
 * fractions of the slide's height) when drawn at scale `from`. It never grows, and it stops
 * at a size still worth reading; text longer than that runs off the slide.
 */
export function fitFor(used: number, room: number, from = 1): number {
  if (!Number.isFinite(used) || !Number.isFinite(room) || used <= room || used <= 0 || room <= 0) return from;
  return Math.max(SMALLEST, Math.floor(from * (room / used) * 100) / 100);
}

export function rememberFit(key: string, fit: number) {
  if (fits.get(key) === fit) return;
  fits.delete(key);
  fits.set(key, fit);
  // Typing in the editor makes a new text with each key; keep only the latest.
  if (fits.size > KEPT) fits.delete(fits.keys().next().value as string);
  watchers.forEach((watcher) => watcher());
}

const watch = (watcher: () => void) => {
  watchers.add(watcher);
  return () => { watchers.delete(watcher); };
};

/** The fit kept for a slide's text, and updates when it changes. */
export const useFit = (key: string) => useSyncExternalStore(watch, () => fits.get(key) ?? 1);
