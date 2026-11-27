"""Live sentences from Qwen's streaming model, heard through a fake upstream socket.

The fake speaks the real flow (run-task, task-started, binary PCM, result-generated,
finish-task, task-finished) on localhost, so no test touches the network.
"""

import asyncio
import base64
import json
import sys
import threading
import time
import wave
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed
from websockets.protocol import State

from gunther import realtime, speech_api, transcription
from gunther.config import Settings
from gunther.main import create_app
from gunther.qwen_stream import (
    BYTES_PER_MS,
    Pcm16Resampler,
    context_turns,
    resample_pcm16,
    split_sentences,
    stream_url,
)
from gunther.speech_registry import is_duplex_stream, qwen_live_id, qwen_upload_id

SIDECAR_TOKEN = "sidecar-token-for-tests"
SIDECAR = {"X-Gunther-Token": SIDECAR_TOKEN}
KEY = "sk-test-key-1234"
FRAME = 3_200  # 100 ms of 16 kHz audio


class FakeQwen:
    """A Qwen live socket on localhost that answers a scripted sentence in every task.

    In a task: a draft after the 2nd frame, a final sentence after the 4th (times count
    from the task's start, like the real service). ``mode`` can be "refuse" (run-task
    fails) or "silent" (run-task is never answered). ``drop_at`` closes the first real
    task's connection after that many frames.
    """

    def __init__(
        self,
        mode: str = "ok",
        drop_at: int | None = None,
        draft_at: int | None = 2,
        finals: bool = True,
        never_finish: bool = False,
        hold_start: bool = False,
        empty_final: bool = False,
    ) -> None:
        self.finals = finals
        self.never_finish = never_finish
        self.mode = mode
        self.drop_at = drop_at
        self.draft_at = draft_at
        self.empty_final = empty_final
        self.tasks: list[dict[str, Any]] = []
        self.connections = 0
        self.loop = asyncio.new_event_loop()
        self.start_gate = asyncio.Event()
        self.stop_event = asyncio.Event()
        ready: Future[int] = Future()

        def serve_forever() -> None:
            asyncio.set_event_loop(self.loop)

            async def run() -> None:
                if not hold_start:
                    self.start_gate.set()
                async with serve(self.handle, "127.0.0.1", 0) as server:
                    ready.set_result(server.sockets[0].getsockname()[1])
                    await self.stop_event.wait()

            try:
                self.loop.run_until_complete(run())
            except Exception as error:
                if ready.done():
                    raise
                ready.set_exception(error)
            finally:
                self.loop.close()

        self.thread = threading.Thread(target=serve_forever, daemon=True)
        self.thread.start()
        self.port = ready.result(timeout=5)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def real_tasks(self) -> list[dict[str, Any]]:
        """Filter out empty tasks when checking replayed audio."""

        return [task for task in self.tasks if task["frames"] > 0]

    @staticmethod
    def result(task_id: str, sentence: dict[str, Any]) -> str:
        return json.dumps(
            {
                "header": {"event": "result-generated", "task_id": task_id},
                "payload": {"output": {"sentence": sentence}},
            }
        )

    @staticmethod
    def event(task_id: str, name: str, **fields: str) -> str:
        return json.dumps({"header": {"event": name, "task_id": task_id, **fields}})

    def send(self, task: dict[str, Any], message: str) -> None:
        asyncio.run_coroutine_threadsafe(task["socket"].send(message), self.loop).result(timeout=5)

    def end(self, task: dict[str, Any], name: str = "task-finished") -> None:
        task["ended"] = True
        self.send(task, self.event(task["id"], name))

    def disconnect(self, task: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(task["socket"].close(), self.loop).result(timeout=3)

    def wait_closed(self, task: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(task["socket"].wait_closed(), self.loop).result(timeout=3)

    async def handle(self, socket) -> None:
        self.connections += 1
        task: dict[str, Any] | None = None
        async for message in socket:
            if isinstance(message, bytes):
                assert task is not None
                task["frames"] += 1
                task["bytes"] += len(message)
                task["pcm"].extend(message)
                if task["frames"] == self.draft_at:
                    await socket.send(
                        self.result(
                            task["id"],
                            {"sentence_id": 1, "text": "We'll use", "begin_time": 0},
                        )
                    )
                if task["frames"] == 4 and self.finals:
                    words = [
                        {"text": "We'll", "punctuation": "", "begin_time": 0, "end_time": 100},
                        {"text": " use", "punctuation": "", "begin_time": 100, "end_time": 200},
                        {"text": " it", "punctuation": ".", "begin_time": 200, "end_time": 300},
                    ]
                    await socket.send(
                        self.result(
                            task["id"],
                            {
                                "sentence_id": 1,
                                "sentence_end": True,
                                "text": "" if self.empty_final else "We'll use it.",
                                "begin_time": 0,
                                "end_time": 300,
                                "words": [] if self.empty_final else words,
                            },
                        )
                    )
                if self.drop_at and not self.dropped and task["frames"] == self.drop_at:
                    self.dropped = True
                    await socket.close()
                    return
                continue
            data = json.loads(message)
            action = data["header"]["action"]
            if action == "run-task":
                assert task is None or task["ended"], "a new task overlapped the preceding task"
                task = {
                    "id": data["header"]["task_id"],
                    "payload": data["payload"],
                    "frames": 0,
                    "bytes": 0,
                    "pcm": bytearray(),
                    "finished": False,
                    "ended": False,
                    "socket": socket,
                }
                self.tasks.append(task)
                await self.start_gate.wait()
                if self.mode == "silent":
                    continue
                if self.mode == "refuse":
                    task["ended"] = True
                    message = self.event(
                        task["id"], "task-failed", error_message="url error, please check url"
                    )
                else:
                    message = self.event(task["id"], "task-started")
                try:
                    await socket.send(message)
                except ConnectionClosed:
                    return
            elif action == "finish-task" and task is not None:
                task["finished"] = True
                if self.never_finish:
                    continue
                task["ended"] = True
                await socket.send(self.event(task["id"], "task-finished"))

    dropped = False

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.start_gate.set)
        self.loop.call_soon_threadsafe(self.stop_event.set)
        self.thread.join(timeout=5)


@pytest.fixture
def fake() -> Iterator[Callable[..., FakeQwen]]:
    made: list[FakeQwen] = []

    def make(**kwargs: Any) -> FakeQwen:
        server = FakeQwen(**kwargs)
        made.append(server)
        return server

    yield make
    for server in made:
        server.stop()


@contextmanager
def make_app(tmp_path: Path, server: FakeQwen, model: str, **job: Any) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        deepseek_api_key=None,
        auth_token=SIDECAR_TOKEN,
        processing_worker_enabled=False,
        service_settings_file=tmp_path / "service-settings.json",
    )
    with TestClient(create_app(settings)) as client:
        added = client.post(
            "/api/settings/speech/providers",
            headers=SIDECAR,
            json={"kind": "qwen", "apiKey": KEY, "baseUrl": server.url, "models": [model]},
        )
        assert added.status_code == 201
        roles = {
            "recording": {"model": f"qwen/{model}", "stream": True, **job},
            "dictation": {"model": f"qwen/{model}", "stream": False},
        }
        saved = client.put("/api/settings/speech/roles", headers=SIDECAR, json={"roles": roles})
        assert saved.status_code == 200
        yield client


