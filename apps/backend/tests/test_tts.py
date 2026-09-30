import dataclasses
import io
import json
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gunther import tts_narration, tts_providers
from gunther.config import Settings
from gunther.database import session_scope
from gunther.main import create_app
from gunther.models import SessionMessage
from gunther.tts_providers import (
    TtsError,
    join_wav,
    split_for_listening,
    split_for_speech,
    wav_seconds,
)
from tests.fake_models import FakeProvider, gateway

SIDECAR_TOKEN = "sidecar-token-for-tests"
SIDECAR = {"X-Gunther-Token": SIDECAR_TOKEN}
KEY = "sk-qwen-test-1234"

TABLE_ANSWER = """Two drugs were compared [1].

| Drug | Dose | Effect |
|------|------|--------|
| A | 5 mg | 40% |
| B | 10 mg | 65% |

**B** works better [2]."""


def wav(seconds: float = 0.1) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b"\x00\x00" * int(8000 * seconds))
    return out.getvalue()


@pytest.fixture
def spoken(monkeypatch) -> list[tuple[dict, str]]:
    """Qwen's voice replaced by silence; records what it was asked to say."""

    calls: list[tuple[dict, str]] = []

    async def synthesize(config, text):
        calls.append((dict(config.options), text))
        return wav()

    monkeypatch.setitem(
        tts_providers.PROVIDERS,
        "qwen",
        dataclasses.replace(tts_providers.PROVIDERS["qwen"], synthesize=synthesize),
    )
    return calls


def app_for(tmp_path: Path, fake: FakeProvider | None = None) -> TestClient:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        speech_dir=tmp_path / "speech",
        seed_demo=False,
        deepseek_api_key="sk-llm" if fake else None,
        processing_worker_enabled=False,
        auth_token=SIDECAR_TOKEN,
        service_settings_file=tmp_path / "service-settings.json",
    )
    factory = fake.factory if fake else None
    return TestClient(create_app(settings, model_client_factory=factory))


def answer(client: TestClient, content: str) -> tuple[str, str]:
    session_id = client.post(
        "/api/knowledge-bases/@home/sessions", headers=SIDECAR, json={}
    ).json()["id"]
    message_id = "msg-tts-1"
    with session_scope(client.app.state.tts.sessions) as session:
        session.add(
            SessionMessage(id=message_id, session_id=session_id, role="assistant", content=content)
        )
    return session_id, message_id


QWEN_TTS = "qwen/qwen3-tts-flash"


def setup(client: TestClient, voice: str | None = None, auto_read: bool = False) -> dict:
    """Qwen with a key, and the Answers job on its voice model."""

    response = client.put(
        "/api/settings/tts/providers/qwen", headers=SIDECAR, json={"apiKey": KEY}
    )
    assert response.status_code == 200, response.text
    return choose(client, voice=voice, auto_read=auto_read)


def choose(
    client: TestClient, model: str = QWEN_TTS, voice: str | None = None, auto_read: bool = False
) -> dict:
    job: dict = {"model": model, "autoRead": auto_read}
    if voice:
        job["options"] = {"voice": voice}
    response = client.put(
        "/api/settings/tts/roles", headers=SIDECAR, json={"roles": {"answers": job}}
    )
    assert response.status_code == 200, response.text
    return response.json()


def answers_job(found: dict) -> dict:
    return next(role for role in found["roles"] if role["id"] == "answers")


# Settings ---------------------------------------------------------------------------


def test_it_starts_off_with_qwen_offered(tmp_path: Path) -> None:
    with app_for(tmp_path) as client:
        found = client.get("/api/settings/tts", headers=SIDECAR).json()
    assert found["autoRead"] is False and "Choose a voice" in found["problem"]
    assert answers_job(found)["model"] is None
    [qwen] = found["providers"]
    assert qwen["kind"] == "qwen" and qwen["status"]["summary"] == "Needs an API key."
    assert [model["ref"] for model in qwen["models"]] == [QWEN_TTS]
    [preset] = found["presets"]
    voice = next(option for option in preset["options"] if option["key"] == "voice")
    assert voice["default"] == "Cherry" and voice["allowCustom"] is True


