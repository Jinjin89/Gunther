import base64
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from gunther import realtime, speech_registry
from gunther.config import Settings
from gunther.main import create_app

SIDECAR_TOKEN = "sidecar-token-for-tests"
SIDECAR = {"X-Gunther-Token": SIDECAR_TOKEN}
KEY = "sk-test-key-1234"
QWEN_JOB = {"model": "qwen/qwen3-asr-flash", "stream": False}


def settings_for(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
        deepseek_api_key=None,
        auth_token=SIDECAR_TOKEN,
        service_settings_file=tmp_path / "service-settings.json",
        **overrides,
    )


def overview(client: TestClient) -> dict:
    response = client.get("/api/settings/speech", headers=SIDECAR)
    assert response.status_code == 200
    return response.json()


def roles(client: TestClient) -> dict[str, dict]:
    return {role["id"]: role for role in overview(client)["roles"]}


def test_older_settings_become_providers_with_recording_live_and_ask_whole(tmp_path: Path) -> None:
    settings = settings_for(
        tmp_path,
        stt_provider="compatible",
        stt_base_url="https://asr.example/v1",
        stt_model="whisper-large",
        stt_language="en",
    )
    with TestClient(create_app(settings)) as client:
        found = overview(client)
        assert found["fromEnvironment"] is True
        assert {provider["kind"] for provider in found["providers"]} == {"sensevoice", "compatible"}
        by_id = roles(client)
        assert by_id["recording"]["model"] == "compatible/whisper-large"
        assert by_id["recording"]["stream"] is True
        assert by_id["dictation"]["model"] == "compatible/whisper-large"
        assert by_id["dictation"]["stream"] is False
        assert by_id["dictation"]["language"] == "en"


def test_each_job_picks_its_own_model_and_live_setting(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        added = client.post(
            "/api/settings/speech/providers",
            headers=SIDECAR,
            json={"kind": "qwen", "apiKey": KEY},
        )
        assert added.status_code == 201
        qwen = next(item for item in added.json()["providers"] if item["kind"] == "qwen")
        assert [model["id"] for model in qwen["models"]] == ["qwen3-asr-flash"]
        assert KEY not in added.text and qwen["keyHint"] == "1234"

        saved = client.put(
            "/api/settings/speech/roles",
            headers=SIDECAR,
            json={"roles": {"dictation": {**QWEN_JOB, "language": "zh"}}},
        )
        assert saved.status_code == 200
        by_id = roles(client)
        assert by_id["dictation"]["model"] == "qwen/qwen3-asr-flash"
        assert by_id["dictation"]["language"] == "zh"
        # The recording job did not move.
        assert by_id["recording"]["model"] != "qwen/qwen3-asr-flash"

        refused = client.put(
            "/api/settings/speech/roles",
            headers=SIDECAR,
            json={"roles": {"recording": {"model": "nobody/model", "stream": True}}},
        )
        assert refused.status_code == 422
        assert (
            client.put(
                "/api/settings/speech/roles",
                headers=SIDECAR,
                json={
                    "roles": {"recording": {"model": None, "stream": True, "language": "english!"}}
                },
            ).status_code
            == 422
        )

    saved_file = json.loads((tmp_path / "service-settings.json").read_text())
    assert saved_file["speechRoles"]["dictation"]["language"] == "zh"
    with TestClient(create_app(settings_for(tmp_path))) as client:
        assert roles(client)["dictation"]["model"] == "qwen/qwen3-asr-flash"


def test_removing_a_provider_moves_its_jobs_to_another_model(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        client.post(
            "/api/settings/speech/providers",
            headers=SIDECAR,
            json={"kind": "openai", "apiKey": KEY},
        )
        client.put(
            "/api/settings/speech/roles",
            headers=SIDECAR,
            json={"roles": {"dictation": {"model": "openai/whisper-1", "stream": False}}},
        )
        removed = client.delete("/api/settings/speech/providers/openai", headers=SIDECAR)
        assert removed.status_code == 200
        assert roles(client)["dictation"]["model"] == "sensevoice/sensevoice-small"


def test_a_provider_without_its_key_is_not_used(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        client.post("/api/settings/speech/providers", headers=SIDECAR, json={"kind": "openai"})
        client.put(
            "/api/settings/speech/roles",
            headers=SIDECAR,
            json={"roles": {"dictation": {"model": "openai/whisper-1", "stream": False}}},
        )
        assert roles(client)["dictation"]["problem"] == "OpenAI needs an API key."
        with client.websocket_connect(
            "/api/recordings/live?purpose=dictation", headers=SIDECAR
        ) as socket:
            event = socket.receive_json()
        assert event["code"] == "not_configured" and "OpenAI needs an API key" in event["message"]


def test_a_qwen_key_saved_for_models_offers_qwen_transcription(tmp_path: Path) -> None:
    intl = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    with TestClient(create_app(settings_for(tmp_path))) as client:
        client.post(
            "/api/settings/providers",
            headers=SIDECAR,
            json={"kind": "qwen", "apiKey": KEY, "baseUrl": intl, "models": ["qwen3-max"]},
        )
        qwen = next(item for item in overview(client)["providers"] if item["kind"] == "qwen")
        # The key belongs to one region, so transcription follows its address.
        assert qwen["baseUrl"] == intl
        assert qwen["keyShared"] is True and qwen["keySet"] is False
        assert qwen["status"]["state"] == "configured"
        # Offered, not chosen: audio goes to the cloud only once a job picks it.
        assert roles(client)["dictation"]["model"] == "sensevoice/sensevoice-small"

        client.put(
            "/api/settings/speech/roles", headers=SIDECAR, json={"roles": {"dictation": QWEN_JOB}}
        )
        assert roles(client)["dictation"]["problem"] is None
        choice, _ = speech_registry.resolve(client.app.state.speech_registry, "dictation")
        assert choice is not None and choice.api_key == KEY and choice.base_url == intl


def test_a_qwen_transcription_provider_without_a_key_uses_the_models_key(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        client.post("/api/settings/speech/providers", headers=SIDECAR, json={"kind": "qwen"})
        client.put(
            "/api/settings/speech/roles", headers=SIDECAR, json={"roles": {"dictation": QWEN_JOB}}
        )
        assert roles(client)["dictation"]["problem"] == "Qwen needs an API key."

        client.post(
            "/api/settings/providers",
            headers=SIDECAR,
            json={"kind": "qwen", "apiKey": KEY, "models": ["qwen3-max"]},
        )
        assert roles(client)["dictation"]["problem"] is None
        choice, _ = speech_registry.resolve(client.app.state.speech_registry, "dictation")
        assert choice is not None and choice.api_key == KEY


def test_ask_dictation_sends_the_whole_take_at_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request)
        return httpx.Response(200, json={"text": " hello there "})

    monkeypatch.setattr(
        realtime.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    settings = settings_for(
        tmp_path,
        stt_provider="compatible",
        stt_base_url="https://asr.example/v1",
        sensevoice_segment_seconds=1,
    )
    # Four seconds at 24 kHz: a live job would have sent segments by now.
    audio = base64.b64encode(bytes(24_000 * 2 * 4)).decode()
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect(
            "/api/recordings/live?purpose=dictation", headers=SIDECAR
        ) as socket:
            assert socket.receive_json()["stream"] is False
            socket.send_json({"type": "input_audio_buffer.append", "audio": audio})
            assert posted == []
            socket.send_json({"type": "input_audio_buffer.commit"})
            event = socket.receive_json()
        assert event["transcript"] == "hello there"
        assert len(posted) == 1
        assert str(posted[0].url) == "https://asr.example/v1/audio/transcriptions"