def audio(milliseconds: int, rate: int = 16_000) -> str:
    return base64.b64encode(bytes(rate * 2 * milliseconds // 1000)).decode()


def wait_for(condition: Callable[[], bool], seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


V2 = "/api/recordings/live?protocol=2&sample_rate=16000&offset_ms={offset}"


def test_live_dictation_overrides_only_this_request_and_keeps_saved_jobs(tmp_path: Path, fake):
    server = fake()
    with make_app(tmp_path, server, "qwen-audio-3.1-asr-flash", stream=False) as client:
        with client.websocket_connect(
            V2.format(offset=0) + "&purpose=dictation&stream=true", headers=SIDECAR
        ) as socket:
            assert socket.receive_json()["live"] == "stream"
            socket.send_json({"type": "input_audio_buffer.append", "audio": audio(600)})
            draft, final = socket.receive_json(), socket.receive_json()
            assert draft["final"] is False and final["final"] is True
            assert draft["id"] == final["id"]
            socket.send_json({"type": "input_audio_buffer.commit"})
            assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 600}
        saved = client.get("/api/settings/speech", headers=SIDECAR).json()["roles"]
        assert all(role["stream"] is False for role in saved)
        # The runtime override is never applied to the Recorder, even if supplied.
        with client.websocket_connect(
            V2.format(offset=0) + "&stream=true", headers=SIDECAR
        ) as socket:
            assert socket.receive_json()["stream"] is False


@pytest.fixture(autouse=True)
def quick_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(realtime, "RECONNECT_BACKOFF", (0.01, 0.01, 0.01, 0.01))


def test_drafts_and_a_final_share_an_id_and_times_follow_the_recording_clock(
    tmp_path: Path, fake
) -> None:
    server = fake()
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash")
    with (
        app as client,
        client.websocket_connect(V2.format(offset=60_000), headers=SIDECAR) as socket,
    ):
        ready = socket.receive_json()
        assert ready["live"] == "stream" and ready["protocol"] == 2
        socket.send_json(
            {"type": "input_audio_buffer.append", "audio": audio(600), "offset_ms": 60_000}
        )
        draft = socket.receive_json()
        final = socket.receive_json()
        assert draft["type"] == "transcript.piece" and draft["final"] is False
        assert draft["end_ms"] is None and draft["start_ms"] == 60_000
        assert "sentences" not in draft
        assert final["id"] == draft["id"] and final["final"] is True
        assert final["sentences"] == [
            {"start_ms": 60_000, "end_ms": 60_300, "text": "We'll use it."}
        ]

        # A pause finishes the task; the next audio starts a new one where it begins.
        socket.send_json({"type": "input_audio_buffer.commit"})
        wait_for(lambda: server.real_tasks()[0]["finished"])
        # Once the task has ended the client hears how far the commit reached.
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 60_600}
        socket.send_json(
            {"type": "input_audio_buffer.append", "audio": audio(600), "offset_ms": 75_000}
        )
        # The audio in between never arrived, and the client is told.
        gap = socket.receive_json()
        assert (gap["type"], gap["start_ms"], gap["end_ms"]) == ("transcript.gap", 60_600, 75_000)
        socket.receive_json()
        second = socket.receive_json()
        assert second["id"] != final["id"]
        assert second["sentences"][0]["start_ms"] == 75_000
        assert second["sentences"][0]["end_ms"] == 75_300
    assert len(server.real_tasks()) == 2
    # The streaming id goes to the socket, whichever of the pair was saved.
    assert {task["payload"]["model"] for task in server.tasks} == {
        "qwen-audio-3.1-asr-flash-streaming"
    }


def test_24_khz_stream_matches_the_mobile_v2_event_fixture(
    tmp_path: Path, fake, mobile_live_v2: dict[str, Any]
) -> None:
    server = fake()
    ready = mobile_live_v2["ready"]
    chunks = mobile_live_v2["chunks"]
    rate = mobile_live_v2["sample_rate"]
    first_offset = chunks[0]["offset_ms"]
    url = f"/api/recordings/live?protocol=2&sample_rate={rate}&offset_ms={first_offset - 100}"
    with (
        make_app(tmp_path, server, ready["model"]) as client,
        client.websocket_connect(url, headers=SIDECAR) as socket,
    ):
        assert socket.receive_json() == ready
        ids: dict[str, str] = {}
        for index, chunk in enumerate(chunks):
            socket.send_json(
                {
                    "type": "input_audio_buffer.append",
                    "audio": audio(chunk["duration_ms"], rate),
                    "offset_ms": chunk["offset_ms"],
                }
            )
            if index == 0:
                assert socket.receive_json() == {
                    "type": "transcript.gap",
                    "reason": "missing",
                    "start_ms": first_offset - 100,
                    "end_ms": first_offset,
                }
            for expected in chunk["events"]:
                actual = socket.receive_json()
                if "id" in actual:
                    actual["id"] = ids.setdefault(actual["id"], f"piece-{len(ids) + 1}")
                assert actual == expected
        socket.send_json({"type": "input_audio_buffer.commit"})
        assert socket.receive_json() == mobile_live_v2["commit"]
    assert len(server.real_tasks()) == 2
    for task, chunk in zip(server.real_tasks(), chunks, strict=True):
        # Mobile sends 24 kHz and the provider hears 16 kHz, within the resampler tail.
        assert abs(task["bytes"] - chunk["duration_ms"] * BYTES_PER_MS) <= BYTES_PER_MS


def test_empty_task_cleanup_does_not_hold_another_commit_or_resumed_pcm(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "TASK_END_TIMEOUT", 3.0)
    server = fake(never_finish=True)
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash")
    with (
        app as client,
        client.websocket_connect(V2.format(offset=5_000), headers=SIDECAR) as socket,
    ):
        socket.receive_json()
        (task,) = server.tasks
        assert not task["finished"]
        socket.send_json({"type": "input_audio_buffer.commit"})
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 5_000}
        wait_for(lambda: task["finished"])
        assert task["bytes"] == 0

        socket.send_json({"type": "input_audio_buffer.commit"})
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 5_000}
        assert task["socket"].state == State.OPEN

        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(400)})
        assert socket.receive_json()["final"] is False
        final = socket.receive_json()
        assert final["final"] and final["start_ms"] == 5_000
        assert task["socket"].state == State.OPEN
        socket.send_json({"type": "input_audio_buffer.commit"})
        wait_for(lambda: len(server.tasks) == 2 and server.tasks[1]["finished"])
        resumed = server.tasks[1]
        assert resumed["bytes"] == 4 * FRAME
        assert resumed["socket"] is not task["socket"]
        server.end(resumed)
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 5_400}
        socket.close()
        server.wait_closed(task)
    assert not task["ended"]  # The provider never acknowledged the unused task.


