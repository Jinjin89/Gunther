import { describe, expect, it } from "vitest";
import { parseTranscript } from "../items/sourceContent";
import {
  applyLiveEvent,
  applyPiece,
  applyRetry,
  applyWindowResult,
  changedRanges,
  clockLabel,
  confirmLive,
  editParagraph,
  emptyTranscript,
  isTranscriptState,
  joinSentences,
  nextWindow,
  paragraphText,
  renderTranscript,
  retryRange,
  storableTranscript,
  tokenize,
  transcriptFromText,
  type PieceEvent,
  type TranscriptState,
} from "./liveTranscript";

const final = (id: string, sentences: [number, number, string][]): PieceEvent => ({
  id,
  start_ms: sentences[0]![0],
  end_ms: sentences.at(-1)![1],
  text: sentences.map((item) => item[2]).join(" "),
  final: true,
  sentences: sentences.map(([start_ms, end_ms, text]) => ({ start_ms, end_ms, text })),
});

const draft = (id: string, start_ms: number, text: string): PieceEvent => ({ id, start_ms, end_ms: null, text, final: false });

const timed = (start_ms: number, end_ms: number, text: string) => ({ start_ms, end_ms, text });

function withLive(...pieces: PieceEvent[]): TranscriptState {
  return pieces.reduce(applyPiece, emptyTranscript());
}

describe("live pieces", () => {
  it("upserts a draft by id and replaces it with the final sentences", () => {
    let state = applyPiece(emptyTranscript(), draft("a-1-1", 1_000, "We'll use"));
    state = applyPiece(state, draft("a-1-1", 1_000, "We'll use gradient"));
    expect(state.draft).toEqual({ pieceId: "a-1-1", startMs: 1_000, text: "We'll use gradient" });
    expect(state.live).toEqual([]);

    state = applyPiece(state, final("a-1-1", [[1_000, 3_000, "We'll use gradient descent."], [3_200, 4_000, "It works."]]));
    expect(state.draft).toBeNull();
    expect(state.live.map((sentence) => [sentence.id, sentence.startMs, sentence.text])).toEqual([
      ["a-1-1.1", 1_000, "We'll use gradient descent."],
      ["a-1-1.2", 3_200, "It works."],
    ]);
  });

  it("keeps the newest draft when an older piece finishes, and ignores empty text", () => {
    let state = applyPiece(emptyTranscript(), draft("a-1-2", 5_000, "next words"));
    state = applyPiece(state, final("a-1-1", [[1_000, 3_000, "Before."]]));
    expect(state.draft?.pieceId).toBe("a-1-2");
    expect(applyPiece(state, draft("a-1-2", 5_000, "  ")).draft).toBeNull();
    expect(applyPiece(emptyTranscript(), final("a-1-3", [[0, 500, "   "]])).live).toEqual([]);
  });

  it("does not take back audio that was replayed after a reconnect", () => {
    let state = withLive(final("a-1-1", [[0, 70_000, "One long first stretch."]]));
    state = confirmLive(state);
    expect(state.confirmedUntilMs).toBe(70_000);
    state = applyPiece(state, final("b-1-1", [[0, 70_000, "One long first stretch."]]));
    expect(state.live).toEqual([]);
  });

  it("applies socket events: drafts, finals, a gap, and nothing for other events", () => {
    const piece = (event: PieceEvent) => ({ type: "transcript.piece", ...event });
    const events: Record<string, unknown>[] = [
      { type: "service.ready", live: "stream" },
      piece(draft("k-1-1", 0, "hello")),
      piece(final("k-1-1", [[0, 900, "Hello there."]])),
      { type: "transcript.gap", start_ms: 900, end_ms: 4_000, reason: "connection" },
      piece(draft("k-1-2", 4_000, "second")),
    ];
    let state = emptyTranscript();
    for (const event of events) state = applyLiveEvent(state, event) ?? state;
    expect(applyLiveEvent(state, { type: "service.ready" })).toBeNull();
    expect(state.live.map((sentence) => sentence.text)).toEqual(["Hello there."]);
    expect(state.draft?.text).toBe("second");
    expect(state.gaps).toEqual([{ startMs: 900, endMs: 4_000, reason: "connection" }]);
  });
});

