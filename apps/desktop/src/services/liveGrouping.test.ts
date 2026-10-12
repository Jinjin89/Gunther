import { describe, expect, it } from "vitest";
import {
  applyGrouping,
  applyPiece,
  applyRegroup,
  applyWindowResult,
  beginGrouping,
  confirmLive,
  editParagraph,
  emptyTranscript,
  groupingRequest,
  nextRegroupChunk,
  paragraphSentences,
  paragraphText,
  storableTranscript,
  type TranscriptState,
} from "./liveTranscript";

/** Confirm sentences of 1 s each as live final text would be, starting where the transcript ends. */
function confirmSentences(state: TranscriptState, texts: string[]): TranscriptState {
  const from = state.sentences.at(-1)?.endMs ?? 0;
  const sentences = texts.map((text, index) => ({ start_ms: from + index * 1_000 + 10, end_ms: from + (index + 1) * 1_000, text }));
  const piece = {
    id: `p${from}`, start_ms: sentences[0]!.start_ms, end_ms: sentences.at(-1)!.end_ms,
    text: texts.join(" "), final: true, sentences,
  };
  return confirmLive(applyPiece(state, piece), { closing: true });
}

const grouped = (): TranscriptState => ({ ...emptyTranscript(), grouping: { state: "idle" } });
const texts = (state: TranscriptState) => state.paragraphs.map((p) => paragraphSentences(state, p).map((s) => s.text));
const numbered = (count: number, prefix = "S") => Array.from({ length: count }, (_, i) => `${prefix}${i + 1}.`);