def test_an_expired_unused_task_restarts_only_when_pcm_arrives(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "RECONNECT_BACKOFF", ())
    retired = threading.Event()
    settle = realtime._LiveStream._settle

    async def track_retirement(self, task):
        await settle(self, task)
        retired.set()

    monkeypatch.setattr(realtime._LiveStream, "_settle", track_retirement)
    server = fake()
    with (
        make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client,
        client.websocket_connect(V2.format(offset=0), headers=SIDECAR) as socket,
    ):
        assert socket.receive_json()["live"] == "stream"
        (task,) = server.tasks
        server.end(task, "task-failed")
        assert retired.wait(3)
        assert len(server.tasks) == 1 and task["bytes"] == 0
        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(400)})
        assert socket.receive_json()["final"] is False
        assert socket.receive_json()["final"] is True
        socket.send_json({"type": "input_audio_buffer.commit"})
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 400}
    assert len(server.tasks) == 2


@pytest.mark.parametrize(
    "early_audio", [False, True], ids=["disconnect", "append-commit-disconnect"]
)
def test_disconnect_while_waiting_for_task_started_closes_upstream(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch, early_audio: bool
) -> None:
    server = fake(hold_start=True)
    published = []
    send = WebSocket.send_json

    async def capture(self, event, *args, **kwargs):
        published.append(event["type"])
        await send(self, event, *args, **kwargs)

    monkeypatch.setattr(WebSocket, "send_json", capture)
    with (
        make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client,
        client.websocket_connect(V2.format(offset=0), headers=SIDECAR) as socket,
    ):
        wait_for(lambda: len(server.tasks) == 1)
        (task,) = server.tasks
        if early_audio:
            socket.send_json({"type": "input_audio_buffer.append", "audio": audio(100)})
            socket.send_json({"type": "input_audio_buffer.commit"})
        socket.close()
        server.wait_closed(task)
    assert task["bytes"] == 0 and not task["finished"]
    assert "service.ready" not in published


@pytest.mark.parametrize("limit", ["pcm", "controls"])
def test_overflowing_the_early_input_buffer_reports_an_error_and_closes(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    monkeypatch.setattr(realtime, "RETAIN_SECONDS", 0.1)
    monkeypatch.setattr(realtime, "EARLY_CONTROL_LIMIT", 2)
    server = fake(hold_start=True)
    with (
        make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client,
        client.websocket_connect(V2.format(offset=0), headers=SIDECAR) as socket,
    ):
        wait_for(lambda: len(server.tasks) == 1)
        if limit == "pcm":
            for _ in range(2):
                socket.send_json({"type": "input_audio_buffer.append", "audio": audio(60)})
        else:
            for _ in range(3):
                socket.send_json({"type": "input_audio_buffer.commit"})
        event = socket.receive_json()
        assert event["type"] == "service.error" and event["code"] == "startup_buffer_full"
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
        server.wait_closed(server.tasks[0])


def test_audio_without_a_finish_acknowledgment_fails_instead_of_flushing(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "TASK_END_TIMEOUT", 0.6)
    server = fake(finals=False, never_finish=True)
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash")
    with (
        app as client,
        client.websocket_connect(V2.format(offset=0), headers=SIDECAR) as socket,
    ):
        socket.receive_json()
        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(400), "offset_ms": 0})
        socket.receive_json()  # the draft after the 2nd frame
        socket.send_json({"type": "input_audio_buffer.commit"})
        wait_for(lambda: server.real_tasks()[0]["finished"])
        # Resume and stop while the prior pause is still awaiting the upstream reply.
        socket.send_json(
            {"type": "input_audio_buffer.append", "audio": audio(600), "offset_ms": 400}
        )
        socket.send_json({"type": "input_audio_buffer.commit"})
        error = socket.receive_json()
        assert error["type"] == "service.error" and error["code"] == "finish_unconfirmed"
        with pytest.raises(WebSocketDisconnect) as disconnected:
            socket.receive_json()
        assert disconnected.value.code == 1011
    assert len(server.tasks) == 1


@pytest.mark.parametrize("terminal", ["task-finished", "task-failed"])
def test_late_results_and_task_completion_precede_flush_after_exact_coverage(
    tmp_path: Path, fake, terminal: str
) -> None:
    server = fake(never_finish=True)
    with (
        make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client,
        client.websocket_connect(V2.format(offset=5_000), headers=SIDECAR) as socket,
    ):
        assert socket.receive_json()["live"] == "stream"
        (task,) = server.tasks
        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(100)})
        wait_for(lambda: task["bytes"] == FRAME)

        def final(number: int, text: str) -> None:
            sentence = {"sentence_id": number, "text": text, "sentence_end": True, "end_time": 100}
            server.send(task, server.result(task["id"], sentence))

        final(1, "first")
        piece = socket.receive_json()
        assert piece["final"] and piece["end_ms"] == 5_100
        socket.send_json({"type": "input_audio_buffer.commit"})
        socket.send_json({"type": "input_audio_buffer.commit"})
        wait_for(lambda: task["finished"])
        final(2, "late")
        assert socket.receive_json()["text"] == "late"
        server.end(task, terminal)
        if terminal == "task-finished":
            for _ in range(2):
                assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 5_100}
        else:
            error = socket.receive_json()
            assert error["type"] == "service.error" and error["code"] == "finish_unconfirmed"
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()


