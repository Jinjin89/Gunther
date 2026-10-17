import { type CSSProperties } from "react";
import type { CardsShape } from "./parseSlide";
import { Frame, Rich } from "./Rich";

/** Parts that stand side by side: a grid of two or three columns, each part a name over a sentence. */
export function CardsSlide({ shape }: { shape: CardsShape }) {
  const count = shape.cards.length;
  // Two and four sit in two columns, so that four make a square; the rest in three.
  const columns = count === 2 || count === 4 ? 2 : 3;
  return <Frame layout={shape.layout} title={shape.title} lead={shape.lead} after={shape.after}>
    <ul className={`outputs-s-cards${count <= 3 ? " is-few" : ""}`} style={{ "--columns": columns } as CSSProperties}>
      {shape.cards.map((card, at) => <li key={at}>
        <div className="outputs-s-card-name"><Rich text={card.name} /></div>
        <div className="outputs-s-card-text"><Rich text={card.text} /></div>
      </li>)}
    </ul>
  </Frame>;
}
