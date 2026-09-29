"""Language models: providers, jobs, one effort scale, and switching mid-conversation."""

import json
from pathlib import Path

import httpx
import openai
import pytest
from fake_models import FakeProvider, api_error, gateway, model
from fastapi.testclient import TestClient

from gunther import service_settings
from gunther.config import Settings
from gunther.llm import ModelError, Turn
from gunther.main import create_app
from gunther.mobile_gateway_runtime import MobileGatewayRuntime
from gunther.model_profiles import DIALECTS, profile_for
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
        "processing_worker_enabled": False,
        "auth_token": SIDECAR_TOKEN,
        "service_settings_file": tmp_path / "service-settings.json",
        **overrides,
    }
    return Settings(**values)


# One effort scale -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "model_id", "effort", "applied", "reasoning_effort", "body"),
    [
        ("deepseek", "deepseek-flash", "off", "off", None, {"thinking": {"type": "disabled"}}),
        ("deepseek", "deepseek-flash", "low", "low", "low", {"thinking": {"type": "enabled"}}),
        ("deepseek", "deepseek-flash", "medium", "high", "high", {"thinking": {"type": "enabled"}}),
        ("deepseek", "deepseek-v4-pro", "max", "max", "max", {"thinking": {"type": "enabled"}}),
        # GLM-5 and Kimi K3 always think: off becomes their lowest; medium is not sent.
        ("glm", "glm-5.3-flash", "off", "low", "low", {}),
        ("glm", "glm-5.3-flash", "medium", "high", "high", {}),
        ("kimi", "kimi-k3", "max", "max", "max", {}),
        # Kimi K2.6 only switches thinking; any level is "on", never silently off.
        ("kimi", "kimi-k2.6", "low", "high", None, {"thinking": {"type": "enabled"}}),
        ("kimi", "kimi-k2.6", "off", "off", None, {"thinking": {"type": "disabled"}}),
        (
            "qwen",
            "qwen3.8-max",
            "medium",
            "medium",
            None,
            {"enable_thinking": True, "thinking_budget": 8192},
        ),
        ("qwen", "qwen3.8-max", "off", "off", None, {"enable_thinking": False}),
        ("openai", "gpt-5.6", "max", "high", "high", {}),
        # No setting at all: nothing is sent.
        ("kimi", "kimi-k2.7-code", "max", None, None, {}),
        ("compatible", "llama-3", "high", None, None, {}),
    ],
)
def test_one_effort_scale_becomes_each_providers_own_parameters(
    kind, model_id, effort, applied, reasoning_effort, body
) -> None:
    fake = FakeProvider("Hello.")
    chosen = model(model_id, kind=kind)
    completion = gateway(fake, chosen).complete(
        chosen, system="Be brief.", messages="Hi", effort=effort
    )
    request = fake.requests[0]
    assert completion.effort_applied == applied
    assert request.get("reasoning_effort") == reasoning_effort
    assert request.get("extra_body", {}) == body
    assert request["stream"] is True and "temperature" not in request
    if applied and applied != effort:
        assert completion.notes and "setting; used" in completion.notes[0]


def test_levels_are_named_for_the_model() -> None:
    assert DIALECTS["thinking_switch"].name("high") == "On"
    assert profile_for("kimi", "kimi-k2.6").dialect.can_disable
    assert not profile_for("glm", "glm-5.3").dialect.can_disable
    assert profile_for("deepseek", "deepseek-flash").vision
    assert not profile_for("deepseek", "deepseek-v4-pro").vision
    # A model added by hand speaks what Settings says.
    custom = profile_for("compatible", "my-model", dialect="qwen_budget", vision=True)
    assert custom.dialect.id == "qwen_budget" and custom.vision


# Asking -----------------------------------------------------------------------------