def test_qwen_empty_final_retracts_a_published_draft(tmp_path: Path, fake) -> None:
    server = fake(empty_final=True)
    with (
        make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client,
        client.websocket_connect(V2.format(offset=8_000), headers=SIDECAR) as socket,
    ):
        socket.receive_json()
        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(400)})
        draft, final = socket.receive_json(), socket.receive_json()
        assert draft["text"] and draft["final"] is False
        assert final == {
            "type": "transcript.piece",
            "id": draft["id"],
            "start_ms": 8_000,
            "end_ms": 8_300,
            "text": "",
            "final": True,
            "sentences": [],
        }


def test_early_frames_go_onto_the_only_live_startup_task_in_order(tmp_path: Path, fake) -> None:
    server = fake(hold_start=True)
    early = b"\x01\x00" * 1_600
    later = b"\x02\x00" * 4_800
    with (
        make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client,
        client.websocket_connect(V2.format(offset=0) + "&context=Terms", headers=SIDECAR) as socket,
    ):
        socket.send_json(
            {"type": "input_audio_buffer.append", "audio": base64.b64encode(early).decode()}
        )
        socket.send_json(
            {"type": "input_audio_buffer.append", "audio": base64.b64encode(later).decode()}
        )
        socket.send_json({"type": "input_audio_buffer.commit"})
        wait_for(lambda: len(server.tasks) == 1)
        (task,) = server.tasks
        assert task["payload"]["input"]["context"] == context_turns("Terms", "")
        assert task["bytes"] == 0 and not task["finished"]
        server.loop.call_soon_threadsafe(server.start_gate.set)
        ready = socket.receive_json()
        assert ready["type"] == "service.ready" and ready["live"] == "stream"
        assert socket.receive_json()["final"] is False
        assert socket.receive_json()["final"] is True
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 400}
    assert len(server.tasks) == 1 and task["pcm"] == early + later


def test_the_fallback_sends_v2_clients_final_pieces_on_the_recording_clock(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "TASK_START_TIMEOUT", 0.3)
    texts = iter(["first piece", "second piece"])

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": {"text": next(texts)}})

    server = fake(mode="refuse")
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash")
    monkeypatch.setattr("gunther.main.AsrHttpTransport", lambda: httpx.MockTransport(answer))
    with (
        app as client,
        client.websocket_connect(V2.format(offset=10_000), headers=SIDECAR) as socket,
    ):
        ready = socket.receive_json()
        assert ready["live"] == "segments-fixed" and ready["protocol"] == 2
        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(3_200)})
        first = socket.receive_json()
        assert first["type"] == "transcript.piece" and first["final"] is True
        assert (first["start_ms"], first["end_ms"]) == (10_000, 13_200)
        assert first["sentences"] == [{"start_ms": 10_000, "end_ms": 13_200, "text": "first piece"}]
        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(1_500)})
        socket.send_json({"type": "input_audio_buffer.commit"})
        second = socket.receive_json()
        assert (second["start_ms"], second["end_ms"]) == (13_200, 14_700)
        assert second["id"] != first["id"]
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 14_700}


def test_a_dropped_task_starts_again_where_the_last_final_ended(tmp_path: Path, fake) -> None:
    server = fake(drop_at=3)
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash")
    with (
        app as client,
        client.websocket_connect(V2.format(offset=60_000), headers=SIDECAR) as socket,
    ):
        socket.receive_json()
        socket.send_json(
            {"type": "input_audio_buffer.append", "audio": audio(600), "offset_ms": 60_000}
        )
        draft, retraction, replacement, final = [socket.receive_json() for _ in range(4)]
    assert draft["final"] is False and draft["text"]
    assert retraction["id"] == draft["id"] and retraction["final"] and retraction["text"] == ""
    assert retraction["start_ms"] == draft["start_ms"]
    assert replacement["id"] != draft["id"] and replacement["id"] == final["id"]
    assert final["final"] is True and final["sentences"][0]["start_ms"] == 60_000
    assert server.connections == 2
    assert server.real_tasks()[1]["bytes"] == 6 * FRAME  # all of it was sent again


def test_late_draft_retraction_does_not_confirm_unheard_audio(tmp_path: Path, fake) -> None:
    server = fake(draft_at=None, finals=False)
    pcm = b"\x01\x00\x02\x00" * 8_000
    with (
        make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client,
        client.websocket_connect(V2.format(offset=0), headers=SIDECAR) as socket,
    ):
        assert socket.receive_json()["live"] == "stream"
        (task,) = server.tasks
        socket.send_json(
            {"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()}
        )
        wait_for(lambda: task["bytes"] == len(pcm))
        draft = {"sentence_id": 1, "text": "late speech", "begin_time": 650}
        server.send(task, server.result(task["id"], draft))
        preview = socket.receive_json()
        assert preview["final"] is False and preview["start_ms"] == 650
        server.disconnect(task)

        assert socket.receive_json() == {
            "type": "transcript.piece",
            "id": preview["id"],
            "start_ms": 0,
            "end_ms": 0,
            "text": "",
            "final": True,
            "sentences": [],
        }
        wait_for(lambda: len(server.tasks) == 2 and server.tasks[1]["bytes"] == len(pcm))
        assert server.tasks[1]["pcm"] == pcm
        socket.send_json({"type": "input_audio_buffer.commit"})
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 1_000}


def test_retraction_after_a_word_timed_final_preserves_the_clients_replay(
    tmp_path: Path, fake
) -> None:
    server = fake(draft_at=None, finals=False)
    pcm = np.arange(19_200, dtype="<i2").tobytes()  # 1.2 s at 16 kHz
    with make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client:
        with client.websocket_connect(V2.format(offset=0), headers=SIDECAR) as socket:
            assert socket.receive_json()["live"] == "stream"
            (task,) = server.tasks
            socket.send_json(
                {"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()}
            )
            wait_for(lambda: task["bytes"] == len(pcm))
            final = {
                "sentence_id": 1,
                "sentence_end": True,
                "text": "confirmed",
                "end_time": 900,
                "words": [{"text": "confirmed", "begin_time": 0, "end_time": 300}],
            }
            server.send(task, server.result(task["id"], final))
            piece = socket.receive_json()
            assert piece["final"] and piece["end_ms"] == 300
            assert piece["sentences"] == [{"start_ms": 0, "end_ms": 300, "text": "confirmed"}]

            upstream_replay = pcm[900 * BYTES_PER_MS :]
            retained_ms = piece["end_ms"]
            for index, origin_ms in enumerate((0, 900)):
                task = server.tasks[index]
                draft = {"sentence_id": 2, "text": "pending", "begin_time": 950 - origin_ms}
                server.send(task, server.result(task["id"], draft))
                preview = socket.receive_json()
                assert preview["final"] is False and preview["start_ms"] == 950
                server.disconnect(task)
                retraction = socket.receive_json()
                assert retraction == {
                    "type": "transcript.piece",
                    "id": preview["id"],
                    "start_ms": 300,
                    "end_ms": 300,
                    "text": "",
                    "final": True,
                    "sentences": [],
                }
                # A recording client discards PCM through the end of each published final.
                retained_ms = max(retained_ms, retraction["end_ms"])
                wait_for(
                    lambda next_index=index + 1: (
                        len(server.tasks) == next_index + 1
                        and server.tasks[next_index]["pcm"] == upstream_replay
                    )
                )

        retained = pcm[retained_ms * BYTES_PER_MS :]
        with client.websocket_connect(V2.format(offset=retained_ms), headers=SIDECAR) as socket:
            assert socket.receive_json()["live"] == "stream"
            socket.send_json(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(retained).decode(),
                    "offset_ms": retained_ms,
                }
            )
            socket.send_json({"type": "input_audio_buffer.commit"})
            assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 1_200}
    assert server.tasks[3]["pcm"] == pcm[300 * BYTES_PER_MS :]