def test_saved_key_is_never_sent_back_and_the_voice_is_kept(tmp_path: Path) -> None:
    with app_for(tmp_path) as client:
        found = setup(client, voice="Ethan", auto_read=True)
        [qwen] = found["providers"]
        job = answers_job(found)
        assert found["autoRead"] is True and found["problem"] is None
        assert qwen["keySet"] and qwen["keyHint"] == "1234" and KEY not in str(found)
        assert job["model"] == QWEN_TTS and job["kind"] == "qwen"
        assert job["options"] == {"voice": "Ethan", "language": "Auto"}
    # A restart reads it back from the file.
    with app_for(tmp_path) as client:
        again = client.get("/api/settings/tts", headers=SIDECAR).json()
        assert answers_job(again)["options"]["voice"] == "Ethan" and again["autoRead"] is True


def test_settings_saved_before_providers_are_carried_over(tmp_path: Path) -> None:
    old = {
        "active": "qwen",
        "autoRead": True,
        "providers": {
            "qwen": {
                "apiKey": KEY,
                "baseUrl": "https://dashscope-intl.aliyuncs.com",
                "model": "qwen3-tts-flash",
                "options": {"voice": "Kai", "language": "Chinese"},
            }
        },
    }
    (tmp_path / "service-settings.json").write_text(json.dumps({"tts": old}))
    with app_for(tmp_path) as client:
        found = client.get("/api/settings/tts", headers=SIDECAR).json()
    [qwen] = found["providers"]
    assert qwen["baseUrl"] == "https://dashscope-intl.aliyuncs.com" and qwen["keySet"]
    job = answers_job(found)
    assert job["model"] == QWEN_TTS and job["options"] == {"voice": "Kai", "language": "Chinese"}
    assert found["autoRead"] is True and found["problem"] is None


def test_a_key_saved_for_qwen_models_is_shared(tmp_path: Path) -> None:
    intl = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    with app_for(tmp_path) as client:
        client.post(
            "/api/settings/providers",
            headers=SIDECAR,
            json={"kind": "qwen", "apiKey": KEY, "baseUrl": intl, "models": ["qwen3-max"]},
        )
        found = choose(client)
        [qwen] = found["providers"]
        assert found["problem"] is None and qwen["keyShared"] is True and qwen["keySet"] is False
        # The key belongs to one region, so the voice follows its address.
        assert qwen["baseUrl"] == "https://dashscope-intl.aliyuncs.com"


def test_providers_are_added_edited_and_removed_like_transcription(tmp_path: Path) -> None:
    with app_for(tmp_path) as client:
        setup(client, voice="Ethan")
        added = client.post(
            "/api/settings/tts/providers",
            headers=SIDECAR,
            json={"kind": "qwen", "baseUrl": "https://dashscope-intl.aliyuncs.com"},
        )
        assert added.status_code == 201
        names = [provider["name"] for provider in added.json()["providers"]]
        second = next(p for p in added.json()["providers"] if p["id"] != "qwen")
        assert names == ["Qwen", "Qwen 2"] and second["status"]["state"] == "not_configured"

        renamed = client.put(
            f"/api/settings/tts/providers/{second['id']}",
            headers=SIDECAR,
            json={"name": "Qwen (international)", "apiKey": "sk-intl-5678"},
        ).json()
        second = next(p for p in renamed["providers"] if p["id"] == second["id"])
        assert second["name"] == "Qwen (international)" and second["keyHint"] == "5678"

        # Removing the provider in use moves the job to the one left, keeping its voice.
        moved = client.delete("/api/settings/tts/providers/qwen", headers=SIDECAR).json()
        job = answers_job(moved)
        assert job["model"] == f"{second['id']}/qwen3-tts-flash"
        assert job["options"]["voice"] == "Ethan" and moved["problem"] is None
        # Removing the last one turns reading off rather than failing.
        off = client.delete(f"/api/settings/tts/providers/{second['id']}", headers=SIDECAR)
        assert answers_job(off.json())["model"] is None
        assert "Choose a voice" in off.json()["problem"]


