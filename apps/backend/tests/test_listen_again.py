"""The audio copy a live recording keeps, listening to it again, and the one switch.

Nothing here touches the network: the upload model is a mock transport, and the
live model is the fake upstream from ``test_realtime_stream``.
"""

import base64
import hashlib
import os
import struct
import time
import wave
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from gunther import listen_copy, realtime
from gunther.config import Settings
from gunther.main import create_app
from gunther.qwen_stream import BYTES_PER_MS
from tests.test_realtime_stream import FakeQwen, fake, make_app  # noqa: F401

SIDECAR_TOKEN = "sidecar-token-for-tests"
SIDECAR = {"X-Gunther-Token": SIDECAR_TOKEN}
KEY = "sk-test-key-1234"


def tone(milliseconds: int, value: int = 1000) -> bytes:
    return struct.pack("<h", value) * (16 * milliseconds)


def pcm_b64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode()


# The copy itself -----------------------------------------------------------------


def test_chunks_land_at_their_offset_a_gap_stays_uncovered_and_a_resend_does_not_double(
    tmp_path: Path,
) -> None:
    copy = listen_copy.ListenCopy(tmp_path, "rec_x")
    copy.write(0, tone(1000, 1))
    copy.write(3000 * BYTES_PER_MS, tone(1000, 2))
    copy.write(0, tone(1000, 1))  # sent again after a reconnect
    copy.close()
    pcm, _ = listen_copy.copy_paths(tmp_path, "rec_x")
    assert pcm.stat().st_size == 4000 * BYTES_PER_MS
    assert listen_copy.covered(tmp_path, "rec_x") == [(0, 1000), (3000, 4000)]
    audio, held = listen_copy.read_range(tmp_path, "rec_x", 500, 3500)
    assert len(audio) == 3000 * BYTES_PER_MS
    assert audio[: 500 * BYTES_PER_MS] == tone(500, 1)
    # The hole is silence, not whatever the file holds there.
    assert audio[500 * BYTES_PER_MS : 2500 * BYTES_PER_MS] == bytes(2000 * BYTES_PER_MS)
    assert audio[2500 * BYTES_PER_MS :] == tone(500, 2)
    assert listen_copy.missing_in(held, 500, 3500) == [(1000, 3000)]
    assert listen_copy.touches(held, 1000, 3000) is False
    assert listen_copy.touches(held, 900, 3000) is True


def test_a_second_connection_keeps_what_the_first_wrote(tmp_path: Path) -> None:
    first = listen_copy.ListenCopy(tmp_path, "rec_x")
    first.write(0, tone(500, 7))
    first.close()
    second = listen_copy.ListenCopy(tmp_path, "rec_x")
    second.write(500 * BYTES_PER_MS, tone(500, 8))
    second.close()
    audio, held = listen_copy.read_range(tmp_path, "rec_x", 0, 1000)
    assert audio == tone(500, 7) + tone(500, 8)
    assert listen_copy.missing_in(held, 0, 1000) == []


def test_the_sweep_removes_old_copies_of_finished_or_deleted_recordings_only(
    tmp_path: Path,
) -> None:
    states = {"rec_done": "completed", "rec_live": "capturing", "rec_new": "completed"}
    for name in ("rec_done", "rec_live", "rec_new", "rec_gone"):
        copy = listen_copy.ListenCopy(tmp_path, name)
        copy.write(0, tone(100))
        copy.close()
    old = time.time() - 8 * 86_400
    for name in ("rec_done", "rec_live", "rec_gone"):
        for path in listen_copy.copy_paths(tmp_path, name):
            os.utime(path, (old, old))
    removed = listen_copy.sweep(tmp_path, status_of=states.get)
    left = {path.name for path in tmp_path.iterdir()}
    assert removed == 2
    assert left == {
        "rec_live.listen.pcm",
        "rec_live.listen.ranges",
        "rec_new.listen.pcm",
        "rec_new.listen.ranges",
    }


# The socket and the switch ----------------------------------------------------------


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
        deepseek_api_key=None,
        auth_token=SIDECAR_TOKEN,
        service_settings_file=tmp_path / "service-settings.json",
    )


