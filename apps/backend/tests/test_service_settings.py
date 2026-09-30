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
        "tavily_api_key": None,
        "stt_provider": "auto",
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
        if request.url.path == "/usage":
            return httpx.Response(200, json={"key": {"usage": 3, "limit": 1000}})
        return httpx.Response(200, json={"data": [{"id": model} for model in answer["models"]]})

    monkeypatch.setattr(
        service_settings.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return seen, answer


def test_services_are_described_without_their_secrets(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path, tavily_api_key=KEY))) as client:
        listed = services(client)
    key = field(listed["web_search"], "tavily_api_key")
    assert key["value"] is None and key["isSet"] is True and key["hint"] == "1234"
    assert key["source"] == "environment"
    assert listed["web_search"]["status"]["state"] == "configured"
    assert listed["summaries"]["status"] == {
        "state": "not_configured",
        "summary": "Needs a model: set one up under Models.",
        "checkedAt": None,
        "check": None,
    }
    assert "transcription" not in listed
    assert "openai" not in listed


def test_saving_applies_at_once_and_survives_a_restart(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/health", headers=SIDECAR).json()
        assert health["webSearchMode"] == "not_configured"
        saved = client.put(
            "/api/settings/services/web_search",
            headers=SIDECAR,
            json={"values": {"tavily_api_key": f"  {KEY} ", "web_search_depth": "advanced"}},
        )
        assert saved.status_code == 200
        assert saved.json()["status"] == {
            "state": "configured",
            "summary": "Tavily · advanced search",
            "checkedAt": None,
            "check": None,
        }
        assert KEY not in saved.text
        assert client.get("/api/health", headers=SIDECAR).json()["webSearchMode"] == "tavily"
        assert client.app.state.settings.tavily_api_key == KEY

    stored = tmp_path / "service-settings.json"
    assert json.loads(stored.read_text())["values"]["web_search_depth"] == "advanced"
    if os.name != "nt":
        assert stored.stat().st_mode & 0o777 == 0o600

    with TestClient(create_app(settings)) as client:
        web = services(client)["web_search"]
        assert field(web, "web_search_depth")["value"] == "advanced"
        assert field(web, "web_search_depth")["source"] == "saved"
        assert client.get("/api/health", headers=SIDECAR).json()["webSearchMode"] == "tavily"

        # null goes back to the default; an empty key removes one set elsewhere.
        client.put(
            "/api/settings/services/web_search",
            headers=SIDECAR,
            json={"values": {"web_search_depth": None, "tavily_api_key": ""}},
        )
        web = services(client)["web_search"]
        assert field(web, "web_search_depth")["value"] == "basic"
        assert field(web, "tavily_api_key")["isSet"] is False
        health = client.get("/api/health", headers=SIDECAR).json()
        assert health["webSearchMode"] == "not_configured"


@pytest.mark.parametrize(
    ("service", "values", "message"),
    [
        ("web_search", {"tavily_api_key": "tvly two"}, "Tavily API key: Paste the key on its own"),
        ("web_search", {"web_search_max_results": 40}, "Use a number from 1 to 10"),
        ("summaries", {"ai_summary_images": "yes"}, "Turn it on or off"),
        ("web_search", {"stt_provider": "auto"}, "Unknown setting"),
        ("web_search", {"auth_token": "x"}, "Unknown setting"),
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
            "/api/settings/services/web_search/test",
            headers=SIDECAR,
            json={"values": {"tavily_api_key": KEY}},
        ).json()
        assert tested["ok"] is True
        assert tested["message"] == "Connected, and the key works. 3 searches used so far."
        assert str(seen[-1].url) == "https://api.tavily.com/usage"
        assert seen[-1].headers["authorization"] == f"Bearer {KEY}"
        # Testing saves nothing, and an unsaved draft's result does not mark the saved one.
        assert client.app.state.settings.tavily_api_key is None
        assert tested["service"]["status"]["state"] == "not_configured"

        client.put(
            "/api/settings/services/web_search",
            headers=SIDECAR,
            json={"values": {"tavily_api_key": KEY}},
        )
        answer["status"] = 401
        tested = client.post(
            "/api/settings/services/web_search/test", headers=SIDECAR, json={"values": {}}
        ).json()
        assert tested["ok"] is False
        assert tested["message"] == "Tavily did not accept the API key."
        assert tested["service"]["status"]["state"] == "error"
        assert tested["service"]["status"]["summary"] == tested["message"]

        # A new key retires the failed check.
        answer["status"] = 200
        client.put(
            "/api/settings/services/web_search",
            headers=SIDECAR,
            json={"values": {"tavily_api_key": "tvly-another-000000005678"}},
        )
        assert services(client)["web_search"]["status"]["state"] == "configured"


def test_a_service_that_cannot_be_tested_says_so(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        assert (
            client.post(
                "/api/settings/services/summaries/test", headers=SIDECAR, json={"values": {}}
            ).status_code
            == 409
        )


def test_a_paired_phone_cannot_read_or_change_service_settings(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
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
                "/api/settings/services/web_search", headers=phone, json={"values": {}}
            ).status_code
            == 403
        )

        # A change saved on the desktop reaches the phone's app too.
        sidecar.put(
            "/api/settings/services/web_search",
            headers=SIDECAR,
            json={"values": {"tavily_api_key": KEY}},
        )
        assert gateway.app.state.settings.tavily_api_key == KEY


def test_an_unreadable_or_outdated_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "service-settings.json"
    path.write_text("{not json")
    assert ServiceSettingsStore(path).values() == {}
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "values": {
                    "stt_provider": "sensevoice",
                    "openai_api_key": "sk-old",
                    "retired_setting": 1,
                },
            }
        )
    )
    assert ServiceSettingsStore(path).values() == {"stt_provider": "sensevoice"}


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
