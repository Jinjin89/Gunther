import io
import wave
from pathlib import Path

import httpx
import openai
from fake_models import FakeProvider, api_error, gateway, model
from fastapi.testclient import TestClient
from legacy_artifacts import insert_legacy_version

from gunther.config import Settings
from gunther.database import create_database_engine
from gunther.extraction import ModelExtractor
from gunther.main import create_app
from gunther.models import Artifact
from gunther.realtime import _pcm16_wav, qwen_endpoint, qwen_request, qwen_text


def make_client() -> TestClient:
    app = create_app(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            seed_demo=False,
            deepseek_api_key=None,
            stt_provider="compatible",
        )
    )
    return TestClient(app)


def test_sensevoice_pcm_segments_are_wrapped_as_24khz_wav() -> None:
    wav_data = _pcm16_wav(b"\x00\x00\xff\x7f")
    with wave.open(io.BytesIO(wav_data), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 24_000
        assert audio.readframes(2) == b"\x00\x00\xff\x7f"


def test_qwen_audio_takes_a_wav_data_uri_and_answers_with_output_text() -> None:
    body = qwen_request("qwen-audio-3.1-asr-flash", b"RIFFwav")
    [message] = body["input"]["messages"]
    audio = message["content"][0]["input_audio"]
    assert audio == {"data": "data:audio/wav;base64,UklGRndhdg==", "format": "wav"}
    assert body["parameters"] == {"format": "wav", "sample_rate": "24000"}
    assert qwen_text({"output": {"text": " 你好 "}}) == "你好"
    assert qwen_text({"output": {}}) == "" and qwen_text("nope") == ""
    # The saved address may still be the compatible-mode one.
    expected = "https://maas.qianwenaiapi.com/api/v1/services/aigc/multimodal-generation/generation"
    assert qwen_endpoint("https://maas.qianwenaiapi.com/compatible-mode/v1") == expected
    assert qwen_endpoint("https://maas.qianwenaiapi.com/") == expected


def test_file_backed_sqlite_uses_desktop_safe_pragmas(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}")
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
            assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 1
    finally:
        engine.dispose()


def create_base(client: TestClient, title: str) -> str:
    created = client.post(
        "/api/knowledge-bases",
        json={
            "title": title,
            "question": f"What belongs in {title}?",
            "description": f"A durable knowledge boundary for {title}.",
        },
    )
    assert created.status_code == 201
    return created.json()["id"]


def answered_session(client: TestClient, title: str) -> tuple[str, str, str]:
    """A library with one source and one cited answer: (library, session, answer) ids."""

    base_id = create_base(client, title)
    client.post(
        "/api/sources",
        json={
            "title": "Learning evidence",
            "kind": "note",
            "content": "Retrieval practice improves retention.",
            "knowledgeBaseId": base_id,
        },
    )
    session_id = client.post(f"/api/knowledge-bases/{base_id}/sessions", json={}).json()["id"]
    turn = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "What improves retention?"},
    ).json()
    return base_id, session_id, turn["assistantMessage"]["id"]


def test_import_builds_evidence_backed_graph() -> None:
    with make_client() as client:
        response = client.post(
            "/api/sources",
            json={
                "title": "PBMC markers",
                "kind": "table",
                "content": "CD3D -> marker_of -> T cell\nT cell -> is_a -> lymphocyte",
            },
        )
        assert response.status_code == 201
        imported = response.json()
        assert imported["created"] == {"entities": 3, "assertions": 2}
        assert imported["extractionMode"] == "local"
        source = client.get(f"/api/sources/{imported['source']['id']}").json()
        assert source["content"] == "CD3D -> marker_of -> T cell\nT cell -> is_a -> lymphocyte"
        assert source["assertionCount"] == 2
        assert len(source["assertions"]) == 2
        assert source["assertions"][0]["evidence"][0]["quote"]

        graph = client.get("/api/graph").json()
        assert len(graph["nodes"]) == 3
        assert {edge["label"] for edge in graph["edges"]} == {"marker_of", "is_a"}

        assertions = client.get("/api/assertions?status=provisional").json()
        assert len(assertions) == 2
        assert assertions[0]["evidence"][0]["locator"].startswith("line ")


