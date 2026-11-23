import type { ContentsSource, ConversationCitation, DigestCitation } from "@gunther/contracts";
import { ArrowUpRight } from "lucide-react";
import { useState } from "react";
import { EvidencePanel } from "../components/evidence/EvidencePanel";
import { DigestCard } from "../items/views/DigestCard";
import { PaperCard } from "../items/views/PaperCard";
import { kindIcon } from "./ContentsRow";
import { addedOn, fileDetail, recordingLength } from "./contentsTree";

export function SourceSummary({ source, onOpen, onAsk }: {
  source: ContentsSource;
  onOpen: () => void;
  onAsk: (question: string, submit: boolean) => void;
}) {
  const Icon = kindIcon[source.kind];
  const file = fileDetail(source);
  const [evidence, setEvidence] = useState<(ConversationCitation & { ref: number }) | null>(null);
  const cite = (citation: DigestCitation, revisionId: string) => setEvidence({
    id: `${revisionId}:${citation.blockId}`, sourceId: source.id, sourceTitle: source.title,
    assertionId: null, quote: citation.quote, locator: citation.locator, status: "provisional",
    confidence: 0, sourceRevisionId: revisionId, blockId: citation.blockId, ref: citation.number,
  });
  return <article className="library-reading" aria-label="Source summary">
    <header className="library-reading-heading">
      <span className={`gx-kind-icon kind-${source.kind}`}><Icon size={16} /></span>
      <h1>{source.title}</h1>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onOpen}>
        <ArrowUpRight size={14} />Open full page
      </button>
    </header>
    <dl className="contents-facts">
      <div><dt>Added</dt><dd>{addedOn(source)}</dd></div>
      {source.durationSeconds ? <div><dt>Length</dt><dd>{recordingLength(source.durationSeconds)}</dd></div> : null}
      {file && <div><dt>File</dt><dd>{file}</dd></div>}
      {source.host && <div><dt>Site</dt><dd>{source.host}</dd></div>}
      <div><dt>Status</dt><dd>{source.processing === "reading" ? "Being read…" : source.processing === "failed" ? "Couldn’t be read" : "Ready"}</dd></div>
    </dl>
    <DigestCard sourceId={source.id} onCite={cite} onAsk={onAsk} />
    {(source.kind === "paper" || source.mediaType === "application/pdf") && <PaperCard sourceId={source.id} revisionId={null} />}
    {evidence && <EvidencePanel citation={evidence} index={evidence.ref - 1}
      onClose={() => setEvidence(null)} onOpenSource={onOpen} />}
  </article>;
}
