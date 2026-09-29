import type { SourcePaper } from "@gunther/contracts";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../../api";

/** What the paper says about itself: authors, year, identifiers, abstract, sections. */
export function PaperCard({ sourceId, revisionId }: { sourceId: string; revisionId: string | null | undefined }) {
  const [paper, setPaper] = useState<SourcePaper | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let active = true;
    setPaper(null);
    // Read in the background after parsing; until then there is nothing to show.
    void knowledgeApi.sourcePaper(sourceId).then((next) => { if (active) setPaper(next); }).catch(() => undefined);
    return () => { active = false; };
  }, [sourceId, revisionId]);

  if (!paper || (!paper.abstract && !paper.authors && !paper.year && !paper.outline.length)) return null;
  const byline = [paper.authors, paper.year ? String(paper.year) : ""].filter(Boolean).join(" · ");
  const long = paper.abstract.length > 420;
  return <section className="gx-paper-card" aria-label="About this paper">
    {byline && <p className="gx-paper-byline">{byline}</p>}
    {(paper.doi || paper.arxivId) && <p className="gx-paper-ids">
      {paper.doi && <a href={`https://doi.org/${paper.doi}`} target="_blank" rel="noreferrer">DOI {paper.doi}</a>}
      {paper.arxivId && <a href={`https://arxiv.org/abs/${paper.arxivId}`} target="_blank" rel="noreferrer">arXiv {paper.arxivId}</a>}
    </p>}
    {paper.abstract && <>
      <h3>Abstract</h3>
      <p className={`gx-paper-abstract ${long && !expanded ? "is-clamped" : ""}`}>{paper.abstract}</p>
      {long && <button type="button" className="gx-link" onClick={() => setExpanded((value) => !value)}>{expanded ? "Show less" : "Show the whole abstract"}</button>}
    </>}
    {paper.outline.length > 0 && <details className="gx-paper-outline"><summary>{paper.outline.length} sections</summary><ol>{paper.outline.map((heading) => <li key={heading}>{heading}</li>)}</ol></details>}
    {paper.copies.length > 0 && <p className="gx-paper-copies">Also saved as {paper.copies.map((copy) => `“${copy.title}”`).join(", ")}: the same paper, counted once.</p>}
  </section>;
}
