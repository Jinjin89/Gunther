/**
 * How many samples `encodePcm16` makes from a block of `length` samples.
 *
 * With `startSample` (how many source samples came before this block) the length carries the
 * fraction from block to block: the blocks of a recording add up to exactly
 * round(all source samples x targetRate / sourceRate). Without it, each block is floored alone.
 */
export function pcm16Length(length: number, sourceRate: number, targetRate = 24_000, startSample?: number) {
  if (startSample === undefined) return Math.max(1, Math.floor(length / (sourceRate / targetRate)));
  const scale = targetRate / sourceRate;
  return Math.round((startSample + length) * scale) - Math.round(startSample * scale);
}

/**
 * Mono float samples as base64 16-bit PCM, the format the live transcription socket takes:
 * 24 kHz for the original protocol, 16 kHz for protocol 2.
 */
export function encodePcm16(samples: Float32Array, sourceRate: number, targetRate = 24_000, startSample?: number) {
  const ratio = sourceRate / targetRate;
  const length = pcm16Length(samples.length, sourceRate, targetRate, startSample);
  const pcm = new Int16Array(length);
  // Output sample `first + index` of the whole recording covers source samples
  // [(first + index) * ratio, (first + index + 1) * ratio); this block holds those from `startSample`.
  const first = startSample === undefined ? 0 : Math.round(startSample * (targetRate / sourceRate));
  const origin = startSample ?? 0;
  for (let index = 0; index < length; index += 1) {
    const start = Math.max(0, Math.min(samples.length - 1, Math.floor((first + index) * ratio) - origin));
    const end = Math.min(samples.length, Math.max(start + 1, Math.floor((first + index + 1) * ratio) - origin));
    let total = 0;
    for (let cursor = start; cursor < end; cursor += 1) total += samples[cursor] ?? 0;
    const normalized = Math.max(-1, Math.min(1, total / Math.max(1, end - start)));
    pcm[index] = normalized < 0 ? normalized * 0x8000 : normalized * 0x7fff;
  }
  const bytes = new Uint8Array(pcm.buffer);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 1) binary += String.fromCharCode(bytes[index]!);
  return window.btoa(binary);
}
