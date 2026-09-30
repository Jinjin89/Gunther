import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useDragWidth } from "./useDragWidth";

const options = { storageKey: "test:width", min: 200, max: () => 500, fallback: 300 };
const pointer = (x: number) => ({ clientX: x, pointerId: 1, currentTarget: { setPointerCapture: () => undefined } }) as never;

describe("useDragWidth", () => {
  beforeEach(() => window.localStorage.clear());

  it("widens as the edge is dragged left, stays within its limits, and is remembered", () => {
    const { result } = renderHook(() => useDragWidth(options));
    expect(result.current.width).toBe(300);
    act(() => result.current.handle.onPointerDown(pointer(800)));
    act(() => result.current.handle.onPointerMove(pointer(700)));
    expect(result.current.width).toBe(400);
    act(() => result.current.handle.onPointerUp(pointer(0)));
    expect(result.current.width).toBe(500);
    expect(window.localStorage.getItem("test:width")).toBe("500");
    const again = renderHook(() => useDragWidth(options));
    expect(again.result.current.width).toBe(500);
  });

  it("moves by the arrow keys and resets on a double-click", () => {
    const { result } = renderHook(() => useDragWidth(options));
    const key = (name: string) => ({ key: name, preventDefault: () => undefined }) as never;
    act(() => result.current.handle.onKeyDown(key("ArrowLeft")));
    expect(result.current.width).toBe(324);
    act(() => result.current.handle.onKeyDown(key("ArrowRight")));
    act(() => result.current.handle.onKeyDown(key("ArrowRight")));
    expect(result.current.width).toBe(276);
    act(() => result.current.handle.onDoubleClick());
    expect(result.current.width).toBe(300);
  });
});
