import { Check, Copy, ExternalLink, ImageIcon } from "lucide-react";
import { createElement, memo, useMemo, useState, type ReactNode } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

/**
 * Markdown rendered with react-markdown + remark-gfm (CommonMark and GitHub
 * Flavored Markdown via micromark). Output is React elements only: raw HTML
 * shows as the literal text the author wrote, links open only for http(s),
 * mailto and in-document anchors, and remote images become links so a note
 * can never load a tracking pixel.
 */
export interface MarkdownViewProps {
  source: string;
  className?: string;
  /** Semantic level for a Markdown `#` heading. The visual size follows the Markdown depth. */
  headingLevel?: 1 | 2 | 3;
  /**
   * When set, task-list checkboxes become toggles. `offset` is where the list
   * item starts in `source`; pass it to `setTaskCheckedAt`.
   */
  onToggleTask?: ((offset: number, checked: boolean) => void) | undefined;
  /** Rendered instead of nothing when the source is blank. */
  empty?: ReactNode;
}

// Single newlines stay line breaks, as in most note apps, so pasted text keeps its shape.
const REMARK_PLUGINS = [remarkGfm, remarkBreaks];
const SAFE_LINK = /^(https?:|mailto:)/i;
const SAFE_IMAGE = /^(data:image\/(png|jpe?g|gif|webp);|blob:)/i;

export function safeHref(href: string | null | undefined): string | null {
  const value = (href ?? "").trim();
  return SAFE_LINK.test(value) ? value : null;
}

const urlTransform = (url: string, key: string) => {
  if (key === "src") return SAFE_IMAGE.test(url) || SAFE_LINK.test(url) ? url : "";
  if (url.startsWith("#")) return url;
  return safeHref(url) ?? "";
};

type HastNode = { type: string; value?: string; tagName?: string; properties?: Record<string, unknown>; children?: HastNode[] };

const textOf = (node: HastNode | undefined): string => {
  if (!node) return "";
  if (node.type === "text") return node.value ?? "";
  return (node.children ?? []).map(textOf).join("");
};

function CodeBlock({ code, language }: { code: string; language: string | null }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_400);
    } catch {
      // Clipboard access can be denied; the code stays selectable.
    }
  };
  return (
    <figure className="gx-md-code">
      <figcaption>
        <span>{language || "Code"}</span>
        <button type="button" onClick={() => void copy()} aria-label={copied ? "Copied" : "Copy code"} title={copied ? "Copied" : "Copy code"}>
          {copied ? <Check size={12} /> : <Copy size={12} />}
        </button>
      </figcaption>
      <pre><code>{code.replace(/\n$/, "")}</code></pre>
    </figure>
  );
}

function TaskBox({ checked, onToggle }: { checked: boolean; onToggle: (() => void) | null }) {
  if (!onToggle) {
    return <span className={`gx-md-task ${checked ? "is-checked" : ""}`} role="img" aria-label={checked ? "Done" : "Not done"}>{checked && <Check size={10} strokeWidth={3} />}</span>;
  }
  return (
    <button
      type="button"
      className={`gx-md-task ${checked ? "is-checked" : ""}`}
      role="checkbox"
      aria-checked={checked}
      aria-label={checked ? "Mark as not done" : "Mark as done"}
      onClick={(event) => {
        event.stopPropagation();
        onToggle();
      }}
    >
      {checked && <Check size={10} strokeWidth={3} />}
    </button>
  );
}