def test_knowledge_base_metadata_can_be_created_and_count_sessions() -> None:
    with make_client() as client:
        created = client.post(
            "/api/knowledge-bases",
            json={
                "title": "Decision Science",
                "eyebrow": "Applied reasoning",
                "subtitle": "Choose well under uncertainty",
                "question": "How can decisions remain explicit and revisable?",
                "description": "A working field for evidence, options, trade-offs, and outcomes.",
                "color": "blue",
            },
        )
        assert created.status_code == 201
        knowledge_base = created.json()
        assert knowledge_base["id"] == "decision-science"
        assert knowledge_base["status"] == "Outline"
        assert knowledge_base["sessionCount"] == 0

        updated = client.patch(
            f"/api/knowledge-bases/{knowledge_base['id']}",
            json={
                "title": "Decision Design",
                "description": "A revised boundary for evidence and explicit trade-offs.",
                "color": "clay",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "Decision Design"
        assert updated.json()["color"] == "clay"

        client.post(f"/api/knowledge-bases/{knowledge_base['id']}/sessions", json={})
        listed = client.get("/api/knowledge-bases").json()
        assert listed[0]["id"] == knowledge_base["id"]
        assert listed[0]["sessionCount"] == 1


def test_review_changes_assertion_status() -> None:
    with make_client() as client:
        client.post(
            "/api/sources",
            json={
                "title": "Course note",
                "kind": "course",
                "content": "Backpropagation -> depends_on -> chain rule",
            },
        )
        assertion = client.get("/api/assertions").json()[0]

        reviewed = client.patch(
            f"/api/assertions/{assertion['id']}/status",
            json={"status": "verified", "reason": "Checked against the lesson"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["status"] == "verified"
        assert client.get("/api/overview").json()["counts"]["provisional"] == 0


def test_source_review_updates_all_candidate_claims_atomically() -> None:
    with make_client() as client:
        imported = client.post(
            "/api/sources",
            json={
                "title": "Cell program",
                "kind": "note",
                "content": "CD3D -> marker_of -> T cell\nT cell -> is_a -> lymphocyte",
            },
        ).json()

        reviewed = client.patch(
            f"/api/sources/{imported['source']['id']}/assertions/status",
            json={"status": "verified", "reason": "Accepted from the source inbox"},
        )

        assert reviewed.status_code == 200
        assert len(reviewed.json()) == 2
        assert {item["status"] for item in reviewed.json()} == {"verified"}
        assert client.get("/api/overview").json()["counts"]["provisional"] == 0


def test_source_identity_preserves_context_but_exact_retry_is_idempotent() -> None:
    payload = {
        "title": "First title",
        "kind": "note",
        "content": "Gradient descent -> uses -> gradients",
    }
    with make_client() as client:
        first = client.post("/api/sources", json=payload).json()
        assert first["duplicate"] is False
        second_payload = {**payload, "title": "Same content"}
        second = client.post("/api/sources", json=second_payload)
        assert second.status_code == 201
        assert second.json()["duplicate"] is False
        assert second.json()["source"]["id"] != first["source"]["id"]

        duplicate = client.post("/api/sources", json=second_payload)
        assert duplicate.status_code == 201
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["source"]["id"] == second.json()["source"]["id"]
        assert client.get("/api/overview").json()["counts"]["sources"] == 2


def test_unified_inbox_files_an_unassigned_source_idempotently() -> None:
    with make_client() as client:
        base_id = create_base(client, "Research Inbox")
        imported = client.post(
            "/api/sources",
            json={
                "title": "Unsorted evidence",
                "kind": "paper",
                "content": "Sleep improves memory consolidation.",
            },
        ).json()
        source_id = imported["source"]["id"]

        assert client.get(f"/api/sources/{source_id}").json()["knowledgeBases"] == []
        unfiled = client.get("/api/inbox?state=unfiled&itemType=source")
        assert unfiled.status_code == 200
        assert unfiled.json() == [
            {
                "id": source_id,
                "itemType": "source",
                "state": "unfiled",
                "title": "Unsorted evidence",
                "preview": "Sleep improves memory consolidation.",
                "sourceKind": "paper",
                "knowledgeBases": [],
                "sourceId": source_id,
                "noteId": None,
                "assertionCount": 1,
                "createdAt": imported["source"]["createdAt"],
                "updatedAt": imported["source"]["createdAt"],
            }
        ]

        missing_base = client.post(
            f"/api/sources/{source_id}/file",
            json={"knowledgeBaseId": "missing"},
        )
        assert missing_base.status_code == 404
        missing_source = client.post(
            "/api/sources/src_missing/file",
            json={"knowledgeBaseId": base_id},
        )
        assert missing_source.status_code == 404

        filed = client.post(
            f"/api/sources/{source_id}/file",
            json={"knowledgeBaseId": base_id},
        )
        assert filed.status_code == 200
        assert filed.json()["knowledgeBase"] == {
            "id": base_id,
            "title": "Research Inbox",
        }
        assert filed.json()["membershipCreated"] is True

        filed_again = client.post(
            f"/api/sources/{source_id}/file",
            json={"knowledgeBaseId": base_id},
        )
        assert filed_again.status_code == 200
        assert filed_again.json()["membershipCreated"] is False
        assert [
            item["id"] for item in client.get(f"/api/knowledge-bases/{base_id}/sources").json()
        ] == [source_id]

        assert client.get("/api/inbox?state=unfiled&itemType=source").json() == []
        assert client.get(f"/api/sources/{source_id}").json()["knowledgeBases"] == [
            {"id": base_id, "title": "Research Inbox"}
        ]
        review_item = client.get("/api/inbox?state=needs_review&itemType=source").json()
        assert len(review_item) == 1
        assert review_item[0]["sourceId"] == source_id
        assert review_item[0]["assertionCount"] == 1
        assert review_item[0]["knowledgeBases"] == [{"id": base_id, "title": "Research Inbox"}]

        reviewed = client.patch(
            f"/api/sources/{source_id}/assertions/status",
            json={"status": "verified", "reason": "Reviewed from the unified inbox"},
        )
        assert reviewed.status_code == 200
        assert all(item["sourceId"] != source_id for item in client.get("/api/inbox").json())


def test_inbox_previews_a_pasted_table_by_its_shape() -> None:
    with make_client() as client:
        client.post(
            "/api/sources",
            json={
                "title": "Cluster sizes",
                "kind": "table",
                "content": "cluster\tcells\tannotation\n0\t1,142\tT cells\n1\t486\tMonocytes",
            },
        )
        client.post(
            "/api/sources",
            json={
                "title": "Markdown rows",
                "kind": "table",
                "content": "| gene | count |\n|---|--:|\n| CD3E | 12 |",
            },
        )
        previews = {
            item["title"]: item["preview"]
            for item in client.get("/api/inbox?state=unfiled&itemType=source").json()
        }
        assert previews["Cluster sizes"] == "2 rows · Columns: cluster, cells, annotation"
        assert previews["Markdown rows"] == "1 row · Columns: gene, count"


def test_unified_inbox_lists_quick_notes_but_never_saved_answers() -> None:
    with make_client() as client:
        base_id, session_id, message_id = answered_session(client, "Learning Queue")
        note = client.post(
            "/api/notes",
            json={
                "title": "Idea to sort",
                "content": "Compare retrieval practice with rereading.",
            },
        ).json()
        saved = client.post(
            f"/api/sessions/{session_id}/messages/{message_id}/proposal",
            json={"title": "Retention finding"},
        ).json()
        assert saved["status"] == "accepted"

        inbox = client.get("/api/inbox").json()
        quick_note = next(item for item in inbox if item["noteId"] == note["id"])
        assert quick_note["itemType"] == "quick_note"
        assert quick_note["state"] == "unfiled"
        assert quick_note["sourceKind"] == "note"
        assert quick_note["preview"] == "Compare retrieval practice with rereading."

        # Saving an answer is the only review step, so it never waits in the Inbox.
        assert {item["itemType"] for item in inbox} <= {"source", "quick_note"}
        assert {item["state"] for item in inbox} <= {"unfiled", "needs_review"}
        assert all(saved["id"] not in (item["id"], item.get("proposalId")) for item in inbox)
        assert client.get("/api/inbox?itemType=knowledge_suggestion").status_code == 422
        assert client.get("/api/inbox?state=held").status_code == 422
        assert base_id == saved["knowledgeBaseId"]


def test_notebook_note_stays_lightweight_until_it_is_filed() -> None:
    with make_client() as client:
        base_id = create_base(client, "Learning Fragments")
        created = client.post(
            "/api/notes",
            json={"content": "Spacing study sessions improves long-term recall."},
        )
        assert created.status_code == 201
        note = created.json()
        assert note["status"] == "inbox"
        assert note["knowledgeBaseId"] is None
        assert note["promotedSourceId"] is None
        assert client.get("/api/overview").json()["counts"]["sources"] == 0
        assert client.get("/api/assertions").json() == []

        updated = client.patch(
            f"/api/notes/{note['id']}",
            json={"title": "Spacing effect", "pinned": True},
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "Spacing effect"
        assert updated.json()["pinned"] is True

        filed = client.post(
            f"/api/notes/{note['id']}/file",
            json={"knowledgeBaseId": base_id},
        )
        assert filed.status_code == 200
        result = filed.json()
        assert result["note"]["status"] == "filed"
        assert result["note"]["knowledgeBaseId"] == base_id
        assert result["note"]["promotedSourceId"] == result["importResult"]["source"]["id"]
        assert client.get("/api/overview").json()["counts"]["sources"] == 1
        assert client.get(f"/api/knowledge-bases/{base_id}/sources").json()[0]["kind"] == "note"


def test_quick_note_client_capture_id_is_exactly_once() -> None:
    payload = {
        "title": "Offline thought",
        "content": "This survives a lost success response.",
        "clientCaptureId": "capture_retry_12345678",
    }
    with make_client() as client:
        first = client.post("/api/notes", json=payload)
        retried = client.post("/api/notes", json=payload)

        assert first.status_code == retried.status_code == 201
        assert first.json()["id"] == retried.json()["id"]
        assert len(client.get("/api/notes").json()) == 1
        assert len(client.get("/api/inbox?type=quick_note").json()) == 1

        invalid = client.post(
            "/api/notes",
            json={**payload, "clientCaptureId": "contains spaces"},
        )
        assert invalid.status_code == 422


def test_notebook_notes_can_be_filtered_archived_and_searched() -> None:
    with make_client() as client:
        kept = client.post(
            "/api/notes",
            json={"title": "Orphan idea", "content": "Bayesian priors shape interpretation."},
        ).json()
        archived = client.post(
            "/api/notes",
            json={"title": "Old fragment", "content": "A thought to keep out of search."},
        ).json()
        response = client.patch(f"/api/notes/{archived['id']}", json={"status": "archived"})
        assert response.status_code == 200

        inbox = client.get("/api/notes?status=inbox&q=priors").json()
        assert [note["id"] for note in inbox] == [kept["id"]]
        search = client.get("/api/search?q=Bayesian").json()
        note_result = next(item for item in search if item["kind"] == "note")
        assert note_result["id"] == kept["id"]
        assert note_result["knowledgeBaseId"] is None
        assert all(item["id"] != archived["id"] for item in search)


def test_search_finds_unfiled_captures_and_reports_their_library_once_filed() -> None:
    with make_client() as client:
        base_id = create_base(client, "Sleep Research")
        imported = client.post(
            "/api/sources",
            json={
                "title": "Unsorted sleep paper",
                "kind": "paper",
                "content": "Slow-wave sleep supports memory consolidation.",
            },
        ).json()
        source_id = imported["source"]["id"]

        unfiled = client.get("/api/search?q=consolidation").json()
        source_result = next(item for item in unfiled if item["id"] == source_id)
        assert source_result["kind"] == "source"
        assert source_result["knowledgeBaseId"] is None

        filed = client.post(
            f"/api/sources/{source_id}/file",
            json={"knowledgeBaseId": base_id},
        )
        assert filed.status_code == 200

        results = [
            item
            for item in client.get("/api/search?q=consolidation").json()
            if item["id"] == source_id
        ]
        assert [item["knowledgeBaseId"] for item in results] == [base_id]


def test_search_can_be_scoped_to_selected_knowledge_bases() -> None:
    with make_client() as client:
        biology = create_base(client, "Biology")
        computing = create_base(client, "Computing")
        for title, base_id in (
            ("Marker genes for T cells", biology),
            ("Marker passes in compilers", computing),
            ("Unfiled marker idea", None),
        ):
            payload = {"title": title, "kind": "paper", "content": f"{title} — marker notes."}
            if base_id:
                payload["knowledgeBaseId"] = base_id
            assert client.post("/api/sources", json=payload).status_code == 201

        everything = client.get("/api/search?q=marker").json()
        assert {item["title"] for item in everything} >= {
            "Marker genes for T cells",
            "Marker passes in compilers",
            "Unfiled marker idea",
        }

        scoped = client.get(f"/api/search?q=marker&knowledgeBaseId={biology}").json()
        assert [item["title"] for item in scoped] == ["Marker genes for T cells"]
        assert all(item["knowledgeBaseId"] == biology for item in scoped)

        both = client.get(
            f"/api/search?q=marker&knowledgeBaseId={biology}&knowledgeBaseId={computing}"
        ).json()
        assert {item["knowledgeBaseId"] for item in both} == {biology, computing}

        too_many = "&".join(f"knowledgeBaseId=base-{index}" for index in range(21))
        assert client.get(f"/api/search?q=marker&{too_many}").status_code == 422


def test_natural_language_search_ignores_query_framing_words() -> None:
    with make_client() as client:
        note = client.post(
            "/api/notes",
            json={
                "title": "Bayesian caveat",
                "content": "Posterior estimates retain substantial uncertainty.",
            },
        ).json()

        results = client.get("/api/search?q=What+have+I+learned+about+uncertainty").json()

        assert any(item["id"] == note["id"] for item in results)
        note_result = next(item for item in results if item["id"] == note["id"])
        assert "uncertainty" in note_result["snippet"].casefold()


def test_punctuation_only_search_does_not_expand_into_a_wildcard() -> None:
    with make_client() as client:
        client.post(
            "/api/notes",
            json={"title": "Private fragment", "content": "Do not match everything."},
        )

        assert client.get("/api/search?q=%25%25").json() == []
        assert client.get("/api/search?q=_").json() == []


def test_notebook_filing_rejects_empty_invalid_and_repeated_promotions() -> None:
    with make_client() as client:
        base_id = create_base(client, "Reliable Filing")
        note = client.post("/api/notes", json={}).json()
        too_empty = client.post(f"/api/notes/{note['id']}/file", json={"knowledgeBaseId": base_id})
        assert too_empty.status_code == 409
        missing_base = client.post(
            f"/api/notes/{note['id']}/file", json={"knowledgeBaseId": "missing"}
        )
        assert missing_base.status_code == 404

        client.patch(f"/api/notes/{note['id']}", json={"content": "A durable fragment."})
        assert (
            client.post(
                f"/api/notes/{note['id']}/file", json={"knowledgeBaseId": base_id}
            ).status_code
            == 200
        )
        repeated = client.post(f"/api/notes/{note['id']}/file", json={"knowledgeBaseId": base_id})
        assert repeated.status_code == 409
        edit_snapshot = client.patch(
            f"/api/notes/{note['id']}", json={"content": "Changed after filing"}
        )
        assert edit_snapshot.status_code == 409


def test_local_extractor_understands_simple_english_relationships() -> None:
    with make_client() as client:
        imported = client.post(
            "/api/sources",
            json={
                "title": "Learning note",
                "kind": "note",
                "content": "Spaced repetition improves retention.",
            },
        ).json()
        assert imported["created"]["assertions"] == 1
        assertion = client.get("/api/assertions").json()[0]
        assert assertion["predicate"] == "improves"


def test_a_language_model_key_selects_model_extraction() -> None:
    app = create_app(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            seed_demo=False,
            deepseek_api_key="test-key",
        )
    )
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["extractionMode"] == "model"
        assert health["analysisModel"] == "DeepSeek · Flash"


def test_search_and_recording_capabilities_degrade_explicitly_without_openai() -> None:
    with make_client() as client:
        health = client.get("/api/health").json()
        assert health["webSearchMode"] == "not_configured"
        assert health["transcriptionMode"] == "not_configured"
        web = client.get("/api/search/web?q=current+research").json()
        assert web["mode"] == "not_configured"
        assert web["answer"] == ""

        with client.websocket_connect("/api/recordings/live") as websocket:
            event = websocket.receive_json()
            assert event["type"] == "service.error"
            assert event["code"] == "not_configured"


def test_lecture_summary_needs_a_model_and_never_stands_in_for_one() -> None:
    from gunther.lecture import AILectureSummarizer

    lecture = {
        "title": "Cell annotation lecture",
        "durationSeconds": 95,
        "transcript": "CD3D supports T cell identity. What remains uncertain?",
    }
    with make_client() as client:
        missing = client.post("/api/lectures/summarize", json=lecture)
        assert missing.status_code == 503 and "API key" in missing.json()["detail"]

        def answering(outcome):
            return AILectureSummarizer(gateway(FakeProvider(outcome)), model(), "low")

        offline = openai.APIConnectionError(request=httpx.Request("POST", "https://model.test"))
        client.app.state.lecture_summarizer = answering(offline)
        failed = client.post("/api/lectures/summarize", json=lecture)
        assert failed.status_code == 502
        assert failed.json()["detail"] == (
            "The model could not write the summary (Could not reach model.test.). Try again."
        )

        client.app.state.lecture_summarizer = answering(
            '{"overview": "CD3D marks T cells.", "key_points": ["CD3D supports T cell '
            'identity."], "open_questions": ["What remains uncertain?"]}'
        )
        written = client.post("/api/lectures/summarize", json=lecture)
        assert written.status_code == 200
        assert written.json()["engine"] == "DeepSeek · deepseek-flash"
        assert written.json()["openQuestions"] == ["What remains uncertain?"]


def test_recording_audio_is_preserved_and_retrievable(tmp_path) -> None:
    app = create_app(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            recordings_dir=tmp_path / "recordings",
            seed_demo=False,
            deepseek_api_key=None,
        )
    )
    with TestClient(app) as client:
        saved = client.post(
            "/api/recordings?title=Live%20lecture",
            content=b"example-audio-bytes",
            headers={"Content-Type": "audio/webm"},
        )
        assert saved.status_code == 201
        asset = saved.json()
        assert asset["id"].startswith("rec_")
        assert asset["sizeBytes"] == len(b"example-audio-bytes")
        fetched = client.get(f"/api/recordings/{asset['id']}")
        assert fetched.status_code == 200
        assert fetched.content == b"example-audio-bytes"


def test_long_recording_is_saved_incrementally(tmp_path) -> None:
    app = create_app(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            recordings_dir=tmp_path / "recordings",
            seed_demo=False,
            deepseek_api_key=None,
        )
    )
    with TestClient(app) as client:
        preflight = client.options(
            "/api/recordings/rec_000000000000000000000000/chunks",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.status_code == 200
        started = client.post(
            "/api/recordings/sessions?title=Four%20hour%20course",
            headers={"Content-Type": "audio/webm"},
        )
        assert started.status_code == 201
        asset = started.json()
        first = client.put(
            f"/api/recordings/{asset['id']}/chunks",
            content=b"first-audio-chunk",
            headers={"Content-Type": "audio/webm"},
        )
        second = client.put(
            f"/api/recordings/{asset['id']}/chunks",
            content=b"second-audio-chunk",
            headers={"Content-Type": "audio/webm"},
        )
        assert first.status_code == 200
        assert second.json()["sizeBytes"] == len(b"first-audio-chunksecond-audio-chunk")
        completed = client.post(f"/api/recordings/{asset['id']}/complete")
        assert completed.status_code == 200
        fetched = client.get(f"/api/recordings/{asset['id']}")
        assert fetched.content == b"first-audio-chunksecond-audio-chunk"

        imported = client.post(
            "/api/recordings/sessions?title=Existing%20lesson",
            headers={"Content-Type": "audio/mpeg"},
        )
        assert imported.status_code == 201
        assert imported.json()["fileName"].endswith(".mp3")


def test_deepseek_extraction_failure_preserves_source_with_local_rules() -> None:
    failing = FakeProvider(api_error(openai.InternalServerError, 500, "provider unavailable"))
    extractor = ModelExtractor(gateway(failing))
    result = extractor.extract("Cell note", "CD3D -> marker_of -> T cell")

    assert result.mode == "local"
    assert result.assertions[0].subject_label == "CD3D"


def test_session_keeps_messages_context_and_citations() -> None:
    with make_client() as client:
        base_id = create_base(client, "Single Cell")
        imported = client.post(
            "/api/sources",
            json={
                "title": "Cell identity note",
                "kind": "note",
                "content": "CD3D -> marker_of -> T cell",
                "knowledgeBaseId": base_id,
            },
        ).json()
        source_id = imported["source"]["id"]
        created = client.post(
            f"/api/knowledge-bases/{base_id}/sessions",
            json={"focusChapterId": "identity", "selectedSourceIds": [source_id]},
        )
        assert created.status_code == 201
        session_id = created.json()["id"]

        turn = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "What supports T-cell identity?"},
        )
        assert turn.status_code == 200
        body = turn.json()
        assert body["session"]["title"] == "What supports T-cell identity?"
        assert body["assistantMessage"]["citations"][0]["sourceId"] == source_id
        assert body["assistantMessage"]["context"] == {
            "sourcesConsidered": 1,
            "assertionsConsidered": 1,
            "verifiedAssertions": 0,
            "retrievalMode": "selected",
            "responderMode": "local",
            "model": None,
            "modelLabel": None,
            "effort": None,
            "effortLabel": None,
            "notes": [],
            "reasoning": None,
            "modelError": "Ask needs a language model. Set one up under Settings → Models.",
            "intent": None,
            "style": "balanced",
            "steps": [],
            "webSearched": False,
            "checked": False,
            "supportNotes": [],
            "work": None,
            "skill": None,
            "research": None,
            "interrupted": None,
        }

        loaded = client.get(f"/api/sessions/{session_id}").json()
        assert [message["role"] for message in loaded["messages"]] == ["user", "assistant"]
        assert loaded["messageCount"] == 2


def test_sessions_can_be_pinned_and_archived_without_deleting_history() -> None:
    with make_client() as client:
        base_id = create_base(client, "Biology")
        session = client.post(
            f"/api/knowledge-bases/{base_id}/sessions", json={"title": "Marker review"}
        ).json()
        session_id = session["id"]
        updated = client.patch(
            f"/api/sessions/{session_id}", json={"pinned": True, "archived": True}
        )
        assert updated.status_code == 200
        assert updated.json()["pinned"] is True
        assert updated.json()["archived"] is True
        assert client.get(f"/api/knowledge-bases/{base_id}/sessions").json() == []
        archived = client.get(
            f"/api/knowledge-bases/{base_id}/sessions?includeArchived=true"
        ).json()
        assert archived[0]["id"] == session_id
        assert client.get(f"/api/sessions/{session_id}").status_code == 200
        reply = client.post(f"/api/sessions/{session_id}/messages", json={"content": "Continue"})
        assert reply.status_code == 400
        assert "Restore the archived session" in reply.json()["detail"]
        context_change = client.patch(
            f"/api/sessions/{session_id}", json={"focusChapterId": "new-focus"}
        )
        assert context_change.status_code == 400
        assert "changing its retrieval context" in context_change.json()["detail"]


def test_a_removed_answer_is_saved_again_with_the_same_unit() -> None:
    with make_client() as client:
        base_id, session_id, message_id = answered_session(client, "Removable")
        proposal_url = f"/api/sessions/{session_id}/messages/{message_id}/proposal"
        units_url = f"/api/knowledge-bases/{base_id}/units"

        saved = client.post(proposal_url, json={}).json()
        unit_id = saved["knowledgeUnitId"]
        assert saved["status"] == "accepted"
        [unit] = client.get(units_url).json()
        assert (unit["id"], unit["status"]) == (unit_id, "trusted")

        removed = client.patch(
            f"/api/proposals/{saved['id']}",
            json={"status": "rejected", "reason": "Removed from knowledge"},
        ).json()
        assert removed["status"] == "rejected"
        assert removed["knowledgeUnitId"] == unit_id
        [unit] = client.get(units_url).json()
        assert unit["status"] == "deprecated"

        again = client.post(proposal_url, json={}).json()
        assert (again["id"], again["status"], again["knowledgeUnitId"]) == (
            saved["id"],
            "accepted",
            unit_id,
        )
        [unit] = client.get(units_url).json()
        assert (unit["id"], unit["status"], unit["revisionCount"]) == (unit_id, "trusted", 1)

        # Saving something that is already saved changes nothing.
        unchanged = client.post(proposal_url, json={}).json()
        assert unchanged == again


def test_saved_answers_without_a_unit_get_one_when_the_app_starts(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}"

    def start() -> TestClient:
        return TestClient(
            create_app(
                Settings(
                    database_url=url,
                    seed_demo=False,
                    deepseek_api_key=None,
                    stt_provider="compatible",
                    processing_worker_enabled=False,
                )
            )
        )

    with start() as client:
        base_id, session_id, message_id = answered_session(client, "Older data")
        saved = client.post(
            f"/api/sessions/{session_id}/messages/{message_id}/proposal", json={}
        ).json()
    # Databases from before saving made the unit hold saved answers without one.
    engine = create_database_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM knowledge_units")
    finally:
        engine.dispose()

    with start() as client:
        [unit] = client.get(f"/api/knowledge-bases/{base_id}/units").json()
        assert unit["sourceProposalId"] == saved["id"]
        assert unit["status"] == "trusted"


def test_assistant_answer_can_be_saved_as_knowledge() -> None:
    with make_client() as client:
        create_base(client, "Learning")
        client.post(
            "/api/sources",
            json={
                "title": "Learning evidence",
                "kind": "note",
                "content": "Spaced repetition improves retention.",
                "knowledgeBaseId": "learning",
            },
        )
        knowledge_session = client.post(
            "/api/knowledge-bases/learning/sessions",
            json={"focusChapterId": "practice"},
        ).json()
        turn = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages",
            json={"content": "What improves retention?"},
        ).json()
        assistant_id = turn["assistantMessage"]["id"]

        created = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages/{assistant_id}/proposal",
            json={"title": "Retention practice"},
        )
        assert created.status_code == 201
        proposal = created.json()
        assert proposal["status"] == "accepted"
        assert proposal["knowledgeUnitId"].startswith("unt_")
        assert proposal["targetChapterId"] == "practice"
        assert proposal["content"] == turn["assistantMessage"]["content"]

        duplicate = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages/{assistant_id}/proposal",
            json={},
        ).json()
        assert duplicate["id"] == proposal["id"]
        pending = client.get("/api/knowledge-bases/learning/proposals?status=pending").json()
        assert pending == []

        accepted = client.patch(
            f"/api/proposals/{proposal['id']}",
            json={"status": "accepted", "reason": "Reviewed against the citation"},
        ).json()
        assert accepted["status"] == "accepted"
        assert accepted["decisionReason"] == "Reviewed against the citation"
        assert accepted["knowledgeUnitId"].startswith("unt_")

        units = client.get("/api/knowledge-bases/learning/units").json()
        assert len(units) == 1
        assert units[0]["id"] == accepted["knowledgeUnitId"]
        assert units[0]["status"] == "trusted"
        assert units[0]["content"] == turn["assistantMessage"]["content"]
        assert units[0]["revisionCount"] == 1
        assert units[0]["sourceSessionId"] == knowledge_session["id"]
        assert units[0]["sourceMessageId"] == assistant_id
        assert units[0]["targetChapterId"] == "practice"
        assert units[0]["evidenceCount"] == len(turn["assistantMessage"]["citations"])

        accepted_again = client.patch(
            f"/api/proposals/{proposal['id']}",
            json={"status": "accepted", "reason": "Still accepted"},
        ).json()
        assert accepted_again["knowledgeUnitId"] == accepted["knowledgeUnitId"]
        assert len(client.get("/api/knowledge-bases/learning/units").json()) == 1

        search = client.get("/api/search?q=retention").json()
        assert {item["kind"] for item in search} == {
            "knowledge_unit",
            "session",
            "source",
        }
        assert {item["knowledgeBaseId"] for item in search} == {"learning"}
        unit_result = next(item for item in search if item["kind"] == "knowledge_unit")
        assert unit_result["sourceSessionId"] == knowledge_session["id"]
        assert unit_result["sourceMessageId"] == assistant_id

        phrase_search = client.get("/api/search?q=spaced+repetition+retention").json()
        assert phrase_search
        assert any(item["knowledgeBaseId"] == "learning" for item in phrase_search)


