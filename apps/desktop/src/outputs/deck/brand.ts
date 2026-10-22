/**
 * A person's own look, read from a PowerPoint template (.pptx or .potx): the theme's colours and fonts and
 * the logo on the slide master. One brand theme is kept per app (brandStore.ts).
 */
import JSZip from "jszip";
import { onColour, readable } from "./colour";

export interface BrandTheme {
  name: string;
  background: string;
  text: string;
  accent: string;
  accent2: string;
  headingFont: string;
  bodyFont: string;
  /** A small picture from the slide master as a data URL, or null. */
  logo: string | null;
  /** The logo's shape, width over height. */
  logoRatio?: number;
}

export interface BrandImport { brand: BrandTheme; notes: string[] }

const EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "svg"]);
/** The picture type from the file's own first bytes (a file's extension can be wrong). */
const sniff = (base64: string): string | null =>
  base64.startsWith("iVBOR") ? "image/png" : base64.startsWith("/9j/") ? "image/jpeg" : base64.startsWith("R0lG") ? "image/gif" : base64.startsWith("PHN2Zy") || base64.startsWith("PD94bW") ? "image/svg+xml" : null;
const MAX_LOGO_BYTES = 1_500_000;

const colourOf = (xml: string, tag: string): string | null => {
  const block = new RegExp(`<a:${tag}>([\\s\\S]*?)</a:${tag}>`).exec(xml)?.[1];
  const value = block ? /(?:srgbClr val|lastClr)="([0-9A-Fa-f]{6})"/.exec(block)?.[1] : undefined;
  return value ? `#${value.toLowerCase()}` : null;
};
const fontOf = (xml: string, tag: "majorFont" | "minorFont", kind: "latin" | "ea"): string => {
  const block = new RegExp(`<a:${tag}>([\\s\\S]*?)</a:${tag}>`).exec(xml)?.[1] ?? "";
  return new RegExp(`<a:${kind} typeface="([^"]*)"`).exec(block)?.[1]?.trim() ?? "";
};

const attrs = (tag: string) => Object.fromEntries([...tag.matchAll(/([\w:]+)="([^"]*)"/g)].map((m) => [m[1]!, m[2]!]));

/** The pictures placed on a master or layout: where their file is, and how big they are in slide units. */
async function picturesOf(zip: JSZip, part: string): Promise<Array<{ path: string; area: number; ratio: number }>> {
  const xml = await zip.file(part)?.async("string");
  const relFile = part.replace(/([^/]+)$/, "_rels/$1.rels");
  const rels = await zip.file(relFile)?.async("string");
  if (!xml || !rels) return [];
  const targets = new Map<string, string>();
  for (const tag of rels.match(/<Relationship\b[^>]*>/g) ?? []) {
    const a = attrs(tag);
    if (a.Id && a.Target) targets.set(a.Id, a.Target);
  }
  const out: Array<{ path: string; area: number; ratio: number }> = [];
  for (const pic of xml.match(/<p:pic>[\s\S]*?<\/p:pic>/g) ?? []) {
    const id = /r:embed="([^"]+)"/.exec(pic)?.[1];
    const ext = /<a:ext cx="(\d+)" cy="(\d+)"/.exec(pic);
    const target = id ? targets.get(id) : undefined;
    if (!target || !ext) continue;
    const path = target.startsWith("/") ? target.slice(1) : `ppt/${target.replace(/^(\.\.\/)+/, "")}`;
    const [w, h] = [Number(ext[1]), Number(ext[2])];
    if (w > 0 && h > 0) out.push({ path, area: w * h, ratio: w / h });
  }
  return out;
}

/** Read a .pptx/.potx template's look. Throws if it is not a PowerPoint file. */
export async function readTemplate(bytes: ArrayBuffer | Uint8Array, filename = "Template"): Promise<BrandImport> {
  const zip = await JSZip.loadAsync(bytes).catch(() => { throw new Error("That file is not a PowerPoint template (.pptx or .potx)."); });
  const themeFile = Object.keys(zip.files).find((file) => /^ppt\/theme\/theme1\.xml$/.test(file));
  if (!themeFile) throw new Error("That file is not a PowerPoint template: it has no theme.");
  const theme = await zip.file(themeFile)!.async("string");
  const notes: string[] = [];

  const master = (await zip.file("ppt/slideMasters/slideMaster1.xml")?.async("string")) ?? "";
  const map = attrs(/<p:clrMap\b[^>]*>/.exec(master)?.[0] ?? "");
  const scheme = (key: string, fallback: string) => colourOf(theme, map[key] ?? fallback) ?? colourOf(theme, fallback) ?? (fallback === "lt1" ? "#ffffff" : "#111111");
  const background = scheme("bg1", "lt1");
  let text = scheme("tx1", "dk1");
  const accent1 = colourOf(theme, "accent1") ?? "#2668b6";
  const accent2 = colourOf(theme, "accent2") ?? accent1;

  const fixText = readable(text, background, 7);
  if (fixText.changed) { text = fixText.colour; notes.push("The template's text colour was adjusted to read clearly on its background."); }
  const a1 = readable(accent1, background);
  const a2 = readable(accent2, background);
  if (a1.changed || a2.changed) notes.push("The template's accent colour was adjusted to keep it readable on its background.");

  const presentation = (await zip.file("ppt/presentation.xml")?.async("string")) ?? "";
  const size = /<p:sldSz cx="(\d+)" cy="(\d+)"/.exec(presentation);
  const [sw, sh] = size ? [Number(size[1]), Number(size[2])] : [12192000, 6858000];
  if (Math.abs(sw / sh - 16 / 9) > 0.02) notes.push("This template is not 16:9. Decks are always drawn 16:9, so its logo and layout proportions may look different.");

  // The logo: the largest picture on the master that covers under a quarter of the slide; else on a layout.
  const layouts = Object.keys(zip.files).filter((f) => /^ppt\/slideLayouts\/slideLayout\d+\.xml$/.test(f)).sort();
  let logo: string | null = null;
  let logoRatio: number | undefined;
  for (const part of ["ppt/slideMasters/slideMaster1.xml", ...layouts]) {
    const found = (await picturesOf(zip, part)).filter((p) => p.area < 0.25 * sw * sh && p.area > 0.0004 * sw * sh && EXTENSIONS.has(p.path.split(".").pop()!.toLowerCase())).sort((x, y) => y.area - x.area)[0];
    if (!found) continue;
    const file = zip.file(found.path);
    const data = file ? await file.async("base64") : null;
    if (!data) continue;
    if (data.length * 0.75 > MAX_LOGO_BYTES) { notes.push("The template's logo is large, so it was left out."); break; }
    const type = sniff(data);
    if (!type) continue;
    logo = `data:${type};base64,${data}`;
    logoRatio = found.ratio;
    break;
  }
  if (!logo) notes.push("No logo was found on the template's master.");

  const themeName = /<a:theme\b[^>]*\bname="([^"]*)"/.exec(theme)?.[1]?.trim();
  const fileName = filename.replace(/\.(pptx|potx)$/i, "");
  const name = themeName && !/^office( theme)?$/i.test(themeName) ? themeName : fileName || "Template";
  const heading = fontOf(theme, "majorFont", "latin") || fontOf(theme, "minorFont", "latin");
  const body = fontOf(theme, "minorFont", "latin") || heading;
  void onColour;
  return {
    notes,
    brand: { name, background, text, accent: a1.colour, accent2: a2.colour, headingFont: heading || "Arial", bodyFont: body || "Arial", logo, ...(logoRatio ? { logoRatio } : {}) },
  };
}
