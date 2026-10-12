"""Does Qwen keep a sentence whole across a thinking pause?

    .venv/bin/python -m evals.transcript.probe_pause

One sentence spoken in two halves with 3 s, then 10 s, of fan noise between them. Prints what
the live model, the HTTP model on the whole stretch, and the HTTP model cut at the pause wrote.
"""

from __future__ import annotations

import json

import numpy as np

from evals.transcript.common import RATE
from evals.transcript.compare import listen, split_sentences, stream, text_of
from evals.transcript.make_sample import pink, rms, speak

before = ["今天我们继续讲优化方法。", "上次我们说到，梯度下降每一步都沿着负梯度的方向走一小步。"]
half_a = "所以学习率如果设得太大的话"
half_b = "模型的损失就会来回震荡，甚至越来越大。"
after = ["这就是为什么我们需要学习率衰减。"]


def build(pause_s: float) -> tuple[np.ndarray, int]:
    v = "Ethan"
    parts = [np.zeros(int(0.8 * RATE))]
    for t in before:
        parts += [speak(t, v), np.zeros(int(0.5 * RATE))]
    parts.append(speak(half_a, v))
    cut_at = sum(len(p) for p in parts) + int(pause_s / 2 * RATE)
    parts.append(np.zeros(int(pause_s * RATE)))
    parts.append(speak(half_b, v))
    for t in after:
        parts += [np.zeros(int(0.5 * RATE)), speak(t, v)]
    parts.append(np.zeros(int(1.0 * RATE)))
    clean = np.concatenate(parts)
    level = rms(np.concatenate([p for p in parts if np.any(p)]))
    noisy = clean + pink(len(clean), np.random.default_rng(3)) * level * 10 ** (-12 / 20)
    return noisy * 0.9 / np.max(np.abs(noisy)), cut_at


def main() -> None:
    for pause in (3.0, 10.0):
        audio, cut_at = build(pause)
        r = {"seconds": round(len(audio) / RATE, 1)}
        s = stream(audio)
        r["stream_pieces"] = [p["text"] for p in s["sentences"]]
        r["stream_sentences"] = [x["text"] for x in split_sentences(s["sentences"])]
        r["http_whole"] = text_of(listen(audio))
        first = text_of(listen(audio[:cut_at]))
        r["http_cut_at_pause"] = [first, text_of(listen(audio[cut_at:], context=[first]))]
        print(json.dumps({f"pause_{pause:g}s": r}, ensure_ascii=False, indent=1), flush=True)


if __name__ == "__main__":
    main()
