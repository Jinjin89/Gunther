import { Bookmark, Download, Pause, Play, RotateCcw, RotateCw, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { MarkdownView } from "../../components/markdown/MarkdownView";
import { useShortcut, withShortcut } from "../../shortcuts/shortcuts";
import { formatClock, type RecordingContent, type TranscriptSegment } from "../sourceContent";

const SPEEDS = [1, 1.25, 1.5, 2, 0.75] as const;

interface PlayerState {
  time: number;
  duration: number;
  playing: boolean;
  seek: (seconds: number, play?: boolean) => void;
}

function useAudio(audioUrl: string | null, fallbackDuration: number | null) {
  const audio = useRef<HTMLAudioElement>(null);
  const [time, setTime] = useState(0);
  const [mediaDuration, setMediaDuration] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState<number>(1);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setTime(0);
    setPlaying(false);
    setFailed(false);
    setMediaDuration(null);
  }, [audioUrl]);
  // Recordings made by MediaRecorder often report an infinite duration until
  // fully read; the duration written at capture time is the reliable fallback.
  const duration = mediaDuration && Number.isFinite(mediaDuration) ? mediaDuration : fallbackDuration ?? 0;
  const seek = (seconds: number, play = false) => {
    const element = audio.current;
    if (!element) return;
    element.currentTime = Math.max(0, Math.min(seconds, duration || seconds));
    setTime(element.currentTime);
    if (play) void element.play().catch(() => undefined);
  };
  const toggle = () => {
    const element = audio.current;
    if (!element) return;
    if (element.paused) void element.play().catch(() => setFailed(true));
    else element.pause();
  };
  const cycleRate = () => {
    const next = SPEEDS[(SPEEDS.indexOf(rate as (typeof SPEEDS)[number]) + 1) % SPEEDS.length]!;
    setRate(next);
    if (audio.current) audio.current.playbackRate = next;
  };
  const element = audioUrl ? (
    <audio
      ref={audio}
      src={audioUrl}
      preload="metadata"
      onLoadedMetadata={(event) => setMediaDuration(event.currentTarget.duration)}
      onDurationChange={(event) => setMediaDuration(event.currentTarget.duration)}
      onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)}
      onPlay={() => setPlaying(true)}
      onPause={() => setPlaying(false)}
      onEnded={() => setPlaying(false)}
      onError={() => setFailed(true)}
    />
  ) : null;
  return { element, time, duration, playing, rate, failed, seek, toggle, cycleRate };
}

function AudioPlayer({ player, moments, audioUrl, downloadName }: { player: ReturnType<typeof useAudio>; moments: RecordingContent["moments"]; audioUrl: string | null; downloadName: string }) {
  const { time, duration, playing, rate, failed } = player;
  const progress = duration ? Math.min(100, (time / duration) * 100) : 0;
  useShortcut("space", player.toggle, { enabled: Boolean(audioUrl) && !failed });
  useShortcut("arrowleft", () => player.seek(time - 5), { enabled: Boolean(audioUrl) && !failed });
  useShortcut("arrowright", () => player.seek(time + 5), { enabled: Boolean(audioUrl) && !failed });
  if (!audioUrl) {
    return <div className="gx-player is-missing"><span>The audio for this recording isn’t attached. Its transcript and summary are below.</span></div>;
  }
  return (
    <div className={`gx-player ${playing ? "is-playing" : ""} ${failed ? "is-failed" : ""}`}>
      {player.element}
      <button type="button" className="gx-player-play" onClick={player.toggle} disabled={failed} aria-label={playing ? "Pause" : "Play"} title={withShortcut(playing ? "Pause" : "Play", "recording-play")}>
        {playing ? <Pause size={18} fill="currentColor" /> : <Play size={18} fill="currentColor" className="gx-player-play-icon" />}
      </button>
      <div className="gx-player-body">
        <div className="gx-player-track" style={{ "--progress": `${progress}%` } as CSSProperties}>
          <input
            type="range"
            min={0}
            max={Math.max(duration, 0.1)}
            step={0.1}
            value={Math.min(time, duration || time)}
            disabled={failed || !duration}
            aria-label="Position"
            aria-valuetext={`${formatClock(time)} of ${formatClock(duration)}`}
            onChange={(event) => player.seek(Number(event.target.value))}
          />
          {duration > 0 && moments.map((moment) => (
            <button
              type="button"
              key={`${moment.seconds}-${moment.label}`}
              className="gx-player-marker"
              style={{ left: `${Math.min(100, (moment.seconds / duration) * 100)}%` }}
              onClick={() => player.seek(moment.seconds, true)}
              aria-label={`Jump to ${formatClock(moment.seconds)}: ${moment.label}`}
              title={`${formatClock(moment.seconds)} · ${moment.label}`}
            />
          ))}
        </div>
        <div className="gx-player-controls">
          <span className="gx-player-time"><strong>{formatClock(time)}</strong> / {formatClock(duration)}</span>
          <button type="button" className="gx-player-skip" onClick={() => player.seek(time - 15)} disabled={failed} aria-label="Back 15 seconds" title="Back 15 seconds"><RotateCcw size={15} /><i>15</i></button>
          <button type="button" className="gx-player-skip" onClick={() => player.seek(time + 15)} disabled={failed} aria-label="Forward 15 seconds" title="Forward 15 seconds"><RotateCw size={15} /><i>15</i></button>
          <button type="button" className="gx-player-rate" onClick={player.cycleRate} disabled={failed} aria-label={`Playback speed ${rate}×`} title="Playback speed">{rate}×</button>
          <span className="gx-item-toolbar-fill" />
          {failed ? <span className="gx-player-error">The audio file couldn’t be played.</span> : null}
          <a className="gx-player-download" href={audioUrl} download={downloadName} title="Download the original audio"><Download size={14} /></a>
        </div>
      </div>
    </div>
  );
}