def test_a_task_starting_between_milliseconds_replays_without_a_gap(tmp_path: Path, fake) -> None:
    server = fake(draft_at=None, finals=False)
    first = b"\x15\x00" * 341  # 682 bytes, or 21.3125 ms
    second = b"\x12\x00\x34\x00" * 3_200
    with (
        make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client,
        client.websocket_connect(V2.format(offset=0), headers=SIDECAR) as socket,
    ):
        assert socket.receive_json()["live"] == "stream"
        socket.send_json(
            {"type": "input_audio_buffer.append", "audio": base64.b64encode(first).decode()}
        )
        socket.send_json({"type": "input_audio_buffer.commit"})
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 21}

        socket.send_json(
            {"type": "input_audio_buffer.append", "audio": base64.b64encode(second).decode()}
        )
        wait_for(lambda: len(server.tasks) == 2 and server.tasks[1]["pcm"] == second)
        server.disconnect(server.tasks[1])
        wait_for(lambda: len(server.tasks) == 3 and server.tasks[2]["pcm"] == second)
        socket.send_json({"type": "input_audio_buffer.commit"})
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 421}


@pytest.mark.parametrize("first_samples", [336, 341])
def test_final_on_fractional_origin_requires_finish_confirmation(
    tmp_path: Path, fake, first_samples: int
) -> None:
    server = fake(draft_at=None, finals=False)
    first = b"\x15\x00" * first_samples
    second = b"\x22\x00" * 1_600  # exactly 100 ms (3,200 bytes)
    with (
        make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client,
        client.websocket_connect(V2.format(offset=0), headers=SIDECAR) as socket,
    ):
        assert socket.receive_json()["live"] == "stream"
        socket.send_json(
            {"type": "input_audio_buffer.append", "audio": base64.b64encode(first).decode()}
        )
        socket.send_json({"type": "input_audio_buffer.commit"})
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 21}

        socket.send_json(
            {"type": "input_audio_buffer.append", "audio": base64.b64encode(second).decode()}
        )
        wait_for(lambda: len(server.tasks) == 2 and server.tasks[1]["pcm"] == second)
        final = {"sentence_id": 1, "text": "done", "sentence_end": True, "end_time": 100}
        server.send(server.tasks[1], server.result(server.tasks[1]["id"], final))
        piece = socket.receive_json()
        assert piece["final"] and piece["text"] == "done"

        server.never_finish = True
        socket.send_json({"type": "input_audio_buffer.commit"})
        wait_for(lambda: server.tasks[1]["finished"])
        server.disconnect(server.tasks[1])
        error = socket.receive_json()
        assert error["type"] == "service.error" and error["code"] == "finish_unconfirmed"
    assert len(server.tasks) == 2


def test_draft_retraction_after_queue_eviction_stays_at_last_acknowledged_boundary(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "RETAIN_SECONDS", 0.2)
    server = fake(draft_at=None, finals=False, never_finish=True)
    with (
        make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client,
        client.websocket_connect(
            "/api/recordings/live?protocol=2&sample_rate=24000&offset_ms=0", headers=SIDECAR
        ) as socket,
    ):
        assert socket.receive_json()["live"] == "stream"
        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(100, rate=24_000)})
        socket.send_json({"type": "input_audio_buffer.commit"})
        wait_for(lambda: server.tasks[0]["finished"])

        # Overflow the 200 ms retain window while task 1's finish reply is held.
        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(350, rate=24_000)})
        gap = socket.receive_json()
        assert gap["type"] == "transcript.gap" and gap["reason"] == "connection"
        assert gap["end_ms"] > 100

        server.end(server.tasks[0])
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 100}

        wait_for(lambda: len(server.tasks) == 2 and server.tasks[1]["frames"] > 0)
        server.send(
            server.tasks[1],
            server.result(
                server.tasks[1]["id"],
                {"sentence_id": 7, "text": "Post-gap draft", "begin_time": 0},
            ),
        )
        draft = socket.receive_json()
        assert draft["type"] == "transcript.piece" and not draft["final"]
        server.disconnect(server.tasks[1])

        retracted = socket.receive_json()
        assert retracted["type"] == "transcript.piece"
        assert retracted["id"] == draft["id"]
        assert retracted["final"] is True and retracted["text"] == ""
        assert retracted["start_ms"] == 100 and retracted["end_ms"] == 100


@pytest.mark.parametrize("next_event", ["audio", "commit"])
def test_upstream_loss_after_full_coverage_stays_idle_until_pcm_or_commit(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch, next_event: str
) -> None:
    server = fake()
    idle = threading.Event()
    drain = realtime._LiveStream._drain

    async def observe_idle(self):
        await drain(self)
        if self.task is None:
            idle.set()

    monkeypatch.setattr(realtime._LiveStream, "_drain", observe_idle)
    with (
        make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client,
        client.websocket_connect(V2.format(offset=0), headers=SIDECAR) as socket,
    ):
        assert socket.receive_json()["live"] == "stream"
        (task,) = server.tasks
        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(100)})
        wait_for(lambda: task["bytes"] == FRAME)
        final = {"sentence_id": 1, "text": "first", "sentence_end": True, "end_time": 100}
        server.send(task, server.result(task["id"], final))
        piece = socket.receive_json()
        assert piece["final"] and piece["end_ms"] == 100
        server.disconnect(task)
        assert idle.wait(3)
        assert len(server.tasks) == 1 and not task["finished"]

        if next_event == "audio":
            socket.send_json({"type": "input_audio_buffer.append", "audio": audio(400)})
            assert socket.receive_json()["final"] is False
            resumed = socket.receive_json()
            assert resumed["final"] and resumed["start_ms"] == 100
            socket.send_json({"type": "input_audio_buffer.commit"})
            assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 500}
        else:
            socket.send_json({"type": "input_audio_buffer.commit"})
            event = socket.receive_json()
            assert event["type"] == "service.error" and event["code"] == "finish_unconfirmed"
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()


