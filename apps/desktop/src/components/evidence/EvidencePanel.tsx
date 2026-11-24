import type { ConversationCitation } from "@gunther/contracts";
import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useDragWidth } from "../../hooks/useDragWidth";
import { useEscape } from "../../shortcuts/shortcuts";
import { EvidenceDetail } from "./EvidenceDetail";

const WIDTH_KEY = "gunther:evidence-width";
const MIN_WIDTH = 340;
const DEFAULT_WIDTH = 460;
const OPENS_EVIDENCE = '[data-opens-evidence], button[aria-label^="Inspect citation"], button[aria-label^="Show evidence"]';
const maxWidth = () => Math.max(MIN_WIDTH, Math.min(960, Math.round(window.innerWidth * 0.8)));

/** The standalone evidence panel used outside the Library workspace. */
export function EvidencePanel({ citation: opened, index, passages, onClose, onOpenSource }: {
  citation: ConversationCitation;
  index?: number | undefined;
  passages?: ConversationCitation[] | undefined;
  onClose: () => void;
  onOpenSource?: ((sourceId: string) => void) | undefined;
}) {
  const [picked, setPicked] = useState<{ openedId: string; id: string }>();
  const citation = (picked?.openedId === opened.id && passages?.find((item) => item.id === picked.id)) || opened;
  const close = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  const { width, handle } = useDragWidth({ storageKey: WIDTH_KEY, min: MIN_WIDTH, max: maxWidth, fallback: DEFAULT_WIDTH });
  useEscape(onClose);
  const latestClose = useRef(onClose);
  latestClose.current = onClose;
  useEffect(() => {
    const away = (event: PointerEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target || panel.current?.contains(target)
        || event.composedPath().some((node) => node instanceof Element && node.matches(OPENS_EVIDENCE))) return;
      latestClose.current();
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, []);
  useEffect(() => { close.current?.focus(); }, [opened.id]);
  return createPortal(
    <aside ref={panel} className="gx-evidence" role="complementary" aria-label="Evidence" style={{ width: `min(${width}px, 100vw)` }}>
      <div className="gx-evidence-resize" role="separator" aria-orientation="vertical" aria-label="Resize evidence" aria-valuenow={width} aria-valuemin={MIN_WIDTH} aria-valuemax={maxWidth()} tabIndex={0} title="Drag to resize · double-click to reset" {...handle} />
      <EvidenceDetail citation={citation} index={index} passages={passages} onPassage={(at) => {
        const next = passages?.[at];
        if (next) setPicked({ openedId: opened.id, id: next.id });
      }} onOpenSource={onOpenSource} headerAction={<button ref={close} type="button" className="gx-icon-button" onClick={onClose} aria-label="Close evidence" title="Close  Esc"><X size={15} /></button>} />
    </aside>,
    document.body,
  );
}
