import type { DeckTheme } from "../sections";

/** The deck with its title slide's theme (and optionally accent) changed; everything else is kept. */
export function withDeckMarker(content: string, change: { theme?: DeckTheme; accent?: string }): string {
  return content.replace(/<!--\s*layout:\s*title\b[^>]*-->/, (marker) => {
    const set = (text: string, key: string, value: string | undefined) => {
      if (value === undefined) return text;
      return new RegExp(`;\\s*${key}:[^;>]*`).test(text) ? text.replace(new RegExp(`;\\s*${key}:[^;>]*?(\\s*(?=;|-->))`), `; ${key}: ${value}$1`) : text.replace(/\s*-->$/, `; ${key}: ${value} -->`);
    };
    return set(set(marker, "theme", change.theme), "accent", change.accent);
  });
}
