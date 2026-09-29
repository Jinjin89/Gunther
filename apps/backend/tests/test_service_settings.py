import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from gunther import service_settings
from gunther.config import Settings
from gunther.main import create_app
from gunther.mobile_gateway_runtime import MobileGatewayRuntime
from gunther.service_settings import ServiceSettingsStore

SIDECAR_TOKEN = "sidecar-token-with-at-least-256-bits-000000000000000000000000"
SIDECAR = {"X-Gunther-Token": SIDECAR_TOKEN}
KEY = "sk-test-0000000000001234"


def settings_for(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        "assets_dir": tmp_path / "assets",
        "recordings_dir": tmp_path / "recordings",
        "seed_demo": False,
        "deepseek_api_key": None,
        "openai_api_key": None,
        "stt_provider": "openai",
        "auth_token": SIDECAR_TOKEN,
        "service_settings_file": tmp_path / "service-settings.json",
        **overrides,
    }
    return Settings(**values)


def services(client: TestClient) -> dict[str, dict]:
    response = client.get("/api/settings/services", headers=SIDECAR)
    assert response.status_code == 200
    return {service["id"]: service for service in response.json()["services"]}


def field(service: dict, key: str) -> dict:
    return next(item for item in service["fields"] if item["key"] == key)


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch):
    """Answer connection tests without the network: returns the requests seen."""

    seen: list[httpx.Request] = []
    answer = {"status": 200, "models": ["deepseek-v4-flash", "gpt-5.6"]}
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if answer["status"] != 200:
            return httpx.Response(answer["status"], json={"error": "no"})
        return httpx.Response(200, json={"data": [{"id": model} for model in answer["models"]]})

    monkeypatch.setattr(
        service_settings.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return seen, answer


def test_services_are_described_without_their_secrets(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path, openai_api_key=KEY))) as client:
        listed = services(client)
        response = client.get("/api/settings/services", headers=SIDECAR)

    # Language models have their own section (see test_models).
    assert list(listed) == ["openai", "transcription", "summaries"]
    assert KEY not in response.text
    key = field(listed["openai"], "openai_api_key")
    assert key["value"] is None and key["isSet"] is True and key["hint"] == "1234"
    assert key["source"] == "environment"
    assert listed["openai"]["status"]["state"] == "configured"
    assert listed["summaries"]["status"] == {
        "state": "not_configured",
        "summary": "Needs a model: set one up under Models.",
        "checkedAt": None,
        "check": None,
    }
    assert field(listed["transcription"], "sensevoice_url")["shownWhen"] == {
        "key": "stt_provider",
        "values": ["auto", "sensevoice"],
    }


