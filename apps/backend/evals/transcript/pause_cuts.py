"""Where do the live pause cuts fall on the test lecture?

    .venv/bin/python -m evals.transcript.pause_cuts [noisy hard ...]

Runs ``gunther.vad.Segmenter`` (the cuts live recording uses for SenseVoice and
OpenAI-style servers) over each sample, in 100 ms chunks like the app sends them, and
prints every piece next to the sentences of the script (``reference.json``) it overlaps.
A cut inside a sentence is normal when the speaker pauses mid-sentence (a comma) or
talks for 15 s without a pause (the cap); a piece that holds a sentence boundary
inside it merely merges two sentences.
"""

from __future__ import annotations

import json
import sys

from evals.transcript.common import RATE, SAMPLE_DIR, load_sample, pcm16
from gunther import vad


def cuts(name: str) -> list[tuple[int, int]]:
    path = vad.default_model_path()
    if path is None:
        raise SystemExit("Fetch the pause model first: npm run models:fetch")
    pcm = pcm16(load_sample(name))
    segmenter = vad.Segmenter(vad.SileroVad(path))
    chunk = RATE * 2 // 10
    pieces: list[tuple[int, int]] = []
    for at in range(0, len(pcm), chunk):
        pieces += segmenter.feed(pcm[at : at + chunk])
    pieces += segmenter.flush()
    return pieces


def main(names: list[str]) -> None:
    sentences = json.loads((SAMPLE_DIR / "reference.json").read_text(encoding="utf-8"))["sentences"]
    for name in names or ["noisy", "hard"]:
        pieces = cuts(name)
        lengths = [(end - start) / (RATE * 2) for start, end in pieces]
        print(f"\n== {name}: {len(pieces)} pieces, longest {max(lengths):.1f} s")
        inside = 0
        for number, (start, end) in enumerate(pieces, 1):
            first, last = start // vad.BYTES_PER_MS, end // vad.BYTES_PER_MS
            # A boundary is "cut through" when the piece's edge falls inside a sentence.
            hit = [
                f"{i}"
                for i, s in enumerate(sentences, 1)
                if s["start_ms"] < last and s["end_ms"] > first
            ]
            edge = [
                i
                for i, s in enumerate(sentences, 1)
                if s["start_ms"] + 150 < first < s["end_ms"] - 150
                or s["start_ms"] + 150 < last < s["end_ms"] - 150
            ]
            inside += bool(edge)
            flag = f"  cuts inside sentence {edge}" if edge else ""
            span = f"{first / 1000:7.2f} - {last / 1000:7.2f} s"
            print(f"{number:3d}  {span}  sentences {','.join(hit)}{flag}")
        print(f"{inside} of {len(pieces)} pieces start or end inside a sentence of the script")


if __name__ == "__main__":
    main(sys.argv[1:])
