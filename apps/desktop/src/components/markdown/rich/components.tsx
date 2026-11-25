import { ExternalLink, ImageIcon } from "lucide-react";
import { createContext, useContext, useEffect, useState, type ComponentProps, type ComponentType } from "react";
import type { Components, UrlTransform } from "react-markdown";
import { sourceAssetUrl } from "../../../api";
import { CodeBlock, type CodeBlockProps } from "./CodeBlock";
import { PictureCard } from "./PictureCard";

const SAFE_LINK = /^(https?:|mailto:)/i;
const EMBEDDED_IMAGE = /^data:(image\/(?:png|jpe?g|gif|webp))(?=[;,])/i;
const BLOB_IMAGE = /^blob:/i;
const RETAINED_IMAGE = /^gunther-asset:([A-Za-z0-9_-]{1,160})$/;
const InsideLink = createContext(false);

export function safeHref(href: string | null | undefined): string | null {
  const value = (href ?? "").trim();
  return SAFE_LINK.test(value) ? value : null;
}

export const markdownUrlTransform: UrlTransform = (url, key) => {
  if (key === "src") return RETAINED_IMAGE.test(url) || EMBEDDED_IMAGE.test(url) || BLOB_IMAGE.test(url) || SAFE_LINK.test(url) ? url : "";
  if (url.startsWith("#")) return url;
  return safeHref(url) ?? "";
};

function embeddedImageBlob(url: string): Blob | undefined {
  const type = EMBEDDED_IMAGE.exec(url)?.[1];
  const comma = url.indexOf(",");
  if (!type || comma < 0) return;
  const data = url.slice(comma + 1).split("#", 1)[0]!;
  const bytes = /;base64$/i.test(url.slice(0, comma))
    ? [Uint8Array.from(atob(decodeURIComponent(data)), (character) => character.charCodeAt(0))]
    : data.split(/%([\da-f]{2})/gi).map((part, index) => index % 2 ? Uint8Array.of(Number.parseInt(part, 16)) : part);
  return new Blob(bytes, { type });
}

export function MarkdownLink({ href, children, title }: ComponentProps<"a">) {
  if (!href) return <span className="gx-md-unsafe-link">{children}</span>;
  const label = <InsideLink.Provider value={true}>{children}</InsideLink.Provider>;
  if (href.startsWith("#")) return <a href={href}>{label}</a>;
  return <a href={href} title={title ?? href} target="_blank" rel="noreferrer noopener">{label}</a>;
}

const blockRenderers = new Map<string, ComponentType<CodeBlockProps>>([
  ["default", CodeBlock],
  ["svg", PictureCard],
  ["mermaid", PictureCard],
]);

export const richComponents = {
  a: MarkdownLink,
  img: function MarkdownImage({ src, alt }) {
    const insideLink = useContext(InsideLink);
    const url = typeof src === "string" ? src : "";
    const [embedded, setEmbedded] = useState<{ source: string; url: string }>();
    useEffect(() => {
      try {
        // Decode locally so the desktop CSP only sees the resulting blob URL.
        const blob = embeddedImageBlob(url);
        if (blob) {
          const objectUrl = URL.createObjectURL(blob);
          setEmbedded({ source: url, url: objectUrl });
          return () => URL.revokeObjectURL(objectUrl);
        }
      } catch {
        // A malformed embedded image keeps its text label.
      }
      setEmbedded(undefined);
    }, [url]);
    const label = alt || url.split("/").pop() || "Image";
    const retained = RETAINED_IMAGE.exec(url);
    if (retained) return <img className="gx-md-image" src={sourceAssetUrl(retained[1]!)} alt={alt ?? ""} loading="lazy" />;
    const imageUrl = embedded?.source === url ? embedded.url : BLOB_IMAGE.test(url) ? url : undefined;
    if (imageUrl) return <img className="gx-md-image" src={imageUrl} alt={alt ?? ""} />;
    return !insideLink && SAFE_LINK.test(url)
      ? <a className="gx-md-image-link" href={url} target="_blank" rel="noreferrer noopener" title="Remote images are not loaded. Open the image in your browser."><ImageIcon size={13} />{label}<ExternalLink size={11} /></a>
      : <span className="gx-md-image-link"><ImageIcon size={13} />{label}</span>;
  },
  pre: ({ node, children }) => {
    const code = node?.children.find((child) => child.type === "element" && child.tagName === "code");
    if (code?.type !== "element") return <pre>{children}</pre>;
    const classes = code.properties.className;
    const language = Array.isArray(classes)
      ? classes.find((name) => typeof name === "string" && name.startsWith("language-"))
      : undefined;
    const label = typeof language === "string" ? language.slice("language-".length) : null;
    const Block = blockRenderers.get(label?.toLowerCase() ?? "default") ?? blockRenderers.get("default")!;
    const text = code.children.map((child) => child.type === "text" ? child.value : "").join("");
    // The code handler appends a newline. remark-math passes display math through as written.
    const body = Array.isArray(classes) && classes.includes("math-display") ? text : text.replace(/\n$/, "");
    return <Block code={body} language={label} closed={code.properties.dataFenceClosed !== false} />;
  },
  code: ({ children }) => <code className="gx-md-inline-code">{children}</code>,
} satisfies Components;