def test_a_version_from_the_old_builder_stays_readable_sealed_and_bound_to_its_library() -> None:
    with make_client() as client:
        base_id = create_base(client, "Artifact Biology")
        other_base_id = create_base(client, "Other Field")
        client.post(
            "/api/sources",
            json={
                "title": "Reviewed evidence",
                "kind": "note",
                "content": "CD3D is a useful T-cell marker.",
                "knowledgeBaseId": base_id,
            },
        )
        knowledge_session = client.post(
            f"/api/knowledge-bases/{base_id}/sessions",
            json={},
        ).json()
        turn = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages",
            json={"content": "Which marker identifies T cells?"},
        ).json()
        proposal = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages/"
            f"{turn['assistantMessage']['id']}/proposal",
            json={"title": "T-cell identity"},
        ).json()
        unit_id = proposal["knowledgeUnitId"]
        workspace_id = client.get("/api/workspace/bootstrap").json()["workspaceId"]
        headers = {"X-Gunther-Workspace-Id": workspace_id}
        sessions = client.app.state.knowledge_service.sessions
        first_id = insert_legacy_version(
            sessions,
            workspace_id=workspace_id,
            base_id=base_id,
            unit_id=unit_id,
            request_id="artifact_request_0001",
            title="Field guide",
        )
        second_id = insert_legacy_version(
            sessions,
            workspace_id=workspace_id,
            base_id=base_id,
            unit_id=unit_id,
            request_id="artifact_request_0002",
            supersedes=first_id,
            title="Field guide",
        )

        detail = client.get(f"/api/knowledge-bases/{base_id}/artifacts/{first_id}", headers=headers)
        assert detail.status_code == 200
        first = detail.json()
        assert first["knowledgeBaseId"] == base_id
        assert first["workspaceId"] == workspace_id
        assert first["versionNumber"] == 1
        assert first["supersedesArtifactId"] is None
        assert len(first["manifestHash"]) == 64
        assert first["acceptedUnitIds"] == [unit_id]
        assert first["unitSnapshots"][0]["unitId"] == unit_id
        assert first["unitSnapshots"][0]["revisionNumber"] == 1
        assert len(first["unitSnapshots"][0]["contentHash"]) == 64
        assert f"accepted knowledge unit `{unit_id}`" in first["content"]
        # It reads as a report from before agents: its format is its style, with no citations.
        assert (first["format"], first["kind"], first["style"], first["origin"]) == (
            "field_guide",
            "report",
            "field_guide",
            "legacy",
        )
        assert (first["citations"], first["sections"], first["scope"], first["modelLabel"]) == (
            [],
            [],
            None,
            None,
        )
        # Its provenance is the version 1 shape, exactly.
        assert first["provenance"] == {
            "schemaVersion": 1,
            "generator": "gunther.local-template.v1",
            "workspaceId": workspace_id,
            "knowledgeBaseId": base_id,
            "knowledgeBaseQuestion": "What belongs in Artifact Biology?",
            "acceptedOnly": True,
            "acceptedUnitIds": [unit_id],
            "revisionIds": [first["unitSnapshots"][0]["revisionId"]],
        }

        listed = client.get(f"/api/knowledge-bases/{base_id}/artifacts", headers=headers)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [second_id, first_id]
        assert [(item["kind"], item["style"], item["origin"]) for item in listed.json()] == [
            ("report", "field_guide", "legacy")
        ] * 2
        assert "content" not in listed.json()[0]
        second = client.get(
            f"/api/knowledge-bases/{base_id}/artifacts/{second_id}", headers=headers
        ).json()
        assert second["lineageId"] == first["lineageId"]
        assert second["versionNumber"] == 2
        assert second["supersedesArtifactId"] == first_id

        # Changing one needs an output built by agents: this one has no citations or scope.
        assert (
            client.post(
                f"/api/knowledge-bases/{base_id}/artifacts/{second_id}/edits",
                headers=headers,
                json={"clientRequestId": "edit_request_0001", "content": "# Typed"},
            ).status_code
            == 400
        )

        assert (
            client.get(
                f"/api/knowledge-bases/{other_base_id}/artifacts/{first_id}",
                headers=headers,
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/knowledge-bases/{base_id}/artifacts",
                headers={"X-Gunther-Workspace-Id": "wsp_wrong"},
            ).status_code
            == 409
        )
        assert client.get(f"/api/knowledge-bases/{base_id}/artifacts").status_code == 422

        with client.app.state.knowledge_service.sessions() as database_session:
            artifact = database_session.get(Artifact, first_id)
            assert artifact is not None
            artifact.content = "X" + artifact.content[1:]
            database_session.commit()
        corrupted_detail = client.get(
            f"/api/knowledge-bases/{base_id}/artifacts/{first_id}",
            headers=headers,
        )
        assert corrupted_detail.status_code == 409
        assert "integrity" in corrupted_detail.json()["detail"].lower()
        assert (
            client.get(
                f"/api/knowledge-bases/{base_id}/artifacts",
                headers=headers,
            ).status_code
            == 409
        )


