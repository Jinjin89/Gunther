import { describe, expect, it } from "vitest";
import { DictationInsertion, dictationText, updateDictation, type DictationText } from "./dictationText";

const piece = (id: string, text: string, start_ms = 0, final = false) => ({ type: "transcript.piece", id, text, start_ms, final });

describe("live dictation text", () => {
  it("replaces drafts, confirms once, ignores late drafts and orders sentences", () => {
    let text: DictationText = [];
    text = updateDictation(text, piece("a", "今天"));
    text = updateDictation(text, piece("a", "今天天气很好。", 0, true));
    text = updateDictation(text, piece("c", "再见。", 2000, true));
    text = updateDictation(text, piece("b", "我们去散步。", 1000, true));
    const confirmed = text;
    expect(updateDictation(text, piece("a", "今天"))).toBe(confirmed);
    expect(updateDictation(text, piece("a", "重复", 0, true))).toBe(confirmed);
    expect(dictationText(text)).toBe("今天天气很好。我们去散步。再见。");
  });

  it("keeps the latest sentence alongside earlier recognized text", () => {
    const pieces = updateDictation(updateDictation([], piece("a", "Hello.", 0, true)), piece("b", "More words", 1000));
    expect(dictationText(pieces)).toBe("Hello. More words");
  });
});

describe("controlled-field insertion", () => {
  it("preserves the initial draft while replacing ASR text", () => {
    const insertion = new DictationInsertion();
    insertion.begin("已有问题：");
    let pieces = updateDictation([], piece("a", "今天"));
    let value = insertion.update("已有问题：", pieces);
    expect(value).toBe("已有问题：今天");
    pieces = updateDictation(pieces, piece("a", "今天天气很好。", 0, true));
    value = insertion.update(value, pieces);
    expect(value).toBe("已有问题：今天天气很好。");
  });

  it("keeps manual edits to a dictated sentence and appends the next sentence", () => {
    const insertion = new DictationInsertion();
    insertion.begin("");
    let pieces = updateDictation([], piece("a", "识别错了"));
    insertion.update("", pieces);
    pieces = updateDictation(pieces, piece("a", "识别错了。", 0, true));
    let value = insertion.update("我手动改好了。", pieces);
    expect(value).toBe("我手动改好了。");
    pieces = updateDictation(pieces, piece("b", "继续说下一句。", 1000, true));
    value = insertion.update(value, pieces);
    expect(value).toBe("我手动改好了。继续说下一句。");
  });

  it("keeps edits before and after the owned region", () => {
    const insertion = new DictationInsertion();
    insertion.begin("Question:");
    let pieces = updateDictation([], piece("a", "hello"));
    insertion.update("Question:", pieces);
    pieces = updateDictation(pieces, piece("a", "hello there"));
    let value = insertion.update("Revised question: hello", pieces);
    expect(value).toBe("Revised question: hello there");
    pieces = updateDictation(pieces, piece("a", "hello there.", 0, true));
    value = insertion.update(value + " [my note]", pieces);
    expect(value).toBe("Revised question: hello there. [my note]");
  });

  it("preserves typing that arrives before the first ASR result", () => {
    const insertion = new DictationInsertion();
    insertion.begin("");
    expect(insertion.update("Typed first.", updateDictation([], piece("a", "Spoken next.")))).toBe("Typed first. Spoken next.");
  });
});