def test_a_stalled_live_socket_is_dropped_and_the_task_starts_again_on_a_new_one(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Qwen stops taking audio on the first socket (a starved machine or a stuck link): the
    # send times out, that socket is dropped, and the task runs again on a new one.
    monkeypatch.setattr(realtime, "SEND_TIMEOUT", 0.3)
    original = realtime.connect
    opened = {"count": 0}

    async def stalling_connect(*args: Any, **kwargs: Any):
        socket = await original(*args, **kwargs)
        opened["count"] += 1
        if opened["count"] == 1:
            real_send = socket.send
            frames = {"count": 0}

            async def send(message: Any, *rest: Any, **options: Any) -> None:
                if isinstance(message, bytes):
                    frames["count"] += 1
                    if frames["count"] >= 3:
                        await asyncio.sleep(3600)
                await real_send(message, *rest, **options)

            socket.send = send  # type: ignore[method-assign]
        return socket

    monkeypatch.setattr(realtime, "connect", stalling_connect)
    server = fake()
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash")
    with (
        app as client,
        client.websocket_connect(V2.format(offset=60_000), headers=SIDECAR) as socket,
    ):
        socket.receive_json()
        socket.send_json(
            {"type": "input_audio_buffer.append", "audio": audio(600), "offset_ms": 60_000}
        )
        final = None
        for _ in range(10):
            event = socket.receive_json()
            assert event["type"] != "transcript.gap"
            if event.get("final") and event["text"]:
                final = event
                break
    assert final is not None and final["sentences"][0]["start_ms"] == 60_000
    assert opened["count"] == 2


def test_audio_queued_past_the_limit_is_reported_as_a_gap(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "RETAIN_SECONDS", 0.3)
    server = fake()
    with (
        make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client,
        client.websocket_connect(V2.format(offset=0), headers=SIDECAR) as socket,
    ):
        socket.receive_json()
        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(600), "offset_ms": 0})
        gap = socket.receive_json()
    assert gap == {"type": "transcript.gap", "start_ms": 0, "end_ms": 300, "reason": "connection"}


