import json
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest
from fake_models import FakeProvider, is_sentence_checking, studio_task
from test_artifact_agent import done, turn
from test_output_cards import EVIDENCE, checks_in, conversation, document
from test_outputs import app_for, read_events, route, workspace
from test_tts import wav

from gunther.models import ArtifactAsset, SpeechClip
from gunther.output_types import SHAPES
from gunther.tts_providers import PROVIDERS, QWEN_VOICES, TtsError, wav_seconds

VOICES = [voice for voice, _ in QWEN_VOICES[:3]]
LINES = ["A: What changed?", "B: Factor T steered cells to the red-cell branch [1].",
         "A: Loss of T expanded the white-cell branch [1]."]
SCRIPT = document("audio_overview", *LINES)


@pytest.mark.parametrize(("lines", "problem"), [
    (["A: Hello", "No speaker", "B: Here"], "Start the line with A:"),
    (["A: Hello", "B: [1]"], "non-empty speech"),
    (["A: Hello"], "both A and B"),
    (["A: Hello", *(["B: Here"] * 120)], "120 lines"),
])
def test_dialogue_format(lines, problem):
    assert SHAPES["audio"].format_check(SCRIPT) == []
    assert any(problem in error for error in SHAPES["audio"].format_check(
        document("audio_overview", *lines),
    ))


def model_reply(request):
    task = studio_task(request)
    if "lead analyst" in task:
        return json.dumps({"title": "Cell fates", "answer": EVIDENCE})
    if "Write the complete Audio overview" in task:
        return SCRIPT
    if is_sentence_checking(request):
        claims = checks_in(request)
        assert claims == [(1, LINES[1][3:]), (2, LINES[2][3:])]
        return '{"sentences":[{"n":1,"verdict":"supported"},{"n":2,"verdict":"supported"}]}'
    raise AssertionError(task)


def configure(client, monkeypatch, probe, *, fail=False):
    calls = []
    supplier = PROVIDERS["qwen"]
    model = "test-voice-model"

    async def synthesize(config, text):
        # A different SQLite connection must be able to write throughout synthesis.
        with closing(sqlite3.connect(probe, timeout=0)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        assert "[" not in text
        calls.append((config.options["voice"], text))
        if fail and len(calls) == 1:
            raise TtsError("The voice provider is busy.")
        return wav(0.1 if config.options["voice"] == VOICES[0] else 0.2)

    monkeypatch.setitem(PROVIDERS, "qwen", replace(
        supplier, synthesize=synthesize, key_optional=True,
    ))
    monkeypatch.setattr(client.app.state.service_settings, "tts", lambda: {
        "providers": [{"id": "qwen", "kind": "qwen", "models": [model]}],
        "roles": {"answers": {"model": f"qwen/{model}", "options": {"voice": VOICES[0]}}},
    })
    return calls


def build(client, **options):
    base, cid = conversation(client)
    events = turn(client, base, cid, "", "build", build={"type": "audio_overview", **options})
    return base, done(events)["artifact"], events


def retry(client, path, headers, **options):
    with client.stream("POST", f"{path}/audio/stream", headers=headers, json=options) as response:
        events = read_events(response)
    return next(result for name, result in events if name == "done")


def test_audio_build_batches_checks_and_attaches_contiguous_wav(tmp_path, monkeypatch):
    database = tmp_path / "gunther.sqlite"
    fake = FakeProvider(model_reply, write_lock_path=database)
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        calls = configure(client, monkeypatch, database)
        headers = workspace(client)
        types = client.get("/api/output-types", headers=headers).raise_for_status().json()
        item = next(item for item in types if item["id"] == "audio_overview")
        assert item["voices"]["defaults"] == VOICES[:2]
        assert "voices" in item["options"]
        assert item["capabilities"]["exports"] == ["markdown", "wav"]
        base, built, events = build(client)
        audio = built["audio"]
        assert audio["status"] == "ready" and audio["contentHash"] == built["contentHash"]
        assert built["content"] == SCRIPT and built["keyResults"]["state"] == "queued"
        assert sum(is_sentence_checking(request) for request in fake.requests) == 1
        assert not fake.write_lock_failures
        assert audio["voices"] == VOICES[:2]
        assert [voice for voice, _ in calls] == [VOICES[0], VOICES[1], VOICES[0]]
        assert [text for _, text in calls] == [line[3:].replace(" [1]", "") for line in LINES]
        timings = audio["timings"]
        assert [t["line"] for t in timings] == [1, 2, 3]
        assert [t["speaker"] for t in timings] == ["A", "B", "A"]
        assert [t["start"] for t in timings] == [0, timings[0]["end"], timings[1]["end"]]
        assert [t["end"] - t["start"] for t in timings] == pytest.approx([0.1, 0.2, 0.1])
        stages = [event.get("detail") for name, event in events if name == "stage"]
        assert "line 3 of 3" in stages
        asset = f"/api/assets/{audio['audioAssetId']}"
        wav_response = client.get(asset, headers=headers).raise_for_status()
        assert wav_seconds(wav_response.content) == pytest.approx(timings[-1]["end"])
        with client.app.state.knowledge_service.sessions() as session:
            assert session.get(ArtifactAsset, (built["id"], audio["audioAssetId"]))
            assert session.query(SpeechClip).count() == 0
        entry = client.post(route(base, built["id"], "/trash"), headers=headers).raise_for_status()
        client.delete(f"/api/trash/output/{entry.json()['id']}", headers=headers).raise_for_status()
        assert client.get(asset, headers=headers).status_code == 404
        assert not list((tmp_path / "assets").rglob("*.wav"))


def test_unconfigured_tts_still_saves_the_checked_script(tmp_path):
    with app_for(tmp_path, FakeProvider(model_reply), ai_summaries="off") as client:
        base, built, _ = build(client)
        reopened = client.get(route(base, built["id"]), headers=workspace(client)).json()
    assert reopened["content"] == SCRIPT and all(s["checked"] for s in reopened["sections"])
    assert reopened["audio"]["status"] == "needs_tts"
    assert reopened["audio"]["audioAssetId"] is None


def test_failed_tts_can_be_retried_and_revoiced_without_a_new_script_version(tmp_path, monkeypatch):
    with app_for(tmp_path, FakeProvider(model_reply), ai_summaries="off") as client:
        configure(client, monkeypatch, tmp_path / "gunther.sqlite", fail=True)
        base, built, _ = build(client, voices=[VOICES[1], VOICES[0]])
        assert built["audio"]["status"] == "error" and "busy" in built["audio"]["error"]
        assert built["content"] == SCRIPT
        path, headers = route(base, built["id"]), workspace(client)
        ready = retry(client, path, headers)
        assert ready["audio"]["status"] == "ready" and ready["audio"]["voices"] == VOICES[:2][::-1]
        changed = retry(client, path, headers, voices=[VOICES[2], VOICES[1]])
        assert changed["audio"]["voices"] == [VOICES[2], VOICES[1]]
        assert changed["audio"]["audioAssetId"] != ready["audio"]["audioAssetId"]
        for field in ("id", "manifestHash", "versionNumber", "contentHash", "provenance"):
            assert changed[field] == ready[field] == built[field]
        reopened = client.get(path, headers=headers).raise_for_status().json()
        assert reopened["audio"] == changed["audio"]
        versions = client.get(f"/api/knowledge-bases/{base}/artifacts", headers=headers).json()
        assert [version["id"] for version in versions] == [built["id"]]