def with_provider(client: TestClient, kind: str = "compatible", **job: Any) -> None:
    body = {
        "compatible": {
            "kind": "compatible",
            "apiKey": KEY,
            "baseUrl": "https://asr.example/v1",
            "models": ["whisper-1"],
        },
        "qwen": {
            "kind": "qwen",
            "apiKey": KEY,
            "baseUrl": "https://qwen.example",
            "models": ["qwen-audio-3.1-asr-flash"],
        },
    }[kind]
    assert (
        client.post("/api/settings/speech/providers", headers=SIDECAR, json=body).status_code == 201
    )
    ref = f"{kind}/{body['models'][0]}"
    roles = {
        "recording": {"model": ref, "stream": True, **job},
        "dictation": {"model": ref, "stream": False},
    }
    assert (
        client.put("/api/settings/speech/roles", headers=SIDECAR, json={"roles": roles}).status_code
        == 200
    )


def start_recording(client: TestClient) -> str:
    started = client.post(
        "/api/recordings/sessions?title=Lecture", headers={**SIDECAR, "Content-Type": "audio/webm"}
    )
    assert started.status_code == 201
    return started.json()["id"]


def answer_text(monkeypatch: pytest.MonkeyPatch, text: str = "hello") -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": text})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        realtime.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    return seen


def live_url(recording_id: str | None, rate: int = 16_000) -> str:
    extra = f"&recording_id={recording_id}" if recording_id else ""
    return f"/api/recordings/live?protocol=2&sample_rate={rate}&offset_ms=0{extra}"


def test_the_segmented_path_keeps_a_copy_at_the_right_offsets_and_says_it_can_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer_text(monkeypatch)
    with TestClient(create_app(settings_for(tmp_path))) as client:
        with_provider(client)
        recording_id = start_recording(client)
        with client.websocket_connect(live_url(recording_id), headers=SIDECAR) as socket:
            ready = socket.receive_json()
            assert ready["check_again"] is True and ready["protocol"] == 2
            for offset, value in ((0, 1), (4000, 2), (0, 1)):
                socket.send_json(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": pcm_b64(tone(1000, value)),
                        "offset_ms": offset,
                    }
                )
            socket.send_json({"type": "input_audio_buffer.commit"})
            while socket.receive_json()["type"] != "transcript.flushed":
                pass
        directory = tmp_path / "recordings"
        assert listen_copy.covered(directory, recording_id) == [(0, 1000), (4000, 5000)]
        audio, _ = listen_copy.read_range(directory, recording_id, 0, 5000)
        assert audio[:32_000] == tone(1000, 1) and audio[4000 * 32 :] == tone(1000, 2)
        assert audio[1000 * 32 : 4000 * 32] == bytes(3000 * 32)


