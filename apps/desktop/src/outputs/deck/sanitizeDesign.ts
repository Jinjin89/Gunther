/**
 * The HTML a designed slide carries comes from a model, so it is cleaned before it is drawn (docs/DESIGNED_SLIDES_PLAN.md §8):
 * no scripts or event handlers, no external addresses (a link or a `url()` may only point at `#something` in the slide),
 * no pictures, frames, style sheets or embedded documents. HTML and inline SVG stay, with their `style` attributes.
 * The one custom element is `<gunther-chart>`, where the app draws the slide's chart.
 */
import DOMPurify, { type DOMPurify as Purifier } from "dompurify";

let purifier: Purifier | null = null;

const FORBIDDEN_TAGS = [
  "script", "style", "link", "meta", "base", "img", "image", "picture", "source", "iframe", "frame", "frameset", "object", "embed", "applet",
  "foreignobject", "form", "input", "button", "textarea", "select", "option", "audio", "video", "track", "canvas", "dialog", "template", "slot",
  "animate", "set", "animatemotion", "animatetransform", "mpath", "use",
];
const FORBIDDEN_ATTRS = ["target", "srcset", "src", "action", "formaction", "ping", "download", "is", "slot", "contenteditable", "autofocus"];
/** A `url(...)` that is not a fragment of this slide. */
const EXTERNAL_URL = /url\(\s*(?!["']?\s*#)/i;
const UNSAFE_STYLE = /@import|expression\s*\(|javascript:|behaviou?r\s*:|-moz-binding/i;

/** The declarations of a style attribute without any that load something from outside. */
export function safeStyle(style: string): string {
  return style.split(";").filter((declaration) => declaration.trim() && !EXTERNAL_URL.test(declaration) && !UNSAFE_STYLE.test(declaration)).join(";");
}

function instance(): Purifier {
  if (purifier) return purifier;
  const clean = DOMPurify(window);
  clean.addHook("uponSanitizeAttribute", (_node, data) => {
    const name = data.attrName.toLowerCase();
    const value = data.attrValue;
    if (name === "href" || name === "xlink:href") { if (!/^\s*#/.test(value)) data.keepAttr = false; return; }
    if (name === "style") { data.attrValue = safeStyle(value); return; }
    if (EXTERNAL_URL.test(value) || UNSAFE_STYLE.test(value)) data.keepAttr = false;
  });
  purifier = clean;
  return clean;
}

/** The slide's HTML cleaned; "" when nothing is left of it. */
export function sanitizeDesign(html: string): string {
  const clean = instance().sanitize(html, {
    USE_PROFILES: { html: true, svg: true },
    FORBID_TAGS: FORBIDDEN_TAGS,
    FORBID_ATTR: FORBIDDEN_ATTRS,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: true,
    CUSTOM_ELEMENT_HANDLING: { tagNameCheck: /^gunther-chart$/, attributeNameCheck: () => false, allowCustomizedBuiltInElements: false },
    RETURN_DOM: false,
    RETURN_DOM_FRAGMENT: false,
  });
  return String(clean).trim();
}
