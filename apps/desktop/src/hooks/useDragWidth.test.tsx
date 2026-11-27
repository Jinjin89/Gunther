import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useDragWidth } from "./useDragWidth";

const options = { storageKey: "test:width", min: 200, max: 500, fallback: 300 };
const pointer = (x: number) => ({ clientX: x, button: 0, pointerId: 1, currentTarget: { setPointerCapture: () => undefined } }) as never;
const key = (name: string) => ({ key: name, preventDefault: () => undefined }) as never;

describe("useDragWidth", () => {
  beforeEach(() => window.localStorage.clear());

  it("widens as the edge is dragged left, stays within its limits, and is remembered", () => {
    const { result } = renderHook(() => useDragWidth(options));
    expect(result.current.width).toBe(300);
    act(() => result.current.handle.onPointerDown(pointer(800)));
    act(() => result.current.handle.onPointerMove(pointer(700)));
    expect(result.current.width).toBe(400);
    expect(result.current.chosenWidth).toBeNull();
    expect(window.localStorage.getItem("test:width")).toBeNull();
    act(() => result.current.handle.onPointerMove(pointer(1_000)));
    expect(result.current.width).toBe(200);
    act(() => result.current.handle.onPointerUp(pointer(0)));
    expect(result.current.width).toBe(500);
    expect(result.current.chosenWidth).toBe(500);
    expect(window.localStorage.getItem("test:width")).toBe("500");
    const again = renderHook(() => useDragWidth(options));
    expect(again.result.current.width).toBe(500);
  });

  it("follows the live default until the arrow keys choose a width and after double-click clears it", () => {
    const { result, rerender } = renderHook((fallback) => useDragWidth({ ...options, fallback }), { initialProps: 300 });
    act(() => result.current.handle.onPointerDown(pointer(800)));
    act(() => result.current.handle.onPointerUp(pointer(800)));
    expect(result.current.chosenWidth).toBeNull();
    rerender(320);
    expect(result.current.width).toBe(320);
    act(() => result.current.handle.onKeyDown(key("ArrowLeft")));
    expect(result.current.width).toBe(344);
    expect(window.localStorage.getItem("test:width")).toBe("344");
    rerender(360);
    expect(result.current.width).toBe(344);
    act(() => result.current.handle.onKeyDown(key("ArrowRight")));
    act(() => result.current.handle.onKeyDown(key("ArrowRight")));
    expect(result.current.width).toBe(296);
    act(() => result.current.handle.onDoubleClick());
    expect(result.current.width).toBe(360);
    expect(result.current.chosenWidth).toBeNull();
    expect(window.localStorage.getItem("test:width")).toBeNull();
    rerender(340);
    expect(result.current.width).toBe(340);
  });

  it("cancels a preview on pointer cancel or when the dock loses its grip", () => {
    const { result, rerender } = renderHook((fallback) => useDragWidth({ ...options, fallback }), { initialProps: 300 });
    act(() => result.current.handle.onPointerDown(pointer(800)));
    act(() => result.current.handle.onPointerMove(pointer(700)));
    rerender(350);
    expect(result.current.width).toBe(400);
    act(() => result.current.handle.onPointerCancel(pointer(700)));
    expect(result.current.width).toBe(350);
    act(() => result.current.handle.onPointerDown(pointer(800)));
    act(() => result.current.handle.onPointerMove(pointer(700)));
    act(() => result.current.handle.ref(null));
    act(() => result.current.handle.onPointerUp(pointer(700)));
    expect(result.current.width).toBe(350);
    expect(window.localStorage.getItem("test:width")).toBeNull();
  });

  it("keeps what is shown within what fits when the window shrinks, without forgetting the choice", () => {
    let room = 500;
    const { result } = renderHook(() => useDragWidth({ ...options, max: () => room }));
    act(() => result.current.handle.onKeyDown({ key: "ArrowLeft", preventDefault: () => undefined } as never));
    act(() => result.current.handle.onKeyDown({ key: "ArrowLeft", preventDefault: () => undefined } as never));
    expect(result.current.width).toBe(348);
    room = 320;
    act(() => { window.dispatchEvent(new Event("resize")); });
    expect(result.current.width).toBe(320);
    expect(result.current.chosenWidth).toBe(348);
    expect(window.localStorage.getItem("test:width")).toBe("348");
    room = 500;
    act(() => { window.dispatchEvent(new Event("resize")); });
    expect(result.current.width).toBe(348);
  });
});
