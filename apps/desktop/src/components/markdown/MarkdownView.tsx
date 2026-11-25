import { Check } from "lucide-react";
import { createElement, memo, useMemo, type ReactNode } from "react";
import Markdown, { type Components, type Options } from "react-markdown";
import remarkBreaks from "remark-breaks";
import { markdownUrlTransform, richComponents } from "./rich/components";
import { noteSource } from "./rich/noteSource";
import { richRemarkPlugins } from "./rich/plugins";

export { safeHref } from "./rich/components";

/**
 * Notes and reader Markdown with task controls and source line breaks.
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
const REMARK_PLUGINS: NonNullable<Options["remarkPlugins"]> = [...richRemarkPlugins, remarkBreaks];

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
    ...richComponents,
    h1: heading(1),
    h2: heading(2),
    h3: heading(3),
    h4: heading(4),
    h5: heading(5),
    h6: heading(6),
    table: ({ children }) => <div className="gx-md-table" role="region" aria-label="Table" tabIndex={0}><table>{children}</table></div>,
    ul: ({ children, className }) => <ul className={className?.includes("contains-task-list") ? "gx-md-tasks" : undefined}>{children}</ul>,
    ol: ({ children, className, start }) => <ol className={className?.includes("contains-task-list") ? "gx-md-tasks" : undefined} start={start}>{children}</ol>,
    // The task box is drawn by `li`, which knows the item's source position.
    input: () => null,
    li: ({ node, children, className, id }) => {
      if (!className?.includes("task-list-item")) return <li id={id}>{children}</li>;
      const box = node?.children.find((child) => child.type === "element" && child.tagName === "input");
      const checked = box?.type === "element" && Boolean(box.properties.checked);
      const offset = node?.position?.start.offset;
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
  const note = useMemo(() => noteSource(source), [source]);
  const components = useMemo(() => buildComponents(headingLevel, onToggleTask && ((offset, checked) => {
    onToggleTask(note.toOriginal(offset), checked);
  })), [headingLevel, note, onToggleTask]);
  if (note.empty) return <>{empty}</>;
  return (
    <div className={`gx-prose ${className}`.trim()}>
      <Markdown remarkPlugins={REMARK_PLUGINS} components={components} urlTransform={markdownUrlTransform}>{note.visible}</Markdown>
    </div>
  );
});
