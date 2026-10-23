/** The CSS a designed slide is drawn with: the shared base, the deck's style, and (for an imported template) its colours and fonts. */
import baseCss from "./styles/base.css?raw";
import lightCss from "./styles/light.css?raw";
import darkCss from "./styles/dark.css?raw";
import boldCss from "./styles/bold.css?raw";
import aroundCss from "./designed.css?raw";
import type { DeckStyle } from "../sections";
import type { BrandTheme } from "./brand";
import { contrast, mix, onColour, readable } from "./colour";

export const STYLE_LABEL: Record<DeckStyle, string> = { light: "Light", dark: "Dark", bold: "Bold" };
const STYLE_CSS: Record<DeckStyle, string> = { light: lightCss, dark: darkCss, bold: boldCss };

const quote = (font: string) => `"${font.replace(/["\;{}<>]/g, "")}"`;
const luminous = (colour: string) => onColour(colour) === "#111317"; // a light colour needs dark text on it

/**
 * An imported template's colours and fonts mapped onto the style variables (the slides' HTML uses only variables, so this
 * recolours every designed slide). The template's text colour is the main (dominant) colour, its accent the sharp one; the
 * muted and accent colours are moved until they read on the template's background, and dark slides use its darkest colour.
 */
export function brandVariables(brand: BrandTheme): string {
  const bg = brand.background;
  const text = readable(brand.text, bg, 7).colour;
  const dominant = text;
  const accent = readable(brand.accent, bg, 3).colour;
  const darkBg = luminous(bg) ? (contrast(text, "#ffffff") >= 7 ? text : mix(text, "#000000", 0.6)) : bg;
  const head = `${quote(brand.headingFont)}, `;
  const body = `${quote(brand.bodyFont)}, `;
  return `.slide { --bg:${bg}; --text:${text}; --muted:${readable(mix(text, bg, 0.38), bg, 4.5).colour}; --panel:${mix(bg, text, 0.06)}; --line:${mix(bg, text, 0.16)};
  --dominant:${dominant}; --accent:${accent}; --accent-soft:${mix(accent, bg, 0.86)};
  --dark-bg:${darkBg}; --on-dark:${onColour(darkBg)}; --on-dark-muted:${mix(onColour(darkBg), darkBg, 0.35)};
  --font-head:${head}var(--font-head-style); --font-body:${body}var(--font-body-style); }`;
}

/** Everything inside a designed slide's shadow root. The template's variables follow the style's, so its fonts keep the style's stack as a fallback. */
export function designCss(style: DeckStyle, brand: BrandTheme | null): string {
  const styleCss = STYLE_CSS[style];
  if (!brand) return `${baseCss}\n${styleCss}\n${aroundCss}`;
  // Keep the style's own font stacks as the fallback of the template's fonts.
  const fonts = (name: "head" | "body") => new RegExp(`--font-${name}:([^;]+);`).exec(styleCss)?.[1]?.trim() ?? "sans-serif";
  const mapped = brandVariables(brand).replace("var(--font-head-style)", fonts("head")).replace("var(--font-body-style)", fonts("body"));
  return `${baseCss}\n${styleCss}\n${mapped}\n${aroundCss}`;
}