def test_bad_settings_are_refused(tmp_path: Path) -> None:
    with app_for(tmp_path) as client:
        setup(client)
        refused = [
            client.post("/api/settings/tts/providers", headers=SIDECAR, json={"kind": "nobody"}),
            client.put(
                "/api/settings/tts/providers/qwen", headers=SIDECAR, json={"baseUrl": "ftp://x"}
            ),
            client.put(
                "/api/settings/tts/providers/qwen", headers=SIDECAR, json={"apiKey": "has space"}
            ),
            client.put(
                "/api/settings/tts/roles",
                headers=SIDECAR,
                json={"roles": {"answers": {"model": "nobody/model"}}},
            ),
            client.put(
                "/api/settings/tts/roles",
                headers=SIDECAR,
                json={"roles": {"answers": {"options": {"language": "Klingon"}}}},
            ),
            client.put(
                "/api/settings/tts/roles",
                headers=SIDECAR,
                json={"roles": {"summaries": {"model": QWEN_TTS}}},
            ),
        ]
        assert [response.status_code for response in refused] == [422] * 6
        missing = client.put("/api/settings/tts/providers/nope", headers=SIDECAR, json={})
        assert missing.status_code == 404


def test_paired_phones_cannot_read_or_change_it(tmp_path: Path) -> None:
    with app_for(tmp_path) as client:
        assert client.get("/api/settings/tts").status_code in (401, 403)


# Reading an answer -----------------------------------------------------------------------


def test_reading_needs_a_voice_first(tmp_path: Path, spoken) -> None:
    with app_for(tmp_path) as client:
        session_id, message_id = answer(client, "Hello.")
        response = client.post(
            f"/api/sessions/{session_id}/messages/{message_id}/speech", headers=SIDECAR
        )
    assert response.status_code == 409 and "Choose a voice" in response.json()["detail"]
    assert spoken == []


def test_the_second_listen_is_played_from_disk_not_made_again(tmp_path: Path, spoken) -> None:
    with app_for(tmp_path) as client:
        setup(client)
        session_id, message_id = answer(client, "## Result\n\nCD3D marks **T cells** [1].")
        url = f"/api/sessions/{session_id}/messages/{message_id}/speech"
        first = client.post(url, headers=SIDECAR).json()
        second = client.post(url, headers=SIDECAR).json()
        assert first["cached"] is False and second["cached"] is True
        assert first["id"] == second["id"] and len(spoken) == 1
        assert spoken[0][1] == "Result.\n\nCD3D marks T cells."
        audio = client.get(f"/api/speech/clips/{first['id']}/audio", headers=SIDECAR)
        assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
        assert wav_seconds(audio.content) == pytest.approx(0.1, abs=0.01)
        # A different voice is a different clip.
        choose(client, voice="Ethan")
        third = client.post(url, headers=SIDECAR).json()
        assert third["id"] != first["id"] and third["voice"] == "Ethan" and len(spoken) == 2
        stats = client.get("/api/settings/tts", headers=SIDECAR).json()["cache"]
        assert stats["clips"] == 2 and stats["bytes"] > 0
        cleared = client.delete("/api/settings/tts/cache", headers=SIDECAR).json()
        assert cleared["cache"]["clips"] == 0
        assert list((tmp_path / "speech").glob("*.wav")) == []
        # After clearing, reading makes it again.
        assert client.post(url, headers=SIDECAR).json()["cached"] is False


def test_a_clip_whose_file_was_deleted_is_made_again(tmp_path: Path, spoken) -> None:
    with app_for(tmp_path) as client:
        setup(client)
        session_id, message_id = answer(client, "One sentence.")
        url = f"/api/sessions/{session_id}/messages/{message_id}/speech"
        client.post(url, headers=SIDECAR)
        for file in (tmp_path / "speech").glob("*.wav"):
            file.unlink()
        assert client.post(url, headers=SIDECAR).json()["cached"] is False
        assert len(spoken) == 2