describe("paragraphs without the minute check", () => {
  it("forms a paragraph at the first live sentence end 60 s on, never at a pause by itself", () => {
    // A 10 s silence between two sentences at 20 s does not end anything.
    let state = withLive(
      final("a-1-1", [[0, 5_000, "First thought."]]),
      final("a-1-2", [[15_000, 20_000, "After a long think."]]),
    );
    expect(nextWindow(state)).toBeNull();
    expect(confirmLive(state).paragraphs).toEqual([]);

    state = applyPiece(state, final("a-1-3", [[20_500, 61_000, "And on it goes."]]));
    expect(nextWindow(state)).toEqual({ startMs: 0, endMs: 61_000 });
    const confirmed = confirmLive(state);
    expect(confirmed.paragraphs).toHaveLength(1);
    expect(confirmed.paragraphs[0]).toMatchObject({ id: "p-0", frozen: true });
    expect(paragraphText(confirmed, confirmed.paragraphs[0]!)).toBe("First thought. After a long think. And on it goes.");
    expect(confirmed.confirmedUntilMs).toBe(61_000);
    expect(confirmed.live).toEqual([]);
    expect(confirmed.windows[0]).toMatchObject({ id: "w-0", state: "off" });
    expect(confirmed.windows[0]!.live).toHaveLength(3);
  });

  it("caps a window at 180 s and goes on with the rest", () => {
    const state = withLive(
      final("a-1-1", [[0, 100_000, "A."]]),
      final("a-1-2", [[100_000, 170_000, "B."]]),
      final("a-1-3", [[170_000, 250_000, "C."]]),
    );
    expect(nextWindow(state)).toEqual({ startMs: 0, endMs: 170_000 });
    const confirmed = confirmLive(state);
    expect(confirmed.paragraphs.map((paragraph) => paragraph.id)).toEqual(["p-0", "p-170000"]);
  });

  it("confirms everything when closing and drops the draft", () => {
    let state = withLive(final("a-1-1", [[0, 4_000, "Short."]]), draft("a-1-2", 5_000, "unfinished"));
    expect(confirmLive(state).paragraphs).toEqual([]);
    state = confirmLive(state, { closing: true });
    expect(state.paragraphs).toHaveLength(1);
    expect(state.live).toEqual([]);
    expect(state.draft).toBeNull();
    expect(renderTranscript(state)).toBe("[00:00] Short.");
  });
});

