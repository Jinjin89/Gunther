"""Shared pieces of the transcription measurements: keys, audio files, scoring."""

from __future__ import annotations

import io
import re
import tomllib
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from gunther.config import Settings

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results" / "transcript"
SAMPLE_DIR = RESULTS / "sample"
RATE = 16_000


@dataclass(frozen=True)
class Qwen:
    api_key: str
    base_url: str

    @property
    def host(self) -> str:
        return self.base_url.split("://", 1)[1].split("/", 1)[0]

    @property
    def stream_url(self) -> str:
        return f"wss://{self.host}/api-ws/v1/inference"

    @property
    def root(self) -> str:
        return f"https://{self.host}"


def qwen() -> Qwen:
    settings = Settings()
    if not settings.qwen_stt_api_key:
        raise SystemExit(
            "Put QWEN_STT_API_KEY in the repo's .env (see evals/transcript/README.md)."
        )
    return Qwen(settings.qwen_stt_api_key, settings.qwen_stt_base_url.rstrip("/"))


def load_script() -> dict:
    return tomllib.loads((HERE / "script.toml").read_text(encoding="utf-8"))


# Audio ---------------------------------------------------------------------------


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Mono float samples in [-1, 1] and the rate. Streamed WAVs may carry a bogus length."""

    with wave.open(io.BytesIO(data), "rb") as reader:
        rate, width, channels = reader.getframerate(), reader.getsampwidth(), reader.getnchannels()
    if width != 2:
        raise ValueError("Only 16-bit WAV is read here.")
    start = data.find(b"data") + 8
    body = data[start:]
    body = body[: len(body) - len(body) % (2 * channels)]
    samples = np.frombuffer(body, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def resample(samples: np.ndarray, rate: int, target: int = RATE) -> np.ndarray:
    """Band-limit with an FFT, then interpolate; good enough for test audio."""

    if rate == target:
        return samples
    spectrum = np.fft.rfft(samples)
    freqs = np.fft.rfftfreq(len(samples), 1 / rate)
    spectrum[freqs > target * 0.45] = 0
    limited = np.fft.irfft(spectrum, len(samples))
    count = int(len(samples) * target / rate)
    return np.interp(np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), limited)


def pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def write_wav(path: Path, samples: np.ndarray, rate: int = RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm16(samples))


def wav_bytes(samples: np.ndarray, rate: int = RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm16(samples))
    return out.getvalue()


def load_sample(name: str = "noisy") -> np.ndarray:
    samples, rate = read_wav((SAMPLE_DIR / f"{name}.wav").read_bytes())
    return resample(samples, rate)


# Scoring -------------------------------------------------------------------------


def normalize(text: str) -> list[str]:
    """Characters for CJK, words for everything else; no punctuation, case or spacing."""

    text = unicodedata.normalize("NFKC", text).lower()
    tokens: list[str] = []
    for piece in re.findall(r"[㐀-鿿]|[a-z0-9]+", text):
        tokens.append(piece)
    return tokens


def error_rate(reference: str, hypothesis: str) -> float:
    """Mixed error rate: edits over reference tokens (a CJK character or a Latin word)."""

    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, token in enumerate(ref, 1):
        current = [i] + [0] * len(hyp)
        for j, other in enumerate(hyp, 1):
            current[j] = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (token != other),
            )
        previous = current
    return previous[-1] / len(ref)