def test_reasoning_is_kept_apart_and_json_is_checked() -> None:
    fake = FakeProvider(
        {"content": "not json at all", "reasoning": "Hmm."},
        {"content": 'Sure: ```json\n{"answer": "42"}\n```', "reasoning": "Better."},
    )
    chosen = model()

    from pydantic import BaseModel

    class Answer(BaseModel):
        answer: str

    parsed, completion = gateway(fake, chosen).complete_json(
        chosen, Answer, system="Answer.", prompt="Six times seven?", effort="low"
    )
    assert parsed.answer == "42" and completion.reasoning == "Better."
    first, second = fake.requests
    assert first["response_format"] == {"type": "json_object"}
    assert "JSON schema" in first["messages"][0]["content"]
    # The retry shows the model its own reply and what was wrong with it.
    assert second["messages"][-2] == {"role": "assistant", "content": "not json at all"}
    assert "not valid JSON" in second["messages"][-1]["content"]

    fake = FakeProvider("still not json")
    with pytest.raises(ModelError, match="shape Gunther could not read"):
        gateway(fake, chosen).complete_json(chosen, Answer, system="Answer.", prompt="?")


def test_a_setting_a_server_refuses_is_dropped_once_and_admitted() -> None:
    def no_effort(request):
        if "reasoning_effort" in request:
            raise api_error(openai.BadRequestError, 400, "Unknown parameter: reasoning_effort")

    fake = FakeProvider("Fine.", rule=no_effort)
    server = model("llama-3", kind="compatible", dialect="reasoning_effort")
    completion = gateway(fake, server).complete(server, system="", messages="Hi", effort="high")
    assert completion.text == "Fine." and completion.effort_applied is None
    assert completion.notes == ("llama-3 refused the effort setting; used its default.",)
    assert len(fake.requests) == 2 and "reasoning_effort" not in fake.requests[1]


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            api_error(openai.AuthenticationError, 401, "bad key"),
            "DeepSeek did not accept the API key.",
        ),
        (api_error(openai.NotFoundError, 404, "no model"), "DeepSeek has no model called"),
        (api_error(openai.RateLimitError, 429, "Insufficient Balance"), "busy or out of credit"),
        ({"content": "", "finish": "length"}, "ran out of room while thinking"),
        ({"content": " "}, "returned an empty answer"),
    ],
)
def test_failures_say_what_to_do(error, message) -> None:
    chosen = model()
    with pytest.raises(ModelError, match=message):
        gateway(FakeProvider(error), chosen).complete(chosen, system="", messages="Hi")


