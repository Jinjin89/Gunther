import { Pause, Play, RotateCcw, RotateCw, Volume2, VolumeX } from "lucide-react";
import { useCallback, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { formatClock } from "../items/sourceContent";
import { withShortcut } from "../shortcuts/shortcuts";

const SPEEDS = [1, 1.25, 1.5, 2, 0.75];
const PLAYBACK_ERROR = "The audio file couldn’t be played.";
// Codes defined by MediaError, including in browsers that leave its message empty.
const MEDIA_ERRORS: Record<number, string> = {
  1: "Audio loading was interrupted.",
  2: "A network error interrupted audio loading.",
  3: "The audio file could not be decoded.",
  4: "The audio file is unavailable or its format is unsupported.",
};
const initial = {
  time: null as number | null,
  mediaDuration: 0,
  playing: false,
  rate: 1,
  volume: 1,
  muted: false,
  failure: null as { message: string; retryable: boolean } | null,
};

function knownDuration(seconds: number | null): number {
  return seconds !== null && Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
}

export function useAudioPlayer(url: string | null, fallbackDuration: number | null, label?: string) {
  const audio = useRef<HTMLAudioElement>(null);
  const playRequest = useRef<Promise<void> | null>(null);
  const attach = useCallback((element: HTMLAudioElement | null) => {
    const previous = audio.current;
    playRequest.current = null;
    audio.current = element;
    if (previous && previous !== element && !previous.paused) previous.pause();
  }, []);
  const [state, setState] = useState({ url, ...initial });
  if (state.url !== url) setState({ url, ...initial });
  const update = useCallback((changes: Partial<typeof initial>) => {
    setState((current) => current.url === url ? { ...current, ...changes } : current);
  }, [url]);
  // Some MediaRecorder files report infinity. Use the duration saved at capture in that case.
  const duration = state.mediaDuration || knownDuration(fallbackDuration);
  const enabled = Boolean(url && (!state.failure || state.failure.retryable));
  const start = useCallback((element: HTMLAudioElement) => {
    const request = element.play();
    playRequest.current = request;
    update({ failure: null });
    void request.catch((reason: unknown) => {
      if (audio.current !== element || playRequest.current !== request) return;
      const error = reason instanceof Error || reason instanceof DOMException ? reason : null;
      if (error?.name === "AbortError") return;
      const retryable = error?.name === "NotAllowedError";
      const message = error?.message || (retryable ? "Playback was blocked. Press Play again to try." : PLAYBACK_ERROR);
      update({ playing: false, failure: { message, retryable } });
    });
  }, [update]);
  const seek = useCallback((seconds: number, play = false) => {
    const element = audio.current;
    if (!element || !enabled || !Number.isFinite(seconds)) return;
    element.currentTime = Math.max(0, Math.min(seconds, duration || seconds));
    update({ time: element.currentTime });
    if (play) start(element);
  }, [duration, enabled, start, update]);

  const toggle = () => {
    const element = audio.current;
    if (!element || !enabled) return;
    if (element.paused) start(element);
    else element.pause();
  };
  const cycleRate = () => {
    if (!audio.current) return;
    const rate = SPEEDS[(SPEEDS.indexOf(state.rate) + 1) % SPEEDS.length]!;
    audio.current.playbackRate = rate;
    update({ rate });
  };
  const setVolume = (volume: number) => {
    const element = audio.current;
    if (!element) return;
    if (volume > 0) element.volume = volume;
    element.muted = volume === 0;
    update({ volume: element.volume, muted: element.muted });
  };
  const toggleMute = () => {
    const element = audio.current;
    if (!element) return;
    element.muted = !element.muted;
    update({ muted: element.muted });
  };
  const element = url ? <audio
    key={url} ref={attach} src={url} preload="metadata" aria-label={label}
    onLoadedMetadata={(event) => update({ mediaDuration: knownDuration(event.currentTarget.duration) })}
    onDurationChange={(event) => update({ mediaDuration: knownDuration(event.currentTarget.duration) })}
    onTimeUpdate={(event) => update({ time: event.currentTarget.currentTime })}
    onPlay={(event) => update({ playing: true, time: event.currentTarget.currentTime })}
    onPause={() => update({ playing: false })}
    onEnded={(event) => update({ playing: false, time: event.currentTarget.currentTime })}
    onError={(event) => {
      const error = event.currentTarget.error;
      const message = error?.message?.trim() || MEDIA_ERRORS[error?.code ?? 0] || PLAYBACK_ERROR;
      playRequest.current = null;
      update({ playing: false, failure: { message, retryable: false } });
    }}
  /> : null;

  return { ...state, error: state.failure?.message ?? null, duration, enabled, element, seek, toggle, cycleRate, setVolume, toggleMute };
}

export function AudioPlayer({ player, trackMarks, actions, volumeControls = false, scopedShortcuts = false }: {
  player: ReturnType<typeof useAudioPlayer>;
  trackMarks?: ReactNode;
  actions?: ReactNode;
  volumeControls?: boolean;
  scopedShortcuts?: boolean;
}) {
  const { playing, duration, rate, volume, muted, enabled } = player;
  const time = player.time ?? 0;
  const elapsed = formatClock(time);
  const total = duration ? formatClock(duration) : "—";
  const progress = duration ? Math.min(100, (time / duration) * 100) : 0;
  const playLabel = playing ? "Pause" : "Play";
  const muteLabel = muted ? "Unmute" : "Mute";
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!enabled || event.defaultPrevented || event.nativeEvent.isComposing || event.altKey
      || event.ctrlKey || event.metaKey || event.shiftKey || event.target !== event.currentTarget) return;
    switch (event.key) {
      case " ": player.toggle(); break;
      case "ArrowLeft": player.seek(time - 5); break;
      case "ArrowRight": player.seek(time + 5); break;
      default: return;
    }
    event.preventDefault();
  };
  return <div className="gx-player" role="group" aria-label="Audio player"
    tabIndex={scopedShortcuts && enabled ? 0 : undefined}
    aria-keyshortcuts={scopedShortcuts && enabled ? "Space ArrowLeft ArrowRight" : undefined}
    onKeyDown={scopedShortcuts ? onKeyDown : undefined}>
    {player.element}
    <button type="button" className="gx-player-play" onClick={player.toggle} disabled={!enabled}
      aria-label={playLabel} title={withShortcut(playLabel, "recording-play")}>
      {playing ? <Pause size={18} fill="currentColor" /> : <Play size={18} fill="currentColor" className="gx-player-play-icon" />}
    </button>
    <div className="gx-player-track" style={{ "--progress": `${progress}%` } as CSSProperties}>
      <input type="range" min={0} max={duration || 0.1} step={0.1}
        value={Math.min(time, duration)} disabled={!enabled || !duration}
        aria-label="Position" aria-valuetext={duration ? `${elapsed} of ${total}` : `${elapsed} elapsed`}
        onChange={(event) => player.seek(event.currentTarget.valueAsNumber)} />
      {trackMarks}
    </div>
    <span className="gx-player-time"><strong>{elapsed}</strong><span className="gx-player-time-separator"> / </span><span className="gx-player-total">{total}</span></span>
    <div className="gx-player-controls">
      <button type="button" className="gx-player-skip" onClick={() => player.seek(time - 15)} disabled={!enabled}
        aria-label="Back 15 seconds" title="Back 15 seconds"><RotateCcw size={15} /><i aria-hidden="true">15</i></button>
      <button type="button" className="gx-player-skip" onClick={() => player.seek(time + 15)} disabled={!enabled}
        aria-label="Forward 15 seconds" title="Forward 15 seconds"><RotateCw size={15} /><i aria-hidden="true">15</i></button>
      <button type="button" className="gx-player-rate" onClick={player.cycleRate} disabled={!enabled}
        aria-label={`Playback speed ${rate}×`} title="Playback speed">{rate}×</button>
      {volumeControls && <div className="gx-player-volume">
        <button type="button" className="gx-player-mute" aria-label={muteLabel} title={muteLabel}
          onClick={player.toggleMute} disabled={!enabled}>{muted ? <VolumeX size={16} /> : <Volume2 size={16} />}</button>
        <input type="range" min={0} max={1} step={0.05} value={muted ? 0 : volume} aria-label="Volume"
          onChange={(event) => player.setVolume(event.currentTarget.valueAsNumber)} disabled={!enabled} />
      </div>}
      {actions}
    </div>
  </div>;
}