describe("the minute check", () => {
  const rules = { windowMs: 60_000, maxWindowMs: 180_000, wholeWindowMs: 150_000 };
  const live = () => withLive(
    final("a-1-1", [[0, 20_000, "gradient didn't decent to train the model."]]),
    final("a-1-2", [[20_000, 40_000, "The learning rate is a step."]]),
    final("a-1-3", [[40_000, 65_000, "Then we shrink it over time"]]),
  );

  it("starts a window only once live sentences reach 60 s past the confirmed point", () => {
    expect(nextWindow(withLive(final("a-1-1", [[0, 59_000, "x"]])), rules)).toBeNull();
    expect(nextWindow(live(), rules)).toEqual({ startMs: 0, endMs: 65_000 });
  });

  it("is one at a time", () => {
    const busy = { ...live(), checking: { startMs: 0, endMs: 65_000 } };
    expect(nextWindow(busy, rules)).toBeNull();
  });

  it("confirms all but the last sentence and holds the last one back", () => {
    const result = {
      text: "",
      sentences: [
        timed(0, 20_000, "Gradient descent to train the model."),
        timed(20_000, 40_000, "The learning rate is a step."),
        timed(40_000, 65_500, "Then we shrink it over time."),
      ],
    };
    const state = applyWindowResult(live(), { startMs: 0, endMs: 65_000 }, result, { rules, now: "t" });
    expect(state.sentences.map((sentence) => sentence.id)).toEqual(["s-0", "s-20000"]);
    expect(state.confirmedUntilMs).toBe(40_000);
    expect(state.paragraphs).toEqual([{ id: "p-0", sentenceIds: ["s-0", "s-20000"], frozen: true }]);
    // The live sentence that covers the held-back speech keeps showing.
    expect(state.live.map((sentence) => sentence.text)).toEqual(["Then we shrink it over time"]);
    expect(state.windows[0]).toMatchObject({ id: "w-0", state: "checked", pass: "minute", at: "t" });
    expect(state.windows[0]!.live).toHaveLength(3);
    // The next window starts at the end of the kept sentences.
    expect(state.checking).toBeNull();
  });

  it("marks the words the check changed, by token", () => {
    const result = {
      text: "",
      sentences: [timed(0, 20_000, "Gradient descent to train the model."), timed(20_000, 40_000, "The learning rate is a step."), timed(40_000, 65_000, "Last.")],
    };
    const state = applyWindowResult(live(), { startMs: 0, endMs: 65_000 }, result, { rules });
    const first = state.sentences[0]!;
    // "gradient didn't decent" -> "Gradient descent": "descent" is new, the rest is the same.
    const tokens = tokenize(first.text);
    const changed = (first.changed ?? []).flatMap(([from, to]) => tokens.slice(from, to));
    expect(changed).toEqual(["descent"]);
    expect(state.sentences[1]!.changed).toBeUndefined();
  });

  it("holds a single sentence back under 150 s and confirms it over that", () => {
    const one = { text: "", sentences: [timed(0, 65_000, "A whole minute without a sentence end.")] };
    const held = applyWindowResult(live(), { startMs: 0, endMs: 65_000 }, one, { rules });
    expect(held.sentences).toEqual([]);
    expect(held.confirmedUntilMs).toBe(0);
    expect(held.live).toHaveLength(3);
    // The same window is not asked again until another minute of speech has come.
    expect(nextWindow(held, rules)).toBeNull();
    expect(nextWindow(applyPiece(held, final("a-1-4", [[65_000, 124_000, "More."]])), rules)).toBeNull();
    expect(nextWindow(applyPiece(held, final("a-1-4", [[65_000, 126_000, "More."]])), rules)).toEqual({ startMs: 0, endMs: 126_000 });

    const long = withLive(final("a-1-1", [[0, 70_000, "x"]]), final("a-1-2", [[70_000, 155_000, "y"]]));
    const confirmed = applyWindowResult(long, { startMs: 0, endMs: 155_000 }, { text: "", sentences: [timed(0, 155_000, "One long sentence.")] }, { rules });
    expect(confirmed.sentences.map((sentence) => sentence.text)).toEqual(["One long sentence."]);
    expect(confirmed.confirmedUntilMs).toBe(155_000);
  });

  it("takes the whole window as one paragraph when the model gives no word times", () => {
    const state = applyWindowResult(live(), { startMs: 0, endMs: 65_000 }, { text: "Everything the model heard.", sentences: [] }, { rules });
    expect(state.sentences).toHaveLength(1);
    expect(state.sentences[0]).toMatchObject({ startMs: 0, endMs: 65_000, text: "Everything the model heard." });
    expect(state.confirmedUntilMs).toBe(65_000);
    expect(state.live).toEqual([]);
  });

  it("confirms everything at Pause and Stop", () => {
    const result = { text: "", sentences: [timed(0, 20_000, "One."), timed(20_000, 65_000, "Two.")] };
    const state = applyWindowResult(live(), { startMs: 0, endMs: 65_000 }, result, { rules, closing: true });
    expect(state.sentences.map((sentence) => sentence.text)).toEqual(["One.", "Two."]);
    expect(state.confirmedUntilMs).toBe(65_000);
    expect(state.live).toEqual([]);
  });

  it("keeps the live text as a failed window and goes on after it", () => {
    const failed = applyWindowResult(live(), { startMs: 0, endMs: 65_000 }, { error: "The service said no" }, { rules });
    expect(failed.windows[0]).toMatchObject({ id: "w-0", state: "failed", error: "The service said no" });
    expect(failed.sentences.map((sentence) => sentence.text)).toEqual([
      "gradient didn't decent to train the model.",
      "The learning rate is a step.",
      "Then we shrink it over time",
    ]);
    expect(failed.confirmedUntilMs).toBe(65_000);
    expect(failed.live).toEqual([]);
    expect(retryRange(failed, "w-0")).toEqual({ startMs: 0, endMs: 65_000 });
    // The next window starts where the failed one ended.
    const more = applyPiece(failed, final("a-1-9", [[66_000, 130_000, "Later."]]));
    expect(nextWindow(more, rules)).toEqual({ startMs: 65_000, endMs: 130_000 });
  });

  it("treats an empty answer as an error, not as text", () => {
    const state = applyWindowResult(live(), { startMs: 0, endMs: 65_000 }, { text: " ", sentences: [] }, { rules });
    expect(state.windows[0]).toMatchObject({ state: "failed", error: "The model returned no text." });
  });
});

