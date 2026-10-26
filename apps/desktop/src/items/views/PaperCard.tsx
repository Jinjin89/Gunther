import type { SourcePaper } from "@gunther/contracts";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../../api";

/** What the paper says about itself: authors, year, identifiers, abstract, sections. */
export function PaperCard({ sourceId, revisionId }: { sourceId: string; revisionId: string | null | undefined }) {
  const [paper, setPaper] = useState<SourcePaper | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setPaper(null);
    // Read in the background after parsing; until then there is nothing to show. Details of an
    // earlier revision are asked for again a few times until the current one's are written.
    const load = (attempt: number) => void knowledgeApi.sourcePaper(sourceId).then((next) => {
      if (!active) return;
      setPaper(next);
      if (revisionId && next.revisionId !== revisionId && attempt < 20) timer = setTimeout(() => load(attempt + 1), 3000);
    }).catch(() => undefined);
    load(0);
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [sourceId, revisionId]);

  if (!paper || (!paper.abstract && !paper.authors && !paper.year && !paper.outline.length)) return null;
  // The card is read from one revision; after the file is read again it waits for the new one.
  if (revisionId && paper.revisionId !== revisionId) return <section className="gx-paper-card" aria-label="About this paper"><p className="gx-paper-byline">Reading the paper's details from the new version…</p></section>;
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
    {paper.outline.length > 0 && <details className="gx-paper-outline"><summary>{paper.outline.length} sections</summary><ol>{paper.outline.map((heading, index) => <li key={`${index}-${heading}`}>{heading}</li>)}</ol></details>}
    {paper.copies.length > 0 && <p className="gx-paper-copies">Also saved as {paper.copies.map((copy) => `“${copy.title}”`).join(", ")}: the same paper, counted once.</p>}
  </section>;
}
