import { Bold, Code, Heading2, Italic, Link2, List, ListChecks, ListOrdered, TextQuote } from "lucide-react";
import type { ReactNode } from "react";

export interface MarkdownEditorHandle {
  focus: (position?: "start" | "end") => void;
}

export interface MarkdownEditorProps {
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
  placeholder?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  maxLength?: number;
  showToolbar?: boolean;
  /** Extra controls placed at the end of the formatting toolbar. */
  toolbarEnd?: ReactNode;
  /** ⌘E — switch between writing and the rendered preview. */
  onTogglePreview?: () => void;
  /** ⌘↵ — the surface's primary action. */
  onSubmit?: () => void;
  /** Esc inside the editor, e.g. to stop editing. */
  onEscape?: () => void;
  className?: string;
}

export type ToolbarCommand = "heading" | "bold" | "italic" | "bullets" | "numbers" | "tasks" | "quote" | "code" | "link";

const TOOLBAR: Array<{ command: ToolbarCommand; label: string; shortcut?: string; icon: typeof Bold; divider?: boolean }> = [
  { command: "heading", label: "Heading", icon: Heading2 },
  { command: "bold", label: "Bold", shortcut: "⌘B", icon: Bold },
  { command: "italic", label: "Italic", shortcut: "⌘I", icon: Italic },
  { command: "bullets", label: "Bulleted list", shortcut: "⌘⇧8", icon: List, divider: true },
  { command: "numbers", label: "Numbered list", shortcut: "⌘⇧7", icon: ListOrdered },
  { command: "tasks", label: "Checklist", shortcut: "⌘⇧9", icon: ListChecks },
  { command: "quote", label: "Quote", icon: TextQuote, divider: true },
  { command: "code", label: "Code", icon: Code },
  { command: "link", label: "Link", shortcut: "⌘K", icon: Link2 },
];

export function EditorToolbar({ onCommand, end, disabled = false }: { onCommand: (command: ToolbarCommand) => void; end?: ReactNode; disabled?: boolean }) {
  return (
    <div className="gx-md-toolbar" role="toolbar" aria-label="Formatting">
      {TOOLBAR.map(({ command, label, shortcut, icon: Icon, divider }) => (
        <button
          type="button"
          key={command}
          className={divider ? "has-divider" : undefined}
          aria-label={label}
          title={shortcut ? `${label}  ${shortcut}` : label}
          disabled={disabled}
          // Keep the caret and selection in the text while clicking.
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => onCommand(command)}
        >
          <Icon size={15} />
        </button>
      ))}
      {end && <span className="gx-md-toolbar-end">{end}</span>}
    </div>
  );
}
