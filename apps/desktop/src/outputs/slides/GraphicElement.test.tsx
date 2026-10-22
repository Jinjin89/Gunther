import { describe, expect, it } from "vitest";
import { parseGraphicBlock } from "./parseSlide";
import { diagramLayout } from "./GraphicElement";

describe("diagram geometry in a composed area", () => {
  const branch = [
    "| Start | Relation | End |",
    "| --- | --- | --- |",
    "| Intake | 进入 [1] | Review |",
    "| Review | 完成检查 [2] | Publish |",
    "| Review | 需要补充 [3] | Request more |",
  ].join("\n");

  it("fits a three-layer four-node branch when node and edge labels have room", () => {
    const shape = parseGraphicBlock("diagram", branch);
    expect(shape?.layout).toBe("diagram");
    if (!shape || shape.layout !== "diagram") throw new Error("The fixture must parse as a cited DAG.");
    const layout = diagramLayout(shape, 596, 283);
    expect(layout).not.toBeNull();
    expect(layout?.nodeWidth).toBeGreaterThanOrEqual(96);
    expect(layout?.nodeHeight).toBeGreaterThanOrEqual(46);
    expect(layout?.edges).toHaveLength(3);
  });

  it("uses the source table when a smaller area cannot hold labels without collision", () => {
    const shape = parseGraphicBlock("diagram", branch);
    expect(shape?.layout).toBe("diagram");
    if (!shape || shape.layout !== "diagram") throw new Error("The fixture must parse as a cited DAG.");
    expect(diagramLayout(shape, 240, 140)).toBeNull();
  });
});