def test_a_table_is_described_by_a_model_before_it_is_spoken(tmp_path: Path, spoken) -> None:
    fake = FakeProvider("Drug B, at ten milligrams, worked better than drug A: sixty-five percent.")
    with app_for(tmp_path, fake) as client:
        setup(client)
        session_id, message_id = answer(client, TABLE_ANSWER)
        clip = client.post(
            f"/api/sessions/{session_id}/messages/{message_id}/speech", headers=SIDECAR
        ).json()
        script = client.get(f"/api/speech/clips/{clip['id']}/script", headers=SIDECAR).json()
    said = spoken[0][1]
    assert "|" not in said and "5 mg" not in said
    assert said.startswith("Two drugs were compared.")
    assert "sixty-five percent" in said and said.endswith("B works better.")
    assert script["script"] == said and clip["describedBy"] == "DeepSeek:deepseek-flash"
    [request] = fake.requests
    assert "| B | 10 mg | 65% |" in str(request["messages"][-1]["content"])


def test_a_table_without_a_model_is_an_error_not_a_guess(tmp_path: Path, spoken) -> None:
    with app_for(tmp_path) as client:
        setup(client)
        session_id, message_id = answer(client, TABLE_ANSWER)
        response = client.post(
            f"/api/sessions/{session_id}/messages/{message_id}/speech", headers=SIDECAR
        )
    assert response.status_code == 502 and "language model" in response.json()["detail"]
    assert spoken == []


def test_a_long_answer_is_cut_at_sentences_and_joined(tmp_path: Path, spoken) -> None:
    sentence = "This sentence is about forty characters." + " "
    with app_for(tmp_path) as client:
        setup(client)
        session_id, message_id = answer(client, sentence * 40)
        clip = client.post(
            f"/api/sessions/{session_id}/messages/{message_id}/speech", headers=SIDECAR
        ).json()
    assert len(spoken) >= 3 and all(len(text) <= 500 for _, text in spoken)
    assert clip["durationSeconds"] == pytest.approx(0.1 * len(spoken), abs=0.05)


def test_a_supplier_failure_is_shown_and_nothing_is_kept(tmp_path: Path, monkeypatch) -> None:
    async def broken(config, text):
        raise TtsError("Qwen did not accept this API key.")

    monkeypatch.setitem(
        tts_providers.PROVIDERS,
        "qwen",
        dataclasses.replace(tts_providers.PROVIDERS["qwen"], synthesize=broken),
    )
    with app_for(tmp_path) as client:
        setup(client)
        session_id, message_id = answer(client, "Hello there.")
        response = client.post(
            f"/api/sessions/{session_id}/messages/{message_id}/speech", headers=SIDECAR
        )
        sample = client.post("/api/settings/tts/sample", headers=SIDECAR, json={})
        assert client.get("/api/settings/tts", headers=SIDECAR).json()["cache"]["clips"] == 0
    assert response.status_code == 502 and "API key" in response.json()["detail"]
    assert sample.status_code == 502


def test_a_sample_can_be_heard_before_saving(tmp_path: Path, spoken) -> None:
    with app_for(tmp_path) as client:
        setup(client)
        sample = client.post(
            "/api/settings/tts/sample", headers=SIDECAR, json={"options": {"voice": "Kai"}}
        )
        # A provider being edited is heard with its unsaved key.
        edited = client.post(
            "/api/settings/tts/sample",
            headers=SIDECAR,
            json={"providerId": "qwen", "apiKey": "sk-unsaved-9999"},
        )
        saved = client.get("/api/settings/tts", headers=SIDECAR).json()
    assert sample.status_code == 200 and spoken[0][0]["voice"] == "Kai"
    assert edited.status_code == 200 and spoken[1][0]["voice"] == "Cherry"
    assert answers_job(saved)["options"]["voice"] == "Cherry"
    assert saved["providers"][0]["keyHint"] == "1234"


def test_only_answers_of_this_session_can_be_read(tmp_path: Path, spoken) -> None:
    with app_for(tmp_path) as client:
        setup(client)
        session_id, message_id = answer(client, "Hello.")
        missing = client.post(
            f"/api/sessions/{session_id}/messages/nope/speech", headers=SIDECAR
        )
        wrong = client.post(f"/api/sessions/other/messages/{message_id}/speech", headers=SIDECAR)
    assert missing.status_code == 404 and wrong.status_code == 404


