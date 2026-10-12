/** How many samples `encodePcm16` makes from a block of `length` samples. */
export function pcm16Length(length: number, sourceRate: number, targetRate = 24_000) {
  return Math.max(1, Math.floor(length / (sourceRate / targetRate)));
}

/**
 * Mono float samples as base64 16-bit PCM, the format the live transcription socket takes:
 * 24 kHz for the original protocol, 16 kHz for protocol 2.
 */
export function encodePcm16(samples: Float32Array, sourceRate: number, targetRate = 24_000) {
  const ratio = sourceRate / targetRate;
  const length = pcm16Length(samples.length, sourceRate, targetRate);
  const pcm = new Int16Array(length);
  for (let index = 0; index < length; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.min(samples.length, Math.floor((index + 1) * ratio));
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
