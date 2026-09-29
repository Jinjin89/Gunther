import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import { highlightRenderer } from "./highlight";

// react-pdf needs the worker set in the module that renders its components.
pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

/** A PDF inside the app, opened on the cited page, with the cited words marked. */
export default function PdfViewer({ url, page, quote, title }: { url: string; page?: number | undefined; quote?: string | undefined; title: string }) {
  const [pages, setPages] = useState(0);
  const [current, setCurrent] = useState(Math.max(1, page ?? 1));
  const [failed, setFailed] = useState(false);
  const [width, setWidth] = useState(0);
  const stage = useRef<HTMLDivElement>(null);
  // The app forbids eval; pdf.js has a slower path that needs none.
  const options = useMemo(() => ({ isEvalSupported: false }), []);
  useEffect(() => setCurrent(Math.max(1, page ?? 1)), [page, url]);
  useEffect(() => {
    const node = stage.current;
    if (!node) return undefined;
    const measure = () => setWidth(Math.max(0, Math.floor(node.clientWidth) - 24));
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const go = (next: number) => setCurrent(Math.min(Math.max(1, next), pages || 1));
  return <div className="gx-pdf">
    <div className="gx-pdf-bar">
      <button type="button" onClick={() => go(current - 1)} disabled={current <= 1} aria-label="Previous page"><ChevronLeft size={15} /></button>
      <span aria-live="polite">{pages ? `Page ${current} of ${pages}` : "Opening…"}</span>
      <button type="button" onClick={() => go(current + 1)} disabled={!pages || current >= pages} aria-label="Next page"><ChevronRight size={15} /></button>
    </div>
    <div className="gx-pdf-stage" ref={stage}>
      {failed
        ? <p className="gx-pdf-error" role="alert">This PDF could not be shown here.</p>
        : <Document file={url} options={options} loading={<div className="gx-pdf-loading" aria-label={`Opening ${title}`} />} onLoadSuccess={({ numPages }) => { setPages(numPages); setCurrent((value) => Math.min(value, numPages)); }} onLoadError={() => setFailed(true)}>
          {width > 0 && <Page pageNumber={current} width={width} renderAnnotationLayer={false} {...(quote ? { customTextRenderer: highlightRenderer(quote) } : {})} />}
        </Document>}
    </div>
  </div>;
}
