"""Live transcription through any OpenAI-style /audio/transcriptions server."""

import asyncio
import base64
import json
import threading
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from gunther.config import Settings
from gunther.main import create_app
from gunther.transcription import sensevoice_health

SIDECAR_TOKEN = "sidecar-token-with-at-least-256-bits-000000000000000000000000"
HEADERS = {"X-Gunther-Token": SIDECAR_TOKEN, "Origin": "http://localhost:5173"}
LIVE = "/api/recordings/live?sample_rate=16000"


def settings_for(tmp_path: Path, **settings) -> Settings:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        service_settings_file=tmp_path / "service-settings.json",
        processing_worker_enabled=False,
        auth_token=SIDECAR_TOKEN,
        **settings,
    )


def append_and_commit(socket) -> None:
    silence = base64.b64encode(bytes(32 * 200)).decode()
    socket.send_json({"type": "input_audio_buffer.append", "audio": silence})
    socket.send_json({"type": "input_audio_buffer.commit"})


class Pool(httpx.MockTransport):
    closes = 0

    async def aclose(self) -> None:
        self.closes += 1


def test_audio_is_sent_to_the_configured_server_in_segments(tmp_path: Path, monkeypatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "hello there"})

    monkeypatch.setattr("gunther.main.AsrHttpTransport", lambda: httpx.MockTransport(handler))
    settings = settings_for(
        tmp_path,
        stt_provider="compatible",
        stt_base_url="https://asr.example/v1/",
        stt_api_key="key-123",
        stt_model="whisper-large",
        stt_language="en",
    )
    with TestClient(create_app(settings)) as client:
        assert (
            client.get("/api/health", headers={"X-Gunther-Token": SIDECAR_TOKEN}).json()[
                "transcriptionProvider"
            ]
            == "compatible"
        )
        with client.websocket_connect(LIVE, headers=HEADERS) as socket:
            ready = socket.receive_json()
            assert ready["type"] == "service.ready" and ready["provider"] == "compatible"
            assert ready["model"] == "whisper-large"
            append_and_commit(socket)
            event = socket.receive_json()
    assert event["type"] == "transcript.piece" and event["text"] == "hello there"
    request = seen[0]
    assert str(request.url) == "https://asr.example/v1/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer key-123"
    body = request.read()
    assert b"whisper-large" in body and b'name="language"' in body


def test_overlapping_takes_isolate_reset_cookies_and_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str | None, str | None, float]] = []
    sessions = iter(("one", "two"))

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        cookie = request.headers.get("cookie")
        key = request.headers.get("authorization")
        seen.append((path, cookie, key, request.extensions["timeout"]["read"]))
        if path == "/health":
            return httpx.Response(
                200,
                json={"ok": True, "diarize": True},
                headers={"Set-Cookie": "health=probe; Path=/"},
            )
        if path == "/reset":
            return httpx.Response(200, headers={"Set-Cookie": f"take={next(sessions)}; Path=/"})
        return httpx.Response(200, json={"text": "piece"})

    pool = Pool(handler)
    monkeypatch.setattr("gunther.main.AsrHttpTransport", lambda: pool)
    base = "http://asr.example"
    settings = settings_for(tmp_path, stt_provider="sensevoice", sensevoice_url=base)
    with TestClient(create_app(settings)) as client:
        provider = {
            "kind": "compatible",
            "baseUrl": f"{base}/v1",
            "apiKey": "dictation-key",
            "models": ["whisper-1"],
        }
        assert client.post(
            "/api/settings/speech/providers", headers=HEADERS, json=provider
        ).is_success
        roles = {
            "recording": {"model": "sensevoice/sensevoice-small", "stream": True},
            "dictation": {"model": "compatible/whisper-1", "stream": False},
        }
        saved = client.put("/api/settings/speech/roles", headers=HEADERS, json={"roles": roles})
        assert saved.is_success
        with ExitStack() as sockets:

            def open_recording():
                socket = sockets.enter_context(client.websocket_connect(LIVE, headers=HEADERS))
                ready = socket.receive_json()
                assert ready["type"] == "service.ready" and ready["diarize"] is True
                return socket

            first, second = open_recording(), open_recording()
            dictation = sockets.enter_context(
                client.websocket_connect(f"{LIVE}&purpose=dictation", headers=HEADERS)
            )
            assert dictation.receive_json()["provider"] == "compatible"
            first.send_json({"type": "input_audio_buffer.commit"})
            assert first.receive_json() == {"type": "transcript.flushed", "until_ms": 0}
            assert seen == [("/health", None, None, 1.2), ("/reset", None, None, 30.0)] * 2
            for take in (second, dictation, first):
                append_and_commit(take)
                assert take.receive_json()["text"] == "piece"
                assert take.receive_json() == {"type": "transcript.flushed", "until_ms": 200}
        assert pool.closes == 0
    assert pool.closes == 1
    assert seen[-3:] == [
        ("/v1/audio/transcriptions", "take=two", None, 30.0),
        ("/v1/audio/transcriptions", None, "Bearer dictation-key", 30.0),
        ("/v1/audio/transcriptions", "take=one", None, 30.0),
    ]


