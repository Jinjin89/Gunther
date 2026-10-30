"""Live sentences from Qwen's streaming model, heard through a fake upstream socket.

The fake speaks the real flow (run-task, task-started, binary PCM, result-generated,
finish-task, task-finished) on localhost, so no test touches the network.
"""

import asyncio
import base64
import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve

from gunther import realtime, speech_api
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
        finals: bool = True,
        never_finish: bool = False,
    ) -> None:
        self.finals = finals
        self.never_finish = never_finish
        self.mode = mode
        self.drop_at = drop_at
        self.tasks: list[dict[str, Any]] = []
        self.connections = 0
        self.loop = asyncio.new_event_loop()
        self.port = 0
        ready = threading.Event()

        def serve_forever() -> None:
            asyncio.set_event_loop(self.loop)

            async def start():
                return await serve(self.handle, "127.0.0.1", 0)

            server = self.loop.run_until_complete(start())
            self.port = server.sockets[0].getsockname()[1]
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=serve_forever, daemon=True)
        self.thread.start()
        ready.wait(5)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def real_tasks(self) -> list[dict[str, Any]]:
        """Tasks that carried audio (the start-up check sends none)."""

        return [task for task in self.tasks if task["frames"] > 0]

    @staticmethod
    def result(task_id: str, sentence: dict[str, Any]) -> str:
        return json.dumps(
            {
                "header": {"event": "result-generated", "task_id": task_id},
                "payload": {"output": {"sentence": sentence}},
            }
        )

    async def handle(self, socket) -> None:
        self.connections += 1
        task: dict[str, Any] | None = None
        async for message in socket:
            if isinstance(message, bytes):
                assert task is not None
                task["frames"] += 1
                task["bytes"] += len(message)
                if task["frames"] == 2:
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
                                "text": "We'll use it.",
                                "begin_time": 0,
                                "end_time": 300,
                                "words": words,
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
                if self.mode == "refuse":
                    await socket.send(
                        json.dumps(
                            {
                                "header": {
                                    "event": "task-failed",
                                    "task_id": data["header"]["task_id"],
                                    "error_message": "url error, please check url",
                                }
                            }
                        )
                    )
                    continue
                if self.mode == "silent":
                    continue
                task = {
                    "id": data["header"]["task_id"],
                    "payload": data["payload"],
                    "frames": 0,
                    "bytes": 0,
                    "finished": False,
                }
                self.tasks.append(task)
                await socket.send(
                    json.dumps(
                        {"header": {"event": "task-started", "task_id": task["id"]}, "payload": {}}
                    )
                )
            elif action == "finish-task" and task is not None:
                task["finished"] = True
                if self.never_finish:
                    continue
                await socket.send(
                    json.dumps(
                        {"header": {"event": "task-finished", "task_id": task["id"]}, "payload": {}}
                    )
                )

    dropped = False

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)


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
        seed_demo=False,
        deepseek_api_key=None,
        auth_token=SIDECAR_TOKEN,
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


def test_a_commit_with_nothing_to_finish_is_flushed_at_once_and_v1_hears_nothing(
    tmp_path: Path, fake
) -> None:
    server = fake()
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash")
    with app as client:
        with client.websocket_connect(V2.format(offset=5_000), headers=SIDECAR) as socket:
            socket.receive_json()
            socket.send_json({"type": "input_audio_buffer.commit"})
            assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 5_000}
        with client.websocket_connect("/api/recordings/live", headers=SIDECAR) as socket:
            socket.receive_json()
            socket.send_json({"type": "input_audio_buffer.commit"})
            socket.send_json({"type": "input_audio_buffer.append", "audio": audio(600, 24_000)})
            socket.send_json({"type": "input_audio_buffer.commit"})
            # Only the final sentence comes back: v1 never sees ``transcript.flushed``.
            assert socket.receive_json()["type"] == (
                "conversation.item.input_audio_transcription.completed"
            )


def test_flushed_waits_for_the_task_to_end_even_when_it_has_to_be_given_up(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "TASK_END_TIMEOUT", 0.3)
    server = fake(finals=False, never_finish=True)
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash")
    with (
        app as client,
        client.websocket_connect(V2.format(offset=0), headers=SIDECAR) as socket,
    ):
        socket.receive_json()
        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(400), "offset_ms": 0})
        socket.receive_json()  # the draft after the 2nd frame
        started = time.monotonic()
        socket.send_json({"type": "input_audio_buffer.commit"})
        flushed = socket.receive_json()
        assert flushed == {"type": "transcript.flushed", "until_ms": 400}
        assert time.monotonic() - started >= 0.25