def test_a_24_khz_client_is_kept_as_16_khz(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answer_text(monkeypatch)
    with TestClient(create_app(settings_for(tmp_path))) as client:
        with_provider(client)
        recording_id = start_recording(client)
        with client.websocket_connect(live_url(recording_id, 24_000), headers=SIDECAR) as socket:
            socket.receive_json()
            socket.send_json(
                {
                    "type": "input_audio_buffer.append",
                    "audio": pcm_b64(struct.pack("<h", 500) * 24_000),
                    "offset_ms": 0,
                }
            )
            socket.send_json({"type": "input_audio_buffer.commit"})
            while socket.receive_json()["type"] != "transcript.flushed":
                pass
        pcm = listen_copy.copy_paths(tmp_path / "recordings", recording_id)[0]
        assert abs(pcm.stat().st_size - 32_000) <= 128


def test_no_copy_and_no_checking_without_a_capturing_recording_or_with_the_switch_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer_text(monkeypatch)
    with TestClient(create_app(settings_for(tmp_path))) as client:
        with_provider(client)
        finished = start_recording(client)
        client.put(
            f"/api/recordings/{finished}/chunks?sequence=0",
            headers={
                **SIDECAR,
                "Content-Type": "audio/webm",
                "X-Chunk-SHA256": hashlib.sha256(b"audio").hexdigest(),
            },
            content=b"audio",
        )
        assert (
            client.post(f"/api/recordings/{finished}/complete", headers=SIDECAR).status_code == 200
        )
        for recording_id in (None, "rec_" + "0" * 24, finished):
            with client.websocket_connect(live_url(recording_id), headers=SIDECAR) as socket:
                assert socket.receive_json()["check_again"] is False
                socket.send_json(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": pcm_b64(tone(500)),
                        "offset_ms": 0,
                    }
                )
        assert not list((tmp_path / "recordings").glob("*.listen.*"))

        capturing = start_recording(client)
        with_provider_off = client.get("/api/settings/speech", headers=SIDECAR).json()
        assert next(r for r in with_provider_off["roles"] if r["id"] == "recording")["checkAgain"]
        roles = {
            "recording": {
                "model": "compatible/whisper-1",
                "stream": True,
                "language": "",
                "check_again": False,
            }
        }
        saved = client.put("/api/settings/speech/roles", headers=SIDECAR, json={"roles": roles})
        assert saved.status_code == 200
        recording_role = next(r for r in saved.json()["roles"] if r["id"] == "recording")
        assert recording_role["checkAgain"] is False
        with client.websocket_connect(live_url(capturing), headers=SIDECAR) as socket:
            assert socket.receive_json()["check_again"] is False
        # Changing the model through a save that leaves the switch out keeps it off.
        again = client.put(
            "/api/settings/speech/roles",
            headers=SIDECAR,
            json={"roles": {"recording": {"model": "compatible/whisper-1", "stream": True}}},
        )
        assert (
            next(r for r in again.json()["roles"] if r["id"] == "recording")["checkAgain"] is False
        )


def test_a_v1_client_gets_the_same_ready_as_before(tmp_path: Path, monkeypatch) -> None:
    answer_text(monkeypatch)
    with TestClient(create_app(settings_for(tmp_path))) as client:
        with_provider(client)
        with client.websocket_connect("/api/recordings/live", headers=SIDECAR) as socket:
            ready = socket.receive_json()
        assert "check_again" not in ready and "protocol" not in ready


def test_the_live_stream_keeps_a_copy_too(tmp_path: Path, fake) -> None:  # noqa: F811
    server = fake()
    with make_app(tmp_path, server, "qwen-audio-3.1-asr-flash") as client:
        recording_id = start_recording(client)
        with client.websocket_connect(live_url(recording_id), headers=SIDECAR) as socket:
            ready = socket.receive_json()
            assert ready["live"] == "stream" and ready["check_again"] is True
            socket.send_json(
                {
                    "type": "input_audio_buffer.append",
                    "audio": pcm_b64(tone(600, 3)),
                    "offset_ms": 0,
                }
            )
            socket.send_json(
                {
                    "type": "input_audio_buffer.append",
                    "audio": pcm_b64(tone(600, 4)),
                    "offset_ms": 5000,
                }
            )
            socket.send_json({"type": "input_audio_buffer.commit"})
            while socket.receive_json()["type"] != "transcript.flushed":
                pass
        directory = tmp_path / "recordings"
        assert listen_copy.covered(directory, recording_id) == [(0, 600), (5000, 5600)]
        audio, _ = listen_copy.read_range(directory, recording_id, 5000, 5600)
        assert audio == tone(600, 4)


# Listening again --------------------------------------------------------------------


def seed_copy(tmp_path: Path, recording_id: str, held: list[tuple[int, int]]) -> None:
    """A copy that holds tone for each stretch, its value telling the stretch apart."""

    copy = listen_copy.ListenCopy(tmp_path / "recordings", recording_id)
    for start, end in held:
        copy.write(start * BYTES_PER_MS, tone(end - start, 100))
    copy.close()


def qwen_answer(words: list[dict[str, Any]], text: str) -> httpx.Response:
    return httpx.Response(
        200, json={"output": {"text": text, "sentence": {"words": words, "text": text}}}
    )


def word(text: str, begin: int, end: int, punctuation: str = "") -> dict[str, Any]:
    return {"text": text, "begin_time": begin, "end_time": end, "punctuation": punctuation}


