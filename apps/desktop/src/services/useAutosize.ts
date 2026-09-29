import { useLayoutEffect, type RefObject } from "react";

/** Grow a textarea with its text, up to `maxHeight` pixels, then let it scroll. */
export function useAutosize(field: RefObject<HTMLTextAreaElement | null>, value: string, maxHeight: number) {
  useLayoutEffect(() => {
    const element = field.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, maxHeight)}px`;
    element.style.overflowY = element.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [field, value, maxHeight]);
}
