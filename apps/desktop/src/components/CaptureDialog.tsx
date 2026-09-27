import { ArrowRight, FileText, Link2, Mic2, Paperclip, Sparkles, Table2, X } from "lucide-react";
import { type FormEvent, useState } from "react";

interface CaptureDialogProps {
  open: boolean;
  onClose: () => void;
  onCaptured: (title: string) => void;
}

const captureKinds = [
  { id: "note", label: "Note", icon: FileText },
  { id: "file", label: "File", icon: Paperclip },
  { id: "link", label: "Link", icon: Link2 },
  { id: "table", label: "Table", icon: Table2 },
  { id: "recording", label: "Recording", icon: Mic2 },
];

export function CaptureDialog({ open, onClose, onCaptured }: CaptureDialogProps) {
  const [kind, setKind] = useState("note");
  const [title, setTitle] = useState("New learning");
  const [content, setContent] = useState("CD247 is another component of the T-cell receptor complex and can support T-cell identity when interpreted with CD3D and CD3E.");
  const [working, setWorking] = useState(false);

  if (!open) return null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!content.trim()) return;
    setWorking(true);
    window.setTimeout(() => {
      setWorking(false);
      onCaptured(title.trim() || "Untitled source");
    }, 650);
  };

  return (
    <div className="capture-overlay" role="presentation" onMouseDown={onClose}>
      <form className="capture-dialog" role="dialog" aria-modal="true" aria-label="Capture knowledge" onSubmit={submit} onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <span className="capture-dialog-icon"><Sparkles size={18} /></span>
          <span><small>Preserve first, organize later</small><strong>Capture new knowledge</strong></span>
          <button type="button" onClick={onClose} aria-label="Close capture"><X size={17} /></button>
        </header>
        <div className="capture-kind-tabs" role="tablist">
          {captureKinds.map(({ id, label, icon: Icon }) => (
            <button type="button" key={id} className={kind === id ? "is-active" : ""} onClick={() => setKind(id)}>
              <Icon size={14} />{label}
            </button>
          ))}
        </div>
        <div className="capture-dialog-fields">
          <input value={title} onChange={(event) => setTitle(event.target.value)} aria-label="Source title" />
          <textarea value={content} onChange={(event) => setContent(event.target.value)} aria-label="Source content" rows={8} />
        </div>
        <div className="capture-preview-row">
          <span><i />Original preserved locally</span>
          <span><i />Exact evidence spans</span>
          <span><i />Review before trust</span>
        </div>
        <footer>
          <span>DeepSeek will propose entities, claims, and links.</span>
          <button className="button button-primary" disabled={working || !content.trim()}>
            {working ? "Interpreting…" : "Preserve & interpret"}<ArrowRight size={14} />
          </button>
        </footer>
      </form>
    </div>
  );
}
