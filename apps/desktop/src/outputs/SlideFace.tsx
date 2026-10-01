import { AnswerBody } from "../pages/AnswerBody";

/** What one slide shows. The deck, its thumbnails and the printed page draw the same face. */
export function SlideFace({ body, title, numbers, onCite }: {
  body: string;
  title: boolean;
  numbers: number[];
  onCite?: ((index: number) => void) | undefined;
}) {
  return <div className={`outputs-slide-face ${title ? "is-title" : ""}`}>
    <AnswerBody content={body} numbers={numbers} onCitation={onCite} />
  </div>;
}
