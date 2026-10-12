import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  clockLabel,
  joinSentences,
  paragraphStartMs,
  paragraphText,
  type TranscriptState,
} from "../services/liveTranscript";

interface TranscriptParagraphsProps {
  state: TranscriptState;
  /** Recording or paused: the view follows the newest text unless the reader scrolled up. */
  active: boolean;
  /** The user's own text for a paragraph. */
  onEdit: (paragraphId: string, text: string) => void;
  /** Play the saved recording from here; absent until the audio is saved. */
  onSeek?: (seconds: number) => void;
}

type Row =
  | { kind: "paragraph"; id: string; startMs: number; text: string; edited: boolean }
  | { kind: "gap"; id: string; startMs: number; text: string }
  | { kind: "live"; id: string; startMs: number; text: string; draft: string; joiner: string };

const AT_BOTTOM_PX = 48;

/** Paragraphs, one time each in the left margin: black when confirmed, grey while still live. */
export function TranscriptParagraphs({ state, active, onEdit, onSeek }: TranscriptParagraphsProps) {
  const scroller = useRef<HTMLDivElement | null>(null);
  const field = useRef<HTMLTextAreaElement | null>(null);
  const following = useRef(true);
  const [editing, setEditing] = useState<{ id: string; value: string } | null>(null);

  const rows = useMemo(() => {
    const list: Row[] = [];
    state.paragraphs.forEach((paragraph) => {
      const text = paragraphText(state, paragraph).replace(/[ \t]+/g, " ").trim();
      if (text) list.push({ kind: "paragraph", id: paragraph.id, startMs: paragraphStartMs(state, paragraph), text, edited: paragraph.edited !== undefined });
    });
    state.gaps.forEach((gap) => {
      list.push({
        kind: "gap",
        id: `gap-${gap.startMs}`,
        startMs: gap.startMs,
        text: `No audio was transcribed from ${clockLabel(gap.startMs)} to ${clockLabel(gap.endMs)}`,
      });
    });
    list.sort((left, right) => left.startMs - right.startMs);
    const live = joinSentences(state.live.map((sentence) => sentence.text));
    const draft = state.draft?.text ?? "";
    if (live || draft) {
      list.push({ kind: "live", id: "live", startMs: state.live[0]?.startMs ?? state.draft?.startMs ?? 0, text: live, draft, joiner: live && draft ? joinSentences([live, draft]).slice(live.length, -draft.length) : "" });
    }
    return list;
  }, [state]);

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
          >{row.text}</p>)}
        {row.kind === "gap" && <p className="lp-gap">{row.text}</p>}
        {row.kind === "live" && <p className="lp-live">
          {row.text && <span className="lp-sentences">{row.text}</span>}
          {row.draft && <span className="lp-draft">{row.joiner}{row.draft}</span>}
        </p>}
        {row.kind === "paragraph" && row.edited && <small className="lp-edited">edited</small>}
      </div>
      <span className="lp-aside" data-slot="status" />
    </div>)}
  </div>;
}