# Pure parts --------------------------------------------------------------------------------


def test_markdown_becomes_plain_speech() -> None:
    text = tts_narration.clean_text(
        "# Title\n- first [1]\n- second, see [the docs](https://x.org/a) and https://y.org\n"
        "> quoted *text*\n---\nUse `pip` with $x^2$ [?]"
    )
    assert text == "Title.\nfirst.\nsecond, see the docs and.\nquoted text.\nUse pip with x^2."


def test_parts_that_cannot_be_read_as_written_are_found() -> None:
    parts = tts_narration.split_answer(
        "Intro\n\n```py\nprint(1)\n```\n\n![Fig 1](data:image/png;base64,AAAA)\n\n$$E=mc^2$$\n\nEnd"
    )
    kinds = [piece.kind if isinstance(piece, tts_narration.Part) else "text" for piece in parts]
    assert kinds == ["text", "code", "text", "image", "text", "math", "text"]
    image = next(p for p in parts if isinstance(p, tts_narration.Part) and p.kind == "image")
    assert image.image and image.image.media_type == "image/png" and image.text == "Fig 1"


def test_a_supplier_that_reads_structure_needs_no_narrator() -> None:
    import asyncio

    script = asyncio.run(
        tts_narration.narrate(TABLE_ANSWER, None, understands_structure=True)
    )
    assert script.described is False and "Drug" in script.text


def test_pictures_go_to_a_model_that_can_see(tmp_path: Path) -> None:
    import asyncio

    fake = FakeProvider("A bar chart of cell counts, highest in the treated group.")
    answer_text = "Before.\n\n![Counts](data:image/png;base64,iVBORw0KGgo=)\n\nAfter."
    from tests.fake_models import model

    seeing = model("vision-model", kind="openai", vision=True)
    script = asyncio.run(tts_narration.narrate(answer_text, gateway(fake, seeing)))
    assert "bar chart" in script.text and script.described
    content = fake.requests[0]["messages"][-1]["content"]
    assert any(isinstance(item, dict) and item.get("type") == "image_url" for item in content)


def test_cutting_keeps_decimals_and_short_pieces() -> None:
    assert split_for_speech("Pi is 3.14 today. Fine.", 500) == ["Pi is 3.14 today. Fine."]
    pieces = split_for_speech("一二三。" * 10, 12)
    assert all(len(piece) <= 12 for piece in pieces) and "".join(pieces) == "一二三。" * 10
    assert len(split_for_speech("word " * 300, 100)) >= 15


def test_joining_wav_files_adds_their_lengths() -> None:
    joined = join_wav([wav(0.1), wav(0.2)])
    assert wav_seconds(joined) == pytest.approx(0.3, abs=0.01)
    with pytest.raises(TtsError):
        join_wav([wav(0.1), b"not audio"])


def test_a_sample_that_speaks_marks_the_provider_connected_until_its_key_changes(
    tmp_path: Path, spoken
) -> None:
    with app_for(tmp_path) as client:
        setup(client)
        before = client.get("/api/settings/tts", headers=SIDECAR).json()["providers"][0]
        assert before["status"]["check"] is None
        sample = client.post(
            "/api/settings/tts/sample", json={"providerId": before["id"]}, headers=SIDECAR
        )
        assert sample.status_code == 200
        after = client.get("/api/settings/tts", headers=SIDECAR).json()["providers"][0]
        assert after["status"]["state"] == "configured" and after["status"]["check"]["ok"] is True
        # A new key has not been heard yet.
        client.put(
            f"/api/settings/tts/providers/{before['id']}",
            json={"apiKey": "sk-qwen-other-9999"},
            headers=SIDECAR,
        )
        changed = client.get("/api/settings/tts", headers=SIDECAR).json()["providers"][0]
        assert changed["status"]["check"] is None


