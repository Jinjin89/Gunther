import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import gunther.api as api_module
from gunther.api import _read_limited_body
from gunther.config import Settings
from gunther.main import create_app
from gunther.recording_service import RecordingTooLargeError
from gunther.request_body_limit import (
    ASSET_REQUEST_BODY_BYTES,
    DEFAULT_REQUEST_BODY_BYTES,
    DIRECT_RECORDING_BODY_BYTES,
    RECORDING_CHUNK_BODY_BYTES,
    RequestBodyLimitMiddleware,
    request_body_limit,
)


def _streaming_request(
    chunks: list[bytes], headers: list[tuple[bytes, bytes]] | None = None
) -> Request:
    pending = iter(chunks)

    async def receive() -> dict[str, object]:
        try:
            chunk = next(pending)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers or [],
        "client": ("test", 1),
        "server": ("testserver", 80),
    }
    return Request(scope, receive)


def test_bounded_reader_accepts_exact_limit() -> None:
    request = _streaming_request([b"abc", b"de"])
    assert asyncio.run(_read_limited_body(request, 5)) == b"abcde"


def test_bounded_reader_rejects_stream_without_content_length() -> None:
    request = _streaming_request([b"abc", b"def", b"never-consumed"])
    with pytest.raises(RecordingTooLargeError):
        asyncio.run(_read_limited_body(request, 5))


def test_recording_endpoints_reject_oversized_bodies_before_storage(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(api_module, "MAX_CHUNK_BYTES", 5)
    monkeypatch.setattr(api_module, "MAX_DIRECT_UPLOAD_BYTES", 5)
    app = create_app(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            recordings_dir=tmp_path / "recordings",
            seed_demo=False,
            deepseek_api_key=None,
            openai_api_key=None,
        )
    )

    with TestClient(app) as client:
        started = client.post(
            "/api/recordings/sessions?title=Bounded",
            headers={"Content-Type": "audio/webm"},
        )
        recording_id = started.json()["id"]

        chunk = client.put(
            f"/api/recordings/{recording_id}/chunks?sequence=0",
            content=b"123456",
            headers={"Content-Type": "audio/webm"},
        )
        assert chunk.status_code == 413
        assert client.get(f"/api/recordings/{recording_id}/metadata").json()["sizeBytes"] == 0

        direct = client.post(
            "/api/recordings?title=Bounded",
            content=b"123456",
            headers={"Content-Type": "audio/webm"},
        )
        assert direct.status_code == 413


def test_global_transport_limit_rejects_declared_json_before_schema_parsing(
    tmp_path,
) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
            assets_dir=tmp_path / "assets",
            recordings_dir=tmp_path / "recordings",
            seed_demo=False,
            deepseek_api_key=None,
            openai_api_key=None,
        )
    )

    with TestClient(app) as client:
        rejected = client.post(
            "/api/sources",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(DEFAULT_REQUEST_BODY_BYTES + 1),
            },
        )

        assert rejected.status_code == 413
        assert "request body exceeds" in rejected.json()["detail"].lower()
        assert client.get("/api/sources").json() == []


def test_chunked_transport_body_is_bounded_without_content_length(monkeypatch) -> None:
    import gunther.request_body_limit as limit_module

    monkeypatch.setattr(limit_module, "DEFAULT_REQUEST_BODY_BYTES", 5)
    messages = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return next(messages)

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    async def consume_body(_scope, receive_body, send_response) -> None:
        while True:
            message = await receive_body()
            if not message.get("more_body", False):
                break
        await send_response({"type": "http.response.start", "status": 204, "headers": []})
        await send_response({"type": "http.response.body", "body": b""})

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/sources",
        "raw_path": b"/api/sources",
        "query_string": b"",
        "headers": [],
        "client": ("test", 1),
        "server": ("testserver", 80),
    }

    asyncio.run(RequestBodyLimitMiddleware(consume_body)(scope, receive, send))
    assert sent[0]["status"] == 413


def test_streaming_routes_keep_their_narrow_explicit_transport_budgets() -> None:
    assert request_body_limit("/api/captures/assets", "POST") == ASSET_REQUEST_BODY_BYTES
    assert request_body_limit("/api/recordings", "POST") == DIRECT_RECORDING_BODY_BYTES
    assert (
        request_body_limit(
            "/api/recordings/rec_0123456789abcdef01234567/chunks",
            "PUT",
        )
        == RECORDING_CHUNK_BODY_BYTES
    )
    assert request_body_limit("/api/sources", "POST") == DEFAULT_REQUEST_BODY_BYTES
