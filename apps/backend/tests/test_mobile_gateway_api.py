from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gunther.config import Settings
from gunther.main import create_app
from gunther.mobile_gateway_runtime import MobileGatewayRuntime
from gunther.pairing_exchange_guard import PairingExchangeGuard, PairingExchangeRejected

SIDECAR_TOKEN = "sidecar-token-with-at-least-256-bits-000000000000000000000000"
SIDECAR_HEADERS = {"X-Gunther-Token": SIDECAR_TOKEN}
CA_CERTIFICATE = """-----BEGIN CERTIFICATE-----
public-ca-certificate-only
-----END CERTIFICATE-----
"""


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
        deepseek_api_key=None,
        stt_provider="compatible",
        auth_token=SIDECAR_TOKEN,
        cors_origins=["http://tauri.localhost"],
    )


def running_gateway() -> MobileGatewayRuntime:
    runtime = MobileGatewayRuntime(enabled=True)
    runtime.prepared(
        address="https://192.168.1.8:8788/api/",
        ca_fingerprint="AA:BB:CC:DD",
        ca_certificate_pem=CA_CERTIFICATE,
    )
    runtime.started()
    return runtime


def test_gateway_status_is_sidecar_admin_only_and_contains_no_secret(
    tmp_path: Path,
) -> None:
    runtime = running_gateway()
    with TestClient(create_app(settings_for(tmp_path), mobile_gateway=runtime)) as client:
        assert client.get("/api/mobile-gateway/status").status_code == 401
        assert (
            client.get(
                "/api/mobile-gateway/status",
                headers={"X-Gunther-Token": "wrong"},
            ).status_code
            == 401
        )

        response = client.get("/api/mobile-gateway/status", headers=SIDECAR_HEADERS)
        assert response.status_code == 200
        assert response.json() == {
            "enabled": True,
            "running": True,
            "address": "https://192.168.1.8:8788/api/",
            "caFingerprint": "AA:BB:CC:DD",
            "caCertificatePem": CA_CERTIFICATE,
            "protocolVersion": 1,
            "error": None,
        }
        assert SIDECAR_TOKEN not in response.text
        assert "PRIVATE KEY" not in response.text


def test_gateway_surface_accepts_pairing_and_bearer_but_never_sidecar_token(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    sidecar_app = create_app(settings, mobile_gateway=running_gateway())
    gateway_app = create_app(
        settings,
        allow_sidecar_auth=False,
        mobile_gateway=running_gateway(),
    )

    with TestClient(sidecar_app) as sidecar, TestClient(gateway_app) as gateway:
        pairing = sidecar.post(
            "/api/pairing/sessions",
            headers=SIDECAR_HEADERS,
            json={"scopes": ["api:access", "transcription:stream"]},
        ).json()
        exchanged = gateway.post(
            "/api/pairing/exchange",
            json={
                "pairingId": pairing["pairingId"],
                "pairingCode": pairing["pairingCode"],
                "deviceName": "LAN phone",
                "platform": "android",
            },
        )
        assert exchanged.status_code == 201
        bearer = {"Authorization": f"Bearer {exchanged.json()['accessToken']}"}

        assert gateway.get("/api/health", headers=bearer).status_code == 200
        assert gateway.get("/api/mobile-gateway/status", headers=bearer).status_code == 403
        assert gateway.get("/api/health", headers=SIDECAR_HEADERS).status_code == 401
        assert gateway.get(f"/api/health?token={SIDECAR_TOKEN}").status_code == 401
        assert (
            gateway.post(
                f"/api/pairing/exchange?token={SIDECAR_TOKEN}",
                json={
                    "pairingId": pairing["pairingId"],
                    "pairingCode": pairing["pairingCode"],
                    "deviceName": "Never created",
                    "platform": "android",
                },
            ).status_code
            == 401
        )

        with (
            pytest.raises(WebSocketDisconnect) as rejected,
            gateway.websocket_connect(f"/api/recordings/live?token={SIDECAR_TOKEN}"),
        ):
            pass
        assert rejected.value.code == 1008


def test_gateway_failure_remains_visible_without_publishable_address(
    tmp_path: Path,
) -> None:
    runtime = MobileGatewayRuntime(enabled=True)
    runtime.stopped("HTTPS gateway could not bind port 8788")
    with TestClient(create_app(settings_for(tmp_path), mobile_gateway=runtime)) as client:
        status = client.get(
            "/api/mobile-gateway/status",
            headers=SIDECAR_HEADERS,
        ).json()

    assert status["enabled"] is True
    assert status["running"] is False
    assert status["address"] is None
    assert status["error"] == "HTTPS gateway could not bind port 8788"


def test_pairing_exchange_rejects_declared_and_chunked_oversized_bodies(
    tmp_path: Path,
) -> None:
    gateway_app = create_app(
        settings_for(tmp_path),
        allow_sidecar_auth=False,
        mobile_gateway=running_gateway(),
    )
    with TestClient(gateway_app) as gateway:
        declared = gateway.post(
            "/api/pairing/exchange",
            content=b"x" * (16 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )

        def chunked_body():
            yield b'{"deviceName":"'
            yield b"x" * (16 * 1024)
            yield b'"}'

        chunked = gateway.post(
            "/api/pairing/exchange",
            content=chunked_body(),
            headers={
                "Content-Type": "application/json",
                "Transfer-Encoding": "chunked",
            },
        )

    assert declared.status_code == 413
    assert declared.json()["detail"] == "Pairing request is too large"
    assert chunked.status_code == 413
    assert chunked.json()["detail"] == "Pairing request is too large"


def test_pairing_exchange_rate_limit_is_explicit(tmp_path: Path) -> None:
    gateway_app = create_app(
        settings_for(tmp_path),
        allow_sidecar_auth=False,
        mobile_gateway=running_gateway(),
    )
    gateway_app.state.pairing_exchange_guard = PairingExchangeGuard(
        max_attempts=2,
        window_seconds=60,
        max_concurrent=1,
    )
    with TestClient(gateway_app) as gateway:
        assert gateway.post("/api/pairing/exchange", json={}).status_code == 422
        assert gateway.post("/api/pairing/exchange", json={}).status_code == 422
        limited = gateway.post("/api/pairing/exchange", json={})

    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    assert limited.json()["detail"] == "Too many pairing attempts from this device"


def test_pairing_exchange_global_concurrency_limit_is_nonblocking() -> None:
    guard = PairingExchangeGuard(
        max_attempts=10,
        window_seconds=60,
        max_concurrent=1,
    )
    with (
        guard.acquire("first-device"),
        pytest.raises(PairingExchangeRejected) as rejected,
        guard.acquire("second-device"),
    ):
        pass

    assert rejected.value.detail == "Too many pairing exchanges are in progress"
    assert rejected.value.retry_after_seconds == 1