function buildComponents(headingLevel: number, onToggleTask: MarkdownViewProps["onToggleTask"]): Components {
  const heading = (depth: number) => ({ children }: { children?: ReactNode }) =>
    createElement(`h${Math.min(6, headingLevel + depth - 1)}`, { className: `gx-md-h${depth}` }, children);
  return {
    h1: heading(1),
    h2: heading(2),
    h3: heading(3),
    h4: heading(4),
    h5: heading(5),
    h6: heading(6),
    a: ({ href, children, title }) => {
      if (!href) return <span className="gx-md-unsafe-link">{children}</span>;
      if (href.startsWith("#")) return <a href={href}>{children}</a>;
      return <a href={href} title={title ?? href} target="_blank" rel="noreferrer noopener">{children}</a>;
    },
    img: ({ src, alt }) => {
      const url = typeof src === "string" ? src : "";
      const label = alt || url.split("/").pop() || "Image";
      if (SAFE_IMAGE.test(url)) return <img className="gx-md-image" src={url} alt={alt ?? ""} />;
      return SAFE_LINK.test(url)
        ? <a className="gx-md-image-link" href={url} target="_blank" rel="noreferrer noopener" title="Remote images are not loaded. Open the image in your browser."><ImageIcon size={13} />{label}<ExternalLink size={11} /></a>
        : <span className="gx-md-image-link"><ImageIcon size={13} />{label}</span>;
    },
    pre: ({ node }) => {
      const code = (node as HastNode | undefined)?.children?.find((child) => child.tagName === "code");
      const className = code?.properties?.className;
      const language = Array.isArray(className)
        ? String(className.find((name) => String(name).startsWith("language-")) ?? "").replace("language-", "") || null
        : null;
      return <CodeBlock code={textOf(code)} language={language} />;
    },
    code: ({ children }) => <code className="gx-md-inline-code">{children}</code>,
    table: ({ children }) => <div className="gx-md-table" role="region" aria-label="Table" tabIndex={0}><table>{children}</table></div>,
    ul: ({ children, className }) => <ul className={className?.includes("contains-task-list") ? "gx-md-tasks" : undefined}>{children}</ul>,
    ol: ({ children, className, start }) => <ol className={className?.includes("contains-task-list") ? "gx-md-tasks" : undefined} start={start}>{children}</ol>,
    // The task box is drawn by `li`, which knows the item's source position.
    input: () => null,
    li: ({ node, children, className, id }) => {
      if (!className?.includes("task-list-item")) return <li id={id}>{children}</li>;
      const box = (node as HastNode | undefined)?.children?.find((child) => child.tagName === "input");
      const checked = Boolean(box?.properties?.checked);
      const offset = (node as { position?: { start: { offset?: number } } } | undefined)?.position?.start.offset;
      const toggle = onToggleTask && typeof offset === "number" ? () => onToggleTask(offset, !checked) : null;
      return (
        <li className={`gx-md-task-item ${checked ? "is-done" : ""}`}>
          <TaskBox checked={checked} onToggle={toggle} />
          <div>{children}</div>
        </li>
      );
    },
  };
}

export const MarkdownView = memo(function MarkdownView({ source, className = "", headingLevel = 2, onToggleTask, empty = null }: MarkdownViewProps) {
  // Processing markers such as <!-- gunther:page=3 --> are never content. Keep
  // the removed ranges so task offsets still point into the original source.
  const { visible, removed } = useMemo(() => {
    const ranges: Array<[number, number]> = [];
    const text = source.replace(/<!--[\s\S]*?-->/g, (match: string, at: number) => {
      ranges.push([at, match.length]);
      return "";
    });
    return { visible: text, removed: ranges };
  }, [source]);
  const toggle = useMemo(() => onToggleTask && ((offset: number, checked: boolean) => {
    let at = offset;
    for (const [start, length] of removed) {
      if (start <= at) at += length;
      else break;
    }
    onToggleTask(at, checked);
  }), [onToggleTask, removed]);
  const components = useMemo(() => buildComponents(headingLevel, toggle), [headingLevel, toggle]);
  if (!visible.trim()) return <>{empty}</>;
  return (
    <div className={`gx-prose ${className}`.trim()}>
      <Markdown remarkPlugins={REMARK_PLUGINS} components={components} urlTransform={urlTransform}>{visible}</Markdown>
    </div>
  );
});
