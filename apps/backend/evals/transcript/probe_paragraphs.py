"""Can the Analysis model group live sentences into paragraphs by content?

    .venv/bin/python -m evals.transcript.probe_paragraphs [--run <results.json>]

Takes the sentences of a stream run (the newest `compare.py` run by default), asks the `LLM_*`
model where paragraphs start (prompt §5.2 of docs/LIVE_TRANSCRIPT_PLAN.md), once over the whole
transcript and once the live way (about once a minute, earlier paragraphs frozen), and prints
the paragraphs next to the script's true paragraph starts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from evals.transcript.common import RESULTS, SAMPLE_DIR
from evals.transcript.compare import split_sentences
from gunther.config import Settings

PROMPT = (
    "You group the sentences of a spoken transcript (speech recognition output) into paragraphs, "
    "the way a careful editor would. A paragraph holds one point of the talk: one idea, one "
    "example, one step of an explanation. Start a new paragraph where the speaker moves to a new "
    "point, where someone asks a question, or where a different person starts speaking. Keep "
    "sentences about the same point together, even many of them. Avoid a paragraph of one short "
    "sentence unless it is a question, an answer's first sentence, or a different speaker. Never "
    "change, merge or split sentences; only say where paragraphs start. Sentences are numbered "
    "from 1, and sentence 1 always starts a paragraph. The last sentences may be unfinished talk; "
    "group them as they are.\n"
    'Answer with JSON only: {"starts": [numbers of the sentences that start a paragraph]}'
)


def starts_for(sentences: list[str], before: str = "") -> list[int]:
    settings = Settings()
    numbered = "\n".join(f"{index + 1}. {text}" for index, text in enumerate(sentences))
    body = f"Before (context only, already grouped):\n{before}\n\nSentences:\n{numbered}"
    response = httpx.post(
        f"{settings.llm_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json={
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": body if before else f"Sentences:\n{numbered}"},
            ],
            "response_format": {"type": "json_object"},
        },
        timeout=120,
    )
    response.raise_for_status()
    answer = json.loads(response.json()["choices"][0]["message"]["content"]).get("starts", [])
    picked = {int(n) - 1 for n in answer if isinstance(n, int) and 1 <= n <= len(sentences)}
    return sorted(picked | {0})


def split(pool: list[dict], starts: list[int]) -> list[list[dict]]:
    return [pool[a:b] for a, b in zip(starts, [*starts[1:], len(pool)], strict=True)]


def show(name: str, groups: list[list[dict]]) -> None:
    print(f"\n== {name}: {len(groups)} paragraphs")
    for group in groups:
        text = "".join(sentence["text"] for sentence in group)
        print(f"[{group[0]['start_ms'] / 1000:6.1f}s] {text[:110]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, help="a compare.py results.json with a stream run")
    args = parser.parse_args()
    run = args.run or max(
        (
            path
            for path in RESULTS.glob("*/results.json")
            if "stream" in json.loads(path.read_text())
        ),
        key=lambda path: path.stat().st_mtime,
    )
    sentences = split_sentences(json.loads(Path(run).read_text())["stream"]["sentences"])
    reference = json.loads((SAMPLE_DIR / "reference.json").read_text())["sentences"]
    truth = [
        round(sentence["start_ms"] / 1000, 1)
        for index, sentence in enumerate(reference)
        if index == 0 or sentence["paragraph"] != reference[index - 1]["paragraph"]
    ]
    print(f"{run}\n{len(sentences)} sentences; true paragraph starts (s): {truth}")

    show("whole transcript", split(sentences, starts_for([s["text"] for s in sentences])))

    frozen: list[list[dict]] = []
    open_paragraph: list[dict] = []
    done = 0
    for cutoff in (60_000, 120_000, None):
        new = [s for s in sentences[done:] if cutoff is None or s["end_ms"] <= cutoff]
        done += len(new)
        pool = open_paragraph + new
        if not pool:
            continue
        before = "".join(s["text"] for s in frozen[-1])[-400:] if frozen else ""
        groups = split(pool, starts_for([s["text"] for s in pool], before))
        frozen += groups[:-1]
        open_paragraph = groups[-1]
    show("live, about once a minute (earlier paragraphs frozen)", [*frozen, open_paragraph])


if __name__ == "__main__":
    main()
