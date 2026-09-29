/**
 * Reading comfort: how big text is and which typeface the interface uses.
 * Every font size in the stylesheets is multiplied by ``--gx-fs``, so one
 * setting scales the whole app and layout follows the text.
 */
export type TextSize = "small" | "default" | "large" | "xlarge";
export type FontChoice = "system" | "serif" | "rounded";

const TEXT_SIZE_STORAGE_KEY = "gunther:text-size";
const FONT_STORAGE_KEY = "gunther:font";

export const TEXT_SIZES: Array<{ id: TextSize; label: string; scale: number }> = [
  { id: "small", label: "Small", scale: 1 },
  { id: "default", label: "Default", scale: 1.1 },
  { id: "large", label: "Large", scale: 1.22 },
  { id: "xlarge", label: "Extra large", scale: 1.36 },
];

export const FONTS: Array<{ id: FontChoice; label: string; detail: string }> = [
  { id: "system", label: "System", detail: "Your computer's own interface font" },
  { id: "serif", label: "Serif", detail: "A bookish face, easy on long reading" },
  { id: "rounded", label: "Rounded", detail: "Soft letter shapes, friendly at small sizes" },
];

const read = <T extends string>(key: string, allowed: readonly T[], fallback: T): T => {
  try {
    const stored = window.localStorage.getItem(key);
    return allowed.includes(stored as T) ? (stored as T) : fallback;
  } catch {
    return fallback;
  }
};

export const readTextSize = (): TextSize => read(TEXT_SIZE_STORAGE_KEY, TEXT_SIZES.map((size) => size.id), "default");
export const readFont = (): FontChoice => read(FONT_STORAGE_KEY, FONTS.map((font) => font.id), "system");

export function applyTypography(size: TextSize = readTextSize(), font: FontChoice = readFont()) {
  const root = document.documentElement;
  root.dataset.textSize = size;
  root.dataset.font = font;
  root.style.setProperty("--gx-fs", String(TEXT_SIZES.find((item) => item.id === size)?.scale ?? 1.1));
}

export function saveTypography(size: TextSize, font: FontChoice) {
  try {
    window.localStorage.setItem(TEXT_SIZE_STORAGE_KEY, size);
    window.localStorage.setItem(FONT_STORAGE_KEY, font);
  } catch {
    // The choice still applies until the window closes.
  }
  applyTypography(size, font);
}