def test_session_retrieval_excludes_unrelated_claims_from_citations() -> None:
    with make_client() as client:
        create_base(client, "Biology")
        imported = client.post(
            "/api/sources",
            json={
                "title": "Mixed starter knowledge",
                "kind": "note",
                "content": (
                    "CD3D -> marker_of -> T cell\n"
                    "T cell -> is_a -> lymphocyte\n"
                    "Backpropagation -> depends_on -> chain rule\n"
                    "Gradient descent -> uses -> gradients"
                ),
                "knowledgeBaseId": "biology",
            },
        ).json()
        knowledge_session = client.post(
            "/api/knowledge-bases/biology/sessions",
            json={"selectedSourceIds": [imported["source"]["id"]]},
        ).json()
        turn = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages",
            json={"content": "What supports T-cell identity?"},
        ).json()

        quotes = {citation["quote"] for citation in turn["assistantMessage"]["citations"]}
        assert quotes == {
            "CD3D -> marker_of -> T cell",
            "T cell -> is_a -> lymphocyte",
        }


def test_knowledge_base_source_membership_bounds_default_retrieval() -> None:
    with make_client() as client:
        create_base(client, "Biology")
        create_base(client, "Software")
        biology = client.post(
            "/api/sources",
            json={
                "title": "Biology source",
                "kind": "note",
                "content": "CD3D -> marker_of -> T cell",
                "knowledgeBaseId": "biology",
            },
        ).json()["source"]
        software = client.post(
            "/api/sources",
            json={
                "title": "Software source",
                "kind": "note",
                "content": "React uses components.",
                "knowledgeBaseId": "software",
            },
        ).json()["source"]

        biology_sources = client.get("/api/knowledge-bases/biology/sources").json()
        assert [source["id"] for source in biology_sources] == [biology["id"]]
        assert client.get("/api/knowledge-bases/software/sources").json()[0]["id"] == software["id"]

        knowledge_session = client.post("/api/knowledge-bases/biology/sessions", json={}).json()
        turn = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages",
            json={"content": "What does CD3D mark?"},
        ).json()
        cited_source_ids = {
            citation["sourceId"] for citation in turn["assistantMessage"]["citations"]
        }
        assert cited_source_ids == {biology["id"]}
        assert software["id"] not in cited_source_ids


