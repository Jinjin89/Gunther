"""Live text cut at pauses for SenseVoice and OpenAI-style servers.

The pause detector is replaced by a loudness stand-in (a tone is speech, zeros are a
pause), so nothing here needs the model file or the network; the provider is a mock
transport.
"""

import asyncio
import logging
import struct
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from gunther import realtime
from gunther.main import create_app
from gunther.speech_registry import Choice
from gunther.transcription import transcribe_segment
from tests.test_listen_again import (
    SIDECAR,
    pcm_b64,
    settings_for,
    start_recording,
    tone,
    with_provider,
)


class Loudness:
    """Speech probability from how loud the frame is."""

    def __call__(self, frame: bytes) -> float:
        loud = sum(abs(v) for v in struct.unpack(f"<{len(frame) // 2}h", frame)) / (len(frame) // 2)
        return 0.9 if loud > 500 else 0.05

    def reset(self) -> None:
        pass


def silence(milliseconds: int) -> bytes:
    return bytes(32 * milliseconds)


@pytest.fixture
def pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(realtime.vad, "new_detector", lambda path=None: Loudness())


class Provider:
    """A transcription server that numbers its answers and keeps what it was sent."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, slow_first: float = 0.0) -> None:
        self.requests: list[httpx.Request] = []
        self.slow_first = slow_first

        async def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/health"):
                return httpx.Response(200, json={"ok": True})
            if path.endswith("/reset"):
                return httpx.Response(200, json={})
            self.requests.append(request)
            if len(self.requests) == 1 and self.slow_first:
                await asyncio.sleep(self.slow_first)
            return httpx.Response(200, json={"text": f"text {len(self.requests)}"})

        real = httpx.AsyncClient
        monkeypatch.setattr(
            realtime.httpx,
            "AsyncClient",
            lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
        )

    def uploads(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/audio/transcriptions")]

    @staticmethod
    def wav_rate(request: httpx.Request) -> int:
        body = request.read()
        at = body.find(b"RIFF")
        return struct.unpack("<I", body[at + 24 : at + 28])[0]


def use(client: TestClient, kind: str) -> None:
    if kind == "sensevoice":
        roles = {
            "recording": {"model": "sensevoice/sensevoice-small", "stream": True},
            "dictation": {"model": "sensevoice/sensevoice-small", "stream": False},
        }
        saved = client.put("/api/settings/speech/roles", headers=SIDECAR, json={"roles": roles})
        assert saved.status_code == 200
    else:
        with_provider(client)


def talk(socket: Any, *parts: bytes, start_ms: int = 0, step_ms: int = 100) -> int:
    """Send audio in 100 ms appends stamped on the recording clock; returns the end time."""

    audio = b"".join(parts)
    size = 32 * step_ms
    for at in range(0, len(audio), size):
        socket.send_json(
            {
                "type": "input_audio_buffer.append",
                "audio": pcm_b64(audio[at : at + size]),
                "offset_ms": start_ms + at // 32,
            }
        )
    return start_ms + len(audio) // 32


def until(socket: Any, kind: str) -> list[dict[str, Any]]:
    seen = []
    while True:
        event = socket.receive_json()
        seen.append(event)
        if event["type"] == kind:
            return seen


def url(offset: int = 0, rate: int = 16_000, extra: str = "") -> str:
    return f"/api/recordings/live?sample_rate={rate}&protocol=2&offset_ms={offset}{extra}"


def test_v2_finals_are_cut_at_pauses_on_the_recording_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pauses: None
) -> None:
    provider = Provider(monkeypatch)
    with TestClient(create_app(settings_for(tmp_path))) as client:
        use(client, "compatible")
        with client.websocket_connect(url(10_000), headers=SIDECAR) as socket:
            assert socket.receive_json()["type"] == "service.ready"
            end = talk(
                socket,
                silence(1000),
                tone(2000),
                silence(1000),
                tone(1500),
                silence(800),
                start_ms=10_000,
            )
            socket.send_json({"type": "input_audio_buffer.commit"})
            events = until(socket, "transcript.flushed")
    pieces = [e for e in events if e["type"] == "transcript.piece"]
    assert [p["final"] for p in pieces] == [True, True]
    first, second = pieces
    # The leading second of silence is never sent; the piece starts 200 ms before speech.
    assert abs(first["start_ms"] - 10_800) <= 40 and abs(first["end_ms"] - 13_200) <= 40
    assert abs(second["start_ms"] - 13_800) <= 40 and abs(second["end_ms"] - 15_700) <= 40
    assert first["sentences"] == [
        {"start_ms": first["start_ms"], "end_ms": first["end_ms"], "text": "text 1"}
    ]
    assert first["id"] != second["id"]
    assert events[-1]["type"] == "transcript.flushed" and events[-1]["until_ms"] == end
    assert len(provider.uploads()) == 2


def test_the_commit_sends_what_is_still_being_heard_before_flushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pauses: None
) -> None:
    Provider(monkeypatch)
    with TestClient(create_app(settings_for(tmp_path))) as client:
        use(client, "compatible")
        with client.websocket_connect(url(), headers=SIDECAR) as socket:
            socket.receive_json()
            talk(socket, tone(2000))
            socket.send_json({"type": "input_audio_buffer.commit"})
            events = until(socket, "transcript.flushed")
    assert [e["type"] for e in events] == ["transcript.piece", "transcript.flushed"]
    assert events[0]["start_ms"] == 0 and events[0]["end_ms"] == 2000


def test_final_piece_keeps_its_position_after_leading_silence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pauses: None
) -> None:
    Provider(monkeypatch)
    with TestClient(create_app(settings_for(tmp_path))) as client:
        use(client, "compatible")
        with client.websocket_connect(url(), headers=SIDECAR) as socket:
            socket.receive_json()
            talk(socket, silence(500), tone(2000), silence(800))
            event = socket.receive_json()
    assert event["type"] == "transcript.piece" and event["final"] is True
    assert event["text"] == "text 1"
    assert abs(event["start_ms"] - 300) < 50


def test_audio_goes_up_as_16_khz_whatever_the_client_sends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pauses: None, mobile_live_v2: dict[str, Any]
) -> None:
    provider = Provider(monkeypatch)
    rate = mobile_live_v2["sample_rate"]
    start = mobile_live_v2["chunks"][0]["offset_ms"]
    duration = 2000
    with TestClient(create_app(settings_for(tmp_path))) as client:
        use(client, "compatible")
        with client.websocket_connect(url(offset=start, rate=rate), headers=SIDECAR) as socket:
            assert socket.receive_json()["protocol"] == 2
            samples = struct.pack("<h", 1000) * (rate * duration // 1000)
            socket.send_json(
                {"type": "input_audio_buffer.append", "audio": pcm_b64(samples), "offset_ms": start}
            )
            socket.send_json({"type": "input_audio_buffer.commit"})
            events = until(socket, "transcript.flushed")
    piece, flushed = events
    assert piece["type"] == "transcript.piece" and piece["final"] is True
    assert piece["start_ms"] == start
    # The resampling filter can keep a sub-millisecond tail.
    assert piece["end_ms"] in (start + duration - 1, start + duration)
    assert flushed == {"type": "transcript.flushed", "until_ms": start + duration}
    assert Provider.wav_rate(provider.uploads()[0]) == 16_000


def test_only_sensevoice_gets_drafts_in_segmented_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pauses: None
) -> None:
    provider = Provider(monkeypatch)
    with TestClient(create_app(settings_for(tmp_path))) as client:
        use(client, "sensevoice")
        with client.websocket_connect(url(), headers=SIDECAR) as socket:
            ready = socket.receive_json()
            assert ready["provider"] == "sensevoice"
            talk(socket, tone(2400))  # 2 s of the piece have come: a draft
            draft = socket.receive_json()
            assert draft["type"] == "transcript.piece" and draft["final"] is False
            assert draft["end_ms"] is None and draft["text"].startswith("text")
            assert "sentences" not in draft
            talk(socket, tone(600), silence(800), start_ms=2400)
            socket.send_json({"type": "input_audio_buffer.commit"})
            events = until(socket, "transcript.flushed")
        final = next(e for e in events if e["type"] == "transcript.piece" and e["final"])
        assert final["id"] == draft["id"] and final["start_ms"] == draft["start_ms"]
        assert all(e["final"] or e["id"] == draft["id"] for e in events if "final" in e)
        # SenseVoice cannot use what was said before: no prompt, drafts or finals.
        assert all(b'name="prompt"' not in r.read() for r in provider.uploads())

        use(client, "compatible")
        with client.websocket_connect(url(), headers=SIDECAR) as socket:
            socket.receive_json()
            talk(socket, tone(6000), silence(800))
            socket.send_json({"type": "input_audio_buffer.commit"})
            events = until(socket, "transcript.flushed")
        assert [e["final"] for e in events if e["type"] == "transcript.piece"] == [True]


def test_an_empty_answer_clears_a_draft_that_was_on_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pauses: None
) -> None:
    answers = iter(["something", ""])

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"ok": True})
        if request.url.path.endswith("/reset"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"text": next(answers)})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        realtime.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    with TestClient(create_app(settings_for(tmp_path))) as client:
        use(client, "sensevoice")
        with client.websocket_connect(url(), headers=SIDECAR) as socket:
            socket.receive_json()
            talk(socket, tone(2400))
            draft = socket.receive_json()
            talk(socket, tone(200), silence(800), start_ms=2400)
            final = socket.receive_json()
    assert draft["final"] is False and draft["text"] == "something"
    assert final["id"] == draft["id"] and final["final"] is True and final["sentences"] == []


def test_the_provider_gets_the_title_and_the_recent_text_as_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pauses: None
) -> None:
    provider = Provider(monkeypatch)
    with TestClient(create_app(settings_for(tmp_path))) as client:
        use(client, "compatible")
        with client.websocket_connect(
            url(extra="&context=Optimisation"), headers=SIDECAR
        ) as socket:
            socket.receive_json()
            talk(socket, tone(1500), silence(800), tone(1500), silence(800))
            socket.send_json({"type": "input_audio_buffer.commit"})
            until(socket, "transcript.flushed")
    first, second = (r.read() for r in provider.uploads())
    assert b"Optimisation" in first and b"text 1" not in first
    assert b"Optimisation" in second and b"text 1" in second


def test_qwen_upload_gets_the_context_as_messages_cut_from_the_front() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.read()))
        return httpx.Response(200, json={"output": {"text": "ok"}})

    choice = Choice(
        kind="qwen",
        provider="Qwen",
        base_url="https://qwen.example",
        api_key="key",
        model="qwen-audio-3.1-asr-flash",
        label="Qwen-Audio",
        stream=True,
        language="",
    )

    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            text = await transcribe_segment(
                client,
                choice,
                bytes(3200),
                title="A title",
                recent="old " * 300 + "the newest words",
            )
            assert text == "ok"

    asyncio.run(go())
    messages = seen[0]["input"]["messages"]
    texts = [m["content"][0].get("text") for m in messages[:-1]]
    assert texts[0] == "A title" and texts[1].endswith("the newest words") and len(texts[1]) <= 400
    assert seen[0]["parameters"]["sample_rate"] == "16000"


def test_pieces_are_written_one_at_a_time_in_order_even_when_the_first_is_slow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pauses: None
) -> None:
    Provider(monkeypatch, slow_first=0.4)
    with TestClient(create_app(settings_for(tmp_path))) as client:
        use(client, "compatible")
        with client.websocket_connect(url(), headers=SIDECAR) as socket:
            socket.receive_json()
            talk(socket, tone(1500), silence(800), tone(1500), silence(800))
            socket.send_json({"type": "input_audio_buffer.commit"})
            events = until(socket, "transcript.flushed")
    texts = [e["text"] for e in events if e["type"] == "transcript.piece"]
    assert texts == ["text 1", "text 2"]


def test_without_the_model_file_fixed_blocks_are_used_and_said_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    Provider(monkeypatch)
    monkeypatch.setattr(realtime, "_warned_no_pause_model", False)
    caplog.set_level(logging.WARNING, logger="gunther.realtime")
    with TestClient(create_app(settings_for(tmp_path))) as client:
        use(client, "compatible")
        for _ in range(2):
            with client.websocket_connect(url(), headers=SIDECAR) as socket:
                ready = socket.receive_json()
                assert ready["live"] == "segments-fixed"
                talk(socket, silence(3500))  # silence still goes up in a fixed block
                piece = socket.receive_json()
                assert piece["start_ms"] == 0 and piece["end_ms"] == 3200
    warnings = [r for r in caplog.records if "pause-detection" in r.getMessage()]
    assert len(warnings) == 1


def test_the_minute_check_is_off_for_sensevoice_and_on_for_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pauses: None
) -> None:
    Provider(monkeypatch)
    with TestClient(create_app(settings_for(tmp_path))) as client:
        recording_id = start_recording(client)
        extra = f"&recording_id={recording_id}"
        use(client, "sensevoice")
        with client.websocket_connect(url(extra=extra), headers=SIDECAR) as socket:
            assert socket.receive_json()["check_again"] is False
        use(client, "compatible")
        with client.websocket_connect(url(extra=extra), headers=SIDECAR) as socket:
            assert socket.receive_json()["check_again"] is True
