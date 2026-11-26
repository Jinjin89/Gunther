import type { Artifact, AudioTiming, VoicePair } from "@gunther/contracts";
import {
  createContext, memo, useCallback, useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
  type ReactNode, type RefObject,
} from "react";
import { knowledgeApi, sourceAssetUrl } from "../api";
import { AudioPlayer, useAudioPlayer } from "../components/AudioPlayer";
import { openSettings } from "../components/SettingsLayout";
import { formatClock } from "../items/sourceContent";
import { AnswerBody } from "../pages/AnswerBody";
import { saveBlob } from "../saveBlob";
import { outputCitations, OutputPartTools, type OutputContentProps } from "./OutputParts";
import type { ReaderProps } from "./OutputViews";
import { splitDocument } from "./sections";
import { readVoices, VoiceFields } from "./VoiceFields";
import "./audio.css";

function recordingFor(artifact: Artifact) {
  return artifact.audio?.contentHash === artifact.contentHash ? artifact.audio : null;
}

function wavAsset(artifact: Artifact) {
  const audio = recordingFor(artifact);
  const id = audio?.audioAssetId;
  return audio?.status === "ready" && id && id.trim() === id ? id : null;
}

function wavIdentity(artifact: Artifact) {
  const asset = wavAsset(artifact);
  return asset && JSON.stringify([artifact.id, artifact.contentHash, asset]);
}

const AudioDuration = createContext<{
  identity: string | null;
  duration: number;
  rememberDuration: (duration: number) => void;
} | null>(null);

/** The reader and its print portal share metadata for the open version's WAV. */
export function AudioDurationScope({ artifact, children }: { artifact: Artifact; children: ReactNode }) {
  const identity = wavIdentity(artifact);
  const [measurement, setMeasurement] = useState<{ identity: string; duration: number } | null>(null);
  const rememberDuration = useCallback((duration: number) => {
    if (identity && duration) {
      setMeasurement((current) => current?.identity === identity && current.duration === duration ? current : { identity, duration });
    }
  }, [identity]);
  const duration = measurement?.identity === identity ? measurement.duration : 0;
  return <AudioDuration.Provider value={{ identity, duration, rememberDuration }}>{children}</AudioDuration.Provider>;
}

function useWavDuration(artifact: Artifact) {
  const measurement = useContext(AudioDuration);
  return measurement?.identity === wavIdentity(artifact) ? measurement : null;
}

export const audioFile = {
  format: "wav",
  label: "Download WAV",
  available: (artifact: Artifact, working: boolean) => !working && Boolean(wavAsset(artifact)),
  async download(artifact: Artifact) {
    const asset = wavAsset(artifact);
    if (!asset) throw new Error("This script has no recording yet.");
    const response = await fetch(sourceAssetUrl(asset));
    if (!response.ok) throw new Error("The recording could not be downloaded.");
    const name = `audio-overview-v${artifact.versionNumber}.wav`;
    saveBlob(await response.blob(), name);
    return name;
  },
} as const;

type ScriptLine = { speaker: AudioTiming["speaker"] | null; text: string };

function readScript(content: string) {
  const { preamble, sections } = splitDocument(content, "audio");
  const lines = sections.map((line): ScriptLine => {
    const speaker = line.startsWith("A:") ? "A" : line.startsWith("B:") ? "B" : null;
    return { speaker, text: speaker ? line.slice(2).trimStart() : line };
  });
  return { preamble: preamble.replace(/^<!-- audio: [a-z][a-z0-9_]*; design: v1 -->(?:\n|$)/, "").trim(), lines };
}

function scriptTimings(artifact: Artifact, lines: ScriptLine[], duration = 0) {
  const byLine = new Map<number, AudioTiming | null>();
  if (!wavAsset(artifact)) return [];
  for (const timing of artifact.audio?.timings ?? []) {
    const { line, speaker, start, end } = timing;
    if (!Number.isInteger(line) || !lines[line - 1]?.text || lines[line - 1]?.speaker !== speaker) continue;
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) continue;
    // The WAV writer allows one microsecond of error when summing line durations.
    if (duration && (start >= duration || end > duration + 1e-6)) continue;
    byLine.set(line, byLine.has(line) ? null : timing);
  }
  const candidates = [...byLine.values()].filter((timing) => timing !== null);
  // Ambiguous ranges cannot identify the spoken line. Other ranges can still be used.
  return candidates.filter((timing) => !candidates.some((other) => other.line > timing.line
    ? other.start < timing.end
    : other.line < timing.line && timing.start < other.end));
}

type ScriptProps = Pick<OutputContentProps, "artifact" | "onCite" | "onPart" | "onAsk" | "changed" | "goTo" | "presentation">;

