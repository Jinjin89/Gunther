import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  clockLabel,
  heardLive,
  joinSentences,
  paragraphCheck,
  paragraphParts,
  paragraphStartMs,
  paragraphText,
  type TextPart,
  type TranscriptState,
} from "../services/liveTranscript";

interface TranscriptParagraphsProps {
  state: TranscriptState;
  /** Recording or paused: the view follows the newest text unless the reader scrolled up. */
  active: boolean;
  /** The user's own text for a paragraph. */
  onEdit: (paragraphId: string, text: string) => void;
  /** Listen again to a window whose check failed. */
  onRetry?: (windowId: string) => void;
  /** Send the same sentences to be grouped again. */
  onRetryGrouping?: () => void;
  /** Play the saved recording from here; absent until the audio is saved. */
  onSeek?: ((seconds: number) => void) | undefined;
}

type Row =
  | {
    kind: "paragraph"; id: string; startMs: number; text: string; edited: boolean; parts: TextPart[];
    failed: { id: string; error?: string } | null; checking: boolean; heard: string | null; groupingFailed: string | null;
  }
  | { kind: "gap"; id: string; startMs: number; text: string }
  | { kind: "live"; id: string; startMs: number; text: string; draft: string; joiner: string; checking: boolean };

const AT_BOTTOM_PX = 48;

/** Paragraphs, one time each in the left margin: black when confirmed, grey while still live. */
export function TranscriptParagraphs({ state, active, onEdit, onRetry, onRetryGrouping, onSeek }: TranscriptParagraphsProps) {
  const scroller = useRef<HTMLDivElement | null>(null);
  const field = useRef<HTMLTextAreaElement | null>(null);
  const following = useRef(true);
  const [editing, setEditing] = useState<{ id: string; value: string } | null>(null);
  const [heardShown, setHeardShown] = useState<Set<string>>(new Set());

  const rows = useMemo(() => {
    const list: Row[] = [];
    state.paragraphs.forEach((paragraph, index) => {
      const text = paragraphText(state, paragraph).replace(/[ \t]+/g, " ").trim();
      if (!text) return;
      const check = paragraphCheck(state, paragraph);
      list.push({
        kind: "paragraph",
        id: paragraph.id,
        startMs: paragraphStartMs(state, paragraph),
        text,
        edited: paragraph.edited !== undefined,
        // The words a check changed are marked while recording, not when reading back.
        parts: active ? paragraphParts(state, paragraph) : [{ text, changed: false }],
        failed: check.failed ? { id: check.failed.id, ...(check.failed.error ? { error: check.failed.error } : {}) } : null,
        checking: check.checking,
        heard: heardLive(state, paragraph),
        // Grouping failed: the note sits under the newest paragraph, where the sentences wait.
        groupingFailed: state.grouping.state === "failed" && index === state.paragraphs.length - 1 ? state.grouping.error ?? "" : null,
      });
    });
    state.gaps.forEach((gap) => {
      list.push({
        kind: "gap",
        id: `gap-${gap.startMs}`,
        startMs: gap.startMs,
        text: `No audio was transcribed from ${clockLabel(gap.startMs)} to ${clockLabel(gap.endMs)}`,
      });
    });
    const live = joinSentences(state.live.map((sentence) => sentence.text));
    const draft = state.draft?.text ?? "";
    if (live || draft) {
      list.push({ kind: "live", id: "live", startMs: state.live[0]?.startMs ?? state.draft?.startMs ?? 0, text: live, draft, joiner: live && draft ? joinSentences([live, draft]).slice(live.length, -draft.length) : "", checking: state.checking !== null });
    }
    // The live text is one row at its first sentence's time; a gap inside it comes after it.
    list.sort((left, right) => left.startMs - right.startMs);
    return list;
  }, [state, active]);

  useLayoutEffect(() => {
    const element = field.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
    element.focus();
  }, [editing?.id, editing?.value]);

  useEffect(() => {
    const element = scroller.current;
    if (!element || !active || !following.current || editing) return;
    element.scrollTop = element.scrollHeight;
  }, [active, rows, editing]);

  const finish = (save: boolean) => {
    if (editing && save) onEdit(editing.id, editing.value);
    setEditing(null);
  };

  return <div
    className="lecture-paragraphs"
    ref={scroller}
    onScroll={(event) => {
      const element = event.currentTarget;
      following.current = element.scrollHeight - element.scrollTop - element.clientHeight < AT_BOTTOM_PX;
    }}
  >
    {rows.map((row) => <div key={row.id} className={`lp-row is-${row.kind}`}>
      {row.kind === "gap"
        ? <span className="lp-time">{clockLabel(row.startMs)}</span>
        : <button type="button" className="lp-time" disabled={!onSeek} onClick={() => onSeek?.(row.startMs / 1000)} aria-label={`Play from ${clockLabel(row.startMs)}`} title={onSeek ? "Play from here" : undefined}>{clockLabel(row.startMs)}</button>}
      <div className="lp-body">
        {row.kind === "paragraph" && (editing?.id === row.id
          ? <textarea
            ref={field}
            className="lp-edit"
            value={editing.value}
            aria-label={`Edit the paragraph at ${clockLabel(row.startMs)}`}
            onChange={(event) => setEditing({ id: row.id, value: event.target.value })}
            onBlur={() => finish(true)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                finish(false);
              } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                finish(true);
              }
            }}
          />
          : <p
            className="lp-confirmed"
            role="button"
            tabIndex={0}
            title="Click to edit"
            onClick={() => {
              if (window.getSelection()?.toString()) return;
              setEditing({ id: row.id, value: row.text });
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") setEditing({ id: row.id, value: row.text });
            }}
          >{row.parts.map((part, index) => (part.changed
            ? <span key={index} className="lp-changed">{part.text}</span>
            : part.text))}</p>)}
        {row.kind === "gap" && <p className="lp-gap">{row.text}</p>}
        {row.kind === "live" && <p className="lp-live">
          {row.text && <span className="lp-sentences">{row.text}</span>}
          {row.draft && <span className="lp-draft">{row.joiner}{row.draft}</span>}
        </p>}
        {row.kind === "paragraph" && row.edited && <small className="lp-edited">edited</small>}
        {row.kind === "paragraph" && row.heard && <>
          <button
            type="button"
            className="lp-heard-toggle"
            aria-expanded={heardShown.has(row.id)}
            onClick={() => setHeardShown((current) => {
              const next = new Set(current);
              if (!next.delete(row.id)) next.add(row.id);
              return next;
            })}
          >{heardShown.has(row.id) ? "Hide what was heard live" : "Show what was heard live"}</button>
          {heardShown.has(row.id) && <p className="lp-heard">{row.heard}</p>}
        </>}
      </div>
      <span className="lp-aside" data-slot="status">
        {row.kind === "live" && row.checking && <span className="lp-status">checking…</span>}
        {row.kind === "paragraph" && row.checking && <span className="lp-status">checking…</span>}
        {row.kind === "paragraph" && row.groupingFailed !== null && <span className="lp-status is-failed" title={row.groupingFailed}>
          Couldn't group into paragraphs · <button type="button" className="lp-retry" onClick={() => onRetryGrouping?.()}>Retry</button>
        </span>}
        {row.kind === "paragraph" && !row.checking && row.failed && <span className="lp-status is-failed" title={row.failed.error}>
          Couldn't check · <button type="button" className="lp-retry" onClick={() => onRetry?.(row.failed!.id)}>Retry</button>
        </span>}
      </span>
    </div>)}
  </div>;
}