def test_a_model_that_cannot_see_is_not_sent_images() -> None:
    from gunther.llm import Image

    fake = FakeProvider("A cat.")
    blind = model("deepseek-v4-pro")
    completion = gateway(fake, blind).complete(
        blind, system="", messages="What is it?", images=[Image("image/png", b"png")]
    )
    assert fake.requests[0]["messages"][-1]["content"] == "What is it?"
    assert completion.notes == ("deepseek-v4-pro cannot see images; it read their text instead.",)

    seeing = model("deepseek-flash")
    gateway(fake, seeing).complete(
        seeing, system="", messages="What is it?", images=[Image("image/png", b"png")]
    )
    parts = fake.requests[1]["messages"][-1]["content"]
    assert parts[0] == {"type": "text", "text": "What is it?"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,cG5n"


def test_only_the_model_that_thought_gets_its_thinking_back() -> None:
    fake = FakeProvider("Next.")
    coder = model("kimi-k2.7-code", kind="kimi")
    flash = model("deepseek-flash")
    history = [
        Turn("user", "First?"),
        Turn("assistant", "One.", coder.ref, "Kimi's thoughts"),
        Turn("user", "Second?"),
        Turn("assistant", "Two.", flash.ref, "DeepSeek's thoughts"),
        Turn("user", "Third?"),
    ]
    models = gateway(fake, coder, flash)
    models.complete(coder, system="", messages=history)
    models.complete(flash, system="", messages=history)
    to_kimi, to_deepseek = (request["messages"] for request in fake.requests)
    assert to_kimi[2] == {
        "role": "assistant",
        "content": "One.",
        "reasoning_content": "Kimi's thoughts",
    }
    assert to_kimi[4] == {"role": "assistant", "content": "Two."}
    # DeepSeek ignores earlier reasoning without tools, so none is sent.
    assert all("reasoning_content" not in message for message in to_deepseek)
    # Kimi K2.7 Code truncates below 16k output tokens.
    assert fake.requests[0]["max_tokens"] == 16_000


# Settings → Models ------------------------------------------------------------------


@pytest.fixture
def listed_models(monkeypatch: pytest.MonkeyPatch):
    """Answer GET /models for connection tests; returns the requests seen."""

    seen: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return httpx.Response(401, json={"error": "no"})
        return httpx.Response(200, json={"data": [{"id": "kimi-k3"}, {"id": "kimi-k2.6"}]})

    monkeypatch.setattr(
        service_settings.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return seen


def overview(client: TestClient) -> dict:
    response = client.get("/api/settings/models", headers=SIDECAR)
    assert response.status_code == 200
    return response.json()


def test_an_existing_setup_becomes_a_deepseek_provider(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path, deepseek_api_key=KEY))) as client:
        models = overview(client)
        response = client.get("/api/settings/models", headers=SIDECAR)

    assert KEY not in response.text
    assert models["fromEnvironment"] is True
    [deepseek] = models["providers"]
    assert deepseek["name"] == "DeepSeek" and deepseek["keySource"] == "environment"
    assert deepseek["keyHint"] == "1234" and deepseek["status"]["state"] == "configured"
    flash, pro = deepseek["models"]
    assert (flash["id"], flash["label"], flash["vision"]) == ("deepseek-flash", "Flash", True)
    assert [level["id"] for level in flash["levels"]] == ["off", "low", "high", "max"]
    assert (pro["id"], pro["vision"]) == ("deepseek-v4-pro", False)
    roles = {role["id"]: role for role in models["roles"]}
    assert roles["analysis"]["model"] == "deepseek/deepseek-flash"
    assert (roles["analysis"]["effort"], roles["ask"]["effort"]) == ("low", "high")
    assert roles["photos"]["model"] == "deepseek/deepseek-flash"
    assert all(role["problem"] is None for role in roles.values())


def test_a_language_model_saved_by_the_first_settings_page_carries_over(tmp_path: Path) -> None:
    path = tmp_path / "service-settings.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "values": {
                    "llm_provider": "compatible",
                    "llm_api_key": KEY,
                    "llm_base_url": "http://10.0.0.9:8000/v1",
                    "llm_model": "qwen3-32b",
                    "stt_provider": "sensevoice",
                },
            }
        )
    )
    with TestClient(create_app(settings_for(tmp_path))) as client:
        [provider] = overview(client)["providers"]
        assert (provider["kind"], provider["baseUrl"]) == ("compatible", "http://10.0.0.9:8000/v1")
        assert [m["id"] for m in provider["models"]] == ["qwen3-32b"]
        client.put(
            "/api/settings/providers/compatible",
            headers=SIDECAR,
            json={"name": "Lab server"},
        )
    saved = json.loads(path.read_text())
    assert saved["version"] == 2 and "llm_api_key" not in saved["values"]
    assert saved["values"]["stt_provider"] == "sensevoice"
    assert saved["providers"][0]["name"] == "Lab server"
    assert saved["providers"][0]["apiKey"] == KEY


