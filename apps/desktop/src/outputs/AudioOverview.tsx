import type { Artifact, AudioTiming, VoicePair } from "@gunther/contracts";
import { useEffect, useRef, useState } from "react";
import { knowledgeApi, sourceAssetUrl } from "../api";
import { openSettings } from "../components/SettingsLayout";
import { AnswerBody, citationNumbers } from "../pages/AnswerBody";
import { saveBlob } from "../saveBlob";
import type { ReaderProps } from "./OutputViews";
import { splitDocument } from "./sections";
import { readVoices, VoiceFields } from "./VoiceFields";

function recordingFor(artifact: Artifact) {
  return artifact.audio?.contentHash === artifact.contentHash ? artifact.audio : null;
}

function wavAsset(artifact: Artifact) {
  const audio = recordingFor(artifact);
  return audio?.status === "ready" ? audio.audioAssetId : null;
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

type ScriptProps = Pick<ReaderProps, "artifact" | "onPart" | "goTo"> & {
  onCite?: ReaderProps["onCite"];
  timings?: AudioTiming[] | undefined;
  active?: number | undefined;
  onSeek?: ((start: number) => void) | undefined;
};

export function AudioScript({ artifact, onCite, onPart, goTo, timings, active, onSeek }: ScriptProps) {
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (goTo) list.current?.children.item(goTo.index)?.scrollIntoView?.({ block: "nearest" });
  }, [goTo, artifact.id]);
  const numbers = citationNumbers(artifact.citations);
  const onCitation = onCite ? (index: number) => {
    const citation = artifact.citations[index];
    if (citation) onCite(citation, index);
  } : undefined;
  const byLine = new Map(timings?.map((timing) => [timing.line, timing]));
  return <ol ref={list} className="audio-script" aria-label="Script">
    {splitDocument(artifact.content, artifact.kind).sections.map((line, index) => {
      const timing = byLine.get(index + 1);
      const speaker = line.startsWith("A:") || line.startsWith("B:") ? line[0] : undefined;
      const seek = onSeek && timing && timing.speaker === speaker ? () => onSeek(timing.start) : undefined;
      return <li key={index} aria-current={active === index + 1 ? "true" : undefined}
        className={seek ? "is-seekable" : undefined} onFocus={() => onPart?.(index)}
        onClick={(event) => {
          if (event.target instanceof Element && event.target.closest("button, a")) return;
          onPart?.(index);
          seek?.();
        }}>
        {seek
          ? <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-label={`Play line ${index + 1}, Speaker ${speaker}`} onClick={seek}>{speaker}</button>
          : <strong>{speaker}</strong>}
        <AnswerBody content={speaker ? line.slice(2).trimStart() : line} numbers={numbers} onCitation={onCitation} />
      </li>;
    })}
  </ol>;
}

export function AudioOverview({ artifact, onCite, onPart, goTo, onNotice, type, work, compact }: ReaderProps) {
  const player = useRef<HTMLAudioElement>(null);
  const [clock, setClock] = useState<{ asset: string; seconds: number } | null>(null);
  const [failedAsset, setFailedAsset] = useState<string | null>(null);
  const working = Boolean(work?.working);
  const audio = recordingFor(artifact);
  const asset = wavAsset(artifact);
  const playbackFailed = Boolean(asset && asset === failedAsset);
  const playingAsset = !working && !playbackFailed ? asset : null;
  const active = playingAsset && clock?.asset === playingAsset
    ? audio?.timings.find(({ start, end }) => start <= clock.seconds && clock.seconds < end)?.line : undefined;
  const seek = (start: number) => {
    if (!player.current || !playingAsset) return;
    player.current.currentTime = start;
    setClock({ asset: playingAsset, seconds: start });
    void player.current.play().catch(() => onNotice?.("The recording could not start playing."));
  };
  const record = (voices?: VoicePair) => {
    setFailedAsset(null);
    setClock(null);
    work?.run("Recording audio", (hear, signal) => knowledgeApi.retryAudioStream(
      artifact.knowledgeBaseId, artifact.id, voices ? { voices } : {}, artifact.workspaceId, hear, signal,
    ));
  };
  const options = type?.voices;
  const needsSetup = audio?.status === "needs_tts";
  const problem = playbackFailed ? "The recording could not be played." : audio?.error;
  if (compact) return <AudioScript artifact={artifact} onCite={onCite} />;

  return <section className="audio-overview" aria-label="Audio overview">
    {playingAsset && <audio key={playingAsset} ref={player} src={sourceAssetUrl(playingAsset)} controls preload="metadata"
      aria-label="Audio overview recording" onTimeUpdate={(event) => setClock({ asset: playingAsset, seconds: event.currentTarget.currentTime })}
      onPlay={(event) => setClock({ asset: playingAsset, seconds: event.currentTarget.currentTime })}
      onEnded={() => setClock(null)} onError={() => setFailedAsset(playingAsset)} />}
    {work && !working && <div className="audio-controls">
      {needsSetup && <div className="audio-banner">
        <p>Set up Read aloud in Settings to generate audio</p>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => openSettings("voice", "tts-settings-heading")}>Open Read aloud settings</button>
      </div>}
      {!needsSetup && problem && <p role="alert" className="output-error">{problem}</p>}
      {!playingAsset && (!needsSetup || options?.defaults) && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm"
        disabled={work.disabled} onClick={() => record()}>Retry audio</button>}
      {options?.defaults && <details className="audio-voices">
        <summary>Change voices</summary>
        <form className="studio-options" key={JSON.stringify(audio?.voices ?? options.defaults)} onSubmit={(event) => {
          event.preventDefault();
          const voices = readVoices(new FormData(event.currentTarget));
          if (voices && !work.disabled) record(voices);
        }}>
          <VoiceFields options={options} initial={audio?.voices} />
          <button type="submit" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={work.disabled}>Record with these voices</button>
        </form>
      </details>}
    </div>}
    <AudioScript artifact={artifact} onCite={onCite} onPart={onPart} goTo={goTo} active={active}
      timings={audio?.timings} onSeek={playingAsset ? seek : undefined} />
  </section>;
}