def test_split_for_listening_starts_with_the_first_paragraph() -> None:
    first = ("First paragraph, long enough to stand alone. " * 4).strip()
    parts = split_for_listening(f"{first}\n\nShort.\n\nAlso short.", 500)
    assert parts[0] == first[:260].rsplit(" ", 1)[0] or parts[0] == first
    assert parts[-1] == "Short.\nAlso short."


def test_a_long_answer_is_handed_over_in_parts_and_kept_whole(tmp_path: Path, spoken) -> None:
    paragraph = "Paragraph says something fairly long about T cells. " * 5
    text = "\n\n".join(paragraph for _ in range(3))
    with app_for(tmp_path) as client:
        setup(client)
        session_id, message_id = answer(client, text)
        url = f"/api/sessions/{session_id}/messages/{message_id}/speech/begin"
        begun = client.post(url, headers=SIDECAR).json()
        assert begun["clip"] is None and begun["parts"] >= 3
        for index in range(begun["parts"]):
            part = client.get(f"/api/speech/jobs/{begun['jobId']}/parts/{index}", headers=SIDECAR)
            assert part.status_code == 200 and part.headers["content-type"] == "audio/wav"
        count = len(spoken)
        assert count == begun["parts"]
        missing = client.get(f"/api/speech/jobs/{begun['jobId']}/parts/99", headers=SIDECAR)
        assert missing.status_code == 404
        # Afterwards the whole answer is on disk and later listens just play it.
        again = client.post(url, headers=SIDECAR).json()
        assert again["clip"]["cached"] is True and again["parts"] == 1 and len(spoken) == count
        # Read again: the kept recording is dropped and made anew.
        redo = client.post(f"{url}?fresh=true", headers=SIDECAR).json()
        assert redo["clip"] is None and redo["jobId"] != begun["jobId"]
        for index in range(redo["parts"]):
            client.get(f"/api/speech/jobs/{redo['jobId']}/parts/{index}", headers=SIDECAR)
        assert len(spoken) == 2 * count
        assert client.get("/api/settings/tts", headers=SIDECAR).json()["cache"]["clips"] == 1


def test_fetch_models_offers_the_voices_the_key_can_use(tmp_path: Path, monkeypatch) -> None:
    from gunther import tts_api
    from gunther.service_settings import CheckResult

    asked: list[tuple[str, str | None]] = []

    async def listed(url, key, name, *, key_optional=False):
        asked.append((url, key))
        return CheckResult(True, "Connected"), [
            "qwen-plus",
            "qwen3-tts-flash",
            "qwen3-tts-flash-realtime",
            "qwen3-tts-instruct-flash",
        ]

    monkeypatch.setattr(tts_api, "list_models", listed)
    with app_for(tmp_path) as client:
        setup(client)
        fetched = client.post(
            "/api/settings/tts/providers/qwen/models",
            headers=SIDECAR,
            json={"apiKey": "sk-unsaved-9999"},
        ).json()
        missing = client.post("/api/settings/tts/providers/nobody/models", headers=SIDECAR, json={})
    # Text models and real-time voices (a WebSocket) are not offered.
    assert fetched == {
        "ok": True,
        "message": "2 voice models offered.",
        "offered": ["qwen3-tts-flash", "qwen3-tts-instruct-flash"],
    }
    # DashScope lists models on its compatible address, asked with the unsaved key.
    assert asked == [("https://dashscope.aliyuncs.com/compatible-mode/v1", "sk-unsaved-9999")]
    assert missing.status_code == 404


def test_fetch_models_says_when_the_key_is_refused(tmp_path: Path, monkeypatch) -> None:
    from gunther import tts_api
    from gunther.service_settings import CheckResult

    async def refused(url, key, name, *, key_optional=False):
        return CheckResult(False, "Qwen did not accept this API key."), []

    monkeypatch.setattr(tts_api, "list_models", refused)
    with app_for(tmp_path) as client:
        setup(client)
        fetched = client.post(
            "/api/settings/tts/providers/qwen/models", headers=SIDECAR, json={}
        ).json()
    assert fetched == {"ok": False, "message": "Qwen did not accept this API key.", "offered": []}