def wav_seconds(request: httpx.Request) -> float:
    body = request.read().decode()
    import json

    message = json.loads(body)["input"]["messages"][-1]["content"][0]["input_audio"]["data"]
    with wave.open(BytesIO(base64.b64decode(message.split(",", 1)[1]))) as wav:
        return wav.getnframes() / wav.getframerate()


def test_qwen_gets_context_before_the_audio_and_sentences_on_the_recording_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        seen.append({"body": body, "seconds": wav_seconds(request)})
        # Audio starts at 9.7 s (10 s minus 300 ms of padding).
        return qwen_answer(
            [
                word("Tail", 0, 200, "."),  # ends at 9.9 s: only in the padding
                word("Hello", 300, 600),
                word(" there", 700, 1000, "."),
                word(" More", 1100, 1500, "."),
                word(" After", 4_900, 5_200, "."),  # starts at 14.6 s: only in the padding
            ],
            "Tail. Hello there. More. After.",
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        realtime.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    with TestClient(create_app(settings_for(tmp_path))) as client:
        with_provider(client, "qwen")
        seed_copy(tmp_path, "rec_a", [(0, 20_000)])
        response = client.post(
            "/api/recordings/rec_a/relisten",
            headers=SIDECAR,
            json={
                "startMs": 10_000,
                "endMs": 14_300,
                "pass": "minute",
                "context": {"terms": ["Lecture"], "before": "x" * 500, "after": "after text"},
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sentences"] == [
        {"start_ms": 10_000, "end_ms": 10_700, "text": "Hello there."},
        {"start_ms": 10_800, "end_ms": 11_200, "text": "More."},
    ]
    assert body["text"] == "Hello there. More."
    assert body["missing"] == [] and body["model"] == "qwen-audio-3.1-asr-flash"
    assert body["provider"] == "Qwen"
    request = seen[0]["body"]
    messages = request["input"]["messages"]
    assert [m["content"][0]["type"] for m in messages] == [
        "input_text",
        "input_text",
        "input_text",
        "input_audio",
    ]
    assert messages[0]["content"][0]["text"] == "Lecture"
    assert messages[1]["content"][0]["text"] == "x" * 400
    assert messages[2]["content"][0]["text"] == "after text"
    assert request["model"] == "qwen-audio-3.1-asr-flash"
    assert abs(seen[0]["seconds"] - 4.9) < 0.01  # 4.3 s asked for, 300 ms of padding each side


def test_parts_the_copy_lacks_are_silence_and_listed_and_nothing_covered_is_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    sent: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.read())["input"]["messages"][-1]["content"][0]["input_audio"]
        with wave.open(BytesIO(base64.b64decode(data["data"].split(",", 1)[1]))) as wav:
            sent.append(wav.readframes(wav.getnframes()))
        return httpx.Response(200, json={"output": {"text": "ok"}})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        realtime.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    with TestClient(create_app(settings_for(tmp_path))) as client:
        with_provider(client, "qwen")
        seed_copy(tmp_path, "rec_b", [(0, 3_000), (6_000, 9_000)])
        gap = client.post(
            "/api/recordings/rec_b/relisten",
            headers=SIDECAR,
            json={"startMs": 2_000, "endMs": 7_000},
        )
        assert gap.status_code == 200, gap.text
        assert gap.json()["missing"] == [[3_000, 6_000]]
        assert gap.json()["sentences"] == [] and gap.json()["text"] == "ok"
        nothing = client.post(
            "/api/recordings/rec_b/relisten",
            headers=SIDECAR,
            json={"startMs": 30_000, "endMs": 40_000},
        )
        assert nothing.status_code == 409
        unknown = client.post(
            "/api/recordings/rec_zzz/relisten",
            headers=SIDECAR,
            json={"startMs": 0, "endMs": 1_000},
        )
        assert unknown.status_code == 409
    audio = sent[0]
    # 1.7 s to 7.3 s: tone to 3 s, silence to 6 s, tone after.
    assert len(audio) == 5_600 * 32
    assert audio[: 1_300 * 32] == tone(1_300, 100)
    assert audio[1_300 * 32 : 4_300 * 32] == bytes(3_000 * 32)
    assert audio[4_300 * 32 : 5_300 * 32] == tone(1_000, 100)


def test_a_check_that_times_out_fails_once_and_a_dropped_connection_is_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[str] = []
    failures = iter(["timeout", "dropped", None])

    def handler(request: httpx.Request) -> httpx.Response:
        failure = next(failures)
        attempts.append(str(failure))
        if failure == "timeout":
            raise httpx.WriteTimeout("slow link", request=request)
        if failure == "dropped":
            raise httpx.ConnectError("no route", request=request)
        return httpx.Response(200, json={"output": {"text": "ok"}})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        realtime.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    with TestClient(create_app(settings_for(tmp_path))) as client:
        with_provider(client, "qwen")
        seed_copy(tmp_path, "rec_t", [(0, 3_000)])
        body = {"startMs": 0, "endMs": 3_000}
        slow = client.post("/api/recordings/rec_t/relisten", headers=SIDECAR, json=body)
        assert slow.status_code == 502
        assert attempts == ["timeout"]  # a timeout is not tried twice
        retried = client.post("/api/recordings/rec_t/relisten", headers=SIDECAR, json=body)
        assert retried.status_code == 200 and retried.json()["text"] == "ok"
        assert attempts == ["timeout", "dropped", "None"]


def test_the_prompt_for_an_openai_style_server_is_terms_then_the_end_of_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = answer_text(monkeypatch, "words")
    with TestClient(create_app(settings_for(tmp_path))) as client:
        with_provider(client, "compatible")
        seed_copy(tmp_path, "rec_c", [(0, 5_000)])
        response = client.post(
            "/api/recordings/rec_c/relisten",
            headers=SIDECAR,
            json={
                "startMs": 1_000,
                "endMs": 4_000,
                "pass": "retry",
                "context": {"terms": ["Gradient", "Adam"], "before": "a" * 2_000},
            },
        )
    assert response.status_code == 200 and response.json()["text"] == "words"
    form = seen[0].read()
    prompt = form.split(b'name="prompt"\r\n\r\n', 1)[1].split(b"\r\n", 1)[0].decode()
    assert prompt.startswith("Gradient, Adam ") and len(prompt) == 800 and prompt.endswith("a")


def test_audio_with_no_speech_is_empty_text_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "ASR_RESPONSE_HAVE_NO_WORDS"})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        realtime.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    with TestClient(create_app(settings_for(tmp_path))) as client:
        with_provider(client, "qwen")
        seed_copy(tmp_path, "rec_d", [(0, 5_000)])
        response = client.post(
            "/api/recordings/rec_d/relisten",
            headers=SIDECAR,
            json={"startMs": 0, "endMs": 4_000},
        )
    assert response.status_code == 200
    assert response.json()["text"] == "" and response.json()["sentences"] == []


def test_relisten_says_why_it_cannot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Invalid API key"})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        realtime.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    with TestClient(create_app(settings_for(tmp_path))) as client:
        seed_copy(tmp_path, "rec_e", [(0, 5_000)])
        ask = {"startMs": 0, "endMs": 4_000}
        cleared = {"recording": {"model": None, "stream": True}}
        client.put("/api/settings/speech/roles", headers=SIDECAR, json={"roles": cleared})
        no_model = client.post("/api/recordings/rec_e/relisten", headers=SIDECAR, json=ask)
        assert no_model.status_code == 424
        assert "Recording model" in no_model.json()["detail"]
        with_provider(client, "qwen")
        refused = client.post("/api/recordings/rec_e/relisten", headers=SIDECAR, json=ask)
        assert refused.status_code == 502 and "Invalid API key" in refused.json()["detail"]
        too_long = client.post(
            "/api/recordings/rec_e/relisten",
            headers=SIDECAR,
            json={"startMs": 0, "endMs": 180_001},
        )
        assert too_long.status_code == 422
        backwards = client.post(
            "/api/recordings/rec_e/relisten",
            headers=SIDECAR,
            json={"startMs": 5_000, "endMs": 5_000},
        )
        assert backwards.status_code == 422
