import { useEffect, type RefObject } from "react";

/** Close only for a press inside the workspace and outside this column. */
export function useSlideOverOutside(workspace: RefObject<HTMLElement | null>, column: RefObject<HTMLElement | null>,
  enabled: boolean, onClose: () => void) {
  useEffect(() => {
    if (!enabled) return;
    const close = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && workspace.current?.contains(target) && column.current && !column.current.contains(target)) onClose();
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [workspace, column, enabled, onClose]);
}
