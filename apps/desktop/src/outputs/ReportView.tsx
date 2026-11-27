import { ChevronDown, ChevronLeft, ChevronRight, MessageSquarePlus } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CitationDetails } from "../components/evidence/CitationButton";
import { AnswerBody } from "../pages/AnswerBody";
import { OutputPartTools, SectionStatus, outputCitations, type OutputContentProps } from "./OutputParts";
import { ReportBlocks } from "./deck/ReportV2";
import { parseReport } from "./deck/parseReport";
import { visible } from "./deck/units";

interface Picked { section: number; quote: string; x: number; y: number }
const QUOTE_LIMIT = 500;

function languageTag(language: string | null | undefined) {
  if (!language) return undefined;
  try {
    return Intl.DateTimeFormat.supportedLocalesOf([language.trim()])[0];
  } catch {
    return undefined;
  }
}

function readerDimensions(pane: HTMLElement, body: HTMLElement) {
  return [pane.clientWidth, pane.clientHeight, body.offsetWidth, body.offsetHeight,
    ...Array.from(body.querySelectorAll<HTMLElement>("[data-section]"), (section) => section.offsetHeight)];
}

/** One report body for the reader, edit preview and print. Navigation is local to the saved version. */
export function ReportView(props: OutputContentProps) {
  return <Report key={props.artifact.id} {...props} />;
}