describe("paragraphs by content", () => {
  it("with grouping on, confirmed sentences join the open paragraph", () => {
    let state = confirmSentences(grouped(), ["A.", "B."]);
    state = confirmSentences(state, ["C."]);
    expect(texts(state)).toEqual([["A.", "B.", "C."]]);
    expect(state.paragraphs[0]).toMatchObject({ frozen: false, id: "p-10" });
  });

  it("with grouping off each window is one frozen paragraph", () => {
    let state = confirmSentences(emptyTranscript(), ["A.", "B."]);
    state = confirmSentences(state, ["C."]);
    expect(texts(state)).toEqual([["A.", "B."], ["C."]]);
    expect(state.paragraphs.every((p) => p.frozen)).toBe(true);
    expect(groupingRequest(state)).toBeNull();
  });

  it("a request holds the open paragraph and the end of the one before, and an answer splits only it", () => {
    let state = confirmSentences(grouped(), ["A.", "B.", "C.", "D."]);
    expect(groupingRequest(state)).toMatchObject({ sentences: ["A.", "B.", "C.", "D."], before: "" });
    state = applyGrouping(state, groupingRequest(state)!, { starts: [0, 2] });
    expect(texts(state)).toEqual([["A.", "B."], ["C.", "D."]]);
    expect(state.paragraphs.map((p) => p.frozen)).toEqual([true, false]);
    expect(state.paragraphs.map((p) => p.id)).toEqual(["p-10", "p-2010"]);

    state = confirmSentences(state, ["E.", "F."]);
    const request = groupingRequest(state)!;
    expect(request.sentences).toEqual(["C.", "D.", "E.", "F."]);
    expect(request.before).toBe("A. B.");
    const frozenBefore = state.paragraphs[0];
    state = applyGrouping(state, request, { starts: [0, 1, 3] });
    expect(state.paragraphs[0]).toBe(frozenBefore);
    expect(texts(state)).toEqual([["A.", "B."], ["C."], ["D.", "E."], ["F."]]);
    expect(state.paragraphs.map((p) => p.frozen)).toEqual([true, true, true, false]);
    expect(state.grouping.state).toBe("idle");
  });

  it("context is at most 400 characters of the paragraph before", () => {
    let state = confirmSentences(grouped(), ["x".repeat(900), "Next."]);
    state = applyGrouping(state, groupingRequest(state)!, { starts: [0, 1] });
    expect(groupingRequest(state)!.before).toHaveLength(400);
  });

  it("sentences confirmed during a grouping request join the open part", () => {
    let state = confirmSentences(grouped(), ["A.", "B.", "C."]);
    const request = groupingRequest(state)!;
    state = beginGrouping(state);
    expect(state.grouping.state).toBe("running");
    state = confirmSentences(state, ["D.", "E."]);
    state = applyGrouping(state, request, { starts: [0, 2] });
    expect(texts(state)).toEqual([["A.", "B."], ["C.", "D.", "E."]]);
    expect(state.paragraphs.map((p) => p.frozen)).toEqual([true, false]);
  });

  it("the pool cap freezes the oldest sentences first", () => {
    let state = confirmSentences(grouped(), numbered(70));
    const request = groupingRequest(state)!;
    expect(request.sentences).toHaveLength(60);
    expect(request.sentences[0]).toBe("S11.");
    expect(request.before).toContain("S10.");
    state = applyGrouping(state, request, { starts: [0] });
    expect(state.paragraphs).toHaveLength(2);
    expect(paragraphSentences(state, state.paragraphs[0]!)).toHaveLength(10);
    expect(state.paragraphs.map((p) => p.frozen)).toEqual([true, false]);

    const long = confirmSentences(grouped(), Array.from({ length: 10 }, () => "y".repeat(1_500)));
    const sent = groupingRequest(long)!.sentences;
    expect(sent.join("").length).toBeLessThanOrEqual(6_000);
    expect(sent).toHaveLength(4);
  });

  it("a failure changes nothing but the grouping state, and the next request has the same sentences", () => {
    let state = confirmSentences(grouped(), ["A.", "B."]);
    const request = groupingRequest(state)!;
    const failed = applyGrouping(beginGrouping(state), request, { error: "down" });
    expect(failed.grouping).toEqual({ state: "failed", error: "down" });
    expect({ ...failed, grouping: state.grouping }).toEqual(state);
    state = confirmSentences(failed, ["C."]);
    expect(groupingRequest(state)!.sentences).toEqual(["A.", "B.", "C."]);
  });

  it("uses only whole numbers inside the range, and always starts at the first sentence", () => {
    const state = confirmSentences(grouped(), ["A.", "B.", "C."]);
    const next = applyGrouping(state, groupingRequest(state)!, { starts: [2, 2, 7, -1, 1.5, NaN, "1" as unknown as number] });
    expect(texts(next)).toEqual([["A.", "B."], ["C."]]);
  });

  it("an edited paragraph is never grouped, live or after Stop", () => {
    let state = confirmSentences(grouped(), ["A.", "B."]);
    state = applyGrouping(state, groupingRequest(state)!, { starts: [0, 1] });
    state = confirmSentences(state, ["C.", "D."]);
    const request = groupingRequest(state)!;
    // The user edits the open paragraph while the request runs: the answer no longer applies.
    const edited = editParagraph(state, state.paragraphs[1]!.id, "My own words.");
    expect(applyGrouping(edited, request, { starts: [0, 1] }).paragraphs).toEqual(edited.paragraphs);
    expect(groupingRequest(edited)).toBeNull();

    let after = confirmSentences(grouped(), ["A.", "B.", "C.", "D.", "E.", "F."]);
    after = applyGrouping(after, groupingRequest(after)!, { starts: [0, 2, 4] });
    after = editParagraph(after, after.paragraphs[1]!.id, "Edited.");
    const chunk = nextRegroupChunk(after)!;
    expect(chunk).toMatchObject({ chunkStart: 0, sentences: ["A.", "B."] });
    expect(nextRegroupChunk(after, chunk.endMs)).toMatchObject({ chunkStart: 2, sentences: ["E.", "F."] });
    const regrouped = applyRegroup(after, 0, chunk.sentenceIds, { starts: [0, 1] });
    expect(regrouped.paragraphs[2]).toBe(after.paragraphs[1]);
    expect(paragraphText(regrouped, regrouped.paragraphs[2]!)).toBe("Edited.");
    // A run that includes an edited paragraph is refused.
    const all = after.paragraphs.flatMap((p) => p.sentenceIds).slice(0, 4);
    expect(applyRegroup(after, 0, all, { starts: [0, 1, 2] }).paragraphs).toEqual(after.paragraphs);
  });

  it("regroup chunks start at paragraph starts and hold at most the limit", () => {
    let state = confirmSentences(grouped(), numbered(10));
    state = applyGrouping(state, groupingRequest(state)!, { starts: [0, 3, 6] });
    const first = nextRegroupChunk(state, 0, 7)!;
    expect(first.chunkStart).toBe(0);
    expect(first.sentences).toHaveLength(6);
    const second = nextRegroupChunk(state, first.endMs, 7)!;
    expect(second.chunkStart).toBe(2);
    expect(second.sentences).toEqual(["S7.", "S8.", "S9.", "S10."]);
    expect(second.before).toBe("S4. S5. S6.");
    expect(nextRegroupChunk(state, second.endMs, 7)).toBeNull();

    const result = applyRegroup(state, 0, first.sentenceIds, { starts: [0, 1, 5] });
    expect(texts(result).slice(0, 3)).toEqual([["S1."], ["S2.", "S3.", "S4.", "S5."], ["S6."]]);
    expect(result.paragraphs.map((p) => p.id).slice(0, 3)).toEqual(["p-10", "p-1010", "p-5010"]);
    expect(result.paragraphs.slice(0, 3).every((p) => p.frozen)).toBe(true);
  });

  it("a failed regroup changes nothing but the grouping state", () => {
    let state = confirmSentences(grouped(), ["A.", "B.", "C."]);
    state = applyGrouping(state, groupingRequest(state)!, { starts: [0, 1] });
    const chunk = nextRegroupChunk(state)!;
    const failed = applyRegroup(state, chunk.chunkStart, chunk.sentenceIds, { error: "no" });
    expect({ ...failed, grouping: state.grouping }).toEqual(state);
    expect(failed.grouping.state).toBe("failed");
  });

  it("a grouping request in flight when the app closes is forgotten", () => {
    const state = beginGrouping(confirmSentences(grouped(), ["A."]));
    expect(storableTranscript(state).grouping.state).toBe("idle");
  });

  it("window results with grouping on also join the open paragraph", () => {
    let state = grouped();
    state = applyPiece(state, { id: "a", start_ms: 0, end_ms: 2_000, text: "x", final: true, sentences: [{ start_ms: 0, end_ms: 2_000, text: "x." }] });
    state = applyWindowResult(state, { startMs: 0, endMs: 2_000 }, null, { checksOff: true });
    expect(state.paragraphs).toHaveLength(1);
    expect(state.paragraphs[0]!.frozen).toBe(false);
  });
});
