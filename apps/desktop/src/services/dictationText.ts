/** Short dictation text, keyed by the recognizer's sentence identity. */
export interface DictationPiece { id: string; start: number; text: string; final: boolean }
export type DictationText = DictationPiece[];

export function joinDictation(before: string, after: string): string {
  if (!before || !after) return before + after;
  const tight = /\s$/.test(before) || /^\s|^[　-鿿＀-￯]/.test(after);
  return before + (tight ? "" : " ") + after;
}

export function updateDictation(pieces: DictationText, event: Record<string, unknown>): DictationText {
  if (event.type !== "transcript.piece" || typeof event.id !== "string" || typeof event.text !== "string"
    || typeof event.start_ms !== "number" || !Number.isFinite(event.start_ms) || typeof event.final !== "boolean") return pieces;
  const index = pieces.findIndex((piece) => piece.id === event.id);
  const previous = pieces[index];
  // A late draft or duplicate final cannot rewrite a confirmed sentence.
  if (previous?.final) return pieces;
  const piece: DictationPiece = { id: event.id, start: event.start_ms, text: event.text.trim(), final: event.final };
  const next = [...pieces];
  if (index < 0) next.push(piece);
  else next[index] = piece;
  return next.sort((a, b) => a.start - b.start);
}

export function dictationText(pieces: DictationText): string {
  return pieces.reduce((text, piece) => joinDictation(text, piece.text), "");
}

/** Track the region owned by ASR; manual edits are never overwritten by a later revision. */
export class DictationInsertion {
  private start = 0;
  private owned = "";
  private last = "";
  private seen = new Set<string>();
  private frozen = new Set<string>();

  begin(value: string) {
    this.start = value.length;
    this.owned = "";
    this.last = value;
    this.seen.clear();
    this.frozen.clear();
  }

  update(value: string, pieces: DictationText): string {
    if (value !== this.last) {
      if (!this.owned) this.start = value.length;
      // Edits wholly before/after the ASR region can keep the region anchored. If the
      // region itself was edited, freeze that text and append only newly heard speech.
      const prefix = this.last.slice(0, this.start);
      const suffix = this.last.slice(this.start + this.owned.length);
      if (!this.owned || value.startsWith(prefix + this.owned)) {
        // User changed only the suffix.
      } else if (this.owned && value.endsWith(this.owned + suffix)) {
        this.start = value.length - this.owned.length - suffix.length;
      } else {
        for (const id of this.seen) this.frozen.add(id);
        this.start = value.length;
        this.owned = "";
      }
    }
    const transcript = dictationText(pieces.filter((piece) => !this.frozen.has(piece.id)));
    const prefix = value.slice(0, this.start);
    const suffix = value.slice(this.start + this.owned.length);
    const replacement = transcript ? joinDictation(prefix, transcript).slice(prefix.length) : "";
    const next = prefix + replacement + suffix;
    this.owned = replacement;
    this.seen = new Set(pieces.map((piece) => piece.id));
    this.last = next;
    return next;
  }
}