def test_unrelated_question_returns_an_evidence_gap_without_padding_citations() -> None:
    with make_client() as client:
        create_base(client, "Biology")
        client.post(
            "/api/sources",
            json={
                "title": "Biology source",
                "kind": "note",
                "content": "CD3D -> marker_of -> T cell",
                "knowledgeBaseId": "biology",
            },
        )
        knowledge_session = client.post("/api/knowledge-bases/biology/sessions", json={}).json()
        turn = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages",
            json={"content": "How do black holes evaporate?"},
        ).json()

        assert turn["assistantMessage"]["citations"] == []
        assert "needs a language model" in turn["assistantMessage"]["content"]
        assert turn["assistantMessage"]["context"]["responderMode"] == "local"
        proposal = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages/"
            f"{turn['assistantMessage']['id']}/proposal",
            json={},
        )
        assert proposal.status_code == 400
        assert "citation is required" in proposal.json()["detail"]


def test_session_does_not_answer_from_one_generic_word_overlap() -> None:
    with make_client() as client:
        create_base(client, "Biology")
        client.post(
            "/api/sources",
            json={
                "title": "Cell identity note",
                "kind": "note",
                "content": "CD3D -> marker_of -> T cell\nT cell -> is_a -> lymphocyte",
                "knowledgeBaseId": "biology",
            },
        )
        knowledge_session = client.post("/api/knowledge-bases/biology/sessions", json={}).json()

        turn = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages",
            json={
                "content": ("What evidence supports checking doublets before cell-type annotation?")
            },
        ).json()

        assert turn["assistantMessage"]["citations"] == []
        assert "needs a language model" in turn["assistantMessage"]["content"]


