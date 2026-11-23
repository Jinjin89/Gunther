import json
from pathlib import Path

import pytest
from fake_models import FakeProvider, agent_replies, output_replies, studio_task
from test_artifact_agent import turn
from test_key_results import checks, create_session, list_results, old_answer, setup
from test_outputs import SIDECAR, app_for, body, build, built, saved_output, workspace

from evals.sse import ask_turn
from gunther.documents import text_document
from gunther.models import Asset, Source

PASSAGE = "The bottles in the shaded rack stayed at four degrees overnight."
RESULT = "The cold-storage trial maintained 4 °C during the night."
QUERY = "cold-storage trial"
ANSWER = "The bottles stayed at four degrees [1]."
OUTLINE = {"title": "Temperature log", "sections": [{"heading": "Reading", "goal": "Report it"}]}
REPORT = f"## Reading\n\n{ANSWER}"
ORIENTATION = "Key results already checked in this library"


def record_result(client, fake, worker, base_id, source_ids):
    old_answer(client, worker, base_id, source_ids)
    fake.replies[:] = [
        json.dumps({"items": [{"text": RESULT, "passages": list(range(1, len(source_ids) + 1))}]}),
        json.dumps(checks("supported")),
    ]
    assert worker.run_once(ai=True)
    [result] = list_results(client, base_id)
    assert result["text"] == RESULT
    return result


@pytest.mark.parametrize("ordinary_hit", [False, True])
def test_ask_points_to_an_original_passage_once(tmp_path: Path, monkeypatch, ordinary_hit):
    fake = FakeProvider(write_lock_path=tmp_path / "gunther.sqlite")
    with app_for(tmp_path, fake, ai_summaries="off", semantic_search=False) as client:
        base_id, sources, worker = setup(client, {"Rack log": PASSAGE})
        result = record_result(client, fake, worker, base_id, list(sources.values()))
        [original] = result["evidence"]
        session_id = create_session(client, base_id)
        query = f"{QUERY} shaded rack" if ordinary_hit else QUERY
        read = worker.index.retrieve
        hits = []

        def search(*args, **kwargs):
            found = read(*args, **kwargs)
            hits.extend(item.block.id for item in found)
            return found

        monkeypatch.setattr(worker.index, "retrieve", search)

        def write(request):
            prompt = request["messages"][-1]["content"]
            notes, pool = prompt.split("\nThe pool:\n", 1)
            assert ORIENTATION in notes and f"- {RESULT} See [1]." in notes
            assert pool.count("(library · ") == 1
            assert PASSAGE in pool and RESULT not in pool
            assert "[2]" not in pool
            return ANSWER

        def check(request):
            prompt = request["messages"][-1]["content"]
            assert PASSAGE in prompt and RESULT not in prompt
            return checks("supported")

        fake.replies[:] = [agent_replies(
            write, {"action": "search_library", "query": query}, {"action": "answer"},
            checked=check,
        )]
        answer = ask_turn(
            client, session_id, {"content": "How cold did the samples stay?"}, headers=SIDECAR,
        )["assistantMessage"]
        assert hits == ([original["blockId"]] if ordinary_hit else [])
        [citation] = answer["citations"]
        assert citation["ref"] == 1 and citation["quote"] == PASSAGE
        assert citation["sourceRevisionId"] == original["revisionId"]
        assert citation["blockId"] == original["blockId"]
        assert answer["context"]["coverage"]["status"] == "complete"
        assert answer["content"] == ANSWER
        assert not fake.write_lock_failures


@pytest.mark.parametrize("scope", ["selection", "section"])
def test_ask_requires_a_complete_origin_in_its_source_and_section_scope(tmp_path: Path, scope):
    fake = FakeProvider(write_lock_path=tmp_path / "gunther.sqlite")
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, sources, worker = setup(client, {
            "Rack log": PASSAGE, "Sensor log": "A recording was taken after sunset.",
        })
        result = record_result(client, fake, worker, base_id, list(sources.values()))
        session_id = create_session(client, base_id)
        if scope == "selection":
            fields = {"selectedSourceIds": [sources["Sensor log"]]}
        else:
            topics = f"/api/knowledge-bases/{base_id}/topics"
            topic_id = client.post(
                topics, headers=SIDECAR, json={"title": "Sensor methods"},
            ).raise_for_status().json()["id"]
            sensor = next(p for p in result["evidence"] if p["sourceId"] == sources["Sensor log"])
            client.post(
                f"{topics}/{topic_id}/evidence", headers=SIDECAR,
                json={"blockId": sensor["blockId"]},
            ).raise_for_status()
            fields = {"focusChapterId": topic_id}

        def write(request):
            prompt = request["messages"][-1]["content"]
            assert ORIENTATION not in prompt and RESULT not in prompt and PASSAGE not in prompt
            return "No matching passage."

        fake.replies[:] = [agent_replies(
            write, {"action": "search_library", "query": QUERY}, {"action": "answer"},
        )]
        answer = ask_turn(
            client, session_id, {"content": "How cold did the samples stay?", **fields},
            headers=SIDECAR,
        )["assistantMessage"]
        assert answer["content"] == "No matching passage."
        assert answer["citations"] == []
        assert not fake.write_lock_failures


