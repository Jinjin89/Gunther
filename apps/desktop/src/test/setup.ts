import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom leaves out a few layout APIs that real browsers (and CodeMirror) rely on.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => undefined;
if (!Range.prototype.getClientRects) {
  Range.prototype.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] }) as unknown as DOMRectList;
  Range.prototype.getBoundingClientRect = () => new DOMRect();
}

afterEach(() => {
  cleanup();
});
