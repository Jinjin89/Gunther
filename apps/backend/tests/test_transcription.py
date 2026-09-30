"""Live transcription through any OpenAI-style /audio/transcriptions server."""

import base64
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from gunther import realtime
from gunther.config import Settings
from gunther.main import create_app

SIDECAR_TOKEN = "sidecar-token-with-at-least-256-bits-000000000000000000000000"


def test_audio_is_sent_to_the_configured_server_in_segments(tmp_path: Path, monkeypatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "hello there"})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        realtime.httpx,
        "AsyncClient",
        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs),
    )
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
        processing_worker_enabled=False,
        stt_provider="compatible",
        stt_base_url="https://asr.example/v1/",
        stt_api_key="key-123",
        stt_model="whisper-large",
        stt_language="en",
        auth_token=SIDECAR_TOKEN,
    )
    with TestClient(create_app(settings)) as client:
        assert (
            client.get("/api/health", headers={"X-Gunther-Token": SIDECAR_TOKEN}).json()[
                "transcriptionProvider"
            ]
            == "compatible"
        )
        with client.websocket_connect(
            f"/api/recordings/live?token={SIDECAR_TOKEN}",
            headers={"Origin": "http://localhost:5173"},
        ) as socket:
            ready = socket.receive_json()
            assert ready["type"] == "service.ready" and ready["provider"] == "compatible"
            assert ready["model"] == "whisper-large"
            silence = base64.b64encode(bytes(24_000 * 2 * 2)).decode()
            socket.send_json({"type": "input_audio_buffer.append", "audio": silence})
            socket.send_json({"type": "input_audio_buffer.commit"})
            event = socket.receive_json()
    assert event["transcript"] == "hello there" and event["provider"] == "compatible"
    request = seen[0]
    assert str(request.url) == "https://asr.example/v1/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer key-123"
    body = request.read()
    assert b"whisper-large" in body and b'name="language"' in body