def test_audio_dropped_from_the_kept_copy_is_a_gap_when_a_task_has_to_start_again(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "RETAIN_SECONDS", 0.3)
    server = fake(drop_at=7, finals=False)
    with (
        make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client,
        client.websocket_connect(V2.format(offset=0), headers=SIDECAR) as socket,
    ):
        socket.receive_json()
        for start in (0, 300):
            socket.send_json(
                {"type": "input_audio_buffer.append", "audio": audio(300), "offset_ms": start}
            )
            wait_for(lambda s=start: sum(t["frames"] for t in server.real_tasks()) >= s // 100 + 3)
        socket.send_json(
            {"type": "input_audio_buffer.append", "audio": audio(100), "offset_ms": 600}
        )
        events = [socket.receive_json() for _ in range(3)]
    # The first 300 ms had been trimmed from the kept copy when the connection dropped.
    gaps = [event for event in events if event["type"] == "transcript.gap"]
    assert gaps == [
        {"type": "transcript.gap", "start_ms": 0, "end_ms": 300, "reason": "connection"}
    ]


def test_run_task_carries_the_title_and_what_was_said_before_each_within_400_chars(
    tmp_path: Path, fake
) -> None:
    server = fake()
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash")
    title = "x" * 500
    with (
        app as client,
        client.websocket_connect(V2.format(offset=0) + f"&context={title}", headers=SIDECAR) as s,
    ):
        assert s.receive_json()["live"] == "stream"
        assert len(server.tasks) == 1 and not server.tasks[0]["finished"]
        s.send_json({"type": "input_audio_buffer.append", "audio": audio(600), "offset_ms": 0})
        s.receive_json()
        s.receive_json()
        s.send_json({"type": "input_audio_buffer.commit"})
        wait_for(lambda: server.real_tasks()[0]["finished"])
        s.send_json({"type": "input_audio_buffer.append", "audio": audio(100), "offset_ms": 700})
        wait_for(lambda: len(server.real_tasks()) == 2)
    first, second = (task["payload"]["input"]["context"] for task in server.real_tasks())
    texts = [[turn["content"][0]["text"] for turn in turns] for turns in (first, second)]
    assert texts[0] == ["x" * 400]
    assert texts[1] == ["x" * 400, "We'll use it."]


@pytest.mark.parametrize("mode", ["refuse", "silent", "connect-error", "connect-timeout"])
def test_live_start_failure_falls_back_once_and_keeps_early_frames_in_order(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(realtime, "TASK_START_TIMEOUT", 0.3)
    uploads: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        uploads.append(json.loads(request.content))
        return httpx.Response(200, json={"output": {"text": "piece"}})

    server = fake(mode=mode, hold_start=True)
    cancelled = threading.Event()
    if mode == "connect-error":
        allow_failure = threading.Event()

        async def refused_connection(*args, **kwargs):
            assert await asyncio.to_thread(allow_failure.wait, 3)
            raise OSError("connection refused")

        monkeypatch.setattr(realtime, "connect", refused_connection)
    elif mode == "connect-timeout":
        monkeypatch.setattr(realtime, "LIVE_SETUP_TIMEOUT", 0.3)

        async def stalled_connection(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(realtime, "connect", stalled_connection)
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash")
    monkeypatch.setattr("gunther.main.AsrHttpTransport", lambda: httpx.MockTransport(answer))
    first, second = b"\x01\x00" * 16_000, b"\x02\x00" * 16_000
    with (
        app as client,
        client.websocket_connect(V2.format(offset=1_000), headers=SIDECAR) as socket,
    ):
        started = time.monotonic()
        for pcm, start in ((first, 1_000), (second, 2_000)):
            socket.send_json(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm).decode(),
                    "offset_ms": start,
                }
            )
            socket.send_json({"type": "input_audio_buffer.commit"})
        if mode == "connect-error":
            allow_failure.set()
        elif mode == "connect-timeout":
            assert cancelled.wait(2)
        else:
            server.loop.call_soon_threadsafe(server.start_gate.set)
        ready = socket.receive_json()
        assert ready["type"] == "service.ready" and ready["live"] == "segments-fixed"
        if mode == "connect-timeout":
            assert time.monotonic() - started < 2
        for start in (1_000, 2_000):
            event = socket.receive_json()
            assert event["type"] == "transcript.piece" and event["text"] == "piece"
            assert event["start_ms"] == start
            assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": start + 1000}
    assert len(server.tasks) == (0 if mode.startswith("connect-") else 1)
    for request, pcm in zip(uploads, (first, second), strict=True):
        assert request["model"] == "qwen-audio-3.1-asr-flash"
        data = request["input"]["messages"][-1]["content"][0]["input_audio"]["data"]
        with wave.open(BytesIO(base64.b64decode(data.split(",", 1)[1]))) as wav:
            assert wav.readframes(wav.getnframes()) == pcm


def test_a_saved_streaming_id_uploads_without_the_suffix(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploads: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        uploads.append(json.loads(request.content))
        return httpx.Response(200, json={"output": {"text": "hello"}})

    server = fake()
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash-streaming")
    monkeypatch.setattr("gunther.main.AsrHttpTransport", lambda: httpx.MockTransport(answer))
    with app as client:
        with client.websocket_connect(
            "/api/recordings/live?purpose=dictation", headers=SIDECAR
        ) as socket:
            assert socket.receive_json()["stream"] is False
            socket.send_json({"type": "input_audio_buffer.append", "audio": audio(500, 24_000)})
            socket.send_json({"type": "input_audio_buffer.commit"})
            assert socket.receive_json()["text"] == "hello"
        # Recording still goes live, on the same id.
        with client.websocket_connect("/api/recordings/live", headers=SIDECAR) as socket:
            assert socket.receive_json()["live"] == "stream"
    assert [upload["model"] for upload in uploads] == ["qwen-audio-3.1-asr-flash"]
    assert server.tasks[0]["payload"]["model"] == "qwen-audio-3.1-asr-flash-streaming"


def test_the_two_ids_of_a_qwen_model_are_one_choice() -> None:
    assert qwen_live_id("qwen-audio-3.1-asr-flash") == "qwen-audio-3.1-asr-flash-streaming"
    assert (
        qwen_live_id("qwen-audio-3.1-asr-flash-streaming") == "qwen-audio-3.1-asr-flash-streaming"
    )
    assert qwen_upload_id("qwen-audio-3.1-asr-flash-streaming") == "qwen-audio-3.1-asr-flash"
    assert qwen_upload_id("qwen-audio-3.1-asr-flash") == "qwen-audio-3.1-asr-flash"
    assert is_duplex_stream("qwen", "qwen-audio-3.1-asr-flash-streaming")
    assert not is_duplex_stream("qwen", "qwen-audio-3.1-asr-flash")
    assert not is_duplex_stream("compatible", "whisper-streaming")
    # A realtime Fun-ASR id is already a live id and is left alone.
    assert qwen_live_id("fun-asr-realtime") == "fun-asr-realtime"


def test_test_connection_checks_both_halves_of_the_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gunther.service_settings import CheckResult

    async def listed(url, key, name, *, key_optional=False):
        return CheckResult(True, "Connected, and the key works."), ["qwen-plus"]

    outcomes = {"upload": None, "live": None}

    async def upload(client, base_url, api_key, model):
        return outcomes["upload"]

    async def live(base_url, api_key, model):
        outcomes["asked_live"] = model
        return outcomes["live"]

    monkeypatch.setattr(speech_api, "list_models", listed)
    monkeypatch.setattr(speech_api, "try_qwen_model", upload)
    monkeypatch.setattr(speech_api, "try_qwen_live", live)
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        auth_token=SIDECAR_TOKEN,
        service_settings_file=tmp_path / "service-settings.json",
    )
    with TestClient(create_app(settings)) as client:
        client.post(
            "/api/settings/speech/providers", headers=SIDECAR, json={"kind": "qwen", "apiKey": KEY}
        )

        def warning() -> str | None:
            return client.post(
                "/api/settings/speech/providers/qwen/test", headers=SIDECAR, json={}
            ).json()["warning"]

        assert warning() is None  # both halves work
        outcomes["live"] = "no answer to run-task within 10 seconds"
        note = warning()  # only the upload half works
        assert note == (
            "qwen-audio-3.1-asr-flash: live text isn't available for this model; "
            "recording will send short pieces instead."
        )
        outcomes["upload"] = "qwen-audio-3.1-asr-flash: Qwen has no model with this id."
        outcomes.pop("asked_live")
        assert warning() == "qwen-audio-3.1-asr-flash: Qwen has no model with this id."
        assert "asked_live" not in outcomes  # nothing to say about live when upload fails


def test_the_live_check_reports_a_refusal_and_a_silence_plainly(
    fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "TASK_START_TIMEOUT", 0.3)

    async def check(server: FakeQwen) -> str | None:
        return await realtime.try_qwen_live(server.url, KEY, "qwen-audio-3.1-asr-flash")

    server = fake()
    assert asyncio.run(check(server)) is None
    (task,) = server.tasks
    assert task["finished"] and task["ended"] and task["bytes"] == 0
    refused = asyncio.run(check(fake(mode="refuse")))
    assert refused and "url error" in refused
    silent = asyncio.run(check(fake(mode="silent")))
    assert silent and "no answer" in silent


def test_silence_answered_with_an_empty_400_is_empty_text_but_other_400s_are_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def response(status: int, body: str) -> httpx.Response:
        return httpx.Response(status, content=body.encode())

    assert transcription.qwen_heard_nothing(response(400, "{}"))
    assert transcription.qwen_heard_nothing(
        response(400, '{"message": "ASR_RESPONSE_HAVE_NO_WORDS"}')
    )
    assert not transcription.qwen_heard_nothing(response(400, '{"message": "boom"}'))
    assert not transcription.qwen_heard_nothing(response(500, "{}"))

    body = {"text": "{}"}

    def answer(request: httpx.Request) -> httpx.Response:
        return response(400, body["text"])

    monkeypatch.setattr("gunther.main.AsrHttpTransport", lambda: httpx.MockTransport(answer))

    async def probe() -> str | None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
            return await transcription.try_qwen_model(client, "https://qwen.example", KEY, "m")

    assert asyncio.run(probe()) is None
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        auth_token=SIDECAR_TOKEN,
        stt_provider="qwen",
        qwen_stt_api_key=KEY,
        qwen_stt_model="qwen-audio-3.0-asr-flash",
        service_settings_file=tmp_path / "service-settings.json",
    )
    with TestClient(create_app(settings)) as client:
        roles = {"recording": {"model": "qwen/qwen-audio-3.0-asr-flash", "stream": False}}
        client.put("/api/settings/speech/roles", headers=SIDECAR, json={"roles": roles})
        silence = audio(4_000, 24_000)
        with client.websocket_connect("/api/recordings/live", headers=SIDECAR) as socket:
            socket.receive_json()
            socket.send_json({"type": "input_audio_buffer.append", "audio": silence})
            socket.send_json({"type": "input_audio_buffer.commit"})
            # An empty result emits no piece. A provider error is still reported.
            assert socket.receive_json()["type"] == "transcript.flushed"
            body["text"] = '{"message": "boom"}'
            socket.send_json({"type": "input_audio_buffer.append", "audio": silence})
            socket.send_json({"type": "input_audio_buffer.commit"})
            event = socket.receive_json()
    assert event["type"] == "service.error" and event["code"] == "segment_failed"


