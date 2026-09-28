import type { KnowledgeBase } from "../atlas";

type GlyphSize = "xs" | "sm" | "md" | "lg";

interface LibraryGlyphProps {
  base: Pick<KnowledgeBase, "title" | "color">;
  size?: GlyphSize;
}

const initialOf = (title: string) => title.trim().match(/[\p{L}\p{N}]/u)?.[0]?.toUpperCase() ?? "·";

/** A library's identity: its colour and first letter, used wherever it is referenced. */
export function LibraryGlyph({ base, size = "md" }: LibraryGlyphProps) {
  return <span className={`gx-glyph gx-glyph-${size} tone-${base.color}`} aria-hidden="true">{initialOf(base.title)}</span>;
}
