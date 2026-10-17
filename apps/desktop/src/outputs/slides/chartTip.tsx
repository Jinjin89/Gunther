import { type CSSProperties, type HTMLAttributes, useState } from "react";
import { type ChartPoint, plainText } from "./parseSlide";
import { useSlide } from "./Rich";

/**
 * Which mark of a chart the pointer or the keyboard is on. Only the deck on the page has marks to
 * point at (it is the one with citations to open); Present, the strip and the printed page do not.
 * A mark that can be pointed at can also be tabbed to, unless it is only a second place to point
 * at the same row (`focusable` false).
 */
export function useTip(): { active: number | null; interactive: boolean; bind: (index: number, focusable?: boolean) => HTMLAttributes<SVGElement | HTMLElement> } {
  const { onCite } = useSlide();
  const [active, setActive] = useState<number | null>(null);
  return {
    active,
    interactive: Boolean(onCite),
    bind: (index, focusable = true) => onCite
      ? {
        ...(focusable ? { tabIndex: 0, onFocus: () => setActive(index), onBlur: () => setActive(null) } : {}),
        onPointerEnter: () => setActive(index),
        onPointerLeave: () => setActive(null),
      }
      : {},
  };
}

/** The value of a mark and the sources it cites: what hovering or focusing it shows. */
export function ChartTip({ point, style }: { point: ChartPoint; style: CSSProperties }) {
  const { numbers, sources } = useSlide();
  const named = point.cites.flatMap((cite) => {
    const source = sources[numbers.indexOf(cite)];
    return source ? [`[${cite}] ${source}`] : [];
  });
  return <div className="outputs-s-tip" role="tooltip" style={style}>
    <strong>{plainText(point.text)}</strong>
    <span>{plainText(point.label)}</span>
    {named.map((source) => <small key={source}>{source}</small>)}
  </div>;
}
