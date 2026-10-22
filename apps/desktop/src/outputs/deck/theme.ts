import type { CSSProperties } from "react";
import type { DeckTheme } from "../sections";
import { ACCENT_COLOURS, isAccent, themeAccentColour, type Accent } from "../slides/accent";
import type { BrandTheme } from "./brand";
import { mix, onColour, readable } from "./colour";

export interface ThemeTokens {
  label: string; bg: string; ink: string; muted: string; rule: string; soft: string; bar: string; onAccent: string;
  titleFont: "sans" | "serif"; dark: boolean;
}

/** The three built-in studio themes. Black and white plus one accent; colour only carries identity. */
export const DECK_THEME_TOKENS: Record<Exclude<DeckTheme, "brand">, ThemeTokens> = {
  paper: { label: "Paper", bg: "#ffffff", ink: "#111317", muted: "#545a63", rule: "#dcdfe3", soft: "#f3f4f6", bar: "#c5c9cf", onAccent: "#ffffff", titleFont: "sans", dark: false },
  ink: { label: "Ink", bg: "#0e1013", ink: "#f6f7f8", muted: "#aeb4bd", rule: "#2c3037", soft: "#1a1d22", bar: "#4b515b", onAccent: "#0e1013", titleFont: "sans", dark: true },
  editorial: { label: "Editorial", bg: "#faf6ee", ink: "#1d1a15", muted: "#5b5446", rule: "#ddd4c2", soft: "#f0e9db", bar: "#c9bfa9", onAccent: "#ffffff", titleFont: "serif", dark: false },
};

export const DEFAULT_ACCENT: Accent = "blue";

export const SANS = `Inter, -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Helvetica, Arial, "Liberation Sans", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC", sans-serif`;
export const SERIF = `"Source Serif 4", "Source Serif Pro", Georgia, "Iowan Old Style", "Songti SC", "Noto Serif CJK SC", "Noto Serif SC", serif`;
const CJK_FALLBACK = `"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC", sans-serif`;

/** What a deck's theme resolves to: a brand theme that is not there (or an unknown name) is paper. */
export interface ResolvedTheme {
  /** The theme actually drawn. */
  theme: Exclude<DeckTheme, "brand">;
  brand: BrandTheme | null;
  tokens: ThemeTokens;
  /** Font stacks for CSS. */
  bodyStack: string;
  titleStack: string;
  /** The brand's own font names, for the PowerPoint export (null for built-in themes). */
  bodyName: string | null;
  titleName: string | null;
  /** The accent colour of the brand (used when the deck's marker names no accent). */
  brandAccent: string | null;
}

const quote = (font: string) => `"${font.replace(/"/g, "")}"`;

export function resolveTheme(theme: DeckTheme, brand: BrandTheme | null | undefined): ResolvedTheme {
  if (theme === "brand" && brand) {
    const bg = brand.background;
    const ink = brand.text;
    const dark = onColour(bg) === "#ffffff";
    const muted = readable(mix(ink, bg, 0.32), bg, 4.5).colour;
    const accent = readable(brand.accent, bg).colour;
    const tokens: ThemeTokens = {
      label: brand.name, bg, ink, muted, rule: mix(ink, bg, 0.86), soft: mix(bg, ink, 0.05), bar: mix(ink, bg, 0.7),
      onAccent: onColour(accent), titleFont: "sans", dark,
    };
    return {
      theme: "paper", brand, tokens,
      bodyStack: `${quote(brand.bodyFont)}, ${SANS}`, titleStack: `${quote(brand.headingFont)}, ${SANS}`,
      bodyName: brand.bodyFont, titleName: brand.headingFont, brandAccent: accent,
    };
  }
  const name = theme === "brand" ? "paper" : theme;
  const tokens = DECK_THEME_TOKENS[name];
  return { theme: name, brand: null, tokens, bodyStack: SANS, titleStack: tokens.titleFont === "serif" ? SERIF : SANS, bodyName: null, titleName: null, brandAccent: null };
}

/** The accent's colour on this theme: the dark theme takes the lighter tone, a brand theme always its own accent. */
export function deckAccent(accent: string | null | undefined, theme: DeckTheme, brand?: BrandTheme | null): string {
  const resolved = resolveTheme(theme, brand);
  if (resolved.brandAccent) return resolved.brandAccent;
  const name: Accent = isAccent(accent) ? accent : DEFAULT_ACCENT;
  return resolved.tokens.dark ? themeAccentColour(name, "keynote") : ACCENT_COLOURS[name];
}

export function deckStyle(theme: DeckTheme, accent: string | null | undefined, brand?: BrandTheme | null): CSSProperties {
  const r = resolveTheme(theme, brand);
  const t = r.tokens;
  return {
    "--dk-bg": t.bg, "--dk-ink": t.ink, "--dk-muted": t.muted, "--dk-rule": t.rule, "--dk-soft": t.soft, "--dk-bar": t.bar,
    "--dk-accent": deckAccent(accent, theme, brand), "--dk-on-accent": t.onAccent,
    "--dk-sans": r.bodyStack, "--dk-title-font": r.titleStack,
  } as CSSProperties;
}

/** The app's stage around a studio slide (Reveal's arrows and progress bar) in the deck's colours. */
export function deckStageStyle(theme: DeckTheme, accent: string | null | undefined, brand?: BrandTheme | null): CSSProperties {
  const t = resolveTheme(theme, brand).tokens;
  return {
    "--outputs-slide-paper": t.bg, "--outputs-slide-ink": t.ink, "--outputs-slide-muted": t.muted,
    "--outputs-slide-rule": t.rule, "--outputs-slide-grey": t.bar, "--outputs-accent": deckAccent(accent, theme, brand),
  } as CSSProperties;
}
export { CJK_FALLBACK };
