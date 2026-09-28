import type { SourceDetail } from "@gunther/contracts";
import { Check, Copy, Download, Maximize2, Minimize2, ScanText, X, ZoomIn } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { sourceAssetUrl } from "../../api";
import { useEscape } from "../../shortcuts/shortcuts";
import { formatBytes, mediaTypeLabel, type FileContent } from "../sourceContent";
import { useStructure } from "./DocumentView";
import { ContextNote, PlainText } from "./ReaderViews";

interface Region {
  text: string;
  box: [number, number, number, number] | null;
}

function Lightbox({ url, title, onClose }: { url: string; title: string; onClose: () => void }) {
  const [actualSize, setActualSize] = useState(false);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEscape(onClose);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeButton.current?.focus();
    return () => previous?.focus?.();
  }, []);
  return createPortal(
    <div className="gx-lightbox" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="gx-lightbox-bar">
        <span>{title}</span>
        <button type="button" onClick={() => setActualSize((value) => !value)} aria-label={actualSize ? "Fit to window" : "Actual size"} title={actualSize ? "Fit to window" : "Actual size"}>{actualSize ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>
        <a href={url} download aria-label="Download original" title="Download original"><Download size={15} /></a>
        <button ref={closeButton} type="button" onClick={onClose} aria-label="Close" title="Close  Esc"><X size={16} /></button>
      </div>
      <div className={`gx-lightbox-stage ${actualSize ? "is-actual" : ""}`} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
        <img src={url} alt={title} onClick={() => setActualSize((value) => !value)} />
      </div>
    </div>,
    document.body,
  );
}

export function ImageBody({ source, file }: { source: SourceDetail; file: FileContent }) {
  const asset = source.asset;
  const url = asset ? sourceAssetUrl(asset.id) : null;
  const structure = useStructure(source.id);
  const [active, setActive] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null);
  const [copied, setCopied] = useState(false);
  const regions = useMemo<Region[]>(() => (structure.data?.blocks ?? [])
    .filter((block) => block.kind !== "heading" && block.headings[0] !== "Your context")
    .map((block) => {
      const box = (block.anchor as { bboxPpm?: number[] }).bboxPpm;
      return { text: block.content, box: box?.length === 4 ? box as [number, number, number, number] : null };
    }), [structure.data]);
  const boxed = regions.filter((region) => region.box);
  const allText = regions.map((region) => region.text).join("\n") || file.extracted;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(allText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_400);
    } catch {
      // The text stays selectable.
    }
  };
  const ocrUnavailable = file.ocrStatus && !["complete", "completed", "ok", "succeeded"].includes(file.ocrStatus);
  return (
    <div className="gx-reader">
      {url ? (
        <figure className="gx-image-stage">
          <button type="button" className="gx-image-frame" onClick={() => setOpen(true)} aria-label="View full size">
            <span className="gx-image-canvas">
            <img
              src={url}
              alt={source.title}
              onLoad={(event) => setDimensions({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
            />
            {boxed.length > 0 && (
              <span className="gx-image-regions" aria-hidden="true">
                {regions.map((region, index) => region.box && (
                  <span
                    key={index}
                    className={index === active ? "is-active" : undefined}
                    style={{ left: `${region.box[0] / 10_000}%`, top: `${region.box[1] / 10_000}%`, width: `${region.box[2] / 10_000}%`, height: `${region.box[3] / 10_000}%` }}
                    onMouseEnter={() => setActive(index)}
                    onMouseLeave={() => setActive(null)}
                  />
                ))}
              </span>
            )}
            </span>
            <span className="gx-image-zoom" aria-hidden="true"><ZoomIn size={14} /></span>
          </button>
          <figcaption>
            <span>{[mediaTypeLabel(asset?.mediaType, asset?.originalName), asset ? formatBytes(asset.sizeBytes) : null, dimensions ? `${dimensions.width} × ${dimensions.height}` : null].filter(Boolean).join(" · ")}</span>
            {asset && <a className="gx-link" href={url} download={asset.originalName}><Download size={12} />Download original</a>}
          </figcaption>
        </figure>
      ) : (
        <p className="gx-item-empty-line">The image file is not attached to this source.</p>
      )}
      <ContextNote text={file.context} />
      <section className="gx-ocr">
        <header>
          <h2 className="gx-section-title"><ScanText size={14} />Text in this image</h2>
          {allText.trim() && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => void copy()}>{copied ? <Check size={13} /> : <Copy size={13} />}{copied ? "Copied" : "Copy text"}</button>}
        </header>
        {regions.length > 0 ? (
          <ol className="gx-ocr-lines">
            {regions.map((region, index) => (
              <li key={index} className={index === active ? "is-active" : undefined} onMouseEnter={() => setActive(region.box ? index : null)} onMouseLeave={() => setActive(null)}>{region.text}</li>
            ))}
          </ol>
        ) : file.extracted.trim() ? (
          <PlainText text={file.extracted} />
        ) : (
          <p className="gx-item-empty-line">{structure.data && !["pending", "queued", "running"].includes(structure.data.processing.state)
            ? ocrUnavailable ? `No text was recognised (${file.ocrStatus}).` : "No text was recognised in this image."
            : "Looking for text in this image…"}</p>
        )}
      </section>
      {open && url && <Lightbox url={url} title={source.title} onClose={() => setOpen(false)} />}
    </div>
  );
}
