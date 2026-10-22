import { describe, expect, it } from "vitest";
import fixture from "../../../../../artifacts/validation/presentation-composer/contract_cases.json";
import { decodeScene, graphicsHash, sourceHash, splitBlocks, validateScene, verifySceneEnvelope, visibleMarkdown, type SceneEnvelope, type SceneGroup, type SceneNode } from "./scene";

describe("the shared slide scene contract", () => {
  it("splits the shared Python fixture into the same heading and ordered blocks", () => {
    expect(splitBlocks(fixture.markdown)).toEqual({ heading: fixture.heading, blocks: fixture.blocks });
  });

  it("preserves a literal hash in an ATX title and removes only a spaced closing sequence", () => {
    expect(splitBlocks("## 使用 C#\n\nA paragraph.").heading).toBe("使用 C#");
    expect(splitBlocks("## A real heading ###\n\nA paragraph.").heading).toBe("A real heading");
  });

  it("strips the same blank-separated header metadata shape as the slide parser", () => {
    const marked = "<!-- layout: composed -->\n\n<!-- scene: malformed! -->\n\n## Heading\n\nText";
    const plain = "## Heading\n\nText";
    expect(visibleMarkdown(marked)).toBe(visibleMarkdown(plain));
    expect(splitBlocks(marked).heading).toBe("Heading");
  });

  it("ignores citation renumbering and speaker notes while hashing visible Markdown", async () => {
    expect(await sourceHash(fixture.markdown)).toBe(await sourceHash(fixture.renumbered));
    expect(visibleMarkdown(fixture.noteChanged)).toBe(visibleMarkdown(fixture.markdown));
    expect(await sourceHash(fixture.noteChanged)).toBe(await sourceHash(fixture.markdown));
  });

  it("requires every typed content block once and rejects malformed structure", () => {
    const { blocks } = splitBlocks(fixture.markdown);
    const leaves: SceneNode[] = blocks.map((block) => ({ kind: "leaf", blockId: block.id, view: block.type, size: "medium", align: "start", role: "main" }));
    const group = (children: SceneNode[]): SceneGroup => ({ kind: "column", children, weights: children.map(() => 1), gap: "normal", padding: "small", surface: "none", align: "stretch" });
    const root: SceneGroup = { kind: "row", children: [group(leaves.slice(0, 3)), group(leaves.slice(3))], weights: [1, 1], gap: "normal", padding: "small", surface: "none", align: "stretch" };
    expect(validateScene(root, blocks)).toBeNull();
    expect(validateScene({ ...root, children: [group(leaves.slice(0, 2)), group(leaves.slice(3))] }, blocks)).toContain("Missing content blocks");
    expect(validateScene({ ...root, children: [group([leaves[0]!, leaves[0]!, leaves[2]!]), root.children[1]!] }, blocks)).toContain("Duplicate content blocks");
    const firstLeaf = leaves[0] as Extract<SceneNode, { kind: "leaf" }>;
    expect(validateScene({ ...root, children: [group([{ ...firstLeaf, blockId: "b99" }, ...leaves.slice(1, 3)]), root.children[1]!] }, blocks)).toContain("Unknown content block");
    expect(validateScene({ ...root, weights: root.weights.slice(1) }, blocks)).toContain("one weight per child");
  });

  it("keeps scene trees tied to the content fingerprint", async () => {
    const envelope: SceneEnvelope = { version: 1, sourceHash: await sourceHash(fixture.markdown), root: { kind: "row", children: [
      { kind: "leaf", blockId: "b1", view: "stat", size: "display", align: "center", role: "focus" },
      { kind: "column", children: [{ kind: "leaf", blockId: "b2", view: "text", size: "medium", align: "start", role: "support" }, { kind: "leaf", blockId: "b3", view: "list", size: "small", align: "start", role: "main" }], weights: [1, 1], gap: "tight", padding: "none", surface: "soft", align: "stretch" },
    ], weights: [1, 2], gap: "wide", padding: "medium", surface: "soft", align: "stretch" } };
    expect(envelope.sourceHash).toHaveLength(64);
    expect(await sourceHash(fixture.renumbered)).toBe(envelope.sourceHash);
    expect(await sourceHash(fixture.markdown.replace("original scope", "changed scope"))).not.toBe(envelope.sourceHash);
  });

  it("uses a citation-sensitive graphics fingerprint while preserving source-hash compatibility", async () => {
    const body = "## Trend\n\n| Year | Change |\n| --- | --- |\n| 2020 | −6 [1] |\n| 2021 | 3 [2] |\n\nThe result kept its original scope.";
    const changedCitation = body.replace("[1]", "[3]");
    const changedCopy = body.replace("Change", "Difference");
    const scene: SceneEnvelope = {
      version: 2,
      sourceHash: await sourceHash(body),
      graphicsHash: await graphicsHash(body),
      graphics: { b1: "line_chart" },
      root: { kind: "row", children: [
        { kind: "leaf", blockId: "b1", view: "line_chart", size: "large", align: "stretch", role: "main" },
        { kind: "leaf", blockId: "b2", view: "text", size: "small", align: "start", role: "support" },
      ], weights: [2, 1], gap: "normal", padding: "small", surface: "none", align: "stretch" },
    };
    await expect(verifySceneEnvelope(scene, changedCitation)).resolves.toEqual({ sourceValid: true, graphicsValid: false });
    await expect(verifySceneEnvelope(scene, changedCopy)).resolves.toEqual({ sourceValid: false, graphicsValid: false });
    expect(validateScene(scene.root, splitBlocks(body).blocks, { ...scene, version: 1 } as SceneEnvelope)).toContain("version 1");
    expect(validateScene(scene.root, splitBlocks(body).blocks, { ...scene, graphics: {} })).toBeNull();
  });

  it("decodes only a v2 envelope whose grant names a supported graphic view", async () => {
    const source = await sourceHash("## A\n\nTwo blocks.\n\nAnother block.");
    const graphics = await graphicsHash("## A\n\nTwo blocks.\n\nAnother block.");
    const envelope = { version: 2, sourceHash: source, graphicsHash: graphics, graphics: { b1: "diagram" }, root: { kind: "row", children: [
      { kind: "leaf", blockId: "b1", view: "diagram" }, { kind: "leaf", blockId: "b2", view: "text" },
    ], weights: [1, 1] } };
    const encode = (value: unknown) => btoa(JSON.stringify(value)).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
    expect(decodeScene(encode(envelope)).scene?.version).toBe(2);
    expect(decodeScene(encode({ ...envelope, graphics: { b1: "pie_chart" } })).scene).toBeNull();
  });
});
