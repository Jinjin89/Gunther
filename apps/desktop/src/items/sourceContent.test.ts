import { describe, expect, it } from "vitest";
import {
  formatBytes,
  formatClock,
  formatDuration,
  inboxPreview,
  mediaTypeLabel,
  parseDelimitedTable,
  parseFileContent,
  parseRecordingContent,
  parseResearchContent,
  parseWebContent,
  sourceView,
  withoutRepeatedTitle,
} from "./sourceContent";

const recording = `# Cell biology lecture

Duration: 00:42:10 · Captured: 9/27/2026, 10:00:00 AM · Local recording: rec_0123456789abcdef01234567

## Marked moments

- 00:03:05 · Definition of a marker gene
- 00:20:00 · Exam hint

## Summary

Markers identify **cell types**.

## Key points

- Markers are context dependent
- Validate with a second method

## Actions

- Re-read chapter 4

## Open questions

- How stable are markers across tissues?

Terms: marker gene · cluster

## Full transcript

[00:00:01] Welcome back.
[00:00:09] Today we talk about markers.
No timestamp here.`;

describe("source content envelopes", () => {
  it("reads a recording into its parts", () => {
    const parsed = parseRecordingContent(recording);
    expect(parsed.title).toBe("Cell biology lecture");
    expect(parsed.durationSeconds).toBe(2530);
    expect(parsed.recordingId).toBe("rec_0123456789abcdef01234567");
    expect(parsed.moments).toEqual([{ seconds: 185, label: "Definition of a marker gene" }, { seconds: 1200, label: "Exam hint" }]);
    expect(parsed.summary).toBe("Markers identify **cell types**.");
    expect(parsed.keyPoints).toHaveLength(2);
    expect(parsed.actions).toEqual(["Re-read chapter 4"]);
    expect(parsed.openQuestions).toEqual(["How stable are markers across tissues?"]);
    expect(parsed.terms).toEqual(["marker gene", "cluster"]);
    expect(parsed.transcript).toEqual([
      { startSeconds: 1, text: "Welcome back." },
      { startSeconds: 9, text: "Today we talk about markers." },
      { startSeconds: null, text: "No timestamp here." },
    ]);
    expect(sourceView("recording", recording)).toBe("recording");
  });

  it("reads a recording saved without a summary", () => {
    const parsed = parseRecordingContent("# Memo\n\nDuration: 00:01:02 · Captured: today\n\n## Transcript\n\nFirst thought.\nSecond line.\n\nNew paragraph.");
    expect(parsed.recordingId).toBeNull();
    expect(parsed.summary).toBeNull();
    expect(parsed.transcript.map((segment) => segment.text)).toEqual(["First thought. Second line.", "New paragraph."]);
  });

  it("reads web snapshots, uploaded files and web research", () => {
    const web = parseWebContent("# Web snapshot\n\nOriginal URL: https://example.com/a\nFinal URL: https://www.example.com/a?b=1\nCaptured at: 2026-09-27T10:00:00.000Z\nHTTP status: 200\nContent type: text/html\nSHA-256: abc\nSnapshot ID: snap\n\n## Your context\n\nWhy I saved it\n\n## Captured content\n\n## A heading inside the page\n\nBody text.");
    expect(web.finalUrl).toBe("https://www.example.com/a?b=1");
    expect(web.context).toBe("Why I saved it");
    expect(web.body).toBe("## A heading inside the page\n\nBody text.");
    expect(sourceView("link", "# Web snapshot\n\nOriginal URL: x")).toBe("web");

    const file = parseFileContent("# Original file\n\nFile: report.pdf\nMedia type: application/pdf\nSize: 2516582 bytes\nSHA-256: def\n\n## Your context\n\nRead section 2\n\n## OCR provenance\n\nOCR status: complete\nOCR provider: vision\n\n## Extracted content\n\nText.");
    expect(file).toMatchObject({ fileName: "report.pdf", mediaType: "application/pdf", sizeBytes: 2516582, context: "Read section 2", ocrStatus: "complete", ocrProvider: "vision", extracted: "Text." });
    expect(sourceView("file", "# Original file\n\nFile: a.png\nMedia type: image/png", "image/png")).toBe("image");
    expect(sourceView("paper", "Pasted text without a file.")).toBe("text");
    expect(sourceView("image", "A description only.")).toBe("text");

    const research = parseResearchContent("Search query: marker genes\n\nAnswer captured from web research:\nMarkers are genes.\n\nReferenced pages:\n1. A guide\nhttps://a.example/guide\nUseful summary\n\n2. Another\nhttps://b.example");
    expect(research.query).toBe("marker genes");
    expect(research.answer).toBe("Markers are genes.");
    expect(research.references).toEqual([
      { title: "A guide", url: "https://a.example/guide", snippet: "Useful summary" },
      { title: "Another", url: "https://b.example", snippet: null },
    ]);
  });

  it("turns service previews into readable Inbox lines", () => {
    expect(inboxPreview({ itemType: "source", sourceKind: "file", preview: "# Original file File: report.pdf Media type: application/pdf Size: 2516582 bytes SHA-256: def ## Your context Read section 2" }))
      .toEqual({ detail: "PDF · 2.4 MB · report.pdf", text: "Read section 2" });
    expect(inboxPreview({ itemType: "source", sourceKind: "link", preview: "# Web snapshot Original URL: https://example.com/a Final URL: https://www.example.com/a Captured at: now HTTP status: 200 Content type: text/html SHA-256: abc Snapshot ID: s ## Captured content Hello world" }))
      .toEqual({ detail: "example.com/a", text: "Hello world" });
    expect(inboxPreview({ itemType: "source", sourceKind: "recording", preview: "# Lecture Duration: 00:42:10 · Captured: today · Local recording: rec_0123456789abcdef01234567 ## Summary Markers identify **cell types**. ## Key points - a" }))
      .toEqual({ detail: "42 min", text: "Markers identify cell types." });
    expect(inboxPreview({ itemType: "quick_note", sourceKind: "note", preview: "## Idea - [ ] call **Ana**" })).toEqual({ detail: null, text: "Idea call Ana" });
    expect(inboxPreview({ itemType: "source", sourceKind: "table", preview: "9 rows · Columns: cluster, cells, top marker" })).toEqual({ detail: "9 rows · 3 columns", text: "cluster, cells, top marker" });
  });

  it("parses pasted tables and detects numeric columns", () => {
    expect(parseDelimitedTable("gene\tcount\tnote\nCD3E\t1,204\tT cells\nMS4A1\t88\t\"B, cells\"")).toEqual({
      header: ["gene", "count", "note"],
      rows: [["CD3E", "1,204", "T cells"], ["MS4A1", "88", "B, cells"]],
      delimiter: "\t",
      numericColumns: [false, true, false],
    });
    expect(parseDelimitedTable("a,b\n1,\"x, y\"\n2,z")?.rows).toEqual([["1", "x, y"], ["2", "z"]]);
    expect(parseDelimitedTable("| a | b |\n|---|--:|\n| 1 | 2 |")).toMatchObject({ delimiter: "|", header: ["a", "b"], rows: [["1", "2"]] });
    expect(parseDelimitedTable("just one line of prose")).toBeNull();
    expect(parseDelimitedTable("\tcount\ttype\nCD3E\t12\tT cells")?.header).toEqual(["", "count", "type"]);
  });

  it("formats sizes, durations and titles", () => {
    expect(formatBytes(812)).toBe("812 bytes");
    expect(formatBytes(2516582)).toBe("2.4 MB");
    expect(formatDuration(2530)).toBe("42 min");
    expect(formatDuration(3900)).toBe("1 h 5 min");
    expect(formatClock(3723)).toBe("1:02:03");
    expect(formatClock(65)).toBe("1:05");
    expect(mediaTypeLabel("application/octet-stream", "notes.md")).toBe("Markdown");
    expect(withoutRepeatedTitle("# My note\n\nBody", "my note")).toBe("Body");
    expect(withoutRepeatedTitle("# Other\n\nBody", "My note")).toBe("# Other\n\nBody");
  });
});
