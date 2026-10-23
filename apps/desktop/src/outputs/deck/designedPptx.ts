/** A captured designed slide (a picture without text, and its text boxes) written into a pptxgenjs slide. */
import type PptxGenJS from "pptxgenjs";
import type { TextBlock } from "./designedText";

export interface DesignedCapture {
  /** The slide without any of its text, as a `data:image/png;base64,…` URL (2560 x 1440). */
  image: string;
  blocks: TextBlock[];
}

type Slide = ReturnType<PptxGenJS["addSlide"]>;
const inch = (px: number) => px / 96;
/** 1280 stage pixels are 960 points. */
const pt = (px: number) => Math.round(px * 75) / 100;

/**
 * The picture covers the slide edge to edge, and each text block sits on it as a real text box at the place it was drawn:
 * no insets, no autofit, wrap on for blocks of several lines only, line spacing exactly the CSS line height. A citation mark
 * is a superscript run (`baseline="30000"`), as in the template slides. Charts and drawings are in the picture.
 */
export function addDesignedSlide(slide: Slide, capture: DesignedCapture): void {
  slide.addImage({ data: capture.image.replace(/^data:/, ""), x: 0, y: 0, w: inch(1280), h: inch(720) });
  for (const block of capture.blocks) {
    const runs = block.runs.map((run) => ({
      text: run.text,
      options: {
        fontFace: run.font, fontSize: pt(run.size), bold: run.bold, italic: run.italic, color: run.color,
        ...(run.underline ? { underline: { style: "sng" as const } } : {}),
        ...(run.transparency > 0 ? { transparency: Math.min(100, run.transparency) } : {}),
        ...(run.superscript ? { superscript: true } : {}),
        ...(Math.abs(run.spacing) > 0.01 ? { charSpacing: pt(run.spacing) } : {}),
        ...(run.breakLine ? { breakLine: true } : {}),
      },
    }));
    slide.addText(runs, {
      x: inch(block.x), y: inch(block.y), w: inch(block.w), h: inch(block.h),
      margin: 0, valign: "top", align: block.align, wrap: block.wrap, fit: "none", lineSpacing: pt(block.lineHeight), paraSpaceAfter: 0, paraSpaceBefore: 0,
    });
  }
}
