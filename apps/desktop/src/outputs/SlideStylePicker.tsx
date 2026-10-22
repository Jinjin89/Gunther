import { Check, Palette } from "lucide-react";
import { type ComponentProps, type CSSProperties, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { THEMES, type SlideTheme } from "./sections";
import { SLIDE_THEMES } from "./slides/themes";
import { themeAccentColour } from "./slides/accent";
import { SlideFace } from "./SlideFace";

type PreviewSlide = Pick<ComponentProps<typeof SlideFace>, "body" | "title" | "layout" | "composition" | "scene">;

function Miniature(props: ComponentProps<typeof SlideFace>) {
  const holder = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(0.1);
  useLayoutEffect(() => {
    const element = holder.current;
    if (!element) return;
    const measure = () => { const width = element.getBoundingClientRect().width; if (width > 0) setScale(width / 1280); };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    window.addEventListener("resize", measure);
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure); };
  }, []);
  return <div ref={holder} className="outputs-theme-real-stage" style={{ "--preview-scale": scale } as CSSProperties}><SlideFace {...props}/></div>;
}

/** A small, visual theme chooser. Samples use the same tokens as SlideFace. */
export function SlideStylePicker({ value, accent, disabled, onSelect, samples = [], numbers = [], sources = [], designVersion = 0 }: {
  value: SlideTheme;
  accent: string | null;
  disabled: boolean;
  onSelect: (theme: SlideTheme) => void;
  samples?: PreviewSlide[];
  numbers?: number[];
  sources?: string[];
  designVersion?: number;
}) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ top: 8, right: 8, maxHeight: 260 });
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const close = () => { setOpen(false); trigger.current?.focus(); };
  useLayoutEffect(() => {
    if (!open || !trigger.current) return;
    const positionPanel = () => {
      const anchor = trigger.current?.getBoundingClientRect();
      const panelElement = panel.current;
      if (!anchor || !panelElement) return;
      const width = Math.max(180, Math.min(360, window.innerWidth - 16));
      const maxHeight = Math.max(80, Math.min(320, window.innerHeight - 16));
      panelElement.style.width = `${width}px`;
      panelElement.style.maxHeight = `${maxHeight}px`;
      const height = Math.min(panelElement.scrollHeight, maxHeight);
      const below = window.innerHeight - anchor.bottom - 5;
      const above = anchor.top - 5;
      const top = height <= below ? anchor.bottom + 5
        : height <= above ? anchor.top - height - 5
          : Math.max(8, Math.min(anchor.bottom + 5, window.innerHeight - height - 8));
      const right = Math.max(8, Math.min(window.innerWidth - width - 8, window.innerWidth - anchor.right));
      setPosition({ top, right, maxHeight });
    };
    positionPanel();
    window.addEventListener("resize", positionPanel);
    panel.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => window.removeEventListener("resize", positionPanel);
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!panel.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) setOpen(false); };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); } };
    const scroll = () => setOpen(false);
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", key, true); document.addEventListener("scroll", scroll, true);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", key, true); document.removeEventListener("scroll", scroll, true); };
  }, [open]);
  return <>
    <button ref={trigger} type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-label="Slide style" aria-haspopup="dialog" aria-expanded={open} disabled={disabled} onClick={() => setOpen((shown) => !shown)}><Palette size={13} />Style</button>
    {open && createPortal(<div ref={panel} className="outputs-theme-picker" role="dialog" aria-label="Choose slide style" style={{ position: "fixed", top: position.top, right: position.right, maxHeight: position.maxHeight }}>
      {THEMES.map((theme) => {
        const sample = SLIDE_THEMES[theme];
        return <div key={theme} className={`outputs-theme-choice${value === theme ? " is-selected" : ""}`} onClick={event => { if (!(event.target as HTMLElement).closest("button")) { close(); onSelect(theme); } }}>
          <button type="button" className="outputs-theme-select" aria-pressed={value === theme} onClick={() => { close(); onSelect(theme); }}>
          <span className="outputs-theme-choice-main">
            {!samples.length && <span className={`outputs-theme-sample theme-${theme}`} style={{ background: sample.paper, color: sample.ink, borderColor: sample.rule }} aria-hidden="true">
              <i style={{ background: themeAccentColour(accent, theme) }} />
              <b style={{ fontFamily: `var(--gx-font-${sample.headingFont})`, color: sample.ink }}>Aa</b>
              <em style={{ background: themeAccentColour(accent, theme) }} />
              <small style={{ background: sample.rule }} />
            </span>}
            <span className="outputs-theme-copy"><strong>{sample.label}</strong><small>{sample.description}</small></span>
          </span>
          {value === theme && <Check size={14} aria-label="Selected" />}
          </button>
          {samples.length > 0 && <div className="outputs-theme-real-samples" aria-hidden="true" inert>
            {samples.map((slide,index) => <Miniature key={index} {...slide} theme={theme} accent={accent} designVersion={theme === "classic" ? designVersion : 1} numbers={numbers} sources={sources}/>)}
          </div>}
        </div>;
      })}
    </div>, document.body)}
  </>;
}