def test_saving_applies_at_once_and_survives_a_restart(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/health", headers=SIDECAR).json()
        assert health["webSearchMode"] == "not_configured"
        saved = client.put(
            "/api/settings/services/openai",
            headers=SIDECAR,
            json={"values": {"openai_api_key": f"  {KEY} ", "openai_web_search_model": "gpt-9"}},
        )
        assert saved.status_code == 200
        assert saved.json()["status"] == {
            "state": "configured",
            "summary": "Web search · gpt-9",
            "checkedAt": None,
            "check": None,
        }
        assert KEY not in saved.text
        assert client.get("/api/health", headers=SIDECAR).json()["webSearchMode"] == "openai"
        assert client.app.state.settings.openai_api_key == KEY

    stored = tmp_path / "service-settings.json"
    assert json.loads(stored.read_text())["values"]["openai_web_search_model"] == "gpt-9"
    if os.name != "nt":
        assert stored.stat().st_mode & 0o777 == 0o600

    with TestClient(create_app(settings)) as client:
        openai_service = services(client)["openai"]
        assert field(openai_service, "openai_web_search_model")["value"] == "gpt-9"
        assert field(openai_service, "openai_web_search_model")["source"] == "saved"
        assert client.get("/api/health", headers=SIDECAR).json()["webSearchMode"] == "openai"

        # null goes back to the default; an empty key removes one set elsewhere.
        client.put(
            "/api/settings/services/openai",
            headers=SIDECAR,
            json={"values": {"openai_web_search_model": None, "openai_api_key": ""}},
        )
        openai_service = services(client)["openai"]
        assert field(openai_service, "openai_web_search_model")["value"] == "gpt-5.6"
        assert field(openai_service, "openai_api_key")["isSet"] is False
        health = client.get("/api/health", headers=SIDECAR).json()
        assert health["webSearchMode"] == "not_configured"


def test_saved_values_win_over_the_environment(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, openai_api_key=KEY, stt_provider="auto")
    with TestClient(create_app(settings)) as client:
        client.put(
            "/api/settings/services/transcription",
            headers=SIDECAR,
            json={"values": {"stt_provider": "sensevoice", "sensevoice_url": "http://10.0.0.5:8765/"}},
        )
        transcription = services(client)["transcription"]
        assert field(transcription, "stt_provider")["value"] == "sensevoice"
        assert field(transcription, "sensevoice_url")["value"] == "http://10.0.0.5:8765"
        assert transcription["status"]["summary"] == "SenseVoice at 10.0.0.5:8765"
        assert client.app.state.settings.sensevoice_url == "http://10.0.0.5:8765"


@pytest.mark.parametrize(
    ("service", "values", "message"),
    [
        ("openai", {"openai_web_search_model": "two words"}, "Web search model: Use the"),
        ("openai", {"openai_api_key": "sk two"}, "API key: Paste the key on its own"),
        ("transcription", {"sensevoice_url": "127.0.0.1:8765"}, "Use a full address"),
        ("transcription", {"stt_provider": "somebody"}, "Engine: Choose one of the options"),
        ("transcription", {"sensevoice_segment_seconds": 40}, "Use a number from 1 to 10"),
        ("transcription", {"openai_transcription_languages": "english!"}, "Languages: Use codes"),
        ("summaries", {"ai_summary_images": "yes"}, "Turn it on or off"),
        ("openai", {"stt_provider": "openai"}, "Unknown setting"),
        ("openai", {"auth_token": "x"}, "Unknown setting"),
    ],
)
def test_values_that_cannot_work_are_refused(
    tmp_path: Path, service: str, values: dict, message: str
) -> None:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        response = client.put(
            f"/api/settings/services/{service}", headers=SIDECAR, json={"values": values}
        )
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert not (tmp_path / "service-settings.json").exists()


def test_a_connection_test_uses_the_values_on_screen(tmp_path: Path, fake_api) -> None:
    seen, answer = fake_api
    with TestClient(create_app(settings_for(tmp_path))) as client:
        tested = client.post(
            "/api/settings/services/openai/test",
            headers=SIDECAR,
            json={"values": {"openai_api_key": KEY}},
        ).json()
        assert tested["ok"] is True
        assert tested["message"] == "Connected. gpt-5.6 is available."
        assert str(seen[-1].url) == "https://api.openai.com/v1/models"
        assert seen[-1].headers["authorization"] == f"Bearer {KEY}"
        # Testing saves nothing, and an unsaved draft's result does not mark the saved one.
        assert client.app.state.settings.openai_api_key is None
        assert tested["service"]["status"]["state"] == "not_configured"

        client.put(
            "/api/settings/services/openai",
            headers=SIDECAR,
            json={"values": {"openai_api_key": KEY, "openai_web_search_model": "gpt-9"}},
        )
        tested = client.post(
            "/api/settings/services/openai/test", headers=SIDECAR, json={"values": {}}
        ).json()
        assert tested["ok"] is True
        assert tested["warning"] == "gpt-9 is not in this account's model list."

        answer["status"] = 401
        tested = client.post(
            "/api/settings/services/openai/test", headers=SIDECAR, json={"values": {}}
        ).json()
        assert tested["ok"] is False
        assert tested["message"] == "OpenAI did not accept this API key."
        assert tested["service"]["status"]["state"] == "error"
        assert tested["service"]["status"]["summary"] == tested["message"]

        # A new key retires the failed check.
        answer["status"] = 200
        client.put(
            "/api/settings/services/openai",
            headers=SIDECAR,
            json={"values": {"openai_api_key": "sk-another-000000005678"}},
        )
        assert services(client)["openai"]["status"]["state"] == "configured"


def test_transcription_test_explains_what_is_missing(tmp_path: Path, monkeypatch) -> None:
    async def not_running(url: str, timeout: float = 1.2) -> None:
        return None

    monkeypatch.setattr(service_settings, "sensevoice_health", not_running)
    with TestClient(create_app(settings_for(tmp_path, stt_provider="auto"))) as client:
        tested = client.post(
            "/api/settings/services/transcription/test", headers=SIDECAR, json={"values": {}}
        ).json()
        assert tested["ok"] is False
        assert "OpenAI has no API key" in tested["message"]
        tested = client.post(
            "/api/settings/services/transcription/test",
            headers=SIDECAR,
            json={"values": {"stt_provider": "sensevoice"}},
        ).json()
        assert tested["message"] == (
            "SenseVoice is not answering at http://127.0.0.1:8765. Is it running?"
        )
        openai_only = client.put(
            "/api/settings/services/transcription",
            headers=SIDECAR,
            json={"values": {"stt_provider": "openai"}},
        ).json()
        assert openai_only["status"]["state"] == "error"
        assert client.post(
            "/api/settings/services/summaries/test", headers=SIDECAR, json={"values": {}}
        ).status_code == 409


def test_a_paired_phone_cannot_read_or_change_service_settings(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, openai_api_key=KEY)
    store = ServiceSettingsStore(settings.service_settings_file)
    sidecar_app = create_app(settings, service_settings=store)
    gateway_app = create_app(
        settings,
        allow_sidecar_auth=False,
        mobile_gateway=MobileGatewayRuntime(enabled=False),
        service_settings=store,
    )
    with TestClient(sidecar_app) as sidecar, TestClient(gateway_app) as gateway:
        pairing = sidecar.post(
            "/api/pairing/sessions", headers=SIDECAR, json={"scopes": ["api:access"]}
        ).json()
        token = gateway.post(
            "/api/pairing/exchange",
            json={
                "pairingId": pairing["pairingId"],
                "pairingCode": pairing["pairingCode"],
                "deviceName": "Phone",
                "platform": "ios",
            },
        ).json()["accessToken"]
        phone = {"Authorization": f"Bearer {token}"}
        assert gateway.get("/api/settings/services", headers=phone).status_code == 403
        assert (
            gateway.put(
                "/api/settings/services/openai", headers=phone, json={"values": {}}
            ).status_code
            == 403
        )

        # A change saved on the desktop reaches the phone's app too.
        sidecar.put(
            "/api/settings/services/transcription",
            headers=SIDECAR,
            json={"values": {"stt_provider": "openai"}},
        )
        assert gateway.app.state.settings.stt_provider == "openai"


def test_an_unreadable_or_outdated_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "service-settings.json"
    path.write_text("{not json")
    assert ServiceSettingsStore(path).values() == {}
    path.write_text(
        json.dumps({"version": 1, "values": {"stt_provider": "openai", "sensevoice_url": "ftp://x",
                                             "retired_setting": 1}})
    )
    assert ServiceSettingsStore(path).values() == {"stt_provider": "openai"}


def test_deepseek_names_from_earlier_versions_still_configure_the_language_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-old")
    settings = Settings()
    assert settings.llm_api_key == KEY
    assert settings.llm_model == "deepseek-old"
    assert Settings.model_fields["llm_model"].default == "deepseek-flash"
    monkeypatch.setenv("LLM_MODEL", "new-model")
    assert Settings().llm_model == "new-model"
