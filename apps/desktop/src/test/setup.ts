import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom leaves out a few layout APIs that real browsers (and CodeMirror) rely on.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => undefined;
if (!Range.prototype.getClientRects) {
  Range.prototype.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] }) as unknown as DOMRectList;
  Range.prototype.getBoundingClientRect = () => new DOMRect();
}

// CI machines are slower than a laptop, and the whole suite runs at once: give findBy and
// waitFor 3 s instead of 1 before a test fails. A passing test is not slowed by it.
configure({ asyncUtilTimeout: 3_000 });

afterEach(() => {
  cleanup();
});
