/** Scene metadata and content blocks for model-composed slides. Kept in step with
 * apps/backend/gunther/slide_scene.py. Metadata is untrusted until its source hash verifies. */
export const SCENE_LIMITS = { blocks: 24, nodes: 40, depth: 4, chars: 24_000 } as const;
export type BlockType = "text" | "stat" | "list" | "table" | "quote" | "image";
export type GraphicView = "flow" | "diagram" | "bar_chart" | "line_chart";
export type SceneView = BlockType | GraphicView;
export interface SlideBlock { id: string; type: BlockType; markdown: string }
export interface SceneLeaf { kind: "leaf"; blockId: string; view: SceneView; size: "small" | "medium" | "large" | "display"; align: "start" | "center" | "end" | "stretch"; role: "main" | "support" | "focus" }
export interface SceneGroup { kind: "row" | "column"; children: SceneNode[]; weights: number[]; gap: "tight" | "normal" | "wide"; padding: "none" | "small" | "medium"; surface: "none" | "soft" | "focus" | "outline"; align: "start" | "center" | "stretch" }
export type SceneNode = SceneLeaf | SceneGroup;
export interface SceneEnvelopeV1 { version: 1; sourceHash: string; root: SceneNode }
export interface SceneEnvelopeV2 { version: 2; sourceHash: string; graphicsHash: string; graphics: Record<string, GraphicView>; root: SceneNode }
export type SceneEnvelope = SceneEnvelopeV1 | SceneEnvelopeV2;

