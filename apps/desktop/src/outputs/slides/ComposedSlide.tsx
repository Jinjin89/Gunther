import type { CSSProperties } from "react";
import { useState } from "react";
import { sourceAssetUrl } from "../../api";
import { Rich } from "./Rich";
import { Heading } from "./Rich";
import { GraphicElement } from "./GraphicElement";
import type { SceneEnvelope, SceneGroup, SceneLeaf, SceneNode, SlideBlock } from "./scene";

function Leaf({ node, block, scene, graphicsValid, numbers, onGraphicIssue }: { node: SceneLeaf; block: SlideBlock; scene: SceneEnvelope; graphicsValid: boolean; numbers: number[]; onGraphicIssue: (blockId: string, issue: string | null) => void }) {
  const match = /^!\[([^\]\n]*)\]\(gunther-asset:([A-Za-z0-9_-]+)\)$/.exec(block.markdown.trim());
  const [missingAsset, setMissingAsset] = useState<string | null>(null);
  if (["flow", "diagram", "bar_chart", "line_chart"].includes(node.view)) {
    return <GraphicElement node={node} block={block} scene={scene} graphicsValid={graphicsValid} numbers={numbers} onIssue={onGraphicIssue} />;
  }
  return <div className={`outputs-composed-leaf is-${node.view} is-${node.size} role-${node.role} align-${node.align}`} data-block-id={node.blockId}>
    {node.view === "image" && match
      ? missingAsset === match[2] ? <div className="outputs-composed-image-missing" role="img" aria-label={match[1] || "Image unavailable"}>{match[1] || "Image unavailable"}</div>
        : <div className="outputs-composed-image"><img src={sourceAssetUrl(match[2]!)} alt={match[1] ?? ""} onError={() => setMissingAsset(match[2]!)} /></div>
      : node.view === "image" ? <div className="outputs-composed-image-missing" role="img" aria-label={block.markdown}>{block.markdown}</div>
        : <Rich text={block.markdown} />}
  </div>;
}
const gaps = { tight: 14, normal: 26, wide: 42 } as const;
const paddings = { none: 0, small: 18, medium: 32 } as const;
function Group({ node, blocks, scene, graphicsValid, numbers, onGraphicIssue }: { node: SceneGroup; blocks: Map<string, SlideBlock>; scene: SceneEnvelope; graphicsValid: boolean; numbers: number[]; onGraphicIssue: (blockId: string, issue: string | null) => void }) {
  const style = {
    "--scene-gap": `${gaps[node.gap]}px`,
    "--scene-padding": `${paddings[node.padding]}px`,
    gridTemplateColumns: node.kind === "row" ? node.weights.map((weight) => `minmax(0, ${weight}fr)`).join(" ") : "minmax(0, 1fr)",
    gridTemplateRows: node.kind === "column" ? node.weights.map((weight) => `minmax(0, ${weight}fr)`).join(" ") : "minmax(0, 1fr)",
    alignItems: node.align === "stretch" ? "stretch" : node.align === "center" ? "center" : "start",
    alignContent: "stretch",
  } as CSSProperties;
  return <div className={`outputs-composed-group is-${node.kind} surface-${node.surface} align-${node.align}`} style={style}>
    {node.children.map((child, index) => <div className="outputs-composed-child" key={`${child.kind}-${"blockId" in child ? child.blockId : index}`}>
      <Node node={child} blocks={blocks} scene={scene} graphicsValid={graphicsValid} numbers={numbers} onGraphicIssue={onGraphicIssue} />
    </div>)}
  </div>;
}
function Node({ node, blocks, scene, graphicsValid, numbers, onGraphicIssue }: { node: SceneNode; blocks: Map<string, SlideBlock>; scene: SceneEnvelope; graphicsValid: boolean; numbers: number[]; onGraphicIssue: (blockId: string, issue: string | null) => void }) {
  if (node.kind === "leaf") { const block = blocks.get(node.blockId); return block ? <Leaf node={node} block={block} scene={scene} graphicsValid={graphicsValid} numbers={numbers} onGraphicIssue={onGraphicIssue} /> : null; }
  return <Group node={node} blocks={blocks} scene={scene} graphicsValid={graphicsValid} numbers={numbers} onGraphicIssue={onGraphicIssue} />;
}
export function ComposedSlide({ scene, graphicsValid, heading, title = false, blocks, numbers, onGraphicIssue }: { scene: SceneEnvelope; graphicsValid: boolean; heading: string; title?: boolean; blocks: SlideBlock[]; numbers: number[]; onGraphicIssue: (blockId: string, issue: string | null) => void }) {
  const blockMap = new Map(blocks.map((block) => [block.id, block]));
  return <div className="outputs-composed-frame" data-scene-version={scene.version}>
    <Heading text={heading} level={title ? 1 : 2} />
    <div className="outputs-composed-root"><Node node={scene.root} blocks={blockMap} scene={scene} graphicsValid={graphicsValid} numbers={numbers} onGraphicIssue={onGraphicIssue} /></div>
  </div>;
}