def test_resampling_24k_to_16k_keeps_the_tone_and_two_thirds_of_the_samples() -> None:
    seconds = 1
    t = np.arange(24_000 * seconds) / 24_000
    tone = (np.sin(2 * np.pi * 440 * t) * 10_000).astype("<i2")
    out = np.frombuffer(resample_pcm16(tone.tobytes(), 24_000, 16_000), dtype="<i2")
    assert abs(len(out) - 16_000) <= 32
    middle = out[1_000:-1_000].astype(float)
    assert 6_500 < np.abs(middle).max() / 1.0 < 10_500
    freq = np.argmax(np.abs(np.fft.rfft(middle))) * 16_000 / len(middle)
    assert abs(freq - 440) < 5
    # Fed in pieces (even with an odd byte count) it gives the same samples.
    resampler = Pcm16Resampler(24_000, 16_000)
    raw = tone.tobytes()
    pieces = b"".join(resampler.feed(raw[i : i + 1_001]) for i in range(0, len(raw), 1_001))
    assert len(pieces) == len(out) * 2
    assert np.abs(np.frombuffer(pieces, dtype="<i2")[:100] - out[:100]).max() <= 1


def test_a_high_tone_is_removed_before_downsampling_not_folded_back() -> None:
    t = np.arange(24_000) / 24_000
    tone = (np.sin(2 * np.pi * 11_000 * t) * 10_000).astype("<i2")  # above 8 kHz
    out = np.frombuffer(resample_pcm16(tone.tobytes(), 24_000, 16_000), dtype="<i2")
    assert np.abs(out[500:-500]).max() < 1_500


def test_sentences_come_from_word_times_and_ending_marks() -> None:
    words = [
        {"text": "梯度", "punctuation": "", "begin_time": 0, "end_time": 400},
        {"text": "下降。", "punctuation": "", "begin_time": 400, "end_time": 900},
        {"text": "Then", "punctuation": "", "begin_time": 1_500, "end_time": 1_700},
        {"text": " why", "punctuation": "?", "begin_time": 1_700, "end_time": 2_000},
        {"text": " so", "punctuation": "", "begin_time": 2_100, "end_time": 2_200},
        {"text": "", "punctuation": "", "begin_time": 2_200, "end_time": 2_300},
        {"text": "skipped", "punctuation": "", "begin_time": None, "end_time": None},
    ]
    assert split_sentences(words, offset_ms=10_000) == [
        {"start_ms": 10_000, "end_ms": 10_900, "text": "梯度下降。"},
        {"start_ms": 11_500, "end_ms": 12_000, "text": "Then why?"},
        {"start_ms": 12_100, "end_ms": 12_300, "text": "so"},
    ]
    assert split_sentences([{"text": "", "punctuation": "", "begin_time": 0}]) == []


def test_context_turns_keep_the_first_terms_and_the_newest_words() -> None:
    turns = context_turns("a" * 500, "b" * 100 + "c" * 400)
    assert [turn["content"][0]["text"] for turn in turns] == ["a" * 400, "c" * 400]
    assert context_turns("", "  ") == []


def test_the_live_address_is_on_the_same_host_as_the_saved_one() -> None:
    assert (
        stream_url("https://maas.qianwenaiapi.com/compatible-mode/v1")
        == "wss://maas.qianwenaiapi.com/api-ws/v1/inference"
    )
    assert stream_url("http://127.0.0.1:9") == "ws://127.0.0.1:9/api-ws/v1/inference"
    assert BYTES_PER_MS == 32


def test_the_live_socket_goes_direct_even_with_a_socks_proxy_in_the_system(monkeypatch):
    """A VPN app's SOCKS proxy made websockets ask for python-socks and fail the request."""

    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "socks5://127.0.0.1:9")
    reason = asyncio.run(
        realtime.try_qwen_live("http://127.0.0.1:9", "sk-test", "qwen-audio-3.1-asr-flash")
    )
    assert reason is not None and reason.startswith("could not connect")


def test_an_unsupported_socks_proxy_only_fails_at_the_http_asr_request(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("all_proxy", "http_proxy", "https_proxy", "no_proxy"):
        monkeypatch.delenv(variable, raising=False)
        monkeypatch.delenv(variable.upper(), raising=False)
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:9")
    monkeypatch.setitem(sys.modules, "socksio", None)
    server = fake()
    with make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client:
        assert client.get("/api/health", headers=SIDECAR).is_success
        with client.websocket_connect(V2.format(offset=0), headers=SIDECAR) as socket:
            assert socket.receive_json()["live"] == "stream"
            socket.send_json({"type": "input_audio_buffer.append", "audio": audio(400)})
            assert socket.receive_json()["final"] is False
            assert socket.receive_json()["final"] is True
            socket.send_json({"type": "input_audio_buffer.commit"})
            assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 400}

        dictation = V2.format(offset=0) + "&purpose=dictation"
        with client.websocket_connect(dictation, headers=SIDECAR) as socket:
            assert socket.receive_json()["stream"] is False
            socket.send_json({"type": "input_audio_buffer.append", "audio": audio(100)})
            socket.send_json({"type": "input_audio_buffer.commit"})
            error = socket.receive_json()
            assert error["type"] == "service.error" and error["code"] == "segment_failed"
            assert "configured HTTP proxy" in error["message"]

        roles = {"dictation": {"model": "sensevoice/sensevoice-small", "stream": True}}
        saved = client.put("/api/settings/speech/roles", headers=SIDECAR, json={"roles": roles})
        assert saved.is_success
        with client.websocket_connect(dictation, headers=SIDECAR) as socket:
            error = socket.receive_json()
            assert error["type"] == "service.error" and error["code"] == "provider_unavailable"
            assert "configured HTTP proxy" in error["message"]

        assert client.get("/api/health", headers=SIDECAR).is_success
