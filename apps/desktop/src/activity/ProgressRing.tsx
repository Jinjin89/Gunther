const SIZE = 14;
const STROKE = 2;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

interface ProgressRingProps {
  /** 0..1 fills the ring; null turns a short arc, for work of unknown length. */
  progress: number | null;
  /** The work finished: the ring completes, then a check draws. */
  done?: boolean;
}

/** A 14 px ring: fills with progress, turns while the length is unknown, ends in a check. */
export function ProgressRing({ progress, done = false }: ProgressRingProps) {
  const turning = !done && progress === null;
  const filled = done ? 1 : Math.min(1, Math.max(0, progress ?? 0));
  return (
    <svg className={`gx-ring ${turning ? "is-turning" : ""} ${done ? "is-done" : ""}`} width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden="true">
      <circle className="gx-ring-track" cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} fill="none" strokeWidth={STROKE} />
      <circle
        className="gx-ring-arc"
        cx={SIZE / 2}
        cy={SIZE / 2}
        r={RADIUS}
        fill="none"
        strokeWidth={STROKE}
        strokeLinecap="round"
        strokeDasharray={CIRCUMFERENCE}
        strokeDashoffset={turning ? CIRCUMFERENCE * 0.75 : CIRCUMFERENCE * (1 - filled)}
        transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
      />
      {done && <path className="gx-ring-check" d="M4.2 7.2 6.3 9.2 9.8 5" fill="none" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" pathLength={1} />}
    </svg>
  );
}