def test_session_grounds_an_answer_in_raw_source_text_without_extracted_claims() -> None:
    with make_client() as client:
        create_base(client, "Lecture notes")
        imported = client.post(
            "/api/sources",
            json={
                "title": "Clustering lecture",
                "kind": "recording",
                "content": (
                    "# Clustering lecture\n\n"
                    "## Summary\n\n"
                    "RNA counts need quality control before clustering. "
                    "Doublets and low quality cells can distort downstream annotation."
                ),
                "knowledgeBaseId": "lecture-notes",
            },
        ).json()
        assert imported["created"]["assertions"] == 0
        source_id = imported["source"]["id"]
        knowledge_session = client.post(
            "/api/knowledge-bases/lecture-notes/sessions", json={}
        ).json()

        turn = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages",
            json={"content": "Do RNA counts need quality control before clustering?"},
        ).json()

        citations = turn["assistantMessage"]["citations"]
        assert citations
        assert citations[0]["sourceId"] == source_id
        assert citations[0]["assertionId"] is None
        assert citations[0]["locator"] == "Summary"
        # A passage of whole sentences, not a lone sentence.
        assert citations[0]["quote"] == (
            "RNA counts need quality control before clustering. "
            "Doublets and low quality cells can distort downstream annotation."
        )
        assert citations[0]["status"] == "provisional"
        assert turn["assistantMessage"]["context"] == {
            "sourcesConsidered": 1,
            "assertionsConsidered": 0,
            "verifiedAssertions": 0,
            "retrievalMode": "all",
            "responderMode": "local",
            "model": None,
            "modelLabel": None,
            "effort": None,
            "effortLabel": None,
            "notes": [],
            "reasoning": None,
            "modelError": "Ask needs a language model. Set one up under Settings → Models.",
            "intent": None,
            "style": "balanced",
            "steps": [],
            "webSearched": False,
            "checked": False,
            "supportNotes": [],
            "work": None,
            "skill": None,
            "research": None,
            "interrupted": None,
        }
        assert "Clustering lecture" in turn["assistantMessage"]["content"]

        proposal = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages/"
            f"{turn['assistantMessage']['id']}/proposal",
            json={"title": "Quality control before clustering"},
        )
        assert proposal.status_code == 201


