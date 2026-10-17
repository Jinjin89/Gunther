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

/**
 * The largest fit at which content still fits, between `low` (a fit it is thought to fit at, the
 * proportional guess of fitFor) and `high` (one it does not fit at). Content that wraps gets
 * shorter faster than its type is scaled, so the proportional guess can be far smaller than
 * needed; this halves the distance until it is within a hundredth. `used(fit)` is how much of the
 * slide's height the content takes when drawn at that fit.
 */
export function settleFit(used: (fit: number) => number, room: number, low: number, high: number): number {
  let fits = low;
  let over = high;
  for (let step = 0; step < 8 && over - fits > 0.011; step += 1) {
    const middle = Math.round(((fits + over) / 2) * 100) / 100;
    if (middle <= fits || middle >= over) break;
    if (used(middle) <= room) fits = middle;
    else over = middle;
  }
  return fits;
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
