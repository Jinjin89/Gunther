import { describe, expect, it } from "vitest";
import { readServerEvents } from "./sse";

const streamOf = (...chunks: string[]) => new ReadableStream<Uint8Array>({
  start(controller) {
    const encoder = new TextEncoder();
    for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
    controller.close();
  },
});

describe("readServerEvents", () => {
  it("reads events split across chunks, in order", async () => {
    const events = [];
    for await (const event of readServerEvents(streamOf('event: text\ndata: {"text":"Hel', 'lo"}\n\nevent: done\n', 'data: {"ok":true}\n\n'))) events.push(event);
    expect(events).toEqual([
      { event: "text", data: '{"text":"Hello"}' },
      { event: "done", data: '{"ok":true}' },
    ]);
  });

  it("ignores an unfinished event at the end", async () => {
    const events = [];
    for await (const event of readServerEvents(streamOf('event: text\ndata: {"a":1}\n\nevent: text\ndata: {"b"'))) events.push(event);
    expect(events).toHaveLength(1);
  });
});
