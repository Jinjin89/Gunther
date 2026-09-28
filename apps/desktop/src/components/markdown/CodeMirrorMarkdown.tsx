import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { HighlightStyle, LanguageSupport, syntaxHighlighting } from "@codemirror/language";
import { markdownKeymap, markdownLanguage, pasteURLAsLink } from "@codemirror/lang-markdown";
import { Annotation, Compartment, EditorState, Prec, type Extension } from "@codemirror/state";
import { EditorView, keymap, placeholder as placeholderExtension, type KeyBinding } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { insertLink, shiftListItems, toggleLinePrefix, toggleWrap, type TextEdit } from "./markdownEditing";
import { EditorToolbar, type MarkdownEditorHandle, type MarkdownEditorProps, type ToolbarCommand } from "./editorShared";

/**
 * Markdown source editing on CodeMirror 6: the text stays plain and portable,
 * while structure is styled as you type (headings, emphasis, code, quotes),
 * lists continue on Enter, and IME composition is handled natively.
 */

const markdownStyle = HighlightStyle.define([
  { tag: tags.heading1, fontFamily: "var(--gx-font-display)", fontSize: "1.5em", fontWeight: "560", letterSpacing: "-0.015em" },
  { tag: tags.heading2, fontFamily: "var(--gx-font-display)", fontSize: "1.28em", fontWeight: "580" },
  { tag: tags.heading3, fontFamily: "var(--gx-font-display)", fontSize: "1.12em", fontWeight: "600" },
  { tag: [tags.heading4, tags.heading5, tags.heading6], fontWeight: "650" },
  { tag: tags.strong, fontWeight: "680" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: tags.strikethrough, textDecoration: "line-through", color: "var(--gx-faint)" },
  { tag: tags.link, color: "var(--gx-ink)", textDecoration: "underline", textDecorationColor: "var(--gx-line-3)", textUnderlineOffset: "3px" },
  { tag: tags.url, color: "var(--gx-faint)" },
  { tag: tags.monospace, fontFamily: "var(--gx-font-mono)", fontSize: "0.88em", color: "var(--gx-ink-2)" },
  { tag: tags.quote, color: "var(--gx-muted)" },
  { tag: [tags.processingInstruction, tags.contentSeparator, tags.labelName], color: "var(--gx-ghost)" },
  { tag: tags.comment, color: "var(--gx-ghost)", fontStyle: "italic" },
]);

