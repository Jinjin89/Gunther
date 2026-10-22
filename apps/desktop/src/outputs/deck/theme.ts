import type { CSSProperties } from "react";
import type { DeckTheme } from "../sections";
import { ACCENT_COLOURS, isAccent, themeAccentColour, type Accent } from "../slides/accent";

/** The three studio themes. Black and white plus one accent; colour only carries identity. */
export const DECK_THEME_TOKENS: Record<DeckTheme, {
  label: string; bg: string; ink: string; muted: string; rule: string; soft: string; bar: string; onAccent: string;
  titleFont: "sans" | "serif"; dark: boolean;
}> = {
  paper: { label: "Paper", bg: "#ffffff", ink: "#111317", muted: "#545a63", rule: "#dcdfe3", soft: "#f3f4f6", bar: "#c5c9cf", onAccent: "#ffffff", titleFont: "sans", dark: false },
  ink: { label: "Ink", bg: "#0e1013", ink: "#f6f7f8", muted: "#aeb4bd", rule: "#2c3037", soft: "#1a1d22", bar: "#4b515b", onAccent: "#0e1013", titleFont: "sans", dark: true },
  editorial: { label: "Editorial", bg: "#faf6ee", ink: "#1d1a15", muted: "#5b5446", rule: "#ddd4c2", soft: "#f0e9db", bar: "#c9bfa9", onAccent: "#ffffff", titleFont: "serif", dark: false },
};

export const DEFAULT_ACCENT: Accent = "blue";

/** The accent's colour on this theme: the dark theme takes the lighter tone. */
export function deckAccent(accent: string | null | undefined, theme: DeckTheme): string {
  const name: Accent = isAccent(accent) ? accent : DEFAULT_ACCENT;
  return DECK_THEME_TOKENS[theme].dark ? themeAccentColour(name, "keynote") : ACCENT_COLOURS[name];
}

export const SANS = `Inter, -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Helvetica, Arial, "Liberation Sans", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC", sans-serif`;
export const SERIF = `"Source Serif 4", "Source Serif Pro", Georgia, "Iowan Old Style", "Songti SC", "Noto Serif CJK SC", "Noto Serif SC", serif`;

export function deckStyle(theme: DeckTheme, accent: string | null | undefined): CSSProperties {
  const t = DECK_THEME_TOKENS[theme];
  return {
    "--dk-bg": t.bg, "--dk-ink": t.ink, "--dk-muted": t.muted, "--dk-rule": t.rule, "--dk-soft": t.soft, "--dk-bar": t.bar,
    "--dk-accent": deckAccent(accent, theme), "--dk-on-accent": t.onAccent,
    "--dk-sans": SANS, "--dk-title-font": t.titleFont === "serif" ? SERIF : SANS,
  } as CSSProperties;
}

/** The app's stage around a studio slide (Reveal's arrows and progress bar) in the deck's colours. */
export function deckStageStyle(theme: DeckTheme, accent: string | null | undefined): CSSProperties {
  const t = DECK_THEME_TOKENS[theme];
  return {
    "--outputs-slide-paper": t.bg, "--outputs-slide-ink": t.ink, "--outputs-slide-muted": t.muted,
    "--outputs-slide-rule": t.rule, "--outputs-slide-grey": t.bar, "--outputs-accent": deckAccent(accent, theme),
  } as CSSProperties;
}
