import { useEffect, useState } from "react";
import { sourceAssetUrl } from "../../api";
import type { FigureShape } from "./parseSlide";
import { Heading, layoutClass, Rich } from "./Rich";

/**
 * A figure from the material: the image as large as the slide has room for, the line about it
 * under it. The image is a managed asset, fetched as everywhere else; if it cannot be (the asset
 * is gone, the service is down) its own caption stands in a frame, so the slide still says what
 * it was meant to show.
 */
export function FigureSlide({ shape }: { shape: FigureShape }) {
  const [missing, setMissing] = useState(false);
  useEffect(() => setMissing(false), [shape.assetId]);
  return <div className={`outputs-s ${layoutClass(shape.layout)}`}>
    <Heading text={shape.title} />
    <figure className="outputs-s-picture">
      {missing
        ? <figcaption className="outputs-s-missing" role="img" aria-label={shape.alt}>{shape.alt || "The figure could not be loaded."}</figcaption>
        : <img src={sourceAssetUrl(shape.assetId)} alt={shape.alt} onError={() => setMissing(true)} />}
    </figure>
    <div className="outputs-s-after"><Rich text={shape.line} /></div>
  </div>;
}
