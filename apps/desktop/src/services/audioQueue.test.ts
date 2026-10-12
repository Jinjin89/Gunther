import { describe, expect, it } from "vitest";
import { flushAudio, offerAudio } from "./audioQueue";

describe("audio queue", () => {
  it("sends in the order captured when the socket opens before the service is ready", () => {
    const wire: number[] = [];
    const queue: number[] = [];
    const send = (item: number) => wire.push(item);
    // Captured while connecting.
    offerAudio(queue, 0, false, send);
    offerAudio(queue, 1, false, send);
    // The socket is open but the older pieces were not flushed yet: the new one waits behind them.
    expect(offerAudio(queue, 2, true, send)).toBe(false);
    expect(wire).toEqual([]);
    // onopen: flush first, then new audio goes straight out.
    expect(flushAudio(queue, send)).toBe(3);
    expect(offerAudio(queue, 3, true, send)).toBe(true);
    expect(wire).toEqual([0, 1, 2, 3]);
  });

  it("flushing again later is harmless", () => {
    const queue: number[] = [];
    expect(flushAudio(queue, () => { throw new Error("nothing to send"); })).toBe(0);
  });
});
