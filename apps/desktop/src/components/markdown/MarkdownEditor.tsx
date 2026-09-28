import { forwardRef, lazy, Suspense } from "react";
import { EditorToolbar, type MarkdownEditorHandle, type MarkdownEditorProps } from "./editorShared";

export type { MarkdownEditorHandle, MarkdownEditorProps } from "./editorShared";

const loadEditor = () => import("./CodeMirrorMarkdown");
const CodeMirrorMarkdown = lazy(loadEditor);

/** Start loading the editor before it is needed, e.g. when a note opens. */
export const preloadMarkdownEditor = () => {
  void loadEditor().catch(() => undefined);
};

/**
 * The Markdown editor. CodeMirror loads on first use; until then the text is
 * shown in place with the same metrics, so nothing shifts when it arrives.
 */
export const MarkdownEditor = forwardRef<MarkdownEditorHandle, MarkdownEditorProps>(function MarkdownEditor(props, ref) {
  const fallback = (
    <div className={`gx-md-editor is-loading ${props.className ?? ""}`.trim()} aria-busy="true">
      {props.showToolbar !== false && !props.disabled && <EditorToolbar onCommand={() => undefined} end={props.toolbarEnd} disabled />}
      <div className="gx-md-surface"><div className="gx-md-placeholder-text">{props.value || props.placeholder}</div></div>
    </div>
  );
  return (
    <Suspense fallback={fallback}>
      <CodeMirrorMarkdown ref={ref} {...props} />
    </Suspense>
  );
});
