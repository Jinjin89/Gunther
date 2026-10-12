"""Answer the open questions about Qwen's streaming model (docs/LIVE_TRANSCRIPT_PLAN.md §2.10).

    .venv/bin/python -m evals.transcript.probe_stream [--trial NAME ...]

Trials, each on the first ~25 s of the test lecture (run make_sample first):
  connect     how long opening the socket and starting a task take; two tasks on one socket
  context     run-task with input.context, then continue-task halfway: accepted or refused
  silence     30 s with no audio in the middle of a task: does the task survive
  heartbeat   the same 30 s filled with silent audio and heartbeat: true
  fast        audio sent at 3x real time: same text, how much sooner
  semantic    semantic_punctuation_enabled: true (sentence ends by meaning)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid

import numpy as np
import websockets

from evals.transcript.common import RATE, load_sample, pcm16, qwen

MODEL = "qwen-audio-3.1-asr-flash-streaming"
CHUNK = RATE // 10 * 2  # 100 ms of 16-bit audio


def header(action: str, task: str) -> dict:
    return {"action": action, "task_id": task, "streaming": "duplex"}


def context_turn(text: str) -> list[dict]:
    return [{"role": "user", "content": [{"type": "input_text", "text": text}]}]


async def task(
    socket,
    pcm: bytes,
    *,
    params=None,
    context=None,
    speed=1.0,
    gap_s=0.0,
    gap_audio=False,
    continue_context=None,
    log=print,
) -> dict:
    task_id = str(uuid.uuid4())
    payload = {
        "task_group": "audio",
        "task": "asr",
        "function": "recognition",
        "model": MODEL,
        "parameters": {"format": "pcm", "sample_rate": RATE, **(params or {})},
        "input": {"context": context_turn(context)} if context else {},
    }
    sent_at = time.monotonic()
    await socket.send(json.dumps({"header": header("run-task", task_id), "payload": payload}))
    finals: list[str] = []
    outcome: dict = {"started_s": None, "failed": None}
    started = asyncio.Event()
    clock = {"audio": 0.0}

    async def reader() -> None:
        async for message in socket:
            if isinstance(message, bytes):
                continue
            data = json.loads(message)
            event = data["header"].get("event")
            if event == "task-started":
                outcome["started_s"] = round(time.monotonic() - sent_at, 2)
                started.set()
            elif event == "result-generated":
                sentence = data["payload"]["output"].get("sentence") or {}
                if sentence.get("sentence_end") and not sentence.get("heartbeat"):
                    finals.append(sentence.get("text", ""))
                    span = f"{sentence.get('begin_time')}-{sentence.get('end_time')}"
                    elapsed = time.monotonic() - clock["audio"]
                    log(f"    {elapsed:6.2f}s final {span} {sentence.get('text')!r}")
            elif event in ("task-finished", "task-failed"):
                if event == "task-failed":
                    outcome["failed"] = (
                        f"{data['header'].get('error_code')}: {data['header'].get('error_message')}"
                    )
                    elapsed = time.monotonic() - clock["audio"]
                    log(f"    {elapsed:6.2f}s task-failed {outcome['failed']}")
                started.set()
                return

    listening = asyncio.create_task(reader())
    await asyncio.wait_for(started.wait(), 30)
    if outcome["failed"]:
        return {**outcome, "text": ""}
    clock["audio"] = time.monotonic()
    half = len(pcm) // 2 // CHUNK * CHUNK
    for start in range(0, len(pcm), CHUNK):
        if listening.done():
            break
        if start == half and continue_context:
            await socket.send(
                json.dumps(
                    {
                        "header": header("continue-task", task_id),
                        "payload": {"input": {"context": context_turn(continue_context)}},
                    }
                )
            )
            log(f"    {time.monotonic() - clock['audio']:6.2f}s sent continue-task")
        if start == half and gap_s:
            kind = "of silent audio" if gap_audio else "with no audio"
            log(f"    {time.monotonic() - clock['audio']:6.2f}s {gap_s:.0f} s {kind}")
            if gap_audio:
                for _ in range(int(gap_s * 10)):
                    await socket.send(b"\x00" * CHUNK)
                    await asyncio.sleep(0.1)
            else:
                await asyncio.sleep(gap_s)
        try:
            await socket.send(pcm[start : start + CHUNK])
        except websockets.ConnectionClosed as error:
            outcome["failed"] = outcome["failed"] or f"socket closed: {error}"
            break
        await asyncio.sleep(0.1 / speed)
    outcome["audio_sent_s"] = round(time.monotonic() - clock["audio"], 2)
    if not listening.done():
        await socket.send(
            json.dumps({"header": header("finish-task", task_id), "payload": {"input": {}}})
        )
        try:
            await asyncio.wait_for(listening, 60)
        except TimeoutError:
            outcome["failed"] = outcome["failed"] or "no task-finished within 60 s"
    outcome["done_s"] = round(time.monotonic() - clock["audio"], 2)
    return {**outcome, "text": "".join(finals)}


async def connect():
    service = qwen()
    opened = time.monotonic()
    socket = await websockets.connect(
        service.stream_url,
        additional_headers={"Authorization": f"Bearer {service.api_key}"},
        open_timeout=30,
        max_size=None,
    )
    return socket, round(time.monotonic() - opened, 2)


async def trial(name: str, pcm: bytes) -> dict:
    socket, connect_s = await connect()
    print(f"\n## {name} (socket opened in {connect_s} s)")
    try:
        if name == "connect":
            first = await task(socket, pcm[: CHUNK * 30])
            second = await task(socket, pcm[: CHUNK * 30])
            return {"connect_s": connect_s, "task1": first, "task2": second}
        if name == "context":
            return await task(
                socket, pcm, context="机器学习 第5讲", continue_context="梯度下降, learning rate"
            )
        if name == "silence":
            return await task(socket, pcm, gap_s=30)
        if name == "heartbeat":
            return await task(socket, pcm, gap_s=30, gap_audio=True, params={"heartbeat": True})
        if name == "fast":
            real = await task(socket, pcm)
            fast = await task(socket, pcm, speed=3.0)
            return {"real": real, "fast": fast, "same_text": real["text"] == fast["text"]}
        if name == "semantic":
            return await task(socket, pcm, params={"semantic_punctuation_enabled": True})
        raise SystemExit(f"Unknown trial {name}")
    finally:
        await socket.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--trial",
        nargs="*",
        default=["connect", "context", "silence", "heartbeat", "fast", "semantic"],
    )
    parser.add_argument("--seconds", type=float, default=25.0)
    args = parser.parse_args()
    samples = load_sample("noisy")[: int(args.seconds * RATE)]
    pcm = pcm16(np.asarray(samples))
    results = {}
    for name in args.trial:
        results[name] = asyncio.run(trial(name, pcm))
        print(json.dumps(results[name], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
