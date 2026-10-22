import type { Artifact, ConversationCitation } from "@gunther/contracts";
import { Check, LoaderCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { MarkdownEditor } from "../components/markdown/MarkdownEditor";
import { LazySlidesView } from "./LazySlides";
import { ReportView } from "./ReportView";
import { restoreUnchangedScenes, withDraft, withoutSceneMarkers } from "./sections";

/**
 * Typing over a version: the Markdown beside a live preview of it. Saving makes the next
 * version exactly as typed; nothing is renumbered or rewritten, and the sections that were
 * typed are marked "not re-checked" until the Checker looks at them again.
 */
export function EditPane({ artifact, saving, onSave, onCancel, onCite }: {
  artifact: Artifact;
  saving: boolean;
  onSave: (content: string) => void;
  onCancel: () => void;
  onCite: (citation: ConversationCitation, index: number) => void;
}) {
  const editableOriginal = useMemo(() => artifact.kind === "slides" ? withoutSceneMarkers(artifact.content) : artifact.content, [artifact.content, artifact.kind]);
  const [draft, setDraft] = useState(editableOriginal);
  const [previewContent, setPreviewContent] = useState(draft);
  useEffect(() => {
    if (artifact.kind !== "slides") { setPreviewContent(draft); return; }
    let alive = true;
    void restoreUnchangedScenes(artifact.content, draft).then((value) => { if (alive) setPreviewContent(value); });
    return () => { alive = false; };
  }, [artifact, draft]);
  const preview = useMemo(() => withDraft(artifact, previewContent), [artifact, previewContent]);
  const changed = draft !== editableOriginal;
  const typed = preview.sections.filter((section) => !section.checked).length - artifact.sections.filter((section) => !section.checked).length;
  const saveDraft = () => {
    if (artifact.kind !== "slides") { onSave(draft); return; }
    void restoreUnchangedScenes(artifact.content, draft).then(onSave);
  };

  return <section className="outputs-edit" aria-label="Edit">
    <div className="outputs-edit-bar">
      <span>{changed ? "Unsaved changes" : "No changes yet"}{changed && typed > 0 ? ` · ${typed} ${typed === 1 ? "part" : "parts"} to re-check` : ""}</span>
      <div>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onCancel} disabled={saving}>Cancel</button>
        <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={saving || !changed || draft.trim().length === 0} onClick={saveDraft}>
          {saving ? <LoaderCircle className="spin" size={13} /> : <Check size={13} />}Save as new version
        </button>
      </div>
    </div>
    <div className="outputs-edit-columns">
      <div className="outputs-edit-source">
        <MarkdownEditor value={draft} onChange={setDraft} ariaLabel={artifact.kind === "slides" ? "Slides, as Markdown" : "Report, as Markdown"} autoFocus maxLength={2_500_000} onEscape={onCancel} onSubmit={() => { if (changed && draft.trim()) saveDraft(); }} />
        {artifact.kind === "slides" && <p className="outputs-edit-hint">A line with only <code>---</code> starts the next slide. A line starting with <code>Note:</code> begins the speaker notes. The comment on a slide's first line, <code>{"<!-- layout: … -->"}</code>, says how it is drawn; keep it with the slide. Scene metadata is hidden and retained for slides whose source text stays unchanged.</p>}
      </div>
      <div className="outputs-edit-preview" aria-label="Preview">
        {artifact.kind === "slides"
          ? <LazySlidesView artifact={preview} onCite={onCite} compact />
          : <ReportView artifact={preview} onCite={onCite} />}
      </div>
    </div>
  </section>;
}
