import DOMPurify, { type DOMPurify as Purifier } from "dompurify";
import { localSvgCss } from "./svgCss";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const URI_PRESENTATION_ATTRIBUTES = new Set([
  "clip-path", "color-profile", "cursor", "fill", "filter", "marker", "marker-end", "marker-mid", "marker-start", "mask", "stroke",
]);
let purifier: Purifier | undefined;

export function hasSingleSvgRoot(source: string): boolean {
  const parsed = new DOMParser().parseFromString(source, "image/svg+xml");
  const root = parsed.documentElement;
  return parsed.childNodes.length === 1 && root.tagName === "svg"
    && (root.namespaceURI === SVG_NAMESPACE || root.namespaceURI === null);
}

function svgPurifier(): Purifier {
  if (purifier) return purifier;
  const clean = DOMPurify(window);
  clean.addHook("uponSanitizeAttribute", (_node, data) => {
    const name = data.attrName.toLowerCase();
    const isLink = name === "href" || name === "xlink:href";
    if (name.startsWith("on") || (isLink && !data.attrValue.startsWith("#"))) {
      data.keepAttr = false;
    } else if (name === "style") {
      data.attrValue = localSvgCss(data.attrValue);
    } else if (URI_PRESENTATION_ATTRIBUTES.has(name)) {
      data.keepAttr = localSvgCss(data.attrValue) === data.attrValue;
    }
  });
  purifier = clean;
  return clean;
}

export function sanitizeSvg(source: string): string {
  const input = source.trim();
  if (!hasSingleSvgRoot(input)) throw new Error("Expected a single SVG element.");
  const clean = svgPurifier().sanitize(input, {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: ["foreignObject", "script"],
    FORBID_ATTR: ["xml:base"],
    RETURN_DOM_FRAGMENT: true,
  });
  const root = clean.firstElementChild;
  if (clean.childElementCount !== 1 || root?.namespaceURI !== SVG_NAMESPACE || root.tagName !== "svg") {
    throw new Error("Expected a single SVG element.");
  }
  for (const sheet of root.querySelectorAll("style")) sheet.textContent = localSvgCss(sheet.textContent ?? "");
  // The standalone image also needs a namespace when the authored inline SVG omitted it.
  return new XMLSerializer().serializeToString(root);
}