def test_empty_knowledge_base_does_not_leak_global_sources() -> None:
    with make_client() as client:
        create_base(client, "Unrelated")
        client.post(
            "/api/sources",
            json={
                "title": "Unassigned private note",
                "kind": "note",
                "content": "Private strategy improves leverage.",
            },
        )
        empty_session = client.post("/api/knowledge-bases/unrelated/sessions", json={}).json()
        turn = client.post(
            f"/api/sessions/{empty_session['id']}/messages",
            json={"content": "What improves leverage?"},
        ).json()

        assert turn["assistantMessage"]["citations"] == []
        assert turn["assistantMessage"]["context"]["sourcesConsidered"] == 0
        assert "needs a language model" in turn["assistantMessage"]["content"]


def test_session_rejects_unknown_source_scope() -> None:
    with make_client() as client:
        create_base(client, "Research")
        created = client.post(
            "/api/knowledge-bases/research/sessions",
            json={"selectedSourceIds": ["src_missing"]},
        )
        assert created.status_code == 400
        assert created.json()["detail"] == "Unknown source IDs: src_missing"


def test_session_rejects_a_source_from_another_knowledge_base() -> None:
    with make_client() as client:
        create_base(client, "Biology")
        create_base(client, "Software")
        software_source = client.post(
            "/api/sources",
            json={
                "title": "Software note",
                "kind": "note",
                "content": "React -> uses -> components",
                "knowledgeBaseId": "software",
            },
        ).json()["source"]

        created = client.post(
            "/api/knowledge-bases/biology/sessions",
            json={"selectedSourceIds": [software_source["id"]]},
        )

        assert created.status_code == 400
        assert "not indexed in Knowledge Base biology" in created.json()["detail"]
        biology_sources = client.get("/api/knowledge-bases/biology/sources").json()
        assert biology_sources == []


