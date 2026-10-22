import { useSyncExternalStore } from "react";
import { persistentStorage } from "../../persistentStorage";
import type { BrandTheme } from "./brand";

const KEY = "gunther:brand-theme";
const watchers = new Set<() => void>();
let cache: { raw: string | null; value: BrandTheme | null } | null = null;

function read(): BrandTheme | null {
  let raw: string | null = null;
  try { raw = persistentStorage.getItem(KEY); } catch { raw = null; }
  if (cache && cache.raw === raw) return cache.value;
  let value: BrandTheme | null = null;
  try {
    const parsed = raw ? JSON.parse(raw) as Partial<BrandTheme> : null;
    if (parsed && typeof parsed.name === "string" && /^#[0-9a-f]{6}$/i.test(parsed.background ?? "") && /^#[0-9a-f]{6}$/i.test(parsed.text ?? "") && /^#[0-9a-f]{6}$/i.test(parsed.accent ?? "")) {
      value = { name: parsed.name, background: parsed.background!, text: parsed.text!, accent: parsed.accent!, accent2: parsed.accent2 ?? parsed.accent!, headingFont: parsed.headingFont ?? "Arial", bodyFont: parsed.bodyFont ?? "Arial", logo: parsed.logo ?? null, ...(parsed.logoRatio ? { logoRatio: parsed.logoRatio } : {}) };
    }
  } catch { value = null; }
  cache = { raw, value };
  return value;
}

/** The brand theme from the imported template, if there is one (one per app). */
export const getBrandTheme = (): BrandTheme | null => read();

export function setBrandTheme(brand: BrandTheme | null): void {
  if (brand) persistentStorage.setItem(KEY, JSON.stringify(brand)); else persistentStorage.removeItem(KEY);
  cache = null;
  watchers.forEach((watcher) => watcher());
}

const watch = (watcher: () => void) => { watchers.add(watcher); return () => { watchers.delete(watcher); }; };
export const useBrandTheme = (): BrandTheme | null => useSyncExternalStore(watch, read, () => null);
