import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gunther.config import Settings
from gunther.device_auth import DeviceCredential, PairingAlreadyUsedError
from gunther.main import create_app

SIDECAR_TOKEN = "sidecar-token-with-at-least-256-bits-000000000000000000000000"
SIDECAR_HEADERS = {"X-Gunther-Token": SIDECAR_TOKEN}
ALLOWED_ORIGIN = "http://tauri.localhost"


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
        deepseek_api_key=None,
        stt_provider="compatible",
        auth_token=SIDECAR_TOKEN,
        cors_origins=[ALLOWED_ORIGIN],
    )


def create_pairing(
    client: TestClient,
    scopes: list[str] | None = None,
) -> dict[str, object]:
    response = client.post(
        "/api/pairing/sessions",
        headers=SIDECAR_HEADERS,
        json={"scopes": scopes or ["api:access", "transcription:stream"]},
    )
    assert response.status_code == 201
    return response.json()


def exchange_pairing(
    client: TestClient,
    pairing: dict[str, object],
    name: str = "Keke's phone",
) -> dict[str, object]:
    response = client.post(
        "/api/pairing/exchange",
        json={
            "pairingId": pairing["pairingId"],
            "pairingCode": pairing["pairingCode"],
            "deviceName": name,
            "platform": "android",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_workspace_identity_is_stable_across_app_restarts(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as first:
        first_bootstrap = first.get("/api/workspace/bootstrap", headers=SIDECAR_HEADERS)
        assert first_bootstrap.status_code == 200
        first_body = first_bootstrap.json()
        assert first_body["protocolVersion"] == 1
        assert first_body["minimumProtocolVersion"] == 1
        assert first_body["authKind"] == "sidecar"

    with TestClient(create_app(settings)) as restarted:
        second_body = restarted.get("/api/workspace/bootstrap", headers=SIDECAR_HEADERS).json()

    assert second_body["workspaceId"] == first_body["workspaceId"]
    assert second_body["workspaceName"] == first_body["workspaceName"]


def test_expected_workspace_header_prevents_cross_workspace_writes(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        workspace_id = client.get("/api/workspace/bootstrap", headers=SIDECAR_HEADERS).json()[
            "workspaceId"
        ]

        accepted = client.post(
            "/api/notes",
            headers={
                **SIDECAR_HEADERS,
                "X-Gunther-Workspace-Id": workspace_id,
            },
            json={"title": "Bound to this workspace", "content": "safe"},
        )
        assert accepted.status_code == 201

        rejected = client.post(
            "/api/notes",
            headers={
                **SIDECAR_HEADERS,
                "X-Gunther-Workspace-Id": "wsp_another_workspace",
            },
            json={"title": "Must not cross the boundary", "content": "unsafe"},
        )
        assert rejected.status_code == 409
        assert rejected.json() == {"detail": "Workspace identity changed"}

        notes = client.get("/api/notes", headers=SIDECAR_HEADERS)
        assert notes.status_code == 200
        assert [item["title"] for item in notes.json()] == ["Bound to this workspace"]


def test_pairing_is_one_time_hashed_and_device_is_revocable(tmp_path: Path) -> None:
    database = tmp_path / "gunther.sqlite"
    with TestClient(create_app(settings_for(tmp_path))) as client:
        pairing = create_pairing(client)
        pairing_id = str(pairing["pairingId"])
        pairing_code = str(pairing["pairingCode"])
        assert len(pairing_code) >= 43

        wrong = client.post(
            "/api/pairing/exchange",
            json={
                "pairingId": pairing_id,
                "pairingCode": "A" * 43,
                "deviceName": "Wrong code",
                "platform": "ios",
            },
        )
        assert wrong.status_code == 401
        assert pairing_code not in wrong.text

        credential = exchange_pairing(client, pairing)
        access_token = str(credential["accessToken"])
        device = credential["device"]
        assert isinstance(device, dict)
        device_id = str(device["id"])
        assert credential["tokenType"] == "Bearer"
        assert access_token.startswith("gdt_")
        assert credential["workspaceId"] == pairing["workspaceId"]

        reused = client.post(
            "/api/pairing/exchange",
            json={
                "pairingId": pairing_id,
                "pairingCode": pairing_code,
                "deviceName": "Replay",
                "platform": "android",
            },
        )
        assert reused.status_code == 409
        assert access_token not in reused.text

        bearer = {"Authorization": f"Bearer {access_token}"}
        assert client.get("/api/health", headers=bearer).status_code == 200
        bootstrap = client.get("/api/workspace/bootstrap", headers=bearer)
        assert bootstrap.status_code == 200
        assert bootstrap.json()["authKind"] == "device"
        assert bootstrap.json()["deviceId"] == device_id
        assert "accessToken" not in bootstrap.text

        device_cannot_administer = client.post("/api/pairing/sessions", headers=bearer, json={})
        assert device_cannot_administer.status_code == 403

        listed = client.get("/api/devices", headers=SIDECAR_HEADERS)
        assert listed.status_code == 200
        listed_body = listed.json()
        assert [item["id"] for item in listed_body] == [device_id]
        assert listed_body[0]["lastUsedAt"] is not None
        assert access_token not in listed.text
        assert pairing_code not in listed.text
        assert "tokenHash" not in listed.text

        wrong_token = f"gdt_{'B' * 43}"
        assert (
            client.get(
                "/api/health", headers={"Authorization": f"Bearer {wrong_token}"}
            ).status_code
            == 401
        )
        assert client.get(f"/api/health?token={access_token}").status_code == 401
        assert client.get(f"/api/health?token={SIDECAR_TOKEN}").status_code == 200

        revoked = client.post(f"/api/devices/{device_id}/revoke", headers=SIDECAR_HEADERS)
        assert revoked.status_code == 200
        assert revoked.json()["revokedAt"] is not None
        assert client.get("/api/health", headers=bearer).status_code == 401

    with sqlite3.connect(database) as connection:
        stored_pairing_hash = connection.execute(
            "SELECT code_hash FROM device_pairing_sessions WHERE id = ?",
            (pairing_id,),
        ).fetchone()[0]
        stored_token_hash = connection.execute(
            "SELECT token_hash FROM paired_devices WHERE id = ?", (device_id,)
        ).fetchone()[0]
    assert len(stored_pairing_hash) == 64
    assert len(stored_token_hash) == 64
    assert stored_pairing_hash != pairing_code
    assert stored_token_hash != access_token


def test_expired_pairing_code_fails_without_creating_a_device(tmp_path: Path) -> None:
    database = tmp_path / "gunther.sqlite"
    with TestClient(create_app(settings_for(tmp_path))) as client:
        pairing = create_pairing(client)
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE device_pairing_sessions SET expires_at = ? WHERE id = ?",
                ("2000-01-01 00:00:00.000000", pairing["pairingId"]),
            )
            connection.commit()

        expired = client.post(
            "/api/pairing/exchange",
            json={
                "pairingId": pairing["pairingId"],
                "pairingCode": pairing["pairingCode"],
                "deviceName": "Late phone",
                "platform": "ios",
            },
        )
        assert expired.status_code == 410
        assert str(pairing["pairingCode"]) not in expired.text
        assert client.get("/api/devices", headers=SIDECAR_HEADERS).json() == []


def test_simultaneous_pairing_exchange_issues_exactly_one_credential(
    tmp_path: Path,
) -> None:
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        pairing = create_pairing(client)

        def exchange(index: int) -> DeviceCredential | PairingAlreadyUsedError:
            try:
                return app.state.device_auth.exchange_pairing_code(
                    str(pairing["pairingId"]),
                    str(pairing["pairingCode"]),
                    f"Concurrent phone {index}",
                    "android",
                )
            except PairingAlreadyUsedError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(exchange, range(2)))

        assert sum(isinstance(result, DeviceCredential) for result in results) == 1
        assert sum(isinstance(result, PairingAlreadyUsedError) for result in results) == 1
        assert len(client.get("/api/devices", headers=SIDECAR_HEADERS).json()) == 1


def test_device_bearer_authenticates_websocket_but_never_from_query(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        credential = exchange_pairing(client, create_pairing(client))
        token = str(credential["accessToken"])
        with client.websocket_connect(
            "/api/recordings/live",
            headers={"Authorization": f"Bearer {token}"},
        ) as websocket:
            event = websocket.receive_json()
            assert event["type"] == "service.error"
            assert event["code"] == "not_configured"

        with (
            pytest.raises(WebSocketDisconnect) as query_rejected,
            client.websocket_connect(f"/api/recordings/live?token={token}"),
        ):
            pass
        assert query_rejected.value.code == 1008

        with (
            pytest.raises(WebSocketDisconnect) as wrong_rejected,
            client.websocket_connect(
                "/api/recordings/live",
                headers={"Authorization": f"Bearer gdt_{'C' * 43}"},
            ),
        ):
            pass
        assert wrong_rejected.value.code == 1008

        with client.websocket_connect(
            f"/api/recordings/live?token={SIDECAR_TOKEN}",
            headers={"Origin": ALLOWED_ORIGIN},
        ) as legacy_sidecar:
            assert legacy_sidecar.receive_json()["type"] == "service.error"

        device_id = str(credential["device"]["id"])
        assert (
            client.post(f"/api/devices/{device_id}/revoke", headers=SIDECAR_HEADERS).status_code
            == 200
        )
        with (
            pytest.raises(WebSocketDisconnect) as revoked_rejected,
            client.websocket_connect(
                "/api/recordings/live",
                headers={"Authorization": f"Bearer {token}"},
            ),
        ):
            pass
        assert revoked_rejected.value.code == 1008


def test_device_scopes_are_enforced_for_rest_and_websocket(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        api_only = exchange_pairing(
            client,
            create_pairing(client, scopes=["api:access"]),
            name="API only",
        )
        api_headers = {"Authorization": f"Bearer {api_only['accessToken']}"}
        assert client.get("/api/health", headers=api_headers).status_code == 200
        with (
            pytest.raises(WebSocketDisconnect) as websocket_rejected,
            client.websocket_connect("/api/recordings/live", headers=api_headers),
        ):
            pass
        assert websocket_rejected.value.code == 1008

        transcription_only = exchange_pairing(
            client,
            create_pairing(client, scopes=["transcription:stream"]),
            name="Transcription only",
        )
        transcription_headers = {"Authorization": f"Bearer {transcription_only['accessToken']}"}
        assert client.get("/api/health", headers=transcription_headers).status_code == 403
        with client.websocket_connect(
            "/api/recordings/live", headers=transcription_headers
        ) as websocket:
            assert websocket.receive_json()["type"] == "service.error"
