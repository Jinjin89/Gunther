"""Transcribe the test lecture the way the app does today and with each new layer, and score them.

    .venv/bin/python -m evals.transcript.compare [--audio noisy|clean] [--only today,stream,...]

Methods (docs/LIVE_TRANSCRIPT_PLAN.md):
  today      3.2 s pieces cut by the clock, each sent alone to the HTTP model (realtime.py today)
  stream     Layer 1: the streaming model, sent at real-time pace; final sentences only
  paragraph  Layer 2: each paragraph (from Layer 1's times) heard again by the HTTP model, with
             the previous paragraph as context
  final      Layer 3: the Analysis model lists the terms; every paragraph heard again with the
             terms and the paragraphs before and after

Error rate = edits / reference tokens, where a token is a CJK character or a Latin word.
Results go to evals/results/transcript/<time>-<audio>/.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np
import websockets

from evals.transcript.common import (
    RATE,
    RESULTS,
    SAMPLE_DIR,
    error_rate,
    load_sample,
    pcm16,
    qwen,
    wav_bytes,
)
from gunther.config import Settings

HTTP_MODEL = "qwen-audio-3.1-asr-flash"
STREAM_MODEL = "qwen-audio-3.1-asr-flash-streaming"
# What a user might type as the recording's title; the app sends it as context.
TITLE = "机器学习 第5讲"
PARAGRAPH_PAUSE_MS = 2_000
PARAGRAPH_MIN_MS = 15_000
PARAGRAPH_MAX_MS = 60_000
TURN_CHARS = 400


def join(texts: list[str]) -> str:
    out = ""
    for text in texts:
        text = text.strip()
        if not text:
            continue
        if out and (out[-1].isascii() and out[-1].isalnum()) and text[0].isascii():
            out += " "
        out += text
    return out


# The HTTP model ------------------------------------------------------------------


def listen(samples: np.ndarray, context: list[str] | None = None) -> dict:
    service = qwen()
    messages = [
        {"role": "user", "content": [{"type": "input_text", "text": item[:TURN_CHARS]}]}
        for item in (context or [])
        if item.strip()
    ]
    encoded = base64.b64encode(wav_bytes(samples)).decode("ascii")
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {"data": f"data:audio/wav;base64,{encoded}", "format": "wav"},
                }
            ],
        }
    )
    for attempt in range(4):
        try:
            response = httpx.post(
                f"{service.root}/api/v1/services/aigc/multimodal-generation/generation",
                headers={"Authorization": f"Bearer {service.api_key}"},
                json={
                    "model": HTTP_MODEL,
                    "input": {"messages": messages},
                    "parameters": {"format": "wav"},
                },
                timeout=httpx.Timeout(120, write=600),
            )
        except httpx.TransportError:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))
            continue
        if response.status_code < 500:
            break
        time.sleep(2 * (attempt + 1))
    # Audio with no speech in it is refused as "no words"; that is an empty transcript.
    if response.status_code == 400 and "ASR_RESPONSE_HAVE_NO_WORDS" in response.text:
        return {"output": {"text": ""}}
    response.raise_for_status()
    return response.json()


def text_of(reply: dict) -> str:
    output = reply.get("output") or {}
    return str(output.get("text") or reply.get("text") or "").strip()


def today(samples: np.ndarray) -> dict:
    size = int(3.2 * RATE)
    pieces = []
    for start in range(0, len(samples), size):
        pieces.append(text_of(listen(samples[start : start + size])))
    return {"text": join(pieces), "pieces": pieces}


# The streaming model -------------------------------------------------------------


async def stream_async(
    samples: np.ndarray,
    speed: float = 1.0,
    params: dict | None = None,
    context: list[str] | None = None,
) -> dict:
    service = qwen()
    pcm = pcm16(samples)
    task = str(uuid.uuid4())
    finals: dict[int, dict] = {}
    drafts: dict[int, list[tuple[float, str]]] = {}
    events: list[str] = []
    started = asyncio.Event()
    t_connect = time.monotonic()
    async with websockets.connect(
        service.stream_url,
        additional_headers={"Authorization": f"Bearer {service.api_key}"},
        open_timeout=30,
        max_size=None,
    ) as socket:
        connected = time.monotonic() - t_connect
        payload: dict = {
            "task_group": "audio",
            "task": "asr",
            "function": "recognition",
            "model": STREAM_MODEL,
            "parameters": {"format": "pcm", "sample_rate": RATE, **(params or {})},
            "input": {},
        }
        if context:
            payload["input"]["context"] = [
                {"role": "user", "content": [{"type": "input_text", "text": item[:TURN_CHARS]}]}
                for item in context
            ]
        await socket.send(
            json.dumps(
                {
                    "header": {"action": "run-task", "task_id": task, "streaming": "duplex"},
                    "payload": payload,
                }
            )
        )
        audio_started = 0.0

        async def reader() -> None:
            async for message in socket:
                if isinstance(message, bytes):
                    continue
                data = json.loads(message)
                event = data["header"].get("event")
                if event == "task-started":
                    started.set()
                elif event == "result-generated":
                    sentence = data["payload"]["output"].get("sentence") or {}
                    if sentence.get("heartbeat"):
                        continue
                    number = int(sentence.get("sentence_id") or 0)
                    if sentence.get("sentence_end"):
                        finals[number] = {
                            "text": sentence.get("text", ""),
                            "start_ms": sentence.get("begin_time"),
                            "end_ms": sentence.get("end_time"),
                            "arrived_s": round(time.monotonic() - audio_started, 2),
                            "words": [
                                {
                                    "start_ms": word.get("begin_time"),
                                    "end_ms": word.get("end_time"),
                                    "text": (word.get("text") or "")
                                    + (word.get("punctuation") or ""),
                                }
                                for word in sentence.get("words") or []
                            ],
                        }
                    elif sentence.get("text"):
                        drafts.setdefault(number, []).append(
                            (round(time.monotonic() - audio_started, 2), sentence["text"])
                        )
                else:
                    events.append(f"{event}: {data['header'].get('error_message', '')}")
                    if event in ("task-finished", "task-failed"):
                        if not started.is_set():
                            started.set()
                        return

        listening = asyncio.create_task(reader())
        await asyncio.wait_for(started.wait(), 30)
        audio_started = time.monotonic()
        chunk = RATE // 10 * 2
        for start in range(0, len(pcm), chunk):
            await socket.send(pcm[start : start + chunk])
            await asyncio.sleep(0.1 / speed)
        await socket.send(
            json.dumps(
                {
                    "header": {"action": "finish-task", "task_id": task, "streaming": "duplex"},
                    "payload": {"input": {}},
                }
            )
        )
        await asyncio.wait_for(listening, 120)
    sentences = [finals[key] for key in sorted(finals)]
    lags = [
        s["arrived_s"] - s["end_ms"] / 1000 / speed
        for s in sentences
        if s.get("end_ms") is not None
    ]
    return {
        "text": join([s["text"] for s in sentences]),
        "sentences": sentences,
        "drafts": {str(k): v for k, v in drafts.items()},
        "draft_updates": sum(len(v) for v in drafts.values()),
        "connect_s": round(connected, 2),
        "final_lag_s": {
            "mean": round(float(np.mean(lags)), 2),
            "max": round(float(np.max(lags)), 2),
        }
        if lags
        else None,
        "events": events,
    }


def stream(samples: np.ndarray, **options) -> dict:
    return asyncio.run(stream_async(samples, **options))


# Paragraphs, checked again -------------------------------------------------------


SENTENCE_END = tuple("。！？!?.；;")


def split_sentences(pieces: list[dict]) -> list[dict]:
    """Real sentences, with times, from the model's pieces: words up to a sentence-ending mark.

    In a noisy room the model's own pieces can run for 20-30 s across paragraph breaks.
    """

    out: list[dict] = []
    for piece in pieces:
        words = [w for w in piece.get("words") or [] if w.get("start_ms") is not None]
        if not str(piece.get("text") or "").strip():
            # Noise taken for speech comes back as an empty piece; it is not a sentence.
            continue
        if not words:
            if piece.get("start_ms") is not None and piece.get("end_ms") is not None:
                out.append({**piece, "words": []})
            continue
        current: list[dict] = []
        for word in words:
            current.append(word)
            if word["text"].rstrip().endswith(SENTENCE_END):
                out.append(_sentence(current))
                current = []
        if current:
            out.append(_sentence(current))
    return out


def _sentence(words: list[dict]) -> dict:
    return {
        "text": "".join(w["text"] for w in words).strip(),
        "start_ms": words[0]["start_ms"],
        "end_ms": words[-1]["end_ms"],
        "words": words,
    }


def paragraphs(sentences: list[dict]) -> list[dict]:
    groups: list[dict] = []
    for sentence in split_sentences(sentences):
        current = groups[-1] if groups else None
        if current is not None:
            pause = sentence["start_ms"] - current["end_ms"]
            length = current["end_ms"] - current["start_ms"]
            if not (
                (pause >= PARAGRAPH_PAUSE_MS and length >= PARAGRAPH_MIN_MS)
                or length >= PARAGRAPH_MAX_MS
            ):
                current["sentences"].append(sentence)
                current["end_ms"] = sentence["end_ms"]
                continue
        groups.append(
            {
                "start_ms": sentence["start_ms"],
                "end_ms": sentence["end_ms"],
                "sentences": [sentence],
            }
        )
    for group in groups:
        group["live"] = join([s["text"] for s in group["sentences"]])
    return groups


def cut(samples: np.ndarray, start_ms: int, end_ms: int, pad_ms: int = 300) -> np.ndarray:
    first = max(0, (start_ms - pad_ms) * RATE // 1000)
    last = min(len(samples), (end_ms + pad_ms) * RATE // 1000)
    return samples[first:last]


def tail(text: str, limit: int = TURN_CHARS) -> str:
    return text[-limit:]


def paragraph_check(samples: np.ndarray, groups: list[dict]) -> dict:
    checked: list[str] = []
    for index, group in enumerate(groups):
        before = checked[index - 1] if index else ""
        context = [TITLE, tail(before)]
        checked.append(
            text_of(listen(cut(samples, group["start_ms"], group["end_ms"]), context))
            or group["live"]
        )
    return {"text": join(checked), "paragraphs": checked}


def terms_for(transcript: str) -> list[str]:
    settings = Settings()
    if not settings.llm_api_key:
        return []
    prompt = (
        "You read a transcript made by speech recognition. List the names, technical terms, "
        "acronyms and other distinctive words or phrases that are used in it, written the way "
        "they are most often and most clearly written in this transcript. These will help a "
        "speech recognizer hear the same words again.\nRules: copy each term exactly as it "
        "appears in the transcript; do not translate, explain or add terms that do not appear; "
        "skip common everyday words; at most 60 "
        'terms, most important first.\nAnswer with JSON only: {"terms": ["...", "..."]}'
    )
    response = httpx.post(
        f"{settings.llm_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json={
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": transcript},
            ],
            "response_format": {"type": "json_object"},
        },
        timeout=120,
    )
    response.raise_for_status()
    raw = json.loads(response.json()["choices"][0]["message"]["content"]).get("terms", [])
    lowered = transcript.lower()
    seen: list[str] = []
    for term in raw:
        term = str(term).strip()
        if term and term.lower() in lowered and term.lower() not in (t.lower() for t in seen):
            seen.append(term)
    return seen[:60]


def pick_terms(terms: list[str], near: str) -> str:
    near_lower = near.lower()
    ordered = [t for t in terms if t.lower() in near_lower] + [
        t for t in terms if t.lower() not in near_lower
    ]
    out = ""
    for term in ordered:
        candidate = f"{out}, {term}" if out else term
        if len(candidate) > TURN_CHARS:
            break
        out = candidate
    return out


def final_check(samples: np.ndarray, groups: list[dict], current: list[str]) -> dict:
    terms = terms_for(join(current))
    checked: list[str] = []
    for index, group in enumerate(groups):
        before = current[index - 1] if index else ""
        after = current[index + 1] if index + 1 < len(groups) else ""
        context = [
            pick_terms(terms, before + current[index] + after),
            tail(before),
            after[:TURN_CHARS],
        ]
        checked.append(
            text_of(listen(cut(samples, group["start_ms"], group["end_ms"]), context))
            or current[index]
        )
    return {"text": join(checked), "paragraphs": checked, "terms": terms}


# Run -------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", default="noisy", choices=["noisy", "clean", "hard"])
    parser.add_argument("--only", default="today,stream,paragraph,final")
    parser.add_argument("--speed", type=float, default=1.0, help="streaming pace; 1 = real time")
    parser.add_argument("--params", default="{}", help="extra streaming parameters, as JSON")
    parser.add_argument("--label", default="", help="added to the results folder's name")
    parser.add_argument(
        "--reuse", help="a results.json whose stream run to use instead of streaming"
    )
    parser.add_argument("--http-model", default=HTTP_MODEL, help="the model heard by upload")
    parser.add_argument("--stream-model", default=STREAM_MODEL, help="the live (WebSocket) model")
    args = parser.parse_args()
    globals().update(HTTP_MODEL=args.http_model, STREAM_MODEL=args.stream_model)
    wanted = set(args.only.split(","))
    samples = load_sample(args.audio)
    reference_doc = json.loads((SAMPLE_DIR / "reference.json").read_text(encoding="utf-8"))
    reference = join([s["text"] for s in reference_doc["sentences"]])
    suffix = f"-{args.label}" if args.label else ""
    out_dir = RESULTS / f"{datetime.now():%Y%m%d-%H%M%S}-{args.audio}{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}

    def record(name: str, result: dict, seconds: float) -> None:
        result["error_rate"] = round(error_rate(reference, result["text"]), 4)
        result["seconds"] = round(seconds, 1)
        results[name] = result
        print(f"{name:<10} error {result['error_rate']:.1%}  ({seconds:.0f} s)", flush=True)
        (out_dir / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    if "today" in wanted:
        started = time.monotonic()
        record("today", today(samples), time.monotonic() - started)
    if wanted & {"stream", "paragraph", "final"}:
        started = time.monotonic()
        if args.reuse:
            live = json.loads(Path(args.reuse).read_text(encoding="utf-8"))["stream"]
            results["stream"] = live
            print(f"stream     error {live['error_rate']:.1%}  (reused)", flush=True)
        else:
            params = json.loads(args.params)
            live = stream(samples, speed=args.speed, context=[TITLE], params=params)
            live["params"] = params
            record("stream", live, time.monotonic() - started)
        groups = paragraphs(live["sentences"])
        print(f"           {len(groups)} paragraphs from the live sentences", flush=True)
        current = [group["live"] for group in groups]
        if wanted & {"paragraph", "final"}:
            started = time.monotonic()
            checked = paragraph_check(samples, groups)
            record("paragraph", checked, time.monotonic() - started)
            current = checked["paragraphs"]
        if "final" in wanted:
            started = time.monotonic()
            record("final", final_check(samples, groups, current), time.monotonic() - started)
    lines = [
        f"# Transcription comparison ({args.audio}, {reference_doc['seconds']:.0f} s)",
        "",
        "| Method | Error rate | Time |",
        "|---|---|---|",
    ]
    lines += [
        f"| {name} | {result['error_rate']:.1%} | {result['seconds']:.0f} s |"
        for name, result in results.items()
    ]
    lines += ["", "## Reference", "", reference, ""]
    for name, result in results.items():
        lines += [f"## {name}", "", result["text"], ""]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"→ {out_dir}/report.md")


if __name__ == "__main__":
    main()