function revealLine(list: HTMLOListElement | null, line: number) {
  const row = list?.children.item(line - 1) as HTMLElement | null | undefined;
  row?.focus({ preventScroll: true });
  row?.scrollIntoView?.({ block: "nearest" });
}

function viewport(list: HTMLOListElement, transport: HTMLElement | null) {
  const pane = list.closest(".library-middle-page");
  const frame = pane?.getBoundingClientRect();
  const bar = pane?.querySelector(".library-middle-bar");
  return {
    top: Math.max(frame?.top ?? 0, bar?.getBoundingClientRect().bottom ?? 0, transport?.getBoundingClientRect().bottom ?? 0),
    bottom: frame?.bottom ?? window.innerHeight,
  };
}

const AudioTranscript = memo(function AudioTranscript({
  artifact, onCite, onPart, onAsk, changed, goTo, presentation, script, timings,
  list, transport, active, onSeek,
}: ScriptProps & {
  script: ReturnType<typeof readScript>;
  timings: AudioTiming[];
  list: RefObject<HTMLOListElement | null>;
  transport?: RefObject<HTMLElement | null>;
  active?: number | undefined;
  onSeek?: ((seconds: number, play?: boolean) => void) | undefined;
}) {
  const title = useId();
  useEffect(() => {
    if (goTo) revealLine(list.current, goTo.index + 1);
  }, [artifact.id, goTo, list]);
  const citations = outputCitations({ artifact, onCite, presentation });
  const byLine = new Map(timings.map((timing) => [timing.line, timing]));
  const interactive = presentation !== "preview" && presentation !== "print";
  if (!script.preamble && !script.lines.some((line) => line.text)) {
    return <p className="audio-empty">This version has no audio script.</p>;
  }
  return <section className="audio-transcript" aria-labelledby={title}>
    <h2 id={title}>Transcript</h2>
    {script.preamble && <AnswerBody content={script.preamble} {...citations} />}
    {script.lines.length > 0 && <ol ref={list} className="audio-script" role="list" aria-label="Script">
      {script.lines.map(({ speaker, text }, index) => {
        const line = index + 1;
        const timing = byLine.get(line);
        const clock = timing && formatClock(timing.start);
        return <li key={line} data-section={interactive ? index : undefined} tabIndex={interactive ? -1 : undefined}
          aria-current={active === line ? "true" : undefined}
          onClick={onPart && (() => onPart(index))}
          onFocus={interactive ? (event) => {
            onPart?.(index);
            const target = event.target;
            if (list.current && target.matches(":focus-visible") &&
              target.getBoundingClientRect().top < viewport(list.current, transport?.current ?? null).top) {
              target.scrollIntoView?.({ block: "nearest" });
            }
          } : undefined}>
          <div className="audio-script-label">
            {timing && (onSeek
              ? <button type="button" className="audio-script-time"
                  aria-label={`Play line ${line}, Speaker ${speaker} from ${clock}`}
                  onClick={() => onSeek(timing.start, true)}>{clock}</button>
              : <span className="audio-script-time">{clock}</span>)}
            {speaker && <strong>Speaker {speaker}</strong>}
            {interactive && <OutputPartTools position={index} onAsk={onAsk} changed={changed} label="Refine line" />}
          </div>
          <div className="audio-script-text">
            <AnswerBody content={text} {...citations} />
          </div>
        </li>;
      })}
    </ol>}
  </section>;
});

export function AudioScript(props: ScriptProps) {
  const list = useRef<HTMLOListElement>(null);
  const measurement = useWavDuration(props.artifact);
  const script = readScript(props.artifact.content);
  const timings = props.presentation === "print" ? scriptTimings(props.artifact, script.lines, measurement?.duration) : [];
  return <AudioTranscript {...props} script={script} timings={timings} list={list} />;
}

