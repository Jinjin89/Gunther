import { Fragment, type ReactNode } from "react";
import { useSlide } from "../slides/Rich";
import { scan } from "./parseDeck";
import { numberPieces, tokens, type Token } from "./inlineTokens";
import { sourceAssetUrl } from "../../api";

function Cite({ token, collapse }: { token: Extract<Token, { t: "cite" }>; collapse: boolean }) {
  const { numbers, onCite } = useSlide();
  if (token.unverified) return <sup className="dk-cite is-unverified" title="From the model's own knowledge; no source was found for it">?</sup>;
  if (collapse && token.n.length >= 3) {
    const more = token.n.slice(2);
    return <sup className="dk-cite">{token.n.slice(0, 2).map((n, at) => <Fragment key={at}>{at > 0 && ","}{onCite
      ? <button type="button" onClick={(event) => { event.stopPropagation(); onCite(numbers.indexOf(n)); }} aria-label={`Inspect citation ${n}`}>{n}</button>
      : <span>{n}</span>}</Fragment>)}<span title={`Also ${more.join(", ")}`}> +{more.length}</span></sup>;
  }
  return <sup className="dk-cite">{token.n.map((n, at) => <Fragment key={at}>{at > 0 && ","}{onCite
    ? <button type="button" onClick={(event) => { event.stopPropagation(); onCite(numbers.indexOf(n)); }} aria-label={`Inspect citation ${n}`}>{n}</button>
    : <span>{n}</span>}</Fragment>)}</sup>;
}

/** Inline Markdown as React: **bold**, *italic*, `code`, and citation marks as small superscripts that open their evidence (as AnswerBody's do). */
function render(raw: string, collapse = false): ReactNode[] {
  return tokens(raw).map((token, at) => {
    switch (token.t) {
      case "text": return <Fragment key={at}>{numberPieces(token.v).map((piece, index) => piece.keep ? <span key={index} className="dk-nb">{piece.text}</span> : piece.text)}</Fragment>;
      case "bold": return <strong key={at}>{render(token.v, collapse)}</strong>;
      case "em": return <em key={at}>{render(token.v, collapse)}</em>;
      case "code": return <code key={at}>{token.v}</code>;
      case "cite": return <Cite key={at} token={token} collapse={collapse} />;
    }
  });
}

/** Inline Markdown. A line break in the slice is a line break on the slide. */
export function Inline({ text, collapse = false }: { text: string | null | undefined; collapse?: boolean }) {
  if (!text) return null;
  const parts = text.split("\n");
  return <>{parts.map((part, at) => <Fragment key={at}>{at > 0 && <br />}{render(part, collapse)}</Fragment>)}</>;
}

/** Markdown that did not take its layout's grammar: drawn as plain blocks, nothing dropped. */
export function Prose({ markdown, paragraphs = false }: { markdown: string; paragraphs?: boolean }) {
  const blocks = scan(markdown, { paragraphs });
  return <div className="dk-prose">{blocks.map((block, at) => {
    switch (block.t) {
      case "line": return <p key={at}><Inline text={block.text} /></p>;
      case "h3": return <h3 key={at}><Inline text={block.text} /></h3>;
      case "quote": return <blockquote key={at}><Inline text={block.text} /></blockquote>;
      case "image": return <img key={at} src={sourceAssetUrl(block.id)} alt={block.alt} />;
      case "items": {
        const List = block.ordered ? "ol" : "ul";
        return <List key={at}>{block.items.map((item, index) => <li key={index}><Inline text={item} /></li>)}</List>;
      }
      case "table": {
        const [head, ...rows] = block.rows;
        return <table key={at}><thead><tr>{head?.map((cell, index) => <th key={index}><Inline text={cell} /></th>)}</tr></thead>
          <tbody>{rows.map((row, index) => <tr key={index}>{row.map((cell, cellAt) => <td key={cellAt}><Inline text={cell} /></td>)}</tr>)}</tbody></table>;
      }
    }
  })}</div>;
}