function Report({ artifact, type, presentation = "read", onCite, onAsk, onPart, goTo, changed }: OutputContentProps) {
  const recipe = type?.shape === "report" ? type.id : "report";
  const reading = presentation === "read";
  const faq = recipe === "faq" && reading;
  const study = recipe === "study_guide";
  const timeline = recipe === "timeline";
  const doc = useMemo(() => parseReport(artifact.content), [artifact.content]);
  const entries = useMemo(() => doc.sections.flatMap(({ heading }, position) => heading
    ? [{ position, label: visible(heading) || heading }] : []), [doc]);
  const references = useMemo(() => outputCitations({ artifact, onCite, presentation }), [artifact, onCite, presentation]);
  const body = useRef<HTMLDivElement>(null);
  const id = useId();
  const [expanded, setExpanded] = useState(() => new Set([entries[0]?.position ?? 0]));
  const [current, setCurrent] = useState<number | null>(null);
  const [pending, setPending] = useState<{ index: number } | null>(null);
  const [picked, setPicked] = useState<Picked | null>(null);
  const jumpLocation = useRef<{ scrollTop: number; dimensions: number[] } | null>(null);
  const currentEntry = Math.max(0, entries.findIndex(({ position }) => position === current));
  const allExpanded = entries.every(({ position }) => expanded.has(position));
  const point = reading ? onAsk : undefined;

  const jumpTo = useCallback((index: number) => {
    if (faq) setExpanded((old) => old.has(index) ? old : new Set([...old, index]));
    setPending({ index });
  }, [faq]);

  useEffect(() => {
    if (reading && goTo) jumpTo(goTo.index);
  }, [goTo?.index, goTo?.nonce, jumpTo, reading]);

  useLayoutEffect(() => {
    const box = body.current;
    if (!pending || !box) return;
    const section = box.querySelector<HTMLElement>(`[data-section="${pending.index}"]`);
    if (section) {
      const focus = section.querySelector<HTMLButtonElement>(".rp-question") ?? section;
      focus.focus({ preventScroll: true });
      section.scrollIntoView?.({ block: "start", behavior: "instant" });
      const pane = section.closest<HTMLElement>(".library-middle-page");
      jumpLocation.current = pane ? { scrollTop: pane.scrollTop, dimensions: readerDimensions(pane, box) } : null;
      setCurrent(pending.index);
      onPart?.(pending.index);
    }
    setPending(null);
  }, [pending, onPart]);

  useEffect(() => {
    const box = body.current;
    if (!reading || !box || doc.sections.length === 0) return;
    const scroller = box.closest<HTMLElement>(".library-middle-page");
    const bar = scroller?.querySelector(".library-middle-bar");
    const navigation = (study || timeline) ? box.querySelector<HTMLElement>(".rp-navigation") : null;
    const sections = [...box.querySelectorAll<HTMLElement>("[data-section]")];
    const update = () => {
      // Near the end the jump can reach its target below the viewport top. Keep that
      // target until the user scrolls or the layout that determined it changes.
      const jump = jumpLocation.current;
      if (jump && scroller && jump.scrollTop === scroller.scrollTop) {
        const dimensions = readerDimensions(scroller, box);
        if (dimensions.length === jump.dimensions.length && dimensions.every((size, index) => size === jump.dimensions[index])) return;
      }
      jumpLocation.current = null;
      let top = bar?.getBoundingClientRect().bottom ?? scroller?.getBoundingClientRect().top ?? 0;
      const controls = navigation?.getBoundingClientRect();
      if (controls && controls.top <= top + 1) top = Math.max(top, controls.bottom);
      const target = sections.find((section) => section.getBoundingClientRect().bottom > top)
        ?? (box.getBoundingClientRect().height === 0 ? sections[0] : sections.at(-1));
      const position = target ? Number(target.dataset.section) : null;
      setCurrent(position);
      onPart?.(position);
    };
    const layoutChanged = () => {
      if (navigation) box.style.setProperty("--rp-navigation-height", `${navigation.offsetHeight}px`);
      update();
    };
    const resize = typeof ResizeObserver !== "undefined" ? new ResizeObserver(layoutChanged) : null;
    if (resize) {
      for (const element of [scroller, box, navigation, ...sections]) if (element) resize.observe(element);
    }
    layoutChanged();
    window.addEventListener("scroll", update, true);
    window.addEventListener("resize", layoutChanged);
    return () => {
      window.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", layoutChanged);
      resize?.disconnect();
      box.style.removeProperty("--rp-navigation-height");
    };
  }, [doc, onPart, reading, study, timeline]);

  useEffect(() => {
    if (!point) return;
    const look = () => {
      const selection = window.getSelection();
      const box = body.current;
      if (!selection || selection.isCollapsed || selection.rangeCount === 0 || !box) { setPicked(null); return; }
      const owner = (node: Node | null) => (node instanceof Element ? node : node?.parentElement)?.closest<HTMLElement>("[data-section]") ?? null;
      const first = owner(selection.anchorNode);
      const last = owner(selection.focusNode);
      const quote = selection.toString().replace(/\s+/g, " ").trim();
      if (!first || first !== last || !box.contains(first) || !quote) { setPicked(null); return; }
      const rect = selection.getRangeAt(0).getBoundingClientRect?.();
      setPicked({ section: Number(first.dataset.section), quote: quote.slice(0, QUOTE_LIMIT), x: rect?.right ?? 0, y: rect?.bottom ?? 0 });
    };
    document.addEventListener("selectionchange", look);
    return () => document.removeEventListener("selectionchange", look);
  }, [point]);

  const anchor = (position: number) => `${id}-section-${position}`;
  const renderSection = (section: (typeof doc.sections)[number], index: number) => {
    const headingId = `${anchor(index)}-heading`;
    const contentId = `${anchor(index)}-body`;
    const disclosure = faq && Boolean(section.heading);
    const open = !disclosure || expanded.has(index);
    const topic = study ? entries.findIndex(({ position }) => position === index) : -1;
    return <section key={index} className={`rp-part${section.summary ? " is-summary" : ""}`} id={anchor(index)}
      aria-labelledby={section.heading ? headingId : undefined} data-section={index} tabIndex={reading ? -1 : undefined}>
      {topic >= 0 && <p className="rp-topic-position"><span className="rp-position">Topic {topic + 1} of {entries.length}</span></p>}
      <div className="rp-part-head">
        {section.heading && <div className={`rp-heading${disclosure ? " has-disclosure" : ""}`}>
          {disclosure && <button className="rp-question" type="button" aria-labelledby={headingId} aria-expanded={open} aria-controls={contentId}
            onClick={() => setExpanded((old) => {
              const next = new Set(old);
              if (next.has(index)) next.delete(index); else next.add(index);
              return next;
            })}><ChevronRight size={16} aria-hidden="true" /></button>}
          <div id={headingId}><AnswerBody content={`## ${section.heading}`} {...references} /></div>
        </div>}
        {reading && <OutputPartTools position={index} onAsk={point} changed={changed} label="Refine section" />}
      </div>
      <div id={contentId} className={`${section.summary ? "rp-summary" : "rp-prose"}${disclosure ? " rp-answer" : ""}`} hidden={!open}>
        <ReportBlocks blocks={section.blocks} presentation={presentation} {...references} />
      </div>
      {presentation !== "print" && <div className="rp-part-status"><SectionStatus info={artifact.sections[index]} /></div>}
    </section>;
  };

  return <CitationDetails.Provider value={artifact.citations}>
    <div ref={body} className={presentation === "print" ? "outputs-body rp" : "outputs-body rp reader-track"}
      data-track={presentation === "print" ? undefined : "report"} data-recipe={recipe} data-presentation={presentation}
      lang={languageTag(artifact.provenance?.language)}>
      {doc.preamble.length > 0 && <div className={doc.sections.length ? "rp-preamble" : "rp-prose"}>
        <ReportBlocks blocks={doc.preamble} presentation={presentation} {...references} />
      </div>}
      {doc.preamble.length === 0 && doc.sections.length === 0 && <p className="rp-empty">This version has no report text.</p>}
      {reading && entries.length > 1 && <nav className="rp-navigation" aria-label={faq ? "Questions" : study ? "Topics" : "Contents"}>
        {faq ? <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm"
          onClick={() => setExpanded(new Set(allExpanded ? [] : entries.map(({ position }) => position)))}>
          {allExpanded ? "Collapse all" : "Expand all"}
        </button> : <details className="rp-contents">
          <summary>{study ? "Topics" : "Contents"}<ChevronDown size={16} aria-hidden="true" /></summary>
          <ol>{entries.map(({ position, label }, index) => <li key={position}>
            <a href={`#${anchor(position)}`} aria-current={index === currentEntry ? "location" : undefined} onClick={(event) => {
              event.preventDefault();
              const details = event.currentTarget.closest("details");
              if (details) details.open = false;
              jumpTo(position);
            }}>{label}</a>
          </li>)}</ol>
        </details>}
        {study && <div className="rp-topic-controls">
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={currentEntry === 0}
            onClick={() => jumpTo(entries[currentEntry - 1]!.position)}><ChevronLeft size={16} aria-hidden="true" />Previous topic</button>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={currentEntry === entries.length - 1}
            onClick={() => jumpTo(entries[currentEntry + 1]!.position)}>Next topic<ChevronRight size={16} aria-hidden="true" /></button>
        </div>}
        {timeline && <p className="rp-position">Event {currentEntry + 1} of {entries.length}</p>}
      </nav>}
      {timeline && doc.sections.length > 0 ? <ol className="rp-events" role="list">
        {doc.sections.map((section, index) => <li key={index}>{renderSection(section, index)}</li>)}
      </ol> : doc.sections.map(renderSection)}
      {picked && point && createPortal(
        <button type="button" className="outputs-ask-selection" style={{ left: Math.max(8, Math.min(picked.x, window.innerWidth - 120)), top: Math.min(picked.y + 6, window.innerHeight - 40) }}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => { point(picked.section, picked.quote); window.getSelection()?.removeAllRanges(); setPicked(null); }}>
          <MessageSquarePlus size={12} />Ask about this
        </button>, document.body,
      )}
    </div>
  </CitationDetails.Provider>;
}
