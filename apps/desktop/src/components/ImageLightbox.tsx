import { Download, Maximize2, Minimize2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useEscape } from "../shortcuts/shortcuts";

interface ImageLightboxProps {
  url: string;
  title: string;
  onClose: () => void;
  filename?: string;
  imageClassName?: string;
}

export function ImageLightbox({ url, title, onClose, filename, imageClassName }: ImageLightboxProps) {
  const [actualSize, setActualSize] = useState(false);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEscape(onClose);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeButton.current?.focus();
    return () => previous?.focus?.();
  }, []);
  const zoomLabel = actualSize ? "Fit to window" : "Actual size";
  const toggleSize = () => setActualSize((value) => !value);
  return createPortal(
    <div className="gx-lightbox" role="dialog" aria-modal="true" aria-label={title}
      onClick={(event) => event.stopPropagation()}
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="gx-lightbox-bar">
        <span>{title}</span>
        <button type="button" onClick={toggleSize} aria-label={zoomLabel} title={zoomLabel}>
          {actualSize ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
        </button>
        <a href={url} download={filename ?? true} aria-label="Download original" title="Download original"><Download size={15} /></a>
        <button ref={closeButton} type="button" onClick={onClose} aria-label="Close" title="Close  Esc"><X size={16} /></button>
      </div>
      <div className={`gx-lightbox-stage ${actualSize ? "is-actual" : ""}`} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
        <img src={url} alt={title} className={imageClassName} onClick={toggleSize} />
      </div>
    </div>,
    document.body,
  );
}
