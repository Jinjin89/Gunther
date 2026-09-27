import type { CreateSourceInput, ImportResult, SourceKind } from "@gunther/contracts";
import {
  ArrowUp,
  BookOpen,
  CheckCircle2,
  FileText,
  Radio,
  Sparkles,
  Table2,
} from "lucide-react";
import { type FormEvent, useState } from "react";

const sourceKinds: Array<{ value: SourceKind; label: string; icon: typeof FileText }> = [
  { value: "note", label: "Note", icon: FileText },
  { value: "paper", label: "Paper", icon: BookOpen },
  { value: "table", label: "Table", icon: Table2 },
  { value: "course", label: "Course", icon: Sparkles },
  { value: "recording", label: "Recording", icon: Radio },
];

interface CaptureComposerProps {
  extractionMode: "local" | "deepseek";
  onImport: (payload: CreateSourceInput) => Promise<ImportResult>;
  variant?: "hero" | "panel";
}

export function CaptureComposer({ extractionMode, onImport, variant = "panel" }: CaptureComposerProps) {
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<SourceKind>("note");
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim() || content.trim().length < 3) return;

    setSubmitting(true);
    setResult(null);
    setError(null);
    try {
      const imported = await onImport({ title, kind, content });
      setResult(imported);
      if (!imported.duplicate) {
        setTitle("");
        setContent("");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Import failed");
    } finally {
      setSubmitting(false);
    }
  };

  const placeholder = extractionMode === "deepseek"
    ? "Paste a note, course transcript, paper excerpt, or anything you just learned…"
    : "CD3D -> marker_of -> T cell\nBackpropagation -> depends_on -> chain rule";

  return (
    <form className={`capture-composer capture-${variant}`} onSubmit={submit}>
      <div className="capture-heading">
        <span className="capture-icon"><Sparkles size={15} strokeWidth={1.7} /></span>
        <div>
          <strong>{variant === "hero" ? "Capture a learning" : "Add a source"}</strong>
          <span>Gunther keeps the original and proposes connected knowledge.</span>
        </div>
        <span className={`mode-badge mode-${extractionMode}`}>
          <i />{extractionMode === "deepseek" ? "DeepSeek" : "Local rules"}
        </span>
      </div>

      <textarea
        aria-label="Knowledge content"
        value={content}
        onChange={(event) => setContent(event.target.value)}
        placeholder={placeholder}
        rows={variant === "hero" ? 4 : 9}
        required
      />

      <div className="capture-controls">
        <input
          aria-label="Source title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Give this source a title"
          maxLength={160}
          required
        />
        <label className="kind-select">
          {(() => {
            const KindIcon = sourceKinds.find((option) => option.value === kind)?.icon ?? FileText;
            return <KindIcon size={14} strokeWidth={1.7} />;
          })()}
          <select aria-label="Source type" value={kind} onChange={(event) => setKind(event.target.value as SourceKind)}>
            {sourceKinds.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <button className="capture-submit" disabled={submitting || !title.trim() || content.trim().length < 3}>
          <span>{submitting ? "Extracting…" : "Add to graph"}</span>
          <ArrowUp size={16} strokeWidth={2.1} />
        </button>
      </div>

      {extractionMode === "local" && variant === "panel" && (
        <p className="capture-hint">Local mode understands relationship statements. Add a DeepSeek key for free-form extraction.</p>
      )}
      {error && <p className="capture-message is-error" role="alert">{error}</p>}
      {result && (
        <p className="capture-message is-success" role="status">
          <CheckCircle2 size={15} />
          {result.duplicate
            ? "This source is already in your library."
            : `${result.created.entities} entities and ${result.created.assertions} assertions added.`}
        </p>
      )}
    </form>
  );
}
