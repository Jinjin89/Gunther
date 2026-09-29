import { ChevronLeft, ChevronRight, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import { highlightRenderer } from "./highlight";

// react-pdf needs the worker set in the module that renders its components.
pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

const PAGE_GAP = 12;
const RENDER_MARGIN = "1600px 0px";

/** A PDF inside the app: every page in one scroll, opened on the cited page, with the cited words marked. */
export default function PdfViewer({ url, page, quote, title }: { url: string; page?: number | undefined; quote?: string | undefined; title: string }) {
  const target = Math.max(1, page ?? 1);
  const [pages, setPages] = useState(0);
  const [current, setCurrent] = useState(target);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [width, setWidth] = useState(0);
  const [ratio, setRatio] = useState(1.3);
  const [file, setFile] = useState<{ data: Uint8Array } | null>(null);
  const [near, setNear] = useState<ReadonlySet<number>>(new Set());
  const stage = useRef<HTMLDivElement>(null);
  const holders = useRef(new Map<number, HTMLDivElement>());
  const settled = useRef(false);
  // The app forbids eval; pdf.js has a slower path that needs none.
  const options = useMemo(() => ({ isEvalSupported: false }), []);
  const renderer = useMemo(() => (quote ? highlightRenderer(quote) : undefined), [quote]);

  useEffect(() => {
    setPages(0);
    setError(null);
    setNear(new Set());
    setCurrent(target);
    settled.current = false;
  }, [url, target, attempt]);

  // Fetched here, past the web view's cache: it may hold an earlier plain load of this
  // address that carries no CORS headers, which pdf.js's own request would then be refused.
  useEffect(() => {
    let active = true;
    setFile(null);
    fetch(url, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`The service answered ${response.status}.`);
        return response.arrayBuffer();
      })
      .then((buffer) => { if (active) setFile({ data: new Uint8Array(buffer) }); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "The file could not be fetched."); });
    return () => { active = false; };
  }, [url, attempt]);

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

  // Only pages near the viewport are drawn; the others keep their height as blank sheets.
  useEffect(() => {
    const node = stage.current;
    if (!node || !pages || typeof IntersectionObserver === "undefined") {
      if (pages) setNear(new Set(Array.from({ length: Math.min(pages, 12) }, (_, index) => index + 1)));
      return undefined;
    }
    const observer = new IntersectionObserver((entries) => {
      setNear((previous) => {
        const next = new Set(previous);
        for (const entry of entries) {
          const number = Number((entry.target as HTMLElement).dataset.page);
          if (entry.isIntersecting) next.add(number); else next.delete(number);
        }
        return next;
      });
    }, { root: node, rootMargin: RENDER_MARGIN });
    holders.current.forEach((holder) => observer.observe(holder));
    return () => observer.disconnect();
  }, [pages, width]);

  const reveal = useCallback((number: number, smooth = false) => {
    const node = stage.current;
    const holder = holders.current.get(number);
    if (node && holder) node.scrollTo({ top: holder.offsetTop - PAGE_GAP, behavior: smooth ? "smooth" : "auto" });
  }, []);

  // Land on the cited page, and keep it in place while the pages above settle to their real height.
  useEffect(() => {
    if (pages && width > 0 && !settled.current) reveal(Math.min(target, pages));
  }, [pages, width, ratio, target, reveal]);

  const onScroll = () => {
    const node = stage.current;
    if (!node) return;
    settled.current = true;
    let visible = 1;
    holders.current.forEach((holder, number) => {
      if (holder.offsetTop + holder.offsetHeight / 2 <= node.scrollTop + node.clientHeight / 3) visible = Math.max(visible, number);
    });
    setCurrent(visible);
  };

  const go = (next: number) => {
    settled.current = true;
    const number = Math.min(Math.max(1, next), pages || 1);
    setCurrent(number);
    reveal(number, true);
  };
  const retry = () => setAttempt((value) => value + 1);
  const holderHeight = Math.round(width * ratio);

  return <div className="gx-pdf">
    <div className="gx-pdf-bar">
      <button type="button" onClick={() => go(current - 1)} disabled={current <= 1} aria-label="Previous page"><ChevronLeft size={15} /></button>
      <span aria-live="polite">{pages ? `Page ${current} of ${pages}` : error ? "Not opened" : "Opening…"}</span>
      <button type="button" onClick={() => go(current + 1)} disabled={!pages || current >= pages} aria-label="Next page"><ChevronRight size={15} /></button>
    </div>
    <div className="gx-pdf-stage" ref={stage} onScroll={onScroll}>
      {error
        ? <div className="gx-pdf-error" role="alert">
          <p>This PDF could not be shown here.</p>
          <small>{error}</small>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={retry}><RotateCcw size={12} />Try again</button>
        </div>
        : !file
          ? <div className="gx-pdf-loading" aria-label={`Opening ${title}`} />
          : <Document key={`${url}:${attempt}`} file={file} options={options} loading={<div className="gx-pdf-loading" aria-label={`Opening ${title}`} />} onLoadSuccess={({ numPages }) => setPages(numPages)} onLoadError={(reason) => setError(reason?.message || "The file could not be read.")}>
          {width > 0 && Array.from({ length: pages }, (_, index) => index + 1).map((number) => (
            <div key={number} className="gx-pdf-sheet" data-page={number} ref={(node) => { if (node) holders.current.set(number, node); else holders.current.delete(number); }} style={{ minHeight: holderHeight }}>
              {near.has(number) && <Page
                pageNumber={number}
                width={width}
                renderAnnotationLayer={false}
                onLoadSuccess={(loaded) => { if (number === target) setRatio(loaded.originalHeight / loaded.originalWidth); }}
                {...(renderer && number === target ? { customTextRenderer: renderer } : {})}
              />}
            </div>
          ))}
        </Document>}
    </div>
  </div>;
}
