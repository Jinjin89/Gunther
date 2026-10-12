"""Make the test lecture: speak script.toml with Qwen's text-to-speech, then add a noisy room.

    .venv/bin/python -m evals.transcript.make_sample

Writes evals/results/transcript/sample/{clean,noisy,hard}.wav and reference.json (every sentence
with its true start and end). Spoken sentences are cached, so a second run costs nothing.
The room: a fan (pink noise), people talking far away (reversed speech), a few knocks, and the
student asking from the back of the room (quieter).
"""

from __future__ import annotations

import hashlib
import json

import httpx
import numpy as np

from evals.transcript.common import (
    RATE,
    SAMPLE_DIR,
    load_script,
    qwen,
    read_wav,
    resample,
    write_wav,
)

TTS_MODEL = "qwen3-tts-flash"
SEED = 7


def speak(text: str, voice: str) -> np.ndarray:
    cache = (
        SAMPLE_DIR / "cache" / f"{hashlib.sha256(f'{voice}|{text}'.encode()).hexdigest()[:16]}.wav"
    )
    if not cache.exists():
        service = qwen()
        response = httpx.post(
            f"{service.root}/api/v1/services/aigc/multimodal-generation/generation",
            headers={"Authorization": f"Bearer {service.api_key}"},
            json={
                "model": TTS_MODEL,
                "input": {"text": text, "voice": voice, "language_type": "Chinese"},
            },
            timeout=120,
        )
        response.raise_for_status()
        audio = httpx.get(response.json()["output"]["audio"]["url"], timeout=120)
        audio.raise_for_status()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(audio.content)
    samples, rate = read_wav(cache.read_bytes())
    return resample(samples, rate)


def pink(length: int, rng: np.random.Generator) -> np.ndarray:
    spectrum = np.fft.rfft(rng.standard_normal(length))
    freqs = np.fft.rfftfreq(length, 1 / RATE)
    spectrum[1:] /= np.sqrt(freqs[1:])
    spectrum[0] = 0
    noise = np.fft.irfft(spectrum, length)
    return noise / np.sqrt(np.mean(noise**2))


def rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(samples**2)))


def main() -> None:
    rng = np.random.default_rng(SEED)
    script = load_script()
    voices = script["voices"]
    pieces: list[np.ndarray] = []
    reference: list[dict] = []
    cursor = int(1.0 * RATE)
    pieces.append(np.zeros(cursor))
    clips: list[np.ndarray] = []
    for p_index, paragraph in enumerate(script["paragraph"]):
        if p_index:
            gap = int(rng.uniform(2.2, 3.0) * RATE)
            pieces.append(np.zeros(gap))
            cursor += gap
        far = paragraph["speaker"] == "student"
        for s_index, text in enumerate(paragraph["sentences"]):
            if s_index:
                gap = int(rng.uniform(0.35, 0.8) * RATE)
                pieces.append(np.zeros(gap))
                cursor += gap
            clip = speak(text, voices[paragraph["speaker"]])
            clips.append(clip)
            if far:
                clip = clip * 0.4
            pieces.append(clip)
            reference.append(
                {
                    "paragraph": p_index,
                    "speaker": paragraph["speaker"],
                    "text": text,
                    "start_ms": round(cursor / RATE * 1000),
                    "end_ms": round((cursor + len(clip)) / RATE * 1000),
                }
            )
            cursor += len(clip)
    pieces.append(np.zeros(int(1.5 * RATE)))
    clean = np.concatenate(pieces)
    level = rms(np.concatenate(clips))

    # A fan, about 12 dB under the speech.
    room = pink(len(clean), rng) * level * 10 ** (-12 / 20)
    # People talking far away: reversed speech, about 20 dB under.
    babble = np.zeros(len(clean))
    for clip in clips:
        at = int(rng.integers(0, max(1, len(clean) - len(clip))))
        babble[at : at + len(clip)] += clip[::-1]
    babble *= level * 10 ** (-20 / 20) / max(rms(babble), 1e-9)
    # Knocks in the middle of four sentences.
    knocks = np.zeros(len(clean))
    for index in (1, 5, 9, 13):
        sentence = reference[index]
        at = int(
            (sentence["start_ms"] + 0.45 * (sentence["end_ms"] - sentence["start_ms"]))
            / 1000
            * RATE
        )
        length = int(0.18 * RATE)
        knocks[at : at + length] += (
            rng.standard_normal(length) * np.exp(-np.linspace(0, 6, length)) * level * 3
        )
    noisy = clean + room + babble + knocks

    # A hard room: the same lecture with echo (about 0.6 s to die away), the fan only 3 dB
    # under the speech, the talking 10 dB under, and the student twice as far away again.
    hard_rng = np.random.default_rng(SEED + 4)
    tail = int(0.6 * RATE)
    echo = hard_rng.standard_normal(tail) * np.exp(-np.linspace(0, 6.9, tail))
    echo[0] = 1.0
    size = len(clean) + tail
    spoken = clean.copy()
    for sentence in reference:
        if sentence["speaker"] == "student":
            first, last = (int(sentence[k] / 1000 * RATE) for k in ("start_ms", "end_ms"))
            spoken[first:last] *= 0.5
    reverberant = np.fft.irfft(np.fft.rfft(spoken, size) * np.fft.rfft(echo, size), size)
    reverberant = reverberant[: len(clean)]
    reverberant *= rms(spoken) / max(rms(reverberant), 1e-9)
    hard = (
        reverberant
        + pink(len(clean), hard_rng) * level * 10 ** (-3 / 20)
        + babble * 10 ** (10 / 20)
        + knocks
    )

    peak = max(float(np.max(np.abs(noisy))), float(np.max(np.abs(hard))))
    if peak > 0.98:
        noisy *= 0.98 / peak
        hard *= 0.98 / peak
        clean *= 0.98 / peak

    write_wav(SAMPLE_DIR / "clean.wav", clean)
    write_wav(SAMPLE_DIR / "noisy.wav", noisy)
    write_wav(SAMPLE_DIR / "hard.wav", hard)
    (SAMPLE_DIR / "reference.json").write_text(
        json.dumps(
            {"sentences": reference, "seconds": len(clean) / RATE}, ensure_ascii=False, indent=1
        ),
        encoding="utf-8",
    )
    print(f"{len(reference)} sentences, {len(clean) / RATE:.1f} s → {SAMPLE_DIR}")


if __name__ == "__main__":
    main()
