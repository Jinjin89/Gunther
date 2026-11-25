import mermaid from "mermaid";
import { expect, it, vi } from "vitest";
import { renderMermaid } from "./renderMermaid";

it("keeps the app's settings when Mermaid processes diagram configuration", async () => {
  const effectiveConfig = vi.fn();
  vi.spyOn(mermaid, "render").mockImplementation(async (_id, source) => {
    // Mermaid's parser processes frontmatter and directives without needing SVG layout in jsdom.
    await mermaid.parse(source);
    effectiveConfig(mermaid.mermaidAPI.getConfig());
    return { diagramType: "flowchart", svg: "<svg/>" };
  });

  await renderMermaid(`---
config:
  htmlLabels: true
  securityLevel: loose
  theme: dark
  look: neo
  flowchart:
    htmlLabels: true
    useMaxWidth: true
  themeVariables:
    dropShadow: drop-shadow(1px 1px 2px #777)
---
flowchart LR
  A[Start] --> B[Done]`);

  expect(effectiveConfig).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    htmlLabels: false,
    securityLevel: "strict",
    theme: "neutral",
    look: "classic",
    flowchart: expect.objectContaining({ htmlLabels: false, useMaxWidth: false }),
    themeVariables: expect.objectContaining({ dropShadow: "none" }),
    secure: expect.arrayContaining([...(mermaid.mermaidAPI.defaultConfig.secure ?? []), "htmlLabels"]),
  }));
});
