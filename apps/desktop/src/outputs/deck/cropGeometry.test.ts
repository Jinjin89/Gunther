import { expect, it } from "vitest";
import { cropGeometry } from "./layouts";

it.each([[1000, 400], [400, 1000]])("fits a panel using its pixel ratio in a %s by %s image", (width, height) => {
  const crop = { x: 0.2, y: 0.1, w: 0.5, h: 0.8 };
  const fit = cropGeometry(620, 400, crop, width, height);
  expect(fit.width / fit.height).toBeCloseTo(width * crop.w / (height * crop.h), 6);
  expect(fit.width).toBeLessThanOrEqual(620);
  expect(fit.height).toBeLessThanOrEqual(400);
  expect(fit.imageWidth * crop.w).toBeCloseTo(fit.width);
  expect(fit.imageHeight * crop.h).toBeCloseTo(fit.height);
  expect(fit.left).toBe(-20);
  expect(fit.top).toBe(-10);
});
