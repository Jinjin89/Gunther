import { Bookmark, Download, Search, X } from "lucide-react";
import { useId, useMemo, useState, type ReactNode } from "react";
import { AudioPlayer, useAudioPlayer } from "../../components/AudioPlayer";
import { MarkdownView } from "../../components/markdown/MarkdownView";
import { useShortcut } from "../../shortcuts/shortcuts";
import { TranscribeAgainButton, TranscribeAgainLine, useTranscribeAgain } from "./TranscribeAgain";
import { formatClock, type RecordingContent, type TranscriptSegment } from "../sourceContent";

function highlight(text: string, needle: string): ReactNode {
  if (!needle) return text;
  const parts = text.split(new RegExp(`(${needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig"));
  return parts.map((part, index) => index % 2 ? <mark key={index}>{part}</mark> : part);
}

/** The transcript, with Find and, when the audio is saved, Transcribe again. */
function Transcript({ segments, player, playbackReasonId, again }: {
  segments: TranscriptSegment[];
  player: Pick<ReturnType<typeof useAudioPlayer>, "time" | "seek" | "enabled">;
  playbackReasonId: string;
  again: ReturnType<typeof useTranscribeAgain> | null;
}) {
  const [query, setQuery] = useState("");
  const needle = query.trim();
  const timed = segments.some((segment) => segment.startSeconds !== null);
  const time = player.time;
  const activeIndex = useMemo(() => {
    if (!timed || !time) return -1;
    let found = -1;
    segments.forEach((segment, index) => {
      if (segment.startSeconds !== null && segment.startSeconds <= time + 0.2) found = index;
    });
    return found;
  }, [time, segments, timed]);
  const matches = needle ? segments.filter((segment) => segment.text.toLowerCase().includes(needle.toLowerCase())).length : 0;
  const words = useMemo(() => segments.reduce((total, segment) => total + segment.text.split(/\s+/).filter(Boolean).length, 0), [segments]);
  if (!segments.length) {
    return (
      <div className="gx-transcript">
        <div className="gx-transcript-bar">
          <p className="gx-item-empty-line">No transcript was saved with this recording.</p>
          {again && <span className="gx-item-toolbar-fill" />}
          {again && <TranscribeAgainButton again={again} />}
        </div>
        {again && <TranscribeAgainLine again={again} />}
      </div>
    );
  }
  return (
    <div className="gx-transcript">
      <div className="gx-transcript-bar">
        <label className="gx-find">
          <Search size={13} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find in transcript" aria-label="Find in transcript" />
          {query && <button type="button" onClick={() => setQuery("")} aria-label="Clear"><X size={12} /></button>}
        </label>
        <span className="gx-transcript-count" role="status">{needle ? `${matches} ${matches === 1 ? "passage" : "passages"}` : `${words.toLocaleString()} words`}</span>
        {again && <TranscribeAgainButton again={again} />}
      </div>
      {again && <TranscribeAgainLine again={again} />}
      <div className={`gx-transcript-body ${timed ? "is-timed" : ""}`}>
        {segments.map((segment, index) => {
          const hidden = needle && !segment.text.toLowerCase().includes(needle.toLowerCase());
          if (hidden) return null;
          return (
            <div key={index} className={`gx-transcript-segment ${index === activeIndex ? "is-active" : ""}`}>
              {segment.startSeconds !== null && (
                <button type="button" className="gx-transcript-time" disabled={!player.enabled}
                  aria-describedby={player.enabled ? undefined : playbackReasonId}
                  onClick={() => player.seek(segment.startSeconds!, true)} aria-label={`Play from ${formatClock(segment.startSeconds)}`}>
                  {formatClock(segment.startSeconds)}
                </button>
              )}
              <p>{highlight(segment.text, needle)}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Overview({ parsed }: { parsed: RecordingContent }) {
  return (
    <div className="gx-recording-overview">
      {parsed.summary && <section><h2 className="gx-section-title">Summary</h2><MarkdownView source={parsed.summary} /></section>}
      {parsed.keyPoints.length > 0 && (
        <section>
          <h2 className="gx-section-title">Key points</h2>
          <ol className="gx-keypoints">{parsed.keyPoints.map((point) => <li key={point}><MarkdownView source={point} className="is-inline" /></li>)}</ol>
        </section>
      )}
      {parsed.actions.length > 0 && (
        <section>
          <h2 className="gx-section-title">Actions</h2>
          <ul className="gx-actions-list">{parsed.actions.map((action) => <li key={action}><span className="gx-md-task" aria-hidden="true" /><MarkdownView source={action} className="is-inline" /></li>)}</ul>
        </section>
      )}
      {parsed.openQuestions.length > 0 && (
        <section>
          <h2 className="gx-section-title">Open questions</h2>
          <ul className="gx-questions">{parsed.openQuestions.map((question) => <li key={question}><MarkdownView source={question} className="is-inline" /></li>)}</ul>
        </section>
      )}
      {parsed.terms.length > 0 && (
        <section>
          <h2 className="gx-section-title">Terms</h2>
          <p className="gx-terms">{parsed.terms.map((term) => <span key={term}>{term}</span>)}</p>
        </section>
      )}
    </div>
  );
}

export function RecordingBody({ parsed, audioUrl, downloadName, onTranscribed }: { parsed: RecordingContent; audioUrl: string | null; downloadName: string; onTranscribed?: () => void }) {
  // Only a recording whose audio is saved can be heard again.
  const canHearAgain = Boolean(audioUrl && parsed.recordingId);
  const again = useTranscribeAgain(canHearAgain ? parsed.recordingId : null, onTranscribed);
  const player = useAudioPlayer(audioUrl, parsed.durationSeconds);
  const { duration, enabled, seek } = player;
  const playbackReasonId = useId();
  const time = player.time ?? 0;
  useShortcut("space", player.toggle, { enabled });
  useShortcut("arrowleft", () => seek(time - 5), { enabled });
  useShortcut("arrowright", () => seek(time + 5), { enabled });
  const hasOverview = Boolean(parsed.summary || parsed.keyPoints.length || parsed.actions.length || parsed.openQuestions.length || parsed.terms.length);
  const [tab, setTab] = useState<"overview" | "transcript">(hasOverview ? "overview" : "transcript");
  return (
    <div className="gx-recording">
      {audioUrl ? <AudioPlayer player={player}
        trackMarks={duration > 0 && parsed.moments.map((moment) => (
          <button type="button" key={`${moment.seconds}-${moment.label}`} className="gx-player-marker"
            style={{ left: `${Math.min(100, (moment.seconds / duration) * 100)}%` }}
            onClick={() => seek(moment.seconds, true)} disabled={!enabled}
            aria-label={`Jump to ${formatClock(moment.seconds)}: ${moment.label}`}
            title={`${formatClock(moment.seconds)} · ${moment.label}`} />
        ))}
        actions={<>
          <span className="gx-item-toolbar-fill" />
          {player.error && <span id={playbackReasonId} role="alert" className="gx-player-error">{player.error}</span>}
          <a className="gx-player-download" href={audioUrl} download={downloadName} title="Download the original audio"><Download size={14} /></a>
        </>}
      /> : <div id={playbackReasonId} className="gx-player is-missing">The audio for this recording isn’t attached. Its transcript and summary are below.</div>}
      {parsed.moments.length > 0 && (
        <div className="gx-moments" role="group" aria-label="Marked moments">
          <span className="gx-moments-label"><Bookmark size={13} />Marked</span>
          {parsed.moments.map((moment) => (
            <button type="button" key={`${moment.seconds}-${moment.label}`} className="gx-moment" onClick={() => player.seek(moment.seconds, true)} disabled={!enabled}>
              <strong>{formatClock(moment.seconds)}</strong>
              <span>{moment.label}</span>
            </button>
          ))}
        </div>
      )}
      {hasOverview && (
        <nav className="gx-tabs gx-item-tabs" aria-label="Recording">
          <button type="button" className={tab === "overview" ? "is-active" : ""} aria-pressed={tab === "overview"} onClick={() => setTab("overview")}>Overview</button>
          <button type="button" className={tab === "transcript" ? "is-active" : ""} aria-pressed={tab === "transcript"} onClick={() => setTab("transcript")}>Transcript</button>
        </nav>
      )}
      {tab === "overview" && hasOverview ? <Overview parsed={parsed} /> : <Transcript segments={parsed.transcript} player={player}
        playbackReasonId={playbackReasonId} again={canHearAgain ? again : null} />}
    </div>
  );
}
