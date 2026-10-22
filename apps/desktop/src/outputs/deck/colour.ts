import { contrast } from "../slides/accent";

const parse = (hex: string): [number, number, number] => {
  const h = hex.replace("#", "");
  return [0, 2, 4].map((at) => parseInt(h.slice(at, at + 2), 16)) as [number, number, number];
};
const toHex = (rgb: number[]) => `#${rgb.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("")}`;

/** `amount` of `b` mixed into `a` (0 is a, 1 is b). */
export function mix(a: string, b: string, amount: number): string {
  const [x, y] = [parse(a), parse(b)];
  return toHex(x.map((v, at) => v + (y[at]! - v) * amount));
}

export { contrast };

/** The colour moved away from the background until it reads at `min`:1 against it. */
export function readable(colour: string, background: string, min = 4.5): { colour: string; changed: boolean } {
  if (contrast(colour, background) >= min) return { colour, changed: false };
  const target = contrast("#000000", background) > contrast("#ffffff", background) ? "#000000" : "#ffffff";
  for (let step = 1; step <= 20; step += 1) {
    const next = mix(colour, target, step * 0.05);
    if (contrast(next, background) >= min) return { colour: next, changed: true };
  }
  return { colour: target, changed: true };
}

/** Black or white, whichever reads better on the colour. */
export const onColour = (colour: string) => contrast("#ffffff", colour) >= contrast("#111317", colour) ? "#ffffff" : "#111317";