def test_providers_are_added_tested_and_given_jobs(tmp_path: Path, listed_models) -> None:
    with TestClient(create_app(settings_for(tmp_path, deepseek_api_key=KEY))) as client:
        # Try before adding: the models it offers come back.
        tried = client.post(
            "/api/settings/providers/test",
            headers=SIDECAR,
            json={"kind": "kimi", "apiKey": KEY},
        ).json()
        assert tried["ok"] and tried["available"] == ["kimi-k2.6", "kimi-k3"]
        assert str(listed_models[-1].url) == "https://api.moonshot.ai/v1/models"

        added = client.post(
            "/api/settings/providers",
            headers=SIDECAR,
            json={"kind": "kimi", "apiKey": KEY, "models": ["kimi-k3", "kimi-k2.6"]},
        )
        assert added.status_code == 201
        kimi = next(p for p in added.json()["providers"] if p["id"] == "kimi")
        assert [(m["label"], m["vision"]) for m in kimi["models"]] == [
            ("K3", True),
            ("kimi-k2.6", True),
        ]
        assert [level["label"] for level in kimi["models"][1]["levels"]] == ["Off", "On"]

        roles = client.put(
            "/api/settings/model-roles",
            headers=SIDECAR,
            json={"roles": {"ask": {"model": "kimi/kimi-k3", "effort": "max"}}},
        ).json()["roles"]
        assert next(r for r in roles if r["id"] == "ask")["model"] == "kimi/kimi-k3"
        health = client.get("/api/health", headers=SIDECAR).json()
        assert (health["askModel"], health["analysisModel"]) == ("Kimi · K3", "DeepSeek · Flash")

        # The model menu lists what is ready, with its levels, and no secrets.
        menu = client.get("/api/models", headers=SIDECAR)
        assert KEY not in menu.text
        assert menu.json()["default"] == {"model": "kimi/kimi-k3", "effort": "max"}
        assert [m["display"] for m in menu.json()["models"]] == [
            "DeepSeek · Flash",
            "DeepSeek · V4 Pro",
            "Kimi · K3",
            "Kimi · kimi-k2.6",
        ]

        # A failed test marks the provider until its key changes.
        failed = client.post(
            "/api/settings/providers/kimi/test", headers=SIDECAR, json={"apiKey": "sk-wrong-00001"}
        ).json()
        assert not failed["ok"] and failed["message"] == "Kimi did not accept this API key."
        assert (
            client.post("/api/settings/providers/kimi/test", headers=SIDECAR, json={}).json()[
                "overview"
            ]["providers"][1]["status"]["state"]
            == "configured"
        )

        # Removing a provider moves its jobs to a model that is still there.
        after = client.delete("/api/settings/providers/kimi", headers=SIDECAR).json()
        ask = next(r for r in after["roles"] if r["id"] == "ask")
        assert ask["model"] == "deepseek/deepseek-flash" and ask["effort"] == "max"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"kind": "somebody"}, "Choose a kind of provider."),
        ({"kind": "glm", "baseUrl": "open.bigmodel.cn"}, "Base URL: use a full address"),
        ({"kind": "glm", "apiKey": "two words"}, "API key: paste the key on its own"),
        ({"kind": "glm", "models": ["glm 5"]}, "Model: use the model's id"),
        ({"kind": "glm", "models": ["glm-5.3", "glm-5.3"]}, "A model is listed twice."),
        ({"kind": "glm", "models": [{"id": "x", "dialect": "telepathy"}]}, "unknown thinking"),
    ],
)
def test_providers_that_cannot_work_are_refused(tmp_path: Path, payload, message) -> None:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        response = client.post("/api/settings/providers", headers=SIDECAR, json=payload)
    assert response.status_code == 422 and message in response.json()["detail"]