describe("retry and edits", () => {
  const two = () => {
    let state = withLive(final("a-1-1", [[0, 65_000, "First minute."]]), final("a-1-2", [[65_000, 130_000, "Second minute."]]));
    state = applyWindowResult(state, { startMs: 0, endMs: 65_000 }, { error: "boom" }, {});
    state = applyWindowResult(state, { startMs: 65_000, endMs: 130_000 }, null, { checksOff: true });
    return state;
  };

  it("replaces only that window's sentences and puts them in the paragraph holding their time", () => {
    const state = two();
    expect(state.paragraphs.map((paragraph) => paragraph.id)).toEqual(["p-0", "p-65000"]);
    const retried = applyRetry(state, "w-0", { text: "", sentences: [timed(0, 30_000, "Checked first."), timed(30_000, 65_000, "Checked second.")] });
    expect(retried.windows[0]).toMatchObject({ id: "w-0", state: "checked", pass: "retry" });
    expect(retried.sentences.map((sentence) => sentence.text)).toEqual(["Checked first.", "Checked second.", "Second minute."]);
    expect(paragraphText(retried, retried.paragraphs[0]!)).toBe("Checked first. Checked second.");
    expect(paragraphText(retried, retried.paragraphs[1]!)).toBe("Second minute.");
  });

  it("keeps the failure when the retry fails too", () => {
    const retried = applyRetry(two(), "w-0", { error: "still no" });
    expect(retried.windows[0]).toMatchObject({ state: "failed", error: "still no", pass: "retry" });
    expect(retried.sentences).toHaveLength(2);
  });

  it("never changes a paragraph the user edited", () => {
    const edited = editParagraph(two(), "p-0", "My own words for the first minute.");
    expect(edited.paragraphs[0]).toMatchObject({ edited: "My own words for the first minute.", frozen: true });
    const retried = applyRetry(edited, "w-0", { text: "", sentences: [timed(0, 65_000, "A check result.")] });
    expect(paragraphText(retried, retried.paragraphs[0]!)).toBe("My own words for the first minute.");
    expect(renderTranscript(retried)).toBe("[00:00] My own words for the first minute.\n\n[01:05] Second minute.");
  });

  it("does not mark a paragraph edited when the text did not change", () => {
    const state = two();
    expect(editParagraph(state, "p-0", "First minute.")).toBe(state);
    expect(editParagraph(state, "p-0", "   ")).toBe(state);
  });
});

