import { useEffect, useState } from "react";

export function MathFormula({ tex, displayMode = false }: { tex: string; displayMode?: boolean }) {
  const [rendered, setRendered] = useState<{ tex: string; displayMode: boolean; html: string | null }>();

  useEffect(() => {
    let active = true;
    void Promise.all([import("katex"), import("katex/dist/katex.min.css")]).then(([{ renderToString }]) => {
      if (!active) return;
      const html = renderToString(tex, {
        displayMode,
        throwOnError: false,
        trust: false,
        strict: "ignore",
        output: "htmlAndMathml",
      });
      setRendered({ tex, displayMode, html });
    }).catch(() => {
      // Keep the original TeX readable if the renderer or stylesheet cannot load.
      if (active) setRendered({ tex, displayMode, html: null });
    });
    return () => { active = false; };
  }, [tex, displayMode]);

  const html = rendered?.tex === tex && rendered.displayMode === displayMode ? rendered.html : undefined;
  const state = html === undefined ? "pending" : html === null ? "failed" : "ready";
  // Only KaTeX-generated markup is inserted here. trust: false blocks commands that can add HTML or load resources.
  const content = html == null
    ? <code className="gx-md-inline-code">{tex}</code>
    : <span dangerouslySetInnerHTML={{ __html: html }} />;
  return displayMode
    ? <div className="gx-md-math-display" role="region" aria-label="Equation" tabIndex={0} data-equation-state={state}>{content}</div>
    : <span data-equation-state={state}>{content}</span>;
}