def test_the_fallback_sends_v2_clients_final_pieces_on_the_recording_clock(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "TASK_START_TIMEOUT", 0.3)
    texts = iter(["first piece", "second piece"])

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": {"text": next(texts)}})

    real = httpx.AsyncClient
    server = fake(mode="refuse")
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash")
    monkeypatch.setattr(
        realtime.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(answer), **kw),
    )
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
        # A draft from the task that dropped, then the new task's draft and final.
        seen = [socket.receive_json() for _ in range(3)]
    assert seen[2]["final"] is True and seen[2]["sentences"][0]["start_ms"] == 60_000
    assert all(event["type"] != "transcript.gap" for event in seen)
    assert server.connections == 2
    assert server.real_tasks()[1]["bytes"] == 6 * FRAME  # all of it was sent again


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
            if event.get("final"):
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


def test_v1_clients_get_one_completed_event_per_final_sentence(tmp_path: Path, fake) -> None:
    server = fake()
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash")
    with app as client, client.websocket_connect("/api/recordings/live", headers=SIDECAR) as socket:
        assert socket.receive_json()["type"] == "service.ready"
        # One second at the old 24 kHz.
        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(1_000, 24_000)})
        socket.send_json({"type": "input_audio_buffer.commit"})
        event = socket.receive_json()
        assert event == {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "qwen-1",
            "transcript": "We'll use it.",
            "provider": "qwen",
            "start_seconds": 0.0,
        }
        wait_for(lambda: server.real_tasks()[0]["finished"])
    # 24 kHz in, 16 kHz out: two thirds of the bytes, give or take one frame.
    assert abs(server.real_tasks()[0]["bytes"] - 32_000) <= FRAME


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
        s.receive_json()
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


@pytest.mark.parametrize("mode", ["refuse", "silent"])
def test_a_model_with_no_live_version_falls_back_to_uploaded_pieces(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(realtime, "TASK_START_TIMEOUT", 0.3)
    uploads: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        uploads.append(json.loads(request.content))
        return httpx.Response(200, json={"output": {"text": "piece"}})

    real = httpx.AsyncClient
    server = fake(mode=mode)
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash")
    monkeypatch.setattr(
        realtime.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(answer), **kw),
    )
    with app as client, client.websocket_connect("/api/recordings/live", headers=SIDECAR) as socket:
        ready = socket.receive_json()
        assert ready["type"] == "service.ready" and ready["live"] == "segments-fixed"
        socket.send_json({"type": "input_audio_buffer.append", "audio": audio(1_000, 24_000)})
        socket.send_json({"type": "input_audio_buffer.commit"})
        event = socket.receive_json()
    assert event["transcript"] == "piece"
    assert uploads[0]["model"] == "qwen-audio-3.1-asr-flash"


def test_a_saved_streaming_id_uploads_without_the_suffix(
    tmp_path: Path, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploads: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        uploads.append(json.loads(request.content))
        return httpx.Response(200, json={"output": {"text": "hello"}})

    real = httpx.AsyncClient
    server = fake()
    app = make_app(tmp_path, server, "qwen-audio-3.1-asr-flash-streaming")
    monkeypatch.setattr(
        realtime.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(answer), **kw),
    )
    with app as client:
        with client.websocket_connect(
            "/api/recordings/live?purpose=dictation", headers=SIDECAR
        ) as socket:
            assert socket.receive_json()["stream"] is False
            socket.send_json({"type": "input_audio_buffer.append", "audio": audio(500, 24_000)})
            socket.send_json({"type": "input_audio_buffer.commit"})
            assert socket.receive_json()["transcript"] == "hello"
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
        seed_demo=False,
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

    assert asyncio.run(check(fake())) is None
    refused = asyncio.run(check(fake(mode="refuse")))
    assert refused and "url error" in refused
    silent = asyncio.run(check(fake(mode="silent")))
    assert silent and "no answer" in silent


def test_silence_answered_with_an_empty_400_is_empty_text_but_other_400s_are_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def response(status: int, body: str) -> httpx.Response:
        return httpx.Response(status, content=body.encode())

    assert realtime.qwen_heard_nothing(response(400, "{}"))
    assert realtime.qwen_heard_nothing(response(400, '{"message": "ASR_RESPONSE_HAVE_NO_WORDS"}'))
    assert not realtime.qwen_heard_nothing(response(400, '{"message": "boom"}'))
    assert not realtime.qwen_heard_nothing(response(500, "{}"))

    body = {"text": "{}"}

    def answer(request: httpx.Request) -> httpx.Response:
        return response(400, body["text"])

    real = httpx.AsyncClient
    monkeypatch.setattr(
        realtime.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(answer), **kw),
    )

    async def probe() -> str | None:
        async with httpx.AsyncClient() as client:
            return await realtime.try_qwen_model(client, "https://qwen.example", KEY, "m")

    assert asyncio.run(probe()) is None
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
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
            # Empty text sends nothing; a real error is told to the client.
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