function AudioOverviewReader({ artifact, onCite, onPart, onAsk, changed, goTo, type, work }: ReaderProps) {
  const transport = useRef<HTMLDivElement>(null);
  const actions = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLOListElement>(null);
  const [hiddenLine, setHiddenLine] = useState<number | undefined>();
  const working = Boolean(work?.working);
  const audio = recordingFor(artifact);
  const asset = working ? null : wavAsset(artifact);
  const script = useMemo(() => readScript(artifact.content), [artifact.content]);
  const savedTimings = useMemo(() => scriptTimings(artifact, script.lines), [artifact, script]);
  const measurement = useWavDuration(artifact);
  const measuredDuration = measurement?.duration ?? 0;
  const rememberDuration = measurement?.rememberDuration;
  const fallbackDuration = measuredDuration || savedTimings.find(({ line }) => line === script.lines.length)?.end || null;
  const player = useAudioPlayer(asset && sourceAssetUrl(asset), fallbackDuration, "Audio overview recording");
  const { mediaDuration, time, enabled } = player;
  useLayoutEffect(() => {
    if (asset && mediaDuration) rememberDuration?.(mediaDuration);
  }, [asset, mediaDuration, rememberDuration]);
  const checkedDuration = mediaDuration || measuredDuration;
  const timings = useMemo(() => {
    if (!asset) return [];
    return checkedDuration ? scriptTimings(artifact, script.lines, checkedDuration) : savedTimings;
  }, [artifact, asset, checkedDuration, savedTimings, script]);
  const active = enabled && time !== null
    ? timings.find(({ start, end }) => start <= time && time < end)?.line
    : undefined;

  useLayoutEffect(() => {
    const transcript = list.current;
    const controls = transport.current;
    if (!transcript) return;
    const pane = transcript.closest(".library-middle-page");
    const bar = pane?.querySelector<HTMLElement>(".library-middle-bar");
    const scroller = pane ?? window;
    const row = active === undefined ? null : transcript.children.item(active - 1);
    const checkVisibility = () => {
      const bounds = row?.getBoundingClientRect();
      if (!bounds?.height) {
        setHiddenLine(undefined);
        return;
      }
      const { top, bottom } = viewport(transcript, controls);
      setHiddenLine(bounds.bottom <= top || bounds.top >= bottom ? active : undefined);
    };
    const measure = () => {
      transcript.style.setProperty("--audio-scroll-offset", `${(bar?.offsetHeight ?? 0) + (controls?.offsetHeight ?? 0)}px`);
      checkVisibility();
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    if (controls) observer?.observe(controls);
    if (actions.current) observer?.observe(actions.current);
    observer?.observe(transcript);
    if (pane) observer?.observe(pane);
    scroller.addEventListener("scroll", checkVisibility, { passive: true });
    window.addEventListener("resize", measure);
    measure();
    return () => {
      observer?.disconnect();
      scroller.removeEventListener("scroll", checkVisibility);
      window.removeEventListener("resize", measure);
    };
  }, [active, asset, script]);

  const record = (voices?: VoicePair) => {
    if (!work || work.disabled || working) return;
    work.run("Recording audio", (hear, signal) => knowledgeApi.retryAudioStream(
      artifact.knowledgeBaseId, artifact.id, voices ? { voices } : {}, artifact.workspaceId, hear, signal,
    ));
  };
  const options = type?.voices ?? work?.voices;
  const needsSetup = audio?.status === "needs_tts";
  const problem = player.error ?? (needsSetup ? null : audio?.error)
    ?? (audio?.status === "ready" && !asset && !working ? "The recording file is unavailable." : null);
  const recovery = <>
    {problem && <p role="alert" className="output-error">{problem}</p>}
    {work && !enabled && (!needsSetup || options?.defaults) && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm"
      disabled={work.disabled} onClick={() => record()}>Retry audio</button>}
  </>;
  return <section className="audio-overview" aria-label="Audio overview">
    {asset && <div ref={transport} className="audio-transport">
      <AudioPlayer player={player} volumeControls scopedShortcuts actions={
        active !== undefined && active === hiddenLine && <button type="button"
          className="gx-btn gx-btn-ghost gx-btn-sm audio-playing-line"
          onClick={() => revealLine(list.current, active)}>Go to playing line</button>
      } />
      {problem && <div className="audio-banner">{recovery}</div>}
    </div>}
    {!working && <div ref={actions} className="audio-actions">
      {needsSetup && <div className="audio-banner">
        <p>Set up Read aloud in Settings to generate audio</p>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => openSettings("voice", "tts-settings-heading")}>Open Read aloud settings</button>
      </div>}
      {!asset && recovery}
      {work && options?.defaults && <details className="audio-voices">
        <summary>Change voices</summary>
        <form className="studio-options" key={JSON.stringify(audio?.voices ?? options.defaults)} onSubmit={(event) => {
          event.preventDefault();
          const voices = readVoices(new FormData(event.currentTarget));
          if (voices) record(voices);
        }}>
          <VoiceFields options={options} initial={audio?.voices} />
          <button type="submit" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={work.disabled}>Record with these voices</button>
        </form>
      </details>}
    </div>}
    <AudioTranscript artifact={artifact} onCite={onCite} onPart={onPart} onAsk={onAsk} changed={changed} goTo={goTo}
      script={script} timings={timings} list={list} transport={transport} active={active} onSeek={enabled ? player.seek : undefined} />
  </section>;
}

export function AudioOverview(props: ReaderProps) {
  const { artifact, onCite, presentation } = props;
  if (presentation === "preview" || presentation === "print") return <AudioScript artifact={artifact} onCite={onCite} presentation={presentation} />;
  return <AudioOverviewReader key={JSON.stringify([artifact.id, artifact.contentHash])} {...props} />;
}