def test_writes_reject_an_unknown_knowledge_base() -> None:
    with make_client() as client:
        session = client.post("/api/knowledge-bases/missing/sessions", json={})
        assert session.status_code == 404
        imported = client.post(
            "/api/sources",
            json={
                "title": "Orphan note",
                "kind": "note",
                "content": "Evidence improves decisions.",
                "knowledgeBaseId": "missing",
            },
        )
        assert imported.status_code == 404


def test_duplicate_source_can_join_another_knowledge_base() -> None:
    payload = {
        "title": "Shared source",
        "kind": "note",
        "content": "Evidence improves decisions.",
        "knowledgeBaseId": "management",
    }
    with make_client() as client:
        create_base(client, "Management")
        create_base(client, "Research")
        source_id = client.post("/api/sources", json=payload).json()["source"]["id"]
        payload["knowledgeBaseId"] = "research"
        duplicate = client.post("/api/sources", json=payload).json()
        assert duplicate["duplicate"] is True
        assert client.get("/api/knowledge-bases/research/sources").json()[0]["id"] == source_id


def test_session_can_branch_from_an_earlier_answer_without_mutating_parent() -> None:
    with make_client() as client:
        create_base(client, "Learning")
        client.post(
            "/api/sources",
            json={
                "title": "Branch evidence",
                "kind": "note",
                "content": "Practice improves recall.",
                "knowledgeBaseId": "learning",
            },
        )
        parent = client.post(
            "/api/knowledge-bases/learning/sessions", json={"title": "Study design"}
        ).json()
        first_turn = client.post(
            f"/api/sessions/{parent['id']}/messages",
            json={"content": "What improves recall?"},
        ).json()
        client.post(
            f"/api/sessions/{parent['id']}/messages",
            json={"content": "What remains uncertain?"},
        )

        branch = client.post(
            f"/api/sessions/{parent['id']}/messages/{first_turn['assistantMessage']['id']}/branch"
        )
        assert branch.status_code == 201
        branched = branch.json()
        assert branched["title"] == "Branch · Study design"
        assert branched["parentSessionId"] == parent["id"]
        assert branched["branchedFromMessageId"] == first_turn["assistantMessage"]["id"]
        assert len(branched["messages"]) == 2
        assert branched["messages"][1]["citations"][0]["quote"] == "Practice improves recall."
        assert branched["messages"][1]["id"] != first_turn["assistantMessage"]["id"]

        original = client.get(f"/api/sessions/{parent['id']}").json()
        assert original["parentSessionId"] is None
        assert len(original["messages"]) == 4