def test_each_app_closes_only_its_own_pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = Pool(lambda _: httpx.Response(200))
    second = Pool(lambda _: httpx.Response(200))
    pools = iter((first, second))
    monkeypatch.setattr("gunther.main.AsrHttpTransport", lambda: next(pools))
    first_app = create_app(settings_for(tmp_path / "first"))
    second_app = create_app(settings_for(tmp_path / "second"))
    with TestClient(first_app):
        assert first_app.state.asr_http_transport is first
        with TestClient(second_app):
            assert second_app.state.asr_http_transport is second
            assert first.closes == second.closes == 0
        assert first.closes == 0 and second.closes == 1
    assert first.closes == second.closes == 1


def test_health_preserves_both_timeouts_and_does_not_close_a_borrowed_transport() -> None:
    read_timeouts = []

    def handler(request: httpx.Request) -> httpx.Response:
        read_timeouts.append(request.extensions["timeout"]["read"])
        if len(read_timeouts) == 1:
            raise httpx.ReadTimeout("slow health", request=request)
        return httpx.Response(200, json={"ok": True})

    pool = Pool(handler)

    async def check() -> None:
        assert await sensevoice_health("http://asr.example/v1", transport=pool) is None
        assert await sensevoice_health("http://asr.example", timeout=4.0, transport=pool) == {
            "ok": True
        }

    asyncio.run(check())
    assert read_timeouts == [1.2, 4.0] and pool.closes == 0


def test_an_upload_timeout_reports_the_segment_failure_and_keeps_the_stream_ordered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploads = []

    def handler(request: httpx.Request) -> httpx.Response:
        uploads.append(request)
        assert request.extensions["timeout"]["read"] == 30.0
        if len(uploads) == 1:
            raise httpx.ReadTimeout("slow upload", request=request)
        return httpx.Response(200, json={"text": "next piece"})

    monkeypatch.setattr("gunther.main.AsrHttpTransport", lambda: Pool(handler))
    settings = settings_for(
        tmp_path, stt_provider="compatible", stt_base_url="https://asr.example/v1"
    )
    with (
        TestClient(create_app(settings)) as client,
        client.websocket_connect(LIVE, headers=HEADERS) as socket,
    ):
        assert socket.receive_json()["type"] == "service.ready"
        append_and_commit(socket)
        error = socket.receive_json()
        assert error["type"] == "service.error" and error["code"] == "segment_failed"
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 200}
        append_and_commit(socket)
        piece = socket.receive_json()
        assert piece["text"] == "next piece" and piece["start_ms"] == 200
        assert socket.receive_json() == {"type": "transcript.flushed", "until_ms": 400}
    assert len(uploads) == 2


@pytest.mark.parametrize("through_proxy", [False, True], ids=["direct", "env-proxy"])
def test_http_pool_reuses_the_connection_across_health_reset_and_takes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, through_proxy: bool
) -> None:
    connections = set()
    requests: list[tuple[str, bool]] = []

    class Provider(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def answer(self) -> None:
            connections.add(self.connection)
            target = urlsplit(self.path)
            requests.append((target.path, bool(target.netloc)))
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.dumps({"ok": True, "text": "piece"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = answer

    with ThreadingHTTPServer(("127.0.0.1", 0), Provider) as server:
        server_url = f"http://127.0.0.1:{server.server_port}"
        # Point configured proxy traffic at the fake too, so a routing regression stays local.
        monkeypatch.setenv("HTTP_PROXY", server_url)
        monkeypatch.delenv("http_proxy", raising=False)
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        monkeypatch.delenv("no_proxy", raising=False)
        base = "http://asr.example" if through_proxy else server_url
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            settings = settings_for(tmp_path, stt_provider="sensevoice", sensevoice_url=base)
            with TestClient(create_app(settings)) as client:
                for _ in range(2):
                    with client.websocket_connect(LIVE, headers=HEADERS) as socket:
                        assert socket.receive_json()["type"] == "service.ready"
                        append_and_commit(socket)
                        assert socket.receive_json()["text"] == "piece"
                        assert socket.receive_json()["until_ms"] == 200
        finally:
            server.shutdown()
            thread.join()
    assert (
        requests
        == [(path, through_proxy) for path in ("/health", "/reset", "/v1/audio/transcriptions")] * 2
    )
    assert len(connections) == 1
