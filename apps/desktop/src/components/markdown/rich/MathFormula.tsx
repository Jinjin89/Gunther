import { useEffect, useState } from "react";

export function MathFormula({ tex, displayMode = false }: { tex: string; displayMode?: boolean }) {
  const [rendered, setRendered] = useState<{ tex: string; displayMode: boolean; html: string }>();

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
    });
    return () => { active = false; };
  }, [tex, displayMode]);

  const html = rendered?.tex === tex && rendered.displayMode === displayMode ? rendered.html : undefined;
  // Only KaTeX-generated markup is inserted here. trust: false blocks commands that can add HTML or load resources.
  const content = html === undefined
    ? <code className="gx-md-inline-code">{tex}</code>
    : <span dangerouslySetInnerHTML={{ __html: html }} />;
  return displayMode ? <div className="gx-md-math-display" role="region" aria-label="Equation" tabIndex={0}>{content}</div> : content;
}