describe("text", () => {
  it("joins with no space inside Chinese and after Chinese punctuation, and one space otherwise", () => {
    expect(joinSentences(["今天讲梯度下降", "然后看学习率"])).toBe("今天讲梯度下降然后看学习率");
    expect(joinSentences(["好，我们开始。", "Today we use SGD."])).toBe("好，我们开始。Today we use SGD.");
    expect(joinSentences(["We use Adam.", "Then we go on."])).toBe("We use Adam. Then we go on.");
    expect(joinSentences(["We use Adam", "然后继续"])).toBe("We use Adam 然后继续");
    expect(joinSentences([" a ", "", "b"])).toBe("a b");
  });

  it("tokenizes Chinese by character and Latin by word", () => {
    expect(tokenize("用 Adam 做 SGD, don't!")).toEqual(["用", "adam", "做", "sgd", "don't"]);
    expect(changedRanges("the cat sat", ["the dog sat"])).toEqual([[[1, 2]]]);
  });

  it("renders gaps and live text, and a draft never", () => {
    let state = withLive(final("a-1-1", [[0, 4_000, "Confirmed one."]]));
    state = confirmLive(state, { closing: true });
    state = applyPiece(state, final("a-1-2", [[700_000, 705_000, "Still live."]]));
    state = applyPiece(state, draft("a-1-3", 706_000, "being heard"));
    state = applyLiveEvent(state, { type: "transcript.gap", start_ms: 123_000, end_ms: 160_000, reason: "connection" })!;
    expect(renderTranscript(state)).toBe([
      "[00:00] Confirmed one.",
      "[02:03] (no audio was transcribed from 02:03 to 02:40)",
      "[11:40] Still live.",
    ].join("\n\n"));
    expect(clockLabel(3_725_000)).toBe("01:02:05");
  });

  it("renders to text that parses back to one timed segment per paragraph", () => {
    let state = confirmLive(withLive(final("a-1-1", [[0, 65_000, "First point. It has two sentences."]])));
    state = confirmLive(applyPiece(state, final("a-1-2", [[70_000, 140_000, "Second point."]])));
    state = applyPiece(state, final("a-1-3", [[141_000, 150_000, "Live tail."]]));
    state = editParagraph(state, "p-0", "First point,\nedited over two lines.");
    const parsed = parseTranscript(renderTranscript(state));
    expect(parsed).toEqual([
      { startSeconds: 0, text: "First point, edited over two lines." },
      { startSeconds: 70, text: "Second point." },
      { startSeconds: 141, text: "Live tail." },
    ]);
  });

  it("makes locked paragraphs from older saved text, and none from text without times", () => {
    const state = transcriptFromText(parseTranscript("[00:00] First.\n[00:30] Second.\n[00:30] Third at the same time."))!;
    expect(state.paragraphs.map((paragraph) => [paragraph.id, paragraph.edited, paragraph.frozen])).toEqual([
      ["p-0", "First.", true],
      ["p-30000", "Second.", true],
      ["p-30000+", "Third at the same time.", true],
    ]);
    expect(state.windows[0]).toMatchObject({ id: "w-restored", state: "off" });
    expect(renderTranscript(state)).toBe("[00:00] First.\n\n[00:30] Second.\n\n[00:30] Third at the same time.");
    expect(transcriptFromText(parseTranscript("Just pasted notes.\n\nAnother paragraph."))).toBeNull();
  });

  it("recognises a stored state and strips what cannot be finished after a restart", () => {
    const state = { ...withLive(draft("a-1-1", 0, "x")), checking: { startMs: 0, endMs: 1 } };
    const stored = storableTranscript(state);
    expect(stored.draft).toBeNull();
    expect(stored.checking).toBeNull();
    expect(isTranscriptState(JSON.parse(JSON.stringify(stored)))).toBe(true);
    expect(isTranscriptState({ version: 2 })).toBe(false);
    expect(isTranscriptState(null)).toBe(false);
  });
});
