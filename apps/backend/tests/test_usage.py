"""Token accounting uses fake providers and local databases only."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace

import httpx
import openai
import pytest
from fake_models import FakeProvider, api_error, model
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import MetaData, inspect, select
from test_models import SIDECAR, settings_for

from gunther import trace, usage, usage_api
from gunther.database import Base, create_database_engine, create_session_factory, session_scope
from gunther.digest import DigestRequest, ModelDigestWriter, Passage
from gunther.extraction import ModelExtractor
from gunther.llm import REQUEST_ADMISSION, ModelError, ModelGateway, _openai_client, _usage
from gunther.main import create_app
from gunther.migrations import MIGRATIONS, get_schema_version, run_migrations
from gunther.models import ModelUsage
from gunther.studio.pipeline import _in_context

COUNTS = {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}
NORMALIZED = {"input": 100, "cached": None, "output": 40, "reasoning": None}


class CountingProvider(FakeProvider):
    def __init__(self, *replies, counts=None, broken=False, **kwargs):
        super().__init__(*replies, **kwargs)
        self.counts = counts
        self.broken = broken

    def create(self, **request):
        chunks = super().create(**request)
        broken = self.broken and len(self.requests) == 1

        def stream():
            yield from chunks
            if self.counts is not None:
                yield SimpleNamespace(choices=[], usage=self.counts)
            if broken:
                raise httpx.ReadError("stream broke")

        return stream()


def gateway(fake, recorder=None, *models):
    chosen = models or (model(),)
    return ModelGateway(
        chosen,
        {"analysis": (chosen[0].ref, "low"), "ask": (chosen[0].ref, "low")},
        client_factory=fake.factory,
        recorder=recorder,
    )


def ask(models, chosen=None, **kwargs):
    return models.complete(chosen or models.models[0], system="", messages="Hello", **kwargs)


@pytest.mark.parametrize(
    "reported,expected",
    [
        ({**COUNTS, "prompt_cache_hit_tokens": 70}, {**NORMALIZED, "cached": 70}),
        (
            SimpleNamespace(
                **COUNTS,
                prompt_tokens_details=SimpleNamespace(cached_tokens=25),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=30),
            ),
            {**NORMALIZED, "cached": 25, "reasoning": 30},
        ),
        ({**COUNTS, "cached_tokens": 12}, {**NORMALIZED, "cached": 12}),
        (
            {**COUNTS, "prompt_cache_hit_tokens": 0, "cached_tokens": 12},
            {**NORMALIZED, "cached": 0},
        ),
        (None, dict.fromkeys(NORMALIZED)),
        ({"prompt_tokens": True, "completion_tokens": -1}, dict.fromkeys(NORMALIZED)),
    ],
)
def test_provider_counts_are_normalized_without_estimates(reported, expected):
    assert _usage(reported) == expected


def test_refused_stream_options_are_remembered_per_provider_and_model():
    def refuse(request):
        if "stream_options" in request:
            raise api_error(openai.BadRequestError, 400, "Unknown parameter: stream_options")

    fake = CountingProvider("OK", rule=refuse, counts=COUNTS)
    records = []
    first, second = model(), model(provider_id="another")
    models = gateway(fake, records.append, first, second)
    for chosen in (first, first, second):
        ask(models, chosen, effort="low", json_mode=True)
    assert ["stream_options" in r for r in fake.requests] == [True, False, False, True, False]
    assert all("reasoning_effort" in r and "response_format" in r for r in fake.requests)
    assert [r["status"] for r in records] == ["error", "ok", "ok", "error", "ok"]
    assert len({r["id"] for r in records}) == 5


def test_effort_and_json_fallbacks_each_count_and_keep_usage():
    def refuse(request):
        if "reasoning_effort" in request or "extra_body" in request:
            raise api_error(openai.BadRequestError, 400, "Unsupported effort")
        if "response_format" in request:
            raise api_error(openai.BadRequestError, 400, "Unsupported response_format")

    fake = CountingProvider("OK", rule=refuse, counts=COUNTS)
    records = []
    ask(gateway(fake, records.append), effort="low", json_mode=True)
    assert [r["status"] for r in records] == ["error", "error", "ok"]
    assert all(r["stream_options"] == {"include_usage": True} for r in fake.requests)


@pytest.mark.parametrize("succeeds", [False, True])
def test_generic_bad_request_remembers_usage_fallback_only_after_success(succeeds):
    error = api_error(openai.BadRequestError, 400, "Invalid request")
    fake = FakeProvider(error, "OK" if succeeds else error)
    records = []
    models = gateway(fake, records.append)
    if succeeds:
        assert ask(models).text == "OK"
    else:
        with pytest.raises(ModelError):
            ask(models)
    assert ["stream_options" in request for request in fake.requests] == [True, False]
    assert (models.models[0].ref in models._without_usage) == succeeds
    assert len(records) == 2
    fake.replies = ["OK"]
    ask(models)
    assert ("stream_options" in fake.requests[-1]) != succeeds


@pytest.mark.parametrize("setting", ["stream_options", "include_usage"])
def test_named_usage_refusal_is_remembered_even_if_retry_fails(setting):
    fake = FakeProvider(
        api_error(openai.BadRequestError, 400, f"Unsupported {setting}"),
        api_error(openai.BadRequestError, 400, "Invalid request"),
    )
    models = gateway(fake)
    with pytest.raises(ModelError):
        ask(models)
    assert models.models[0].ref in models._without_usage


def test_empty_and_json_repair_retries_and_trace_keep_every_request():
    fake = CountingProvider(
        "",
        "not json",
        '{"answer": "yes"}',
        counts={
            **COUNTS,
            "prompt_cache_hit_tokens": 60,
            "completion_tokens_details": {"reasoning_tokens": 20},
        },
    )
    records = []
    models = gateway(fake, records.append)
    kept = trace.Trace()

    class Answer(BaseModel):
        answer: str

    with usage.job("ask", ref="conversation"), trace.tracing(kept):
        answer, _ = models.complete_json(models.models[0], Answer, system="", prompt="?")
    assert answer.answer == "yes"
    assert [r["status"] for r in records] == ["error", "ok", "ok"]
    assert all(r["job"] == "ask" and r["ref"] == "conversation" for r in records)
    assert sum(r["input"] for r in records) == 300
    assert all(r["ms"] >= 0 and r["at"].tzinfo is None for r in records)
    attempts = [r for step in kept.steps for r in step["usageAttempts"]]
    assert [r["id"] for r in attempts] == [r["id"] for r in records]
    assert kept.steps[-1]["usage"]["cached"] == 60
    assert kept.steps[-1]["usage"]["reasoning"] == 20


@pytest.mark.parametrize("streaming", [False, True])
def test_broken_stream_preserves_reported_counts_and_counts_retry(streaming):
    fake = CountingProvider("OK", counts=COUNTS, broken=True)
    records = []
    models = gateway(fake, records.append)
    if streaming:
        with pytest.raises(ModelError, match="connection"):
            ask(models, on_text=lambda text: None)
    else:
        assert ask(models).text == "OK"
    assert [r["status"] for r in records] == (["error"] if streaming else ["error", "ok"])
    assert all(r["input"] == 100 for r in records)


def test_connection_failure_is_recorded_without_a_gateway_retry():
    fake = CountingProvider(
        openai.APIConnectionError(request=httpx.Request("POST", "https://fake"))
    )
    records = []
    with pytest.raises(ModelError, match="reach"):
        ask(gateway(fake, records.append))
    assert len(fake.requests) == len(records) == 1
    assert all(r["status"] == "error" and r["input"] is None for r in records)


def test_unreported_and_failed_requests_do_not_invent_counts():
    records = []
    fake = FakeProvider("OK", api_error(openai.AuthenticationError, 401, "bad key"))
    models = gateway(fake, records.append)
    ask(models)
    with pytest.raises(ModelError):
        ask(models)
    assert [r["status"] for r in records] == ["unreported", "error"]
    assert all(r["job"] == "other" and r["input"] is None for r in records)


def test_rejected_admission_is_not_a_request():
    records = []
    fake = FakeProvider("")
    calls = 0

    def admit():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ModelError("budget")

    token = REQUEST_ADMISSION.set(admit)
    try:
        with pytest.raises(ModelError, match="budget"):
            ask(gateway(fake, records.append))
    finally:
        REQUEST_ADMISSION.reset(token)
    assert len(fake.requests) == len(records) == 1


def test_recording_failure_never_fails_an_answer(caplog):
    def fail(_entry):
        raise RuntimeError("database unavailable")

    assert ask(gateway(FakeProvider("OK"), fail)).text == "OK"
    assert "Could not record model usage" in caplog.text


def test_nested_jobs_restore_on_error_and_remain_independent_in_workers():
    assert usage.current() == {"job": "other", "detail": None, "ref": None}
    with usage.job("outputs", "slides", ref="artifact"):
        with pytest.raises(ValueError), usage.job(detail="title"):
            assert usage.current() == {"job": "outputs", "detail": "title", "ref": "artifact"}
            raise ValueError()
        assert usage.current()["detail"] == "slides"
        with usage.job(ref=None):
            assert usage.current()["ref"] is None
        with ThreadPoolExecutor(2) as pool:

            def worker(detail):
                with usage.job(detail=detail):
                    return usage.current()

            records = list(pool.map(_in_context(worker), ["one", "two"]))
        assert records == [
            {"job": "outputs", "detail": d, "ref": "artifact"} for d in ("one", "two")
        ]
        assert usage.current()["detail"] == "slides"
    assert usage.current()["job"] == "other"

    async def concurrent():
        async def work(name):
            with usage.job(name):
                await asyncio.sleep(0)
                return usage.current()["job"]

        return await asyncio.gather(work("ask"), work("photos"))

    assert asyncio.run(concurrent()) == ["ask", "photos"]


def test_analysis_and_photo_entry_points_tag_requests(tmp_path):
    records = []
    fake = FakeProvider(
        '{"assertions": []}', '{"title": "Photo", "overview": "A scene", "key_points": []}'
    )
    models = gateway(fake, records.append)
    with usage.job("outputs", ref="source"):
        ModelExtractor(models).extract("Title", "Body")
    image = tmp_path / "photo.png"
    image.write_bytes(b"fake image for a fake provider")
    writer = ModelDigestWriter(models, (model(), "low"), (model(), "low"))
    writer.write(
        DigestRequest(
            "photo", "rev", "Photo", "image", [], image_path=image, image_type="image/png"
        )
    )
    writer.write(DigestRequest("text", "rev", "Text", "note", [Passage(1, "block", "Text", "p1")]))
    assert [(r["job"], r["detail"], r["ref"]) for r in records] == [
        ("analysis", "extraction", "source"),
        ("photos", "summary", "photo"),
        ("analysis", "summary", "text"),
    ]


@pytest.fixture
def client(tmp_path):
    app = create_app(settings_for(tmp_path), model_client_factory=FakeProvider("OK").factory)
    with TestClient(app, headers=SIDECAR) as client:
        yield client


def test_app_recorder_survives_settings_refresh_and_uses_independent_session(client):
    app = client.app
    sessions = app.state.knowledge_service.sessions
    # Configure the real app gateway, backed only by the injected fake client.
    from gunther.main import _connect_models

    settings = app.state.settings.model_copy(update={"llm_api_key": "fake"})
    for _ in range(2):
        _connect_models(
            app,
            settings,
            app.state.service_settings,
            app.state.knowledge_service,
            app.state.processing_worker,
        )
        with session_scope(sessions) as outer:
            ask(app.state.models)
            outer.rollback()
    assert client.get("/api/usage/summary?range=all").json()["totals"]["requests"] == 2


@pytest.mark.parametrize("tz_offset", [0, 480, -480, 840, -840])
def test_summary_ranges_grouping_recent_and_clear(client, monkeypatch, tz_offset):
    now = datetime(2026, 10, 23, 12, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(usage_api, "datetime", Clock)
    sessions = client.app.state.knowledge_service.sessions
    offset = timedelta(minutes=tz_offset)
    midnight = (now.replace(tzinfo=None) + offset).replace(hour=0) - offset
    dates = [
        midnight,
        midnight - timedelta(microseconds=1),
        midnight - timedelta(days=6),
        midnight - timedelta(days=6, microseconds=1),
        midnight - timedelta(days=29),
        midnight - timedelta(days=29, microseconds=1),
    ]
    for i, at in enumerate(dates):
        usage.record(
            sessions,
            {
                "id": str(i),
                "at": at,
                "provider_id": "a" if i % 2 else "b",
                "model_id": "same-name",
                "job": "ask" if i % 2 else "analysis",
                "detail": "brief" if i % 2 == 0 else None,
                "ref": None,
                "input": 100 if i else None,
                "cached": 20 if i else None,
                "output": 40 if i else None,
                "reasoning": 10 if i else None,
                "status": "error" if i == 1 else "ok" if i else "unreported",
                "ms": i,
            },
        )
    for range, requests in (("today", 1), ("7d", 3), ("30d", 5), ("all", 6)):
        response = client.get(f"/api/usage/summary?range={range}&tzOffset={tz_offset}")
        assert response.status_code == 200
        result = response.json()
        assert result["totals"] == {
            "input": 100 * (requests - 1) if requests > 1 else None,
            "cached": 20 * (requests - 1) if requests > 1 else None,
            "output": 40 * (requests - 1) if requests > 1 else None,
            "reasoning": 10 * (requests - 1) if requests > 1 else None,
            "requests": requests,
        }
        for group in ("byDay", "byModel", "byJob"):
            assert sum(row["requests"] for row in result[group]) == requests
            for count in ("input", "cached", "output", "reasoning"):
                reported = [row[count] for row in result[group] if row[count] is not None]
                assert (sum(reported) if reported else None) == result["totals"][count]
            if range == "today":
                assert all(row["reasoning"] is None for row in result[group])
    assert [row["day"] for row in result["byDay"]] == sorted(
        {(at + offset).date().isoformat() for at in dates}
    )
    assert len(result["byModel"]) == 2
    assert {r["providerId"] for r in result["byModel"]} == {"a", "b"}
    rows = client.get("/api/usage/recent?limit=2").json()["requests"]
    assert [r["id"] for r in rows] == ["0", "1"]
    assert rows[0]["input"] is None and rows[0]["at"].endswith("+00:00")
    assert rows[1]["status"] == "error"
    assert client.get("/api/usage/summary?range=invalid").status_code == 422
    assert client.get("/api/usage/recent?limit=0").status_code == 422
    assert client.get("/api/usage/recent?limit=201").status_code == 422
    assert client.delete("/api/usage").status_code == 204
    assert client.get("/api/usage/recent").json() == {"requests": []}
    assert client.get("/api/usage/summary").json() == {
        "totals": {"input": None, "cached": None, "output": None, "reasoning": None, "requests": 0},
        "byDay": [],
        "byModel": [],
        "byJob": [],
    }


@pytest.mark.parametrize("reasoning", [None, 0, 10])
def test_summary_preserves_unreported_reasoning_for_known_token_counts(client, reasoning):
    sessions = client.app.state.knowledge_service.sessions
    at = datetime.now(UTC).replace(tzinfo=None)
    for i, reported in enumerate((None, reasoning)):
        usage.record(
            sessions,
            {
                "id": f"reasoning-{i}",
                "at": at,
                "provider_id": "provider",
                "model_id": "model",
                "job": "ask",
                "detail": None,
                "ref": None,
                "input": 100,
                "cached": 0,
                "output": 40,
                "reasoning": reported,
                "status": "ok",
                "ms": 1,
            },
        )
    result = client.get("/api/usage/summary?range=all").json()
    expected = {"input": 200, "cached": 0, "output": 80, "reasoning": reasoning, "requests": 2}
    assert result["totals"] == expected
    for group in ("byDay", "byModel", "byJob"):
        assert len(result[group]) == 1
        assert {key: result[group][0][key] for key in expected} == expected


def test_summary_groups_evening_utc_requests_into_next_local_day(client, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 23, 23, tzinfo=tz)

    monkeypatch.setattr(usage_api, "datetime", Clock)
    sessions = client.app.state.knowledge_service.sessions
    for hour in (15, 20):
        usage.record(
            sessions,
            {
                "id": str(hour),
                "at": datetime(2026, 10, 23, hour),
                "provider_id": "deepseek",
                "model_id": "deepseek-flash",
                "job": "ask",
                "status": "ok",
                "ms": 1,
                **NORMALIZED,
            },
        )
    local = client.get("/api/usage/summary?range=today&tzOffset=480").json()
    assert local["totals"]["requests"] == 1
    assert local["byDay"] == [{"day": "2026-10-24", **local["totals"]}]
    utc = client.get("/api/usage/summary?range=today").json()
    assert utc["totals"]["requests"] == 2
    assert utc["byDay"] == [{"day": "2026-10-23", **utc["totals"]}]
    all_days = client.get("/api/usage/summary?range=all&tzOffset=480").json()["byDay"]
    assert [row["day"] for row in all_days] == ["2026-10-23", "2026-10-24"]
    for invalid in ("-841", "841", "1.5", "invalid"):
        assert client.get(f"/api/usage/summary?tzOffset={invalid}").status_code == 422


def test_migration_29_upgrades_existing_database_idempotently(tmp_path):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'usage.sqlite'}")
    try:
        legacy = MetaData()
        for table in Base.metadata.sorted_tables:
            if table.name != "model_usage":
                table.to_metadata(legacy)
        run_migrations(engine, legacy, MIGRATIONS[:28])
        assert not inspect(engine).has_table("model_usage")
        run_migrations(engine, Base.metadata)
        assert get_schema_version(engine) == 29
        assert {i["name"] for i in inspect(engine).get_indexes("model_usage")} == {
            "ix_model_usage_at"
        }
        sessions = create_session_factory(engine)
        ask(gateway(FakeProvider("OK"), partial(usage.record, sessions)))
        run_migrations(engine, Base.metadata)
        with session_scope(sessions) as session:
            assert len(session.scalars(select(ModelUsage)).all()) == 1
    finally:
        engine.dispose()


def test_database_failure_is_logged_not_raised(tmp_path, caplog):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'empty.sqlite'}")
    try:
        ask(gateway(FakeProvider("OK"), partial(usage.record, create_session_factory(engine))))
        assert "Could not record model usage" in caplog.text
    finally:
        engine.dispose()


def test_ask_and_brief_are_tagged_at_their_entry_points(tmp_path):
    from fake_models import agent_replies
    from test_agent import app_for, ready_session

    fake = CountingProvider(agent_replies("CD3D marks T cells [1]."), counts=COUNTS)
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        client.delete("/api/usage", headers=SIDECAR)
        before = len(fake.requests)
        response = client.post(
            f"/api/sessions/{session_id}/messages",
            headers=SIDECAR,
            json={"content": "What marks T cells?"},
        )
        assert response.status_code == 200, response.text
        client.app.state.knowledge_service.refresh_brief(session_id)
        rows = client.get("/api/usage/recent?limit=200", headers=SIDECAR).json()["requests"]
        assert len(rows) == len(fake.requests) - before
        assert {r["job"] for r in rows} == {"ask", "analysis"}
        assert all(r["ref"] == session_id for r in rows)
        assert all(r["detail"] == "brief" for r in rows if r["job"] == "analysis")


def test_output_build_tags_every_nested_request(tmp_path):
    from fake_models import output_replies
    from test_outputs import ONE_SECTION, app_for, body, build, built, library

    fake = CountingProvider(
        output_replies(ONE_SECTION, ["## T cells\n\nCD3D marks T cells [1]."]), counts=COUNTS
    )
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        client.delete("/api/usage", headers=SIDECAR)
        before = len(fake.requests)
        built(build(client, base_id, body(brief="Explain T cells", conversationId="conversation")))
        rows = client.get("/api/usage/recent?limit=200", headers=SIDECAR).json()["requests"]
        assert len(rows) == len(fake.requests) - before > 0
        assert {(r["job"], r["detail"], r["ref"]) for r in rows} == {
            ("outputs", "report", "conversation")
        }


def test_usage_is_owner_only(client, monkeypatch):
    from gunther.device_auth import AuthPrincipal

    monkeypatch.setattr(
        client.app.state.device_auth,
        "authenticate_device",
        lambda token: AuthPrincipal(kind="device", scopes=("api:access",)),
    )
    for method, path in (
        ("GET", "/api/usage/summary"),
        ("GET", "/api/usage/recent"),
        ("DELETE", "/api/usage"),
    ):
        request = client.build_request(method, path)
        del request.headers["X-Gunther-Token"]
        assert client.send(request).status_code == 401
        request.headers["Authorization"] = "Bearer fake-device"
        assert client.send(request).status_code == 403


def test_default_sdk_client_retries_once():
    with _openai_client(model()) as client:
        assert client.max_retries == 1


@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.parametrize("failure", ["connection", 429, 500])
def test_sdk_retries_unless_admission_is_set(admitted, failure):
    requests = []
    records = []

    def broken(request):
        requests.append(request)
        if failure == "connection":
            raise httpx.ConnectError("unavailable", request=request)
        return httpx.Response(failure, json={"error": {"message": "unavailable"}})

    with openai.OpenAI(
        api_key="fake",
        max_retries=1,
        http_client=httpx.Client(transport=httpx.MockTransport(broken)),
    ) as client:
        models = ModelGateway(
            [model()], client_factory=lambda model: client, recorder=records.append
        )
        token = REQUEST_ADMISSION.set((lambda: None) if admitted else None)
        try:
            with pytest.raises(ModelError):
                ask(models)
        finally:
            REQUEST_ADMISSION.reset(token)
    assert len(requests) == (1 if admitted else 2)
    assert len(records) == 1
    assert records[0]["status"] == "error" and records[0]["input"] is None
