let queue: Promise<unknown> = Promise.resolve();
let nextId = 0;

export function renderMermaid(source: string): Promise<string> {
  // Mermaid's configuration and drawing state are global. Keep them in one queue.
  const drawing = queue.then(async () => {
    const { default: mermaid } = await import("mermaid");
    const defaults = mermaid.mermaidAPI.defaultConfig;
    const { maxTextSize } = defaults;
    if (maxTextSize !== undefined && source.length > maxTextSize) {
      throw new Error(`Mermaid diagrams are limited to ${maxTextSize} characters.`);
    }

    const fontFamily = getComputedStyle(document.documentElement).getPropertyValue("--gx-font-sans").trim() || "sans-serif";
    const sizes = Object.fromEntries(Object.entries(defaults).flatMap(([name, config]) =>
      config && typeof config === "object" && "useMaxWidth" in config ? [[name, { useMaxWidth: false }]] : [],
    ));
    mermaid.initialize({
      ...sizes,
      startOnLoad: false,
      securityLevel: "strict",
      secure: [...(defaults.secure ?? []), "htmlLabels", "theme", "look", "dropShadow", "useMaxWidth"],
      htmlLabels: false,
      flowchart: { htmlLabels: false, useMaxWidth: false },
      theme: "neutral",
      look: "classic",
      themeVariables: { dropShadow: "none" },
      fontFamily,
      suppressErrorRendering: true,
    });
    const container = document.createElement("div");
    container.style.position = "absolute";
    container.style.visibility = "hidden";
    container.setAttribute("aria-hidden", "true");
    document.body.append(container);
    try {
      const { svg } = await mermaid.render(`gx-mermaid-${++nextId}`, source, container);
      return svg;
    } finally {
      container.remove();
    }
  });
  queue = drawing.catch(() => undefined);
  return drawing;
}
