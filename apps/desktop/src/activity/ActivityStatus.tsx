import type { ActivityItem, ActivityOpen } from "@gunther/contracts";
import { AlertCircle, Check, Pause, Play, Square, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { knowledgeApi } from "../api";
import { ProgressRing } from "./ProgressRing";
import { sendRecordingControl, useRecordingStatus, type RecordingStatus } from "./recordingStatus";
import { ago, clock, earlierRows, failureKey, FLASH_MS, isActive, nowRows, pillView, type PillView } from "./summary";
import { useActivity } from "./useActivity";

const OFFLINE_HINT = "The local knowledge service is not responding. New captures stay on this device and are sent when it returns.";
const IDLE_HINT = "Everything is stored on this device";
const EMPTY_TEXT = "Nothing running. Recordings, transcripts, reports, slides and answers show here while they work.";

interface ActivityStatusProps {
  /** The app's own check that the service answers. */
  serviceOnline: boolean;
  /** Go to the page a row stands for. */
  onOpen: (open: ActivityOpen) => void;
  /** Whether that page is already on screen (a job that finishes there is not flashed). */
  isHere: (open: ActivityOpen) => boolean;
}

const quietly = (work: Promise<unknown>) => { work.catch(() => undefined); };

function PillIconView({ view }: { view: PillView }) {
  switch (view.icon) {
    case "dot": return <i />;
    case "recording": return <i className="is-recording" />;
    case "grey-dot": return <i className="is-grey" />;
    case "pause": return <Pause size={12} fill="currentColor" className="gx-activity-glyph" aria-hidden="true" />;
    case "spinner": return <ProgressRing progress={null} />;
    case "ring": return <ProgressRing progress={view.progress ?? null} />;
    case "check": return <ProgressRing progress={1} done />;
    case "alert": return <AlertCircle size={13} className="gx-activity-glyph" aria-hidden="true" />;
  }
}

const RECORDING_WORDS: Record<RecordingStatus["phase"], string> = {
  requesting: "Starting recording",
  importing: "Importing audio",
  recording: "Recording",
  paused: "Paused",
  stopped: "Recording to save",
};

/** The top bar's status pill and the Activity panel under it. */
export function ActivityStatus({ serviceOnline, onOpen, isHere }: ActivityStatusProps) {
  const { items, reachable } = useActivity();
  const recording = useRecordingStatus();
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState<ReadonlySet<string>>(new Set());
  const [flash, setFlash] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const pill = useRef<HTMLButtonElement>(null);

  // Flash a job that just finished, unless the person is already looking at its page.
  const states = useRef(new Map<string, ActivityItem["state"]>());
  const here = useRef(isHere);
  here.current = isHere;
  useEffect(() => {
    const before = states.current;
    const after = new Map<string, ActivityItem["state"]>();
    let spoken = "";
    for (const item of items) {
      after.set(item.id, item.state);
      const was = before.get(item.id);
      if (!was || was === item.state || (was !== "waiting" && was !== "running")) continue;
      if (item.state === "failed") spoken = item.title;
      if (item.state !== "done") continue;
      spoken = item.title;
      if (item.open && here.current(item.open)) quietly(knowledgeApi.dismissActivity(item.id));
      else setFlash(item.id);
    }
    states.current = after;
    if (spoken) setAnnouncement(spoken);
  }, [items]);
  useEffect(() => {
    if (!flash) return undefined;
    const timer = setTimeout(() => setFlash((current) => (current === flash ? null : current)), FLASH_MS);
    return () => clearTimeout(timer);
  }, [flash]);

  // Say when the recording changes state, never what its clock reads.
  const phase = recording?.phase ?? null;
  useEffect(() => {
    if (phase) setAnnouncement(RECORDING_WORDS[phase]);
  }, [phase]);

  // Opening the panel marks every failure seen; a new one while it is open is seen at once.
  useEffect(() => {
    if (!open) return;
    setSeen((current) => {
      const keys = items.filter((item) => item.state === "failed").map(failureKey);
      return keys.every((key) => current.has(key)) ? current : new Set([...current, ...keys]);
    });
  }, [open, items]);

  const view = pillView({ items, recording, reachable: serviceOnline && reachable, seenFailures: seen, flash });
  const swap = useRef(false);
  useEffect(() => { swap.current = true; }, []);

  const close = useCallback((refocus = true) => {
    setOpen(false);
    if (refocus) pill.current?.focus();
  }, []);
  const toggle = () => {
    if (!open) setNow(Date.now());
    setOpen((value) => !value);
  };

  const idleHint = view.tone === "offline" ? OFFLINE_HINT : IDLE_HINT;
  const hint = view.tone === "idle" || view.tone === "offline" ? idleHint : view.ariaLabel;
  const textWords = view.percent && view.text ? `${view.text} · ` : view.text;

  return (
    <>
      <button
        type="button"
        ref={pill}
        className={`gx-save-state gx-activity-pill is-${view.tone}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={hint}
        onClick={toggle}
        data-no-drag
      >
        <span className="gx-activity-icon"><PillIconView view={view} /></span>
        <span className={`gx-activity-swap ${swap.current ? "is-changing" : ""}`} key={`${view.tone}:${view.text}`}>
          {view.clock && <span className="gx-activity-clock">{view.clock}</span>}
          <span className="gx-activity-text">{textWords}</span>
          {view.percent && <span className="gx-activity-percent">{view.percent}</span>}
        </span>
        {view.extra > 0 && <span className="gx-activity-chip" title={`${view.extra} more running`}>+{view.extra}</span>}
      </button>
      <span className="gx-sr-only" role="status" aria-live="polite">{announcement}</span>
      {open && <ActivityPanel anchor={pill} items={items} recording={recording} now={now} onClose={close} onOpen={onOpen} />}
    </>
  );
}

interface PanelProps {
  anchor: React.RefObject<HTMLButtonElement | null>;
  items: ActivityItem[];
  recording: RecordingStatus | null;
  now: number;
  onClose: (refocus?: boolean) => void;
  onOpen: (open: ActivityOpen) => void;
}

function ActivityPanel({ anchor, items, recording, now: openedAt, onClose, onOpen }: PanelProps) {
  const panel = useRef<HTMLElement>(null);
  const [place, setPlace] = useState<{ top: number; right: number } | null>(null);
  const [now, setNow] = useState(openedAt);

  // Under the pill, right edges aligned; follows the window as it resizes.
  useLayoutEffect(() => {
    const measure = () => {
      const box = anchor.current?.getBoundingClientRect();
      if (box) setPlace({ top: Math.round(box.bottom + 6), right: Math.max(8, Math.round(window.innerWidth - box.right)) });
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [anchor]);

  useEffect(() => { panel.current?.focus(); }, []);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } };
    const outside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (panel.current?.contains(target) || anchor.current?.contains(target)) return;
      onClose(false);
    };
    window.addEventListener("keydown", escape, true);
    document.addEventListener("mousedown", outside, true);
    return () => {
      window.removeEventListener("keydown", escape, true);
      document.removeEventListener("mousedown", outside, true);
    };
  }, [anchor, onClose]);

  const current = useMemo(() => nowRows(items, recording), [items, recording]);
  const earlier = useMemo(() => earlierRows(items), [items]);

  const openItem = (target: ActivityOpen) => { onOpen(target); onClose(false); };
  const showRecording = () => { quietly(sendRecordingControl("show")); onClose(false); };

  const row = (key: string, props: RowProps) => <ActivityRow key={key} {...props} />;

  return createPortal(
    <section
      ref={panel}
      className="gx-activity-panel"
      role="dialog"
      aria-label="Activity"
      tabIndex={-1}
      style={place ? { top: place.top, right: place.right } : { visibility: "hidden" }}
      data-no-drag
    >
      {current.length === 0 && earlier.length === 0 && <p className="gx-activity-empty">{EMPTY_TEXT}</p>}
      {current.length > 0 && (
        <div className="gx-activity-group">
          <h3>Now</h3>
          <ul>
            {current.map((entry) => entry.type === "recording"
              ? row("recording", recordingRow(entry.recording, showRecording))
              : row(entry.item.id, jobRow(entry.item, now, openItem)))}
          </ul>
        </div>
      )}
      {earlier.length > 0 && (
        <div className="gx-activity-group">
          <h3>
            Earlier
            <button type="button" className="gx-activity-clear" onClick={() => quietly(knowledgeApi.clearActivity())}>Clear</button>
          </h3>
          <ul>{earlier.map((item) => row(item.id, jobRow(item, now, openItem)))}</ul>
        </div>
      )}
    </section>,
    document.body,
  );
}

interface RowProps {
  icon: ReactNode;
  headline: string;
  detail: string;
  /** 0..1 draws the thin bar. */
  bar?: number | null;
  side: string;
  sideIsClock?: boolean;
  failed?: boolean;
  onClick?: (() => void) | undefined;
  actions: ReactNode;
}

function ActivityRow({ icon, headline, detail, bar = null, side, sideIsClock = false, failed = false, onClick, actions }: RowProps) {
  const body = (
    <>
      <span className="gx-activity-row-icon">{icon}</span>
      <span className="gx-activity-row-text">
        <strong>{headline}</strong>
        {detail && <small className={failed ? "is-failed" : undefined}>{detail}</small>}
        {bar !== null && <span className="gx-activity-bar" aria-hidden="true"><i style={{ width: `${Math.round(Math.min(1, Math.max(0, bar)) * 100)}%` }} /></span>}
      </span>
      <span className={`gx-activity-row-side ${sideIsClock ? "is-clock" : ""}`}>{side}</span>
    </>
  );
  return (
    <li className="gx-activity-row">
      {onClick ? <button type="button" className="gx-activity-row-main" onClick={onClick}>{body}</button> : <div className="gx-activity-row-main">{body}</div>}
      {actions && <span className="gx-activity-row-actions">{actions}</span>}
    </li>
  );
}

function iconButton(label: string, icon: ReactNode, onClick: () => void) {
  return <button type="button" className="gx-activity-action" aria-label={label} title={label} onClick={onClick}>{icon}</button>;
}

function recordingRow(recording: RecordingStatus, show: () => void): RowProps {
  const word = RECORDING_WORDS[recording.phase];
  const live = recording.phase === "recording" || recording.phase === "paused";
  const dot = recording.phase === "recording" ? <i className="gx-activity-dot is-recording" /> : recording.phase === "paused" ? <Pause size={12} fill="currentColor" aria-hidden="true" /> : recording.phase === "stopped" ? <i className="gx-activity-dot is-grey" /> : <ProgressRing progress={null} />;
  return {
    icon: dot,
    headline: recording.title ? `${word}: ${recording.title}` : word,
    detail: recording.transcriptWords > 0 ? `${recording.transcriptWords.toLocaleString("en-US")} words` : recording.phase === "stopped" ? "Open Capture to save it" : "",
    side: live ? clock(recording.seconds) : "",
    sideIsClock: true,
    onClick: show,
    actions: live ? (
      <>
        {iconButton(recording.phase === "paused" ? "Resume recording" : "Pause recording", recording.phase === "paused" ? <Play size={12} fill="currentColor" /> : <Pause size={12} fill="currentColor" />, () => quietly(sendRecordingControl(recording.phase === "paused" ? "resume" : "pause")))}
        {iconButton("Finish recording", <Square size={10} fill="currentColor" />, () => quietly(sendRecordingControl("finish")))}
      </>
    ) : null,
  };
}

function jobRow(item: ActivityItem, now: number, open: (target: ActivityOpen) => void): RowProps {
  const active = isActive(item);
  const known = typeof item.progress === "number";
  const icon = active
    ? <ProgressRing progress={known ? item.progress : null} />
    : item.state === "done" ? <Check size={14} className="gx-activity-done" aria-hidden="true" /> : <AlertCircle size={14} className="gx-activity-failed" aria-hidden="true" />;
  const target = item.open;
  return {
    icon,
    headline: item.about ? `${item.title}: ${item.about}` : item.title,
    detail: item.state === "failed" ? item.error ?? "" : item.detail,
    bar: active && known ? item.progress : null,
    side: active ? (known ? `${Math.round((item.progress as number) * 100)}%` : "") : ago(item.endedAt, now),
    failed: item.state === "failed",
    onClick: target ? () => open(target) : undefined,
    actions: active
      ? (item.canStop ? iconButton(`Stop ${item.title}`, <Square size={10} fill="currentColor" />, () => quietly(knowledgeApi.stopActivity(item.id))) : null)
      : iconButton(`Dismiss ${item.title}`, <X size={13} />, () => quietly(knowledgeApi.dismissActivity(item.id))),
  };
}