def test_studio_seeds_the_pinned_reading_and_renders_the_result_as_orientation(tmp_path: Path):
    fake = FakeProvider(write_lock_path=tmp_path / "gunther.sqlite")
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, sources, worker = setup(client, {"Rack log": PASSAGE})
        source_id = sources["Rack log"]
        with worker.sessions.begin() as session:
            session.add(Asset(
                id="cooling_pdf", content_hash="a" * 64, original_name="log.pdf",
                media_type="application/pdf", size_bytes=24, relative_path="aa/log.pdf",
            ))
            session.flush()
            source = session.get(Source, source_id)
            source.asset_id, source.kind = "cooling_pdf", "paper"
        result = record_result(client, fake, worker, base_id, [source_id])
        [original] = result["evidence"]
        with worker.sessions.begin() as session:
            source = session.get(Source, source_id)
            reading = text_document("Four degrees was the overnight reading in the shade.")
            newest = worker.index.publish(
                session, source, reading.chunks(), document=reading, parser="docling",
            )
            assert newest.id != original["revisionId"]

        before = len(fake.requests)
        fake.replies[:] = [output_replies(OUTLINE, [REPORT])]
        conversation = client.post(
            f"/api/knowledge-bases/{base_id}/artifact-conversations", headers=workspace(client),
            json={"id": "checked_passage"},
        ).raise_for_status().json()
        artifact = built(turn(
            client, base_id, conversation["id"], "", "build",
            build={"type": "report", "focus": QUERY},
        ))["artifact"]
        [citation] = artifact["citations"]
        assert citation["ref"] == 1 and citation["quote"] == PASSAGE
        assert citation["sourceRevisionId"] == original["revisionId"]
        assert citation["blockId"] == original["blockId"]
        assert citation["sourceOrigin"] == "selected"
        assert RESULT not in artifact["content"]
        writer = next(r for r in fake.requests[before:] if "Write the report" in studio_task(r))
        prompt = writer["messages"][-1]["content"]
        assert ORIENTATION in prompt and f"- {RESULT} See [1]." in prompt
        assert PASSAGE in prompt
        assert "Four degrees was the overnight reading in the shade." in prompt
        assert not fake.write_lock_failures


def test_rebuild_uses_the_old_outputs_saved_answer_scope(tmp_path: Path):
    fake = FakeProvider(agent_replies(ANSWER), write_lock_path=tmp_path / "gunther.sqlite")
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, _, _ = setup(client, {"Rack log": PASSAGE})
        session_id = create_session(client, base_id)
        saved = ask_turn(client, session_id, {"content": "shaded rack"}, headers=SIDECAR)
        message_id = saved["assistantMessage"]["id"]
        unit_id = client.post(
            f"/api/sessions/{session_id}/messages/{message_id}/proposal", headers=SIDECAR,
            json={"title": "Saved temperature answer"},
        ).raise_for_status().json()["knowledgeUnitId"]
        scope = {"mode": "selection", "unitIds": [unit_id]}
        conversation = client.post(
            f"/api/knowledge-bases/{base_id}/artifact-conversations", headers=workspace(client),
            json={"id": "old_scope"},
        ).raise_for_status().json()
        assert conversation["scope"]["mode"] == "library"
        parent = saved_output(
            client, base_id, outline=OUTLINE, written=[REPORT],
            scope=scope, conversationId=conversation["id"],
        )

        before = len(fake.requests)
        fake.replies[:] = [output_replies(OUTLINE, [REPORT])]
        rebuilt = built(turn(
            client, base_id, conversation["id"], "", "rebuild",
            rebuild={"artifactId": parent["id"], "outline": parent["outline"]},
        ))["artifact"]
        assert rebuilt["scope"] == parent["scope"]
        assert rebuilt["unitSnapshots"] == parent["unitSnapshots"]
        assert rebuilt["citations"][0]["quote"] == PASSAGE
        writing = next(r for r in fake.requests[before:] if "Write the report" in studio_task(r))
        assert "Saved temperature answer" in writing["messages"][-1]["content"]

        before = len(fake.requests)
        fresh = built(build(client, base_id, body()))
        assert fresh["scope"]["mode"] == "library" and fresh["unitSnapshots"] == []
        writing = next(r for r in fake.requests[before:] if "Write the report" in studio_task(r))
        assert "Saved temperature answer" not in writing["messages"][-1]["content"]
        assert not fake.write_lock_failures