function highlight(text: string, needle: string): ReactNode {
  if (!needle) return text;
  const parts = text.split(new RegExp(`(${needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig"));
  return parts.map((part, index) => index % 2 ? <mark key={index}>{part}</mark> : part);
}

function Transcript({ segments, player }: { segments: TranscriptSegment[]; player: PlayerState }) {
  const [query, setQuery] = useState("");
  const needle = query.trim();
  const timed = segments.some((segment) => segment.startSeconds !== null);
  const activeIndex = useMemo(() => {
    if (!timed || !player.time) return -1;
    let found = -1;
    segments.forEach((segment, index) => {
      if (segment.startSeconds !== null && segment.startSeconds <= player.time + 0.2) found = index;
    });
    return found;
  }, [player.time, segments, timed]);
  const matches = needle ? segments.filter((segment) => segment.text.toLowerCase().includes(needle.toLowerCase())).length : 0;
  const words = useMemo(() => segments.reduce((total, segment) => total + segment.text.split(/\s+/).filter(Boolean).length, 0), [segments]);
  if (!segments.length) return <p className="gx-item-empty-line">No transcript was saved with this recording.</p>;
  return (
    <div className="gx-transcript">
      <div className="gx-transcript-bar">
        <label className="gx-find">
          <Search size={13} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find in transcript" aria-label="Find in transcript" />
          {query && <button type="button" onClick={() => setQuery("")} aria-label="Clear"><X size={12} /></button>}
        </label>
        <span className="gx-transcript-count" role="status">{needle ? `${matches} ${matches === 1 ? "passage" : "passages"}` : `${words.toLocaleString()} words`}</span>
      </div>
      <div className={`gx-transcript-body ${timed ? "is-timed" : ""}`}>
        {segments.map((segment, index) => {
          const hidden = needle && !segment.text.toLowerCase().includes(needle.toLowerCase());
          if (hidden) return null;
          return (
            <div key={index} className={`gx-transcript-segment ${index === activeIndex ? "is-active" : ""}`}>
              {segment.startSeconds !== null && (
                <button type="button" className="gx-transcript-time" onClick={() => player.seek(segment.startSeconds!, true)} aria-label={`Play from ${formatClock(segment.startSeconds)}`}>
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

export function RecordingBody({ parsed, audioUrl, downloadName }: { parsed: RecordingContent; audioUrl: string | null; downloadName: string }) {
  const player = useAudio(audioUrl, parsed.durationSeconds);
  const hasOverview = Boolean(parsed.summary || parsed.keyPoints.length || parsed.actions.length || parsed.openQuestions.length || parsed.terms.length);
  const [tab, setTab] = useState<"overview" | "transcript">(hasOverview ? "overview" : "transcript");
  const state: PlayerState = { time: player.time, duration: player.duration, playing: player.playing, seek: player.seek };
  return (
    <div className="gx-recording">
      <AudioPlayer player={player} moments={parsed.moments} audioUrl={audioUrl} downloadName={downloadName} />
      {parsed.moments.length > 0 && (
        <div className="gx-moments" role="group" aria-label="Marked moments">
          <span className="gx-moments-label"><Bookmark size={13} />Marked</span>
          {parsed.moments.map((moment) => (
            <button type="button" key={`${moment.seconds}-${moment.label}`} className="gx-moment" onClick={() => player.seek(moment.seconds, true)} disabled={!audioUrl}>
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
      {tab === "overview" && hasOverview ? <Overview parsed={parsed} /> : <Transcript segments={parsed.transcript} player={state} />}
    </div>
  );
}