def test_a_provider_without_a_key_is_listed_but_not_offered(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        [deepseek] = overview(client)["providers"]
        assert deepseek["status"] == {
            "state": "not_configured",
            "summary": "Needs an API key.",
            "check": None,
        }
        assert client.get("/api/models", headers=SIDECAR).json()["models"] == []
        roles = {role["id"]: role for role in overview(client)["roles"]}
        assert roles["ask"]["problem"] == "DeepSeek needs an API key."
        # A self-hosted server needs no key.
        added = client.post(
            "/api/settings/providers",
            headers=SIDECAR,
            json={
                "kind": "compatible",
                "baseUrl": "http://127.0.0.1:11434/v1",
                "models": ["qwen3:8b"],
            },
        ).json()
        assert added["providers"][1]["status"]["state"] == "configured"
        assert [m["ref"] for m in client.get("/api/models", headers=SIDECAR).json()["models"]] == [
            "compatible/qwen3:8b"
        ]


def test_a_paired_phone_may_list_models_but_never_set_them_up(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, deepseek_api_key=KEY)
    store = ServiceSettingsStore(settings.service_settings_file)
    sidecar_app = create_app(settings, service_settings=store)
    gateway_app = create_app(
        settings,
        allow_sidecar_auth=False,
        mobile_gateway=MobileGatewayRuntime(enabled=False),
        service_settings=store,
    )
    with TestClient(sidecar_app) as sidecar, TestClient(gateway_app) as phone_app:
        pairing = sidecar.post(
            "/api/pairing/sessions", headers=SIDECAR, json={"scopes": ["api:access"]}
        ).json()
        token = phone_app.post(
            "/api/pairing/exchange",
            json={
                "pairingId": pairing["pairingId"],
                "pairingCode": pairing["pairingCode"],
                "deviceName": "Phone",
                "platform": "ios",
            },
        ).json()["accessToken"]
        phone = {"Authorization": f"Bearer {token}"}
        menu = phone_app.get("/api/models", headers=phone)
        assert menu.status_code == 200 and KEY not in menu.text
        assert phone_app.get("/api/settings/models", headers=phone).status_code == 403
        assert (
            phone_app.post(
                "/api/settings/providers", headers=phone, json={"kind": "glm"}
            ).status_code
            == 403
        )
        # A job changed on the desktop reaches the phone's app at once.
        sidecar.put(
            "/api/settings/model-roles",
            headers=SIDECAR,
            json={"roles": {"ask": {"model": "deepseek/deepseek-v4-pro", "effort": "max"}}},
        )
        assert phone_app.get("/api/models", headers=phone).json()["default"] == {
            "model": "deepseek/deepseek-v4-pro",
            "effort": "max",
        }


# Switching models mid-conversation --------------------------------------------------


def test_a_conversation_can_switch_model_and_effort_at_any_turn(tmp_path: Path) -> None:
    def answer(request):
        # Each model cites the evidence it was given, and says who it is.
        return {
            "content": f"{request['model']}: CD3D marks T cells [1].",
            "reasoning": f"thoughts of {request['model']}",
        }

    fake = FakeProvider(answer)
    app = create_app(
        settings_for(tmp_path, deepseek_api_key=KEY), model_client_factory=fake.factory
    )
    with TestClient(app) as client:
        client.post(
            "/api/settings/providers",
            headers=SIDECAR,
            json={"kind": "kimi", "apiKey": KEY, "models": ["kimi-k2.7-code"]},
        )
        base_id = client.post(
            "/api/knowledge-bases",
            headers=SIDECAR,
            json={
                "title": "Cells",
                "question": "What marks cells?",
                "description": "Markers.",
            },
        ).json()["id"]
        client.post(
            "/api/sources",
            headers=SIDECAR,
            json={
                "title": "Markers",
                "kind": "note",
                "knowledgeBaseId": base_id,
                "content": "CD3D is a marker of T cells.",
            },
        )
        session_id = client.post(
            f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={}
        ).json()["id"]

        def ask(content: str, **choice) -> dict:
            response = client.post(
                f"/api/sessions/{session_id}/messages",
                headers=SIDECAR,
                json={"content": content, **choice},
            )
            assert response.status_code == 200, response.text
            return response.json()["assistantMessage"]

        # The Ask job's defaults when nothing is picked.
        first = ask("Which marker identifies T cells?")
        assert first["content"].startswith("deepseek-flash:")
        assert first["context"]["responderMode"] == "model"
        assert (first["context"]["modelLabel"], first["context"]["effortLabel"]) == (
            "DeepSeek · Flash",
            "High",
        )
        assert first["context"]["reasoning"] == "thoughts of deepseek-flash"

        second = ask(
            "Think harder: which marker identifies T cells?",
            model="kimi/kimi-k2.7-code",
            effort="max",
        )
        assert second["content"].startswith("kimi-k2.7-code:")
        assert second["context"]["model"] == "kimi/kimi-k2.7-code"
        assert second["context"]["effortLabel"] is None  # it has no setting

        third = ask(
            "Once more: which marker identifies T cells?", model="kimi/kimi-k2.7-code", effort="max"
        )
        assert third["context"]["model"] == "kimi/kimi-k2.7-code"
        # Kimi got its own earlier thinking back; DeepSeek's stayed out.
        # Reading the note asked the Analysis model too; only Ask requests count here.
        asked = [r for r in fake.requests if "Evidence:" in str(r["messages"][-1]["content"])]
        history = asked[2]["messages"]
        assistants = [m for m in history if m["role"] == "assistant"]
        assert "reasoning_content" not in assistants[0]
        assert assistants[1]["reasoning_content"] == "thoughts of kimi-k2.7-code"
        assert assistants[0]["content"].startswith("deepseek-flash:")

        fourth = ask(
            "Back to DeepSeek: which marker identifies T cells?",
            model="deepseek/deepseek-flash",
            effort="medium",
        )
        assert fourth["context"]["effortLabel"] == "High"
        assert fourth["context"]["notes"] == ["Flash has no Medium setting; used High."]
        asked = [r for r in fake.requests if "Evidence:" in str(r["messages"][-1]["content"])]
        assert asked[3]["reasoning_effort"] == "high"
        assert all("reasoning_content" not in m for m in asked[3]["messages"])

        # Every earlier answer keeps the label of the model that wrote it.
        messages = client.get(f"/api/sessions/{session_id}", headers=SIDECAR).json()["messages"]
        labels = [m["context"]["modelLabel"] for m in messages if m["role"] == "assistant"]
        assert labels == [
            "DeepSeek · Flash",
            "Kimi · kimi-k2.7-code",
            "Kimi · kimi-k2.7-code",
            "DeepSeek · Flash",
        ]

        gone = client.post(
            f"/api/sessions/{session_id}/messages",
            headers=SIDECAR,
            json={"content": "Hello?", "model": "glm/glm-5.3"},
        )
        assert gone.status_code == 400 and "not set up" in gone.json()["detail"]


def test_a_failing_model_says_so_and_the_quotes_stand_in(tmp_path: Path) -> None:
    fake = FakeProvider(api_error(openai.RateLimitError, 429, "Insufficient Balance"))
    app = create_app(
        settings_for(tmp_path, deepseek_api_key=KEY), model_client_factory=fake.factory
    )
    with TestClient(app) as client:
        base_id = client.post(
            "/api/knowledge-bases",
            headers=SIDECAR,
            json={
                "title": "Cells",
                "question": "What marks cells?",
                "description": "Markers.",
            },
        ).json()["id"]
        client.post(
            "/api/sources",
            headers=SIDECAR,
            json={
                "title": "Markers",
                "kind": "note",
                "knowledgeBaseId": base_id,
                "content": "CD3D is a marker of T cells.",
            },
        )
        session_id = client.post(
            f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={}
        ).json()["id"]
        reply = client.post(
            f"/api/sessions/{session_id}/messages",
            headers=SIDECAR,
            json={"content": "Which marker identifies T cells?"},
        ).json()["assistantMessage"]
    assert reply["context"]["responderMode"] == "local"
    assert reply["context"]["modelLabel"] == "DeepSeek · Flash"
    assert reply["context"]["modelError"] == (
        "DeepSeek is busy or out of credit (Insufficient Balance). Try again shortly."
    )
