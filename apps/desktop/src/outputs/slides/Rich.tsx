import { createContext, memo, type ReactNode, useContext } from "react";
import { AnswerBody } from "../../pages/AnswerBody";

/** What every part of a slide needs to draw a citation. */
export interface SlideContextValue {
  numbers: number[];
  /** What each citation names, in the order of `numbers`: the title and place of its source. */
  sources: string[];
  /** Opens the evidence of a citation. Only the deck on the page has it (not Present, the strip or the printed page). */
  onCite: ((index: number) => void) | undefined;
}

export const SlideContext = createContext<SlideContextValue>({ numbers: [], sources: [], onCite: undefined });
export const useSlide = () => useContext(SlideContext);

// A citation: [3], [1, 2], [?] and the Checker's [i:3], [p:3], [d:3].
const CITATION = /[ \t]+(?=\[(?:[ipd]:)?\d+(?:\s*[,，、]\s*\d+)*\]|\[\?\])/g;

/** The text with the space before each citation made a no-break space, so a number never starts a line of its own. */
export const tight = (text: string) => text.replace(CITATION, "\u00a0");

/**
 * A piece of the slide's Markdown. AnswerBody draws it, so a citation is a number that opens its
 * evidence. It is drawn again only when its text or the deck's citations change: a chart changes
 * when a mark is pointed at, and drawing the words again would replace the citation buttons under
 * the pointer.
 */
export const Rich = memo(function Rich({ text }: { text: string | null | undefined }) {
  const { numbers, onCite } = useSlide();
  return text ? <AnswerBody content={tight(text)} numbers={numbers} onCitation={onCite} /> : null;
});

/** The slide's heading, drawn as a Markdown heading is in every slide (an h1 for the title, an h2 for the rest). */
export function Heading({ text, level = 2 }: { text: string | null; level?: 1 | 2 }) {
  return <Rich text={text ? `${"#".repeat(level)} ${text}` : null} />;
}

/** The layout's name as a modifier of its block: big_number gives is-big-number. */
export const layoutClass = (layout: string) => `is-${layout.replaceAll("_", "-")}`;

/**
 * A layout's frame: the heading, the words before what the layout draws, what it draws, and the
 * words after it. The block is as tall as its content (never stretched to the slide), so what is
 * too long for the slide makes it taller than the slide, which is how SlideFace finds out that the
 * slide must be drawn smaller.
 */
export function Frame({ layout, title, lead, after, children }: {
  layout: string;
  title: string | null;
  lead?: string | null | undefined;
  after?: string | null | undefined;
  children: ReactNode;
}) {
  return <div className={`outputs-s ${layoutClass(layout)}`}>
    <Heading text={title} />
    {lead && <div className="outputs-s-lead"><Rich text={lead} /></div>}
    {children}
    {after && <div className="outputs-s-after"><Rich text={after} /></div>}
  </div>;
}
