import type { Artifact, SlideVisualFinding } from "@gunther/contracts";
import { useState } from "react";
import { artifactRenderUrl } from "../api";

export function visualFollowUp(finding: SlideVisualFinding, slide: number): string {
  const action = finding.route === "plan" ? "Replan the outline and split or reorder" : finding.route === "write" ? "Rewrite and fact-check" : "Repair the layout of";
  return `${action} slide ${slide}: ${finding.problem} ${finding.correction} Preserve all source bindings, values, units and limiting qualifications. Respect existing locks.`;
}

/** Review diagnostics belong beside the deck, never in its text or speaker notes. */
export function SlideReviewPanel({ artifact, baseId, workspaceId, disabled, onFollowUp, onReview }: {
  artifact: Artifact; baseId: string; workspaceId: string; disabled: boolean;
  onFollowUp: (prompt: string) => void;
  onReview?: (() => void) | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const render = artifact.provenance.skill?.renderCheck;
  const visual = artifact.provenance.skill?.visualReview;
  if (artifact.kind !== "slides") return null;
  if (!visual) return onReview ? <section className="outputs-slide-review"><button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={disabled} onClick={onReview}>Review rendered slides</button></section> : null;
  const label = { clean: "Rendered slides reviewed", repaired: "Rendered slides reviewed · layout repaired", needs_review: "Slides need visual review", unavailable: "Rendered-slide review unavailable", stale: "Slide review is out of date" }[visual.status];
  const url = (name: string) => artifactRenderUrl(baseId, artifact.id, workspaceId, render!.id!, name);
  return <section className="outputs-slide-review" aria-label="Rendered-slide review">
    <button className="gx-btn gx-btn-ghost gx-btn-sm" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>{label}</button>
    {open && <div><p>{visual.reason ?? (visual.status === "clean" || visual.status === "repaired" ? "The saved candidate matches the inspected renders." : "The deck is saved. Resolve these findings before presenting.")}</p>
      {onReview && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={disabled} onClick={onReview}>Review current renders · save a version</button>}
      <small>Image review: {visual.imageReview}. {visual.repairCycles} repair cycle · {visual.repairedSlides.length} pages changed.</small>
      {visual.findings.length > 0 && <ul>{visual.findings.map((finding, index) => {
        const position = artifact.outline.findIndex((item, at) => (item.id || `slide-${at+1}`) === finding.slideId) + 1;
        return <li key={index}><strong>{position ? `Slide ${position}` : finding.slideId}: {finding.problem}</strong><p>{finding.correction}</p>
          {position > 0 && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={disabled} onClick={() => onFollowUp(visualFollowUp(finding, position))}>{finding.route === "plan" ? "Replan with checks" : finding.route === "write" ? "Rewrite with checks" : "Request layout repair"}</button>}</li>;
      })}</ul>}
      {render?.id && render.status === "captured" && <><a href={url("deck.pdf")} download="gunther-reviewed-deck.pdf">Inspected PDF</a>
        {imageFailed ? <p>Cached renders are unavailable. Rebuild to capture them again.</p> : <button type="button" aria-label="Inspect contact sheet at full size" onClick={() => setPreview("contact.png")} style={{ border: 0, padding: 0, background: "transparent", cursor: "zoom-in" }}><img src={url("contact.png")} alt="Contact sheet of the inspected slides" onError={() => setImageFailed(true)}/></button>}
        <div className="outputs-slide-review-images">{render.slides?.map(slide => <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" key={slide.id} onClick={() => setPreview(slide.image)}>Slide {slide.index+1}</button>)}</div>
        {preview && <div role="region" aria-label="Full-size inspected render"><button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => setPreview(null)}>Close render</button><div style={{ overflow: "auto", maxHeight: "70vh" }}><img src={url(preview)} alt="Full-size inspected render" style={{ width: preview === "contact.png" ? 640 : 1280, maxWidth: "none", maxHeight: "none" }}/></div></div>}
      </>}
    </div>}
  </section>;
}