const FENCE = /^\s*(?:```|~~~)/;
const LAYOUT = /^[ \t]*<!--[ \t]*layout:[^\r\n]*-->[ \t]*$/;
const SCENE_HEADER = /^[ \t]*<!--[ \t]*scene:[^\r\n]*-->[ \t]*$/;
const NOTE = /^Note:/;
const HEADING = /^#{1,2}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/;
const CITATION = /\[(?:[ipd]:)?\d+(?:\s*[,，、]\s*\d+)*\]|\[\?\]/g;
const IMAGE = /^!\[[^\]\n]*\]\(gunther-asset:[A-Za-z0-9_-]+\)$/;
const TABLE_RULE = /^\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?$/;
const LIST = /^[ \t]*(?:[-+*]|\d+[.)])[ \t]+/;
const VALUE = /^[~≈<>≤≥±+\-−–]?[$€£¥￥₹₩]?\d[\d,]*(?:\.\d+)?(?:[%‰°万亿])?$/;
type Row = { line: string; fenced: boolean };
const rowsOf = (text: string): Row[] => {
  let fenced = false;
  return text.replace(/\r\n?/g, "\n").split("\n").map((line) => {
    if (FENCE.test(line)) { const row = { line, fenced: true }; fenced = !fenced; return row; }
    return { line, fenced };
  });
};
const withoutMetadata = (text: string): string => {
  const rows = rowsOf(text); const at = rows.findIndex(({ line }) => line.trim());
  if (at < 0 || !LAYOUT.test(rows[at]?.line ?? "")) return text;
  let skip = at + 1;
  while (skip < rows.length && !rows[skip]?.line.trim()) skip++;
  if (skip < rows.length && SCENE_HEADER.test(rows[skip]?.line ?? "")) skip++;
  while (skip < rows.length && !rows[skip]?.line.trim()) skip++;
  return [...rows.slice(0, at), ...rows.slice(skip)].map(({ line }) => line).join("\n");
};
export function visibleMarkdown(text: string, preserveCitations = false): string {
  const visible: string[] = [];
  for (const { line, fenced } of rowsOf(withoutMetadata(text))) {
    if (!fenced && NOTE.test(line)) break;
    visible.push(fenced || preserveCitations ? line : line.replace(CITATION, ""));
  }
  return visible.join("\n").trim();
}
export async function sourceHash(text: string): Promise<string> {
  return hashMarkdown(visibleMarkdown(text));
}
export async function graphicsHash(text: string): Promise<string> {
  return hashMarkdown(visibleMarkdown(text, true));
}
async function hashMarkdown(markdown: string): Promise<string> {
  const bytes = new TextEncoder().encode(markdown);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
function blockType(rows: Row[]): BlockType {
  const lines = rows.map(({ line }) => line.trim()).filter(Boolean);
  if (!lines.length || rows.some(({ fenced }) => fenced)) return "text";
  if (lines.length === 1 && IMAGE.test(lines[0] ?? "")) return "image";
  if (lines.some((line) => line.startsWith("|")) && lines.some((line) => TABLE_RULE.test(line))) return "table";
  if (lines.every((line) => LIST.test(line))) return "list";
  const quoted = lines.filter((line) => line.startsWith(">"));
  if (quoted.length && quoted.length + lines.filter((line) => line.startsWith("—")).length === lines.length) return "quote";
  if (lines.length === 1) {
    const candidate = (lines[0] ?? "").replace(CITATION, "").trim();
    const stat = /^\*\*([^*\n]+)\*\*$/.exec(candidate);
    if (stat && VALUE.test((stat[1] ?? "").trim())) return "stat";
  }
  return "text";
}
export function splitBlocks(text: string): { heading: string; blocks: SlideBlock[] } {
  const visible: Row[] = [];
  for (const row of rowsOf(withoutMetadata(text))) { if (!row.fenced && NOTE.test(row.line)) break; visible.push(row); }
  let heading = "";
  const first = visible.findIndex(({ line, fenced }) => line.trim() && !fenced);
  if (first >= 0) { const match = HEADING.exec(visible[first]?.line.trim() ?? ""); if (match) { heading = match[1]?.trim() ?? ""; visible.splice(first, 1); } }
  const groups: Row[][] = []; let current: Row[] = [];
  for (const row of visible) {
    if (!row.line.trim() && !row.fenced) { if (current.length) groups.push(current); current = []; }
    else current.push(row);
  }
  if (current.length) groups.push(current);
  if (groups.length > SCENE_LIMITS.blocks) return { heading, blocks: [] };
  return { heading, blocks: groups.map((group, index) => ({ id: `b${index + 1}`, type: blockType(group), markdown: group.map(({ line }) => line).join("\n").trim() })).filter((block) => block.markdown) };
}
const isObj = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const oneOf = <T extends string>(value: unknown, values: readonly T[]): value is T => typeof value === "string" && values.includes(value as T);
const hasOnlyKeys = (value: Record<string, unknown>, allowed: readonly string[]) => Object.keys(value).every((key) => allowed.includes(key));
const views = ["text", "stat", "list", "table", "quote", "image", "flow", "diagram", "bar_chart", "line_chart"] as const;
const graphicViews = ["flow", "diagram", "bar_chart", "line_chart"] as const;
export function decodeScene(payload: string | null | undefined): { scene: SceneEnvelope | null; issue: string | null } {
  if (!payload) return { scene: null, issue: null };
  if (payload.length > SCENE_LIMITS.chars) return { scene: null, issue: "Scene metadata is too large." };
  if (!/^[A-Za-z0-9_-]+$/.test(payload)) return { scene: null, issue: "Scene metadata has an invalid encoding." };
  try {
    const raw = atob(payload.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - payload.length % 4) % 4));
    const bytes = Uint8Array.from(raw, (char) => char.charCodeAt(0));
    if (bytes.length > SCENE_LIMITS.chars) return { scene: null, issue: "Scene metadata is too large." };
    const parsed: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    if (!isObj(parsed) || (parsed.version !== 1 && parsed.version !== 2) || typeof parsed.sourceHash !== "string" || !/^[0-9a-f]{64}$/.test(parsed.sourceHash)) return { scene: null, issue: "Scene metadata has an unsupported version or format." };
    if (parsed.version === 1 && !hasOnlyKeys(parsed, ["version", "sourceHash", "root"])) return { scene: null, issue: "Scene metadata has an unsupported version or format." };
    if (parsed.version === 2 && (!hasOnlyKeys(parsed, ["version", "sourceHash", "graphicsHash", "graphics", "root"]) || typeof parsed.graphicsHash !== "string" || !/^[0-9a-f]{64}$/.test(parsed.graphicsHash) || !isObj(parsed.graphics) || !Object.entries(parsed.graphics).every(([blockId, view]) => /^b[1-9][0-9]*$/.test(blockId) && oneOf(view, graphicViews)))) return { scene: null, issue: "Scene metadata has an unsupported version or format." };
    const root = normalizeNode(parsed.root);
    if (!root) return { scene: null, issue: "Scene metadata has an invalid element tree." };
    if (parsed.version === 1) return { scene: { version: 1, sourceHash: parsed.sourceHash, root }, issue: null };
    return { scene: { version: 2, sourceHash: parsed.sourceHash, graphicsHash: parsed.graphicsHash as string, graphics: parsed.graphics as Record<string, GraphicView>, root }, issue: null };
  } catch { return { scene: null, issue: "Scene metadata could not be decoded." }; }
}
function normalizeNode(value: unknown): SceneNode | null {
  if (!isObj(value)) return null;
  if (value.kind === "leaf") return {
    ...value,
    kind: "leaf",
    size: value.size ?? "medium",
    align: value.align ?? "start",
    role: value.role ?? "main",
  } as SceneLeaf;
  if (value.kind === "row" || value.kind === "column") {
    if (!Array.isArray(value.children)) return null;
    const children = value.children.map(normalizeNode);
    if (children.some((child) => !child)) return null;
    return {
      ...value,
      kind: value.kind,
      children: children as SceneNode[],
      gap: value.gap ?? "normal",
      padding: value.padding ?? "small",
      surface: value.surface ?? "none",
      align: value.align ?? "stretch",
    } as SceneGroup;
  }
  return null;
}
const leafSizes = ["small", "medium", "large", "display"] as const;
const leafAligns = ["start", "center", "end", "stretch"] as const;
const roles = ["main", "support", "focus"] as const;
const gaps = ["tight", "normal", "wide"] as const;
const paddings = ["none", "small", "medium"] as const;
const surfaces = ["none", "soft", "focus", "outline"] as const;
const groupAligns = ["start", "center", "stretch"] as const;
export function validateScene(root: unknown, blocks: SlideBlock[], scene?: SceneEnvelope | null): string | null {
  if (!isObj(root) || !["row", "column"].includes(String(root.kind))) return "The scene root must be a row or column.";
  if (blocks.length < 2 || blocks.length > SCENE_LIMITS.blocks) return "The page does not have a supported number of content blocks.";
  const expected = new Map(blocks.map((block) => [block.id, block.type])); const seen: string[] = []; let count = 0; let graphicCount = 0;
  if (scene?.version === 2) {
    const grants = Object.entries(scene.graphics);
    if (grants.length > 3) return "A page may permit at most three graphic elements.";
    for (const [blockId, view] of grants) {
      const type = expected.get(blockId);
      if (!type) return `Unknown graphic content block ${blockId}.`;
      if (type !== (view === "flow" ? "list" : "table")) return `The ${view} graphic cannot use ${blockId}'s content type.`;
    }
  }
  const walk = (node: unknown, depth: number): string | null => {
    count++; if (count > SCENE_LIMITS.nodes) return "The scene contains too many elements.";
    if (depth > SCENE_LIMITS.depth) return "The scene is nested too deeply.";
    if (!isObj(node)) return "Scene elements have an invalid format.";
    if (node.kind === "leaf") {
      if (!hasOnlyKeys(node, ["kind", "blockId", "view", "size", "align", "role"])) return "A scene element has an unknown or invalid property.";
      if (typeof node.blockId !== "string" || !oneOf(node.view, views) || !oneOf(node.size, leafSizes) || !oneOf(node.align, leafAligns) || !oneOf(node.role, roles)) return "A scene element has an unknown or invalid property.";
      const type = expected.get(node.blockId); if (!type) return `Unknown content block ${node.blockId}.`;
      if (oneOf(node.view, graphicViews)) {
        graphicCount++;
        if (graphicCount > 3) return "A page may use at most three graphic elements.";
        if (type !== (node.view === "flow" ? "list" : "table")) return `The ${node.view} view cannot use ${node.blockId}'s content type.`;
      } else if (type !== node.view) return `The ${node.view} view does not match ${node.blockId}'s content type.`;
      if (scene?.version === 1 && oneOf(node.view, graphicViews)) return "Scene version 1 does not support graphic views.";
      seen.push(node.blockId); return null;
    }
    if (!hasOnlyKeys(node, ["kind", "children", "weights", "gap", "padding", "surface", "align"]) || !oneOf(node.kind, ["row", "column"] as const) || !Array.isArray(node.children) || node.children.length < 2 || node.children.length > 4 || !Array.isArray(node.weights)) return "A scene group has an unknown or invalid property.";
    if (node.weights.length !== node.children.length) return "Every scene group needs one weight per child.";
    if (!node.weights.every((weight) => Number.isInteger(weight) && weight >= 1 && weight <= 4) || !oneOf(node.gap, gaps) || !oneOf(node.padding, paddings) || !oneOf(node.surface, surfaces) || !oneOf(node.align, groupAligns)) return "A scene group has an unknown or invalid property.";
    for (const child of node.children) { const issue = walk(child, depth + 1); if (issue) return issue; }
    return null;
  };
  const issue = walk(root, 1); if (issue) return issue;
  const duplicates = [...new Set(seen.filter((id, index) => seen.indexOf(id) !== index))].sort();
  const missing = [...expected.keys()].filter((id) => !seen.includes(id)).sort();
  if (duplicates.length) return `Duplicate content blocks: ${duplicates.join(", ")}.`;
  if (missing.length) return `Missing content blocks: ${missing.join(", ")}.`;
  return null;
}
const hashes = new Map<string, Promise<string>>();
export function verifyScene(scene: SceneEnvelopeV1, body: string): Promise<boolean> {
  const key = `${scene.sourceHash}\n${visibleMarkdown(body)}`;
  let pending = hashes.get(key);
  if (!pending) { pending = sourceHash(body); hashes.set(key, pending); if (hashes.size > 128) hashes.delete(hashes.keys().next().value as string); }
  return pending.then((actual) => actual === scene.sourceHash);
}
const verifications = new Map<string, Promise<{ sourceValid: boolean; graphicsValid: boolean }>>();
export function verifySceneEnvelope(scene: SceneEnvelope, body: string): Promise<{ sourceValid: boolean; graphicsValid: boolean }> {
  const key = `${scene.version}\n${scene.sourceHash}\n${scene.version === 2 ? scene.graphicsHash : ""}\n${visibleMarkdown(body, true)}`;
  let pending = verifications.get(key);
  if (!pending) {
    pending = Promise.all([sourceHash(body), scene.version === 2 ? graphicsHash(body) : Promise.resolve("")]).then(([source, graphics]) => ({
      sourceValid: source === scene.sourceHash,
      graphicsValid: scene.version === 2 && graphics === scene.graphicsHash,
    }));
    verifications.set(key, pending);
    if (verifications.size > 128) verifications.delete(verifications.keys().next().value as string);
  }
  return pending;
}
