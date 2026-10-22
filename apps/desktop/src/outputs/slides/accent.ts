import { ACCENTS } from "../sections";

/** One of the names a deck's title slide can carry in its marker (`accent: blue`). */
export type Accent = (typeof ACCENTS)[number];

/**
 * The deck's one colour. A slide is white paper and black ink; colour comes only from here, and
 * `ink` (the default) is no colour at all, so an undecorated deck stays black and white.
 *
 * Each hex is at least 4.5:1 against white (WCAG AA for text), because the accent also colours
 * big numerals and the figures in white-on-colour circles. They were checked as a set with the
 * dataviz palette validator (light, white surface), and accent.test.ts computes the contrast.
 */
export const ACCENT_COLOURS: Record<Accent, string> = {
  ink: "#1a1a18",
  blue: "#2668b6",
  teal: "#028066",
  red: "#ba3a34",
  amber: "#925c00",
  violet: "#7455b8",
};

/** What the Accent menu calls each one. */
export const ACCENT_LABELS: Record<Accent, string> = {
  ink: "Ink (no colour)",
  blue: "Blue",
  teal: "Teal",
  red: "Red",
  amber: "Amber",
  violet: "Violet",
};

export const isAccent = (name: string | null | undefined): name is Accent => ACCENTS.some((accent) => accent === name);

/** The colour for an accent name; anything else (none, a name from a newer version) is ink. */
export const accentColour = (name: string | null | undefined): string => (isAccent(name) ? ACCENT_COLOURS[name] : ACCENT_COLOURS.ink);

/** Accent tones that remain visible on the dark Keynote surface. */
export const themeAccentColour = (name: string | null | undefined, theme: string): string => {
  if (theme !== "keynote") return accentColour(name);
  const tones: Record<Accent, string> = {
    ink: "#f4f1e9", blue: "#8fc4ff", teal: "#76ddc2", red: "#ff9e96", amber: "#ffd27a", violet: "#c4a8ff",
  };
  return tones[isAccent(name) ? name : "ink"];
};

/** WCAG 2.x relative luminance of an `#rrggbb` colour. */
function luminance(hex: string): number {
  const [r = 0, g = 0, b = 0] = [1, 3, 5].map((at) => {
    const channel = parseInt(hex.slice(at, at + 2), 16) / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two `#rrggbb` colours, 1 to 21. */
export function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (light + 0.05) / (dark + 0.05);
}