const theme = EditorView.theme({
  "&": { color: "var(--gx-ink)", backgroundColor: "transparent", fontSize: "15px" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { overflow: "visible", fontFamily: "var(--gx-font-sans)", lineHeight: "1.75" },
  ".cm-content": { padding: "0 0 40px", minHeight: "var(--gx-md-editor-min, 220px)", caretColor: "var(--gx-brand)" },
  ".cm-line": { padding: "0" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--gx-brand)", borderLeftWidth: "1.5px" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection": { backgroundColor: "var(--gx-selection) !important" },
  ".cm-placeholder": { color: "var(--gx-ghost)" },
  ".cm-gutters": { display: "none" },
});

/** Marks programmatic syncs so they are not reported back as user edits. */
const syncAnnotation = Annotation.define<boolean>();

const call = (callback: (() => void) | undefined) => {
  if (!callback) return false;
  callback();
  return true;
};

const run = (view: EditorView, edit: TextEdit | null) => {
  if (!edit) return false;
  view.dispatch({
    changes: { from: edit.from, to: edit.to, insert: edit.insert },
    selection: { anchor: edit.selectionStart, head: edit.selectionEnd },
    scrollIntoView: true,
    userEvent: "input.format",
  });
  return true;
};

const withSelection = (build: (value: string, start: number, end: number) => TextEdit | null) => (view: EditorView) => {
  const { from, to } = view.state.selection.main;
  return run(view, build(view.state.doc.toString(), from, to));
};

export const COMMANDS: Record<ToolbarCommand, (view: EditorView) => boolean> = {
  heading: withSelection((value, start, end) => toggleLinePrefix(value, start, end, "## ")),
  bold: withSelection((value, start, end) => toggleWrap(value, start, end, "**", "bold")),
  italic: withSelection((value, start, end) => toggleWrap(value, start, end, "_", "italic")),
  bullets: withSelection((value, start, end) => toggleLinePrefix(value, start, end, "- ")),
  numbers: withSelection((value, start, end) => toggleLinePrefix(value, start, end, "1. ")),
  tasks: withSelection((value, start, end) => toggleLinePrefix(value, start, end, "- [ ] ")),
  quote: withSelection((value, start, end) => toggleLinePrefix(value, start, end, "> ")),
  code: withSelection((value, start, end) => toggleWrap(value, start, end, "`", "code")),
  link: withSelection(insertLink),
};

const CodeMirrorMarkdown = forwardRef<MarkdownEditorHandle, MarkdownEditorProps>(function CodeMirrorMarkdown({
  value,
  onChange,
  ariaLabel,
  placeholder,
  disabled = false,
  autoFocus = false,
  maxLength,
  showToolbar = true,
  toolbarEnd,
  onTogglePreview,
  onSubmit,
  onEscape,
  className = "",
}, ref) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const editable = useRef(new Compartment());
  // Keep the latest callbacks without rebuilding the editor.
  const callbacks = useRef({ onChange, onTogglePreview, onSubmit, onEscape });
  callbacks.current = { onChange, onTogglePreview, onSubmit, onEscape };

  useImperativeHandle(ref, () => ({
    focus: (position = "end") => {
      const current = view.current;
      if (!current) return;
      const at = position === "start" ? 0 : current.state.doc.length;
      current.dispatch({ selection: { anchor: at }, scrollIntoView: true });
      current.focus();
    },
  }), []);

  useEffect(() => {
    if (!host.current) return undefined;
    const bindings: KeyBinding[] = [
      ...markdownKeymap,
      { key: "Mod-b", run: COMMANDS.bold },
      { key: "Mod-i", run: COMMANDS.italic },
      // ⌘K links a selection; with nothing selected it stays the global search shortcut.
      { key: "Mod-k", run: (target) => !target.state.selection.main.empty && COMMANDS.link(target) },
      { key: "Mod-Shift-7", run: COMMANDS.numbers },
      { key: "Mod-Shift-8", run: COMMANDS.bullets },
      { key: "Mod-Shift-9", run: COMMANDS.tasks },
      { key: "Mod-e", run: () => call(callbacks.current.onTogglePreview) },
      { key: "Mod-Enter", run: () => call(callbacks.current.onSubmit) },
      { key: "Escape", run: () => call(callbacks.current.onEscape) },
      // Only list lines capture Tab, so keyboard focus can still leave the editor.
      { key: "Tab", run: withSelection((text, start, end) => shiftListItems(text, start, end, false)) },
      { key: "Shift-Tab", run: withSelection((text, start, end) => shiftListItems(text, start, end, true)) },
    ];
    const extensions: Extension[] = [
      history(),
      EditorView.lineWrapping,
      new LanguageSupport(markdownLanguage),
      syntaxHighlighting(markdownStyle),
      pasteURLAsLink,
      Prec.high(keymap.of(bindings)),
      keymap.of([...defaultKeymap, ...historyKeymap]),
      theme,
      EditorView.contentAttributes.of({ "aria-label": ariaLabel, "aria-multiline": "true", spellcheck: "true", autocorrect: "on" }),
      editable.current.of([EditorView.editable.of(!disabled), EditorState.readOnly.of(disabled)]),
      EditorView.updateListener.of((update) => {
        if (update.docChanged && !update.transactions.some((transaction) => transaction.annotation(syncAnnotation))) {
          callbacks.current.onChange(update.state.doc.toString());
        }
      }),
      // Handled shortcuts must not also reach the app's global shortcuts.
      EditorView.domEventHandlers({ keydown: (event) => { if (event.defaultPrevented) event.stopPropagation(); return false; } }),
    ];
    if (placeholder) extensions.push(placeholderExtension(placeholder));
    if (maxLength) extensions.push(EditorState.changeFilter.of((transaction) => transaction.newDoc.length <= maxLength));
    const created = new EditorView({ parent: host.current, state: EditorState.create({ doc: value, extensions }) });
    view.current = created;
    if (autoFocus) {
      created.dispatch({ selection: { anchor: created.state.doc.length } });
      created.focus();
    }
    return () => {
      created.destroy();
      view.current = null;
    };
    // The editor is created once; later prop changes are applied below.
  }, []);

  // Follow value changes made elsewhere (a toggled task, a switched note).
  useEffect(() => {
    const current = view.current;
    if (!current) return;
    const text = current.state.doc.toString();
    if (text === value) return;
    const head = Math.min(current.state.selection.main.head, value.length);
    current.dispatch({ changes: { from: 0, to: text.length, insert: value }, selection: { anchor: head }, annotations: syncAnnotation.of(true) });
  }, [value]);

  useEffect(() => {
    view.current?.dispatch({ effects: editable.current.reconfigure([EditorView.editable.of(!disabled), EditorState.readOnly.of(disabled)]) });
  }, [disabled]);

  return (
    <div className={`gx-md-editor is-codemirror ${disabled ? "is-disabled" : ""} ${className}`.trim()}>
      {showToolbar && !disabled && <EditorToolbar onCommand={(command) => { const current = view.current; if (current) { COMMANDS[command](current); current.focus(); } }} end={toolbarEnd} />}
      <div ref={host} className="gx-md-surface" />
    </div>
  );
});

export default CodeMirrorMarkdown;
