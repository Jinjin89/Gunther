import type { ArtifactSummary, OutputKind } from "@gunther/contracts";
import { Copy, Download, FileText, History, Pencil, Presentation, Sparkles } from "lucide-react";
import { PopMenu } from "../contents/SectionMenu";

export type ArtifactAction = "edit" | "revise" | "rebuild" | "versions" | "copy" | "download";

/** Creation tiles and an independent action menu for every saved artifact. */
export function ArtifactStudio({ groups, activeLineageId, loading, disabled, opening, canGenerate, describe, onCreate, onOpen, onAction }: {
  groups: ArtifactSummary[][];
  activeLineageId: string | null;
  loading: boolean;
  disabled: boolean;
  opening: string | null;
  canGenerate: boolean;
  describe: (item: ArtifactSummary) => string;
  onCreate: (kind: OutputKind, trigger: HTMLButtonElement) => void;
  onOpen: (item: ArtifactSummary) => void;
  onAction: (item: ArtifactSummary, action: ArtifactAction) => void;
}) {
  return <aside className="outputs-history artifacts-studio" aria-label="Saved artifacts">
    <header><strong>Create</strong></header>
    <div className="artifacts-studio-types">
      <button type="button" aria-label="New report" disabled={loading || disabled} onClick={(event) => onCreate("report", event.currentTarget)}><FileText size={18} /><span>Report</span></button>
      <button type="button" aria-label="New slides" disabled={loading || disabled} onClick={(event) => onCreate("slides", event.currentTarget)}><Presentation size={18} /><span>Slides</span></button>
    </div>
    <header><strong>Saved artifacts</strong><small>{groups.length}</small></header>
    {loading ? <p role="status">Loading artifacts…</p> : groups.length === 0 ? <p>No artifacts yet.</p> : <ul>{groups.map((versions) => {
      const item = versions[0]!;
      const selected = activeLineageId === item.lineageId;
      const available = !disabled && opening === null;
      return <li key={item.lineageId} className={selected ? "is-active" : ""}>
        <button type="button" className="artifact-open" aria-current={selected ? "page" : undefined} disabled={!available} onClick={() => onOpen(item)} title={item.title}>
          {item.kind === "slides" ? <Presentation size={15} /> : <FileText size={15} />}
          <span><strong>{item.title}</strong><small>{describe(item)} · v{item.versionNumber}</small></span>
        </button>
        <PopMenu label={`Actions for ${item.title}`} items={[
          { label: "Open", icon: FileText, disabled: !available, onSelect: () => onOpen(item) },
          { label: "Versions", icon: History, disabled: !available, onSelect: () => onAction(item, "versions") },
          { label: "Edit", icon: Pencil, disabled: !available || item.origin === "legacy", onSelect: () => onAction(item, "edit") },
          { label: "Revise", icon: Sparkles, disabled: !available || !canGenerate || item.origin === "legacy", onSelect: () => onAction(item, "revise") },
          { label: "Rebuild", icon: Sparkles, disabled: !available, onSelect: () => onAction(item, "rebuild") },
          { label: "Copy Markdown", icon: Copy, divider: true, disabled: !available, onSelect: () => onAction(item, "copy") },
          { label: "Download Markdown", icon: Download, disabled: !available, onSelect: () => onAction(item, "download") },
        ]} />
      </li>;
    })}</ul>}
  </aside>;
}
