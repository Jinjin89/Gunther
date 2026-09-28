interface BrandMarkProps {
  size?: number;
  /** Draw the mark in once when it first appears. */
  animated?: boolean;
  /** Trace the stroke and pulse the node while work is in progress. */
  busy?: boolean;
  className?: string;
}

/**
 * The Gunther mark: a single-stroke "G" whose crossbar ends in a knowledge
 * node. It inherits `currentColor`, so its colour comes from the context.
 */
export function BrandMark({ size = 22, animated = false, busy = false, className = "" }: BrandMarkProps) {
  const classes = ["gx-brandmark", animated ? "is-animated" : "", busy ? "is-busy" : "", className].filter(Boolean).join(" ");
  return (
    <svg className={classes} width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true" focusable="false">
      <path d="M23.8 8.2A11 11 0 1 0 27 16H18.5" pathLength={1} stroke="currentColor" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="16" cy="16" r="2.7" fill="currentColor" />
    </svg>
  );
}
