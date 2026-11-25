import { Check, Copy } from "lucide-react";
import { Fragment, createContext, useContext, useEffect, useId, useState, type CSSProperties } from "react";
import type { ThemedToken } from "shiki/types";

export interface CodeBlockProps {
  code: string;
  language: string | null;
  closed: boolean;
}

const FOLD_AFTER = 25;
const PREVIEW_LINES = 20;
export const PrintCodeContext = createContext(false);

export function CopyCodeButton({ code, label = "Copy code" }: { code: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1_400);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
    } catch {
      // Clipboard access can be denied; the code stays selectable.
    }
  };
  const title = copied ? "Copied" : label;
  return <button type="button" onClick={(event) => { event.stopPropagation(); void copy(); }} aria-label={title} title={title}>
    {copied ? <Check size={12} /> : <Copy size={12} />}
  </button>;
}

export function CodeBlock({ code, language, closed, showHeader = true }: CodeBlockProps & { showHeader?: boolean }) {
  const printing = useContext(PrintCodeContext);
  const [expanded, setExpanded] = useState(false);
  const [highlight, setHighlight] = useState<{ code: string; language: string; tokens: ThemedToken[][] }>();
  const codeId = useId();

  useEffect(() => {
    if (!closed || !language || !code) return;
    let active = true;
    void import("./highlightCode").then(({ highlightCode }) => active ? highlightCode(code, language) : null).then((tokens) => {
      if (active && tokens) setHighlight({ code, language, tokens });
    }).catch(() => {
      // Keep the code readable when a grammar or the highlighter cannot load.
    });
    return () => { active = false; };
  }, [closed, code, language]);

  const breaks = [...code.matchAll(/\r\n?|\n/g)];
  const lineCount = breaks.length + 1;
  const canFold = !printing && closed && lineCount > FOLD_AFTER;
  const folded = canFold && !expanded;
  const tokens = closed && highlight?.code === code && highlight.language === language ? highlight.tokens : undefined;
  const text = folded ? code.slice(0, breaks[PREVIEW_LINES - 1]?.index) : code;

  return (
    <figure className="gx-md-code">
      {!printing && showHeader && <figcaption>
        <span>{language || "Code"}</span>
        <CopyCodeButton code={code} />
      </figcaption>}
      <pre id={codeId}><code>{tokens?.length === lineCount
        ? tokens.slice(0, folded ? PREVIEW_LINES : lineCount).map((line, index) => <Fragment key={index}>
          {breaks[index - 1]?.[0]}
          {line.map((token) => <span key={token.offset} style={token.htmlStyle as CSSProperties}>{token.content}</span>)}
        </Fragment>)
        : text}</code></pre>
      {canFold && <button type="button" className="gx-md-code-toggle" aria-controls={codeId} aria-expanded={expanded} onClick={(event) => {
        event.stopPropagation();
        setExpanded(!expanded);
      }}>{folded ? `Show all ${lineCount} lines` : "Show fewer lines"}</button>}
    </figure>
  );
}
