import { Download, Maximize2 } from "lucide-react";
import { createContext, useContext, useEffect, useState } from "react";
import { saveBlob } from "../../../saveBlob";
import { ImageLightbox } from "../../ImageLightbox";
import { CodeBlock, CopyCodeButton, PrintCodeContext, type CodeBlockProps } from "./CodeBlock";
import { renderMermaid } from "./renderMermaid";
import { sanitizeSvg } from "./svg";

type Drawing = { code: string; mermaid: boolean } & (
  { picture: { blob: Blob; url: string }; error?: never } |
  { picture?: never; error: string }
);

const FILENAME = "diagram.svg";
export const PictureCardsContext = createContext(true);

export function PictureCard({ code, language, closed }: CodeBlockProps) {
  const printing = useContext(PrintCodeContext);
  const showPictures = useContext(PictureCardsContext) && !printing;
  const mermaid = language?.toLowerCase() === "mermaid";
  const title = mermaid ? "Mermaid diagram" : "SVG diagram";
  const [drawing, setDrawing] = useState<Drawing>();
  const [showCode, setShowCode] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    setDrawing(undefined);
    if (!closed || !showPictures) return;

    let active = true;
    let url: string | undefined;
    const draw = async () => {
      try {
        const source = mermaid ? await renderMermaid(code) : code;
        if (!active) return;
        const blob = new Blob([sanitizeSvg(source)], { type: "image/svg+xml" });
        url = URL.createObjectURL(blob);
        setDrawing({ code, mermaid, picture: { blob, url } });
      } catch (error) {
        if (!active) return;
        const message = error && typeof error === "object" && "message" in error ? error.message : error;
        const firstLine = String(message).split(/\r\n?|\n/, 1)[0];
        setDrawing({ code, mermaid, error: firstLine || "Unknown error" });
      }
    };
    void draw();
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [code, closed, mermaid, showPictures]);

  if (!showPictures) return <CodeBlock code={code} language={language} closed={closed} />;

  const current = closed && drawing?.code === code && drawing.mermaid === mermaid ? drawing : undefined;
  const picture = current?.picture;
  const showingPicture = picture && !showCode;

  return (
    <figure className="gx-md-picture">
      <figcaption className="gx-md-picture-toolbar" onClick={(event) => event.stopPropagation()}>
        <div className="gx-md-picture-switch" role="group" aria-label={`${title} view`}>
          <button type="button" aria-pressed={!!showingPicture} disabled={!picture} onClick={() => setShowCode(false)}>Picture</button>
          <button type="button" aria-pressed={!showingPicture} onClick={() => setShowCode(true)}>Code</button>
        </div>
        <div className="gx-md-picture-actions">
          <CopyCodeButton code={code} label="Copy source" />
          <button type="button" aria-label="Save SVG" title="Save SVG" disabled={!picture} onClick={() => { if (picture) saveBlob(picture.blob, FILENAME); }}>
            <Download size={12} />
          </button>
          <button type="button" aria-label="Expand" title="Expand" disabled={!picture} onClick={() => setExpanded(true)}>
            <Maximize2 size={12} />
          </button>
        </div>
      </figcaption>
      {!picture && <div className="gx-md-picture-status" role="status">
        {current?.error ? `Couldn't draw this ${mermaid ? "diagram" : "picture"}: ${current.error}` : "Drawing…"}
      </div>}
      {showingPicture ? <div className="gx-md-picture-canvas"><img src={picture.url} alt={title} onLoad={({ currentTarget: image }) => {
        image.style.setProperty("--gx-picture-width", `${image.naturalWidth}px`);
        image.style.setProperty("--gx-picture-height", `${image.naturalHeight}px`);
      }} /></div>
        : <CodeBlock code={code} language={language} closed={closed} showHeader={false} />}
      {expanded && picture && <ImageLightbox url={picture.url} title={title} filename={FILENAME}
        imageClassName="gx-lightbox-picture" onClose={() => setExpanded(false)} />}
    </figure>
  );
}
