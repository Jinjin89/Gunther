import json
from datetime import timedelta
from pathlib import Path

import httpx
from fake_models import FakeProvider, agent_replies
from sqlalchemy import select
from test_outputs import SIDECAR, app_for, library, route, saved_output, workspace

from evals.sse import ask_turn
from gunther import key_result_origins, key_results, online_search
from gunther.llm import ModelError
from gunther.models import (
    ContentBlock,
    KeyResult,
    KeyResultEvidence,
    KeyResultJob,
    KnowledgeUnit,
    KnowledgeUnitRevision,
    SessionMessage,
    Source,
    SourceIndexHead,
    utc_now,
)
from gunther.processing import JOB_LEASE
from gunther.schemas import ConversationCitationOut

FACT = "The trial measured soil moisture."
EXTRA = "The field log records a measurement of soil moisture."
QUESTION = "What did the trial measure?"


def checks(*labels: str) -> dict:
    return {"sentences": [{"n": i, "verdict": label} for i, label in enumerate(labels, 1)]}


def reply():
    return agent_replies(
        FACT.removesuffix(".") + " [1].",
        {"action": "search_library", "query": "soil moisture"},
        {"action": "answer"},
        checked=checks("supported"),
    )


def finding() -> str:
    return json.dumps({"items": [{"text": FACT, "passages": [1]}]})


def prompt(request: dict) -> dict:
    return json.loads(request["messages"][-1]["content"])


def create_session(client, base_id: str) -> str:
    return client.post(
        f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={},
    ).raise_for_status().json()["id"]


def ask(client, session_id: str, **fields) -> dict:
    return ask_turn(
        client, session_id, {"content": QUESTION, **fields}, headers=SIDECAR,
    )["assistantMessage"]


def setup(client, notes=None):
    # These tests exercise the Analysis lane's distiller. Index the notes without
    # also queuing assertion extraction for the same provider.
    client.app.state.knowledge_service.extractor = None
    base_id, sources = library(client, notes=notes if notes is not None else {"Trial log": FACT})
    return base_id, sources, client.app.state.processing_worker


def results_path(base_id: str) -> str:
    return f"/api/knowledge-bases/{base_id}/key-results"


def list_results(client, base_id: str) -> list[dict]:
    return client.get(results_path(base_id), headers=SIDECAR).raise_for_status().json()


def job_state(client, base_id: str, origin: dict, kind="answer") -> dict:
    return client.get(
        f"{results_path(base_id)}/origins/{kind}/{origin['id']}", headers=SIDECAR,
    ).raise_for_status().json()


def old_answer(client, worker, base_id: str, source_ids: list[str], *, enqueue: bool = True):
    """Seed a previously saved answer with citations to indexed originals."""
    session_id = create_session(client, base_id)
    citations = []
    with worker.sessions.begin() as session:
        for n, source_id in enumerate(source_ids, 1):
            source = session.get(Source, source_id)
            head = session.get(SourceIndexHead, source_id)
            block = session.scalar(select(ContentBlock).where(
                ContentBlock.revision_id == head.revision_id,
            ).order_by(ContentBlock.ordinal).limit(1))
            citations.append(ConversationCitationOut(
                id=f"cit_{session_id}_{n}", source_id=source.id, source_title=source.title,
                quote=block.content, locator=block.locator, status="provisional", confidence=1,
                source_revision_id=head.revision_id, block_id=block.id, ref=n,
            ).model_dump())
        refs = "".join(f"[{n}]" for n in range(1, len(citations) + 1))
        message = SessionMessage(
            id=f"msg_{session_id}", session_id=session_id, role="assistant",
            content=f"{FACT.removesuffix('.')} {refs}.", citations_json=json.dumps(citations),
        )
        session.add(message)
        session.flush()
        if enqueue:
            key_result_origins.enqueue_answer(session, base_id, message)
    return {"id": message.id, "sessionId": session_id}


def test_only_raw_supported_results_are_published_and_model_calls_leave_no_write_lock(
    tmp_path: Path,
):
    inference = "The trial must have reduced water use"
    fake = FakeProvider(agent_replies(
        f"The trial measured soil moisture [1]. {inference} [1].",
        {"action": "search_library", "query": "soil moisture"},
        {"action": "answer"}, checked=checks("supported", "inference"),
    ), write_lock_path=tmp_path / "gunther.sqlite")
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, sources, worker = setup(client)
        session_id = create_session(client, base_id)
        origin = ask(client, session_id)
        assert origin["context"]["coverage"]["status"] == "complete"
        assert origin["keyResults"]["state"] == "queued"
        worker.run_once(ai=False)
        assert job_state(client, base_id, origin)["state"] == "queued"

        def distill(request):
            material = prompt(request)
            assert material["claims"] == [f"{FACT} [1]"]
            assert inference not in json.dumps(material)
            assert material["passages"][0]["quote"] == FACT
            return json.dumps({"items": [
                {"text": FACT, "passages": [1]},
                {"text": "The trial used a new watering method.", "passages": [1]},
                {"text": "How did the trial measure it?", "passages": [1]},
                {"text": "There was a second trial.", "passages": [1]},
                {"text": "The trial saved water.", "passages": [99]},
            ]})

        fake.replies[:] = [distill, json.dumps(checks("supported", "inference", "nonfactual"))]
        assert worker.run_once(ai=True)
        [result] = list_results(client, base_id)
        [evidence] = result["evidence"]
        assert result["text"] == FACT
        assert result["origin"]["id"] == origin["id"]
        assert result["origin"]["sessionId"] == session_id
        assert (evidence["sourceId"], evidence["sourceTitle"], evidence["quote"]) == (
            sources["Trial log"], "Trial log", FACT,
        )
        assert evidence["locator"] and evidence["regionIds"]
        state = job_state(client, base_id, origin)
        assert (state["state"], state["keptCount"], state["keptIds"]) == ("done", 1, [result["id"]])
        message = client.get(f"/api/sessions/{session_id}", headers=SIDECAR).json()["messages"][-1]
        assert message["keyResults"] == state
        with worker.sessions.begin() as session:
            stored = session.scalar(select(KeyResultEvidence))
            assert stored.source_content_hash == session.get(Source, stored.source_id).content_hash
            key_result_origins.enqueue_answer(
                session, base_id, session.get(SessionMessage, origin["id"]),
            )
        before = len(fake.requests)
        assert not worker.run_once(ai=True)
        assert len(fake.requests) == before
        assert job_state(client, base_id, origin) == state
        assert not fake.write_lock_failures


def test_same_as_merge_undo_preserves_the_first_origins_evidence(tmp_path: Path):
    fake = FakeProvider(finding(), json.dumps(checks("supported")))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, sources, worker = setup(client, {"Trial log": FACT, "Field log": EXTRA})
        first = old_answer(client, worker, base_id, [sources["Trial log"]])
        assert worker.run_once(ai=True)
        [initial] = list_results(client, base_id)
        second = old_answer(client, worker, base_id, list(sources.values()))

        def merge(request):
            material = prompt(request)
            assert material["existing"] == [
                {"id": initial["id"], "text": FACT, "status": "kept"},
            ]
            return json.dumps({"items": [
                {"same_as": initial["id"], "passages": [2]},
                {"text": EXTRA, "passages": [2]},
            ]})

        fake.replies[:] = [merge, json.dumps(checks("supported", "supported"))]
        assert worker.run_once(ai=True)
        assert job_state(client, base_id, second)["keptCount"] == 2
        merged = next(r for r in list_results(client, base_id) if r["id"] == initial["id"])
        assert {p["quote"] for p in merged["evidence"]} == {FACT, EXTRA}
        undone = client.post(
            f"{results_path(base_id)}/origins/answer/{second['id']}/undo", headers=SIDECAR,
        ).raise_for_status().json()
        assert undone["keptCount"] == 0 and undone["keptIds"] == []
        assert list_results(client, base_id) == [initial]
        assert job_state(client, base_id, first)["keptIds"] == [initial["id"]]
        with worker.sessions() as session:
            removed = session.scalar(select(KeyResult).where(KeyResult.text == EXTRA))
            assert removed.status == "removed"


def test_removed_result_stays_suppressed_for_both_same_as_and_exact_text(tmp_path: Path):
    fake = FakeProvider(finding(), json.dumps(checks("supported")))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, sources, worker = setup(client)
        old_answer(client, worker, base_id, list(sources.values()))
        assert worker.run_once(ai=True)
        [result] = list_results(client, base_id)
        assert client.post(
            f"{results_path(base_id)}/{result['id']}/remove", headers=SIDECAR,
        ).status_code == 204

        def merge(request):
            assert prompt(request)["existing"] == [
                {"id": result["id"], "text": FACT, "status": "removed"},
            ]
            return json.dumps({"items": [{"same_as": result["id"], "passages": [1]}]})

        for replies in ([merge], [finding(), json.dumps(checks("supported"))]):
            origin = old_answer(client, worker, base_id, list(sources.values()))
            fake.replies[:] = replies
            assert worker.run_once(ai=True)
            assert job_state(client, base_id, origin)["keptCount"] == 0
            assert list_results(client, base_id) == []
        with worker.sessions() as session:
            [stored] = session.scalars(select(KeyResult)).all()
            assert stored.id == result["id"] and stored.status == "removed"
            assert len(session.scalars(select(KeyResultEvidence)).all()) == 1


def test_remove_with_undo_restores_the_result_and_rejects_an_old_removal(tmp_path: Path):
    fake = FakeProvider(finding(), json.dumps(checks("supported")))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, sources, worker = setup(client)
        origin = old_answer(client, worker, base_id, list(sources.values()))
        assert worker.run_once(ai=True)
        [result] = list_results(client, base_id)
        path = f"{results_path(base_id)}/{result['id']}"

        removed = client.post(f"{path}/remove", headers=SIDECAR, params={"withUndo": True})
        assert removed.status_code == 200, removed.text
        stamp = removed.json()["removedAt"]
        assert isinstance(stamp, str) and stamp
        assert list_results(client, base_id) == []
        assert client.post(
            f"{path}/remove", headers=SIDECAR, params={"withUndo": True},
        ).status_code == 409

        assert client.post(
            f"{path}/restore", headers=SIDECAR, params={"removedAt": stamp},
        ).status_code == 204
        assert list_results(client, base_id) == [result]
        assert job_state(client, base_id, origin)["keptIds"] == [result["id"]]

        later = client.post(
            f"{path}/remove", headers=SIDECAR, params={"withUndo": True},
        ).raise_for_status().json()["removedAt"]
        assert later != stamp
        assert client.post(
            f"{path}/restore", headers=SIDECAR, params={"removedAt": stamp},
        ).status_code == 409
        assert list_results(client, base_id) == []
        assert client.post(
            f"{path}/restore", headers=SIDECAR, params={"removedAt": later},
        ).status_code == 204
        assert list_results(client, base_id) == [result]


def test_restore_rejects_a_result_whose_creating_origin_was_undone(tmp_path: Path):
    fake = FakeProvider(finding(), json.dumps(checks("supported")))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, sources, worker = setup(client, {"Trial log": FACT, "Field log": EXTRA})
        first = old_answer(client, worker, base_id, [sources["Trial log"]])
        assert worker.run_once(ai=True)
        [result] = list_results(client, base_id)
        second = old_answer(client, worker, base_id, list(sources.values()))
        fake.replies[:] = [
            json.dumps({"items": [{"same_as": result["id"], "passages": [2]}]}),
            json.dumps(checks("supported")),
        ]
        assert worker.run_once(ai=True)
        assert job_state(client, base_id, second)["keptIds"] == [result["id"]]

        path = f"{results_path(base_id)}/{result['id']}"
        stamp = client.post(
            f"{path}/remove", headers=SIDECAR, params={"withUndo": True},
        ).raise_for_status().json()["removedAt"]
        client.post(
            f"{results_path(base_id)}/origins/answer/{first['id']}/undo", headers=SIDECAR,
        ).raise_for_status()
        with worker.sessions() as session:
            assert list(session.scalars(select(KeyResultEvidence.origin_id).where(
                KeyResultEvidence.key_result_id == result["id"],
            ))) == [second["id"]]
        assert client.post(
            f"{path}/restore", headers=SIDECAR, params={"removedAt": stamp},
        ).status_code == 409
        assert list_results(client, base_id) == []


def test_home_queues_on_filing_and_ignores_rework_asking_and_web_only(tmp_path: Path, monkeypatch):
    fake = FakeProvider(reply())
    with app_for(
        tmp_path, fake, ai_summaries="off", tavily_api_key="tvly-test-0000000000001234",
    ) as client:
        base_id, _, worker = setup(client)
        home = create_session(client, "@home")
        origin = ask(client, home, knowledgeBaseIds=[base_id])
        assert origin["citations"][0]["kind"] == "library" and origin["keyResults"] is None
        fake.replies[:] = [agent_replies(
            "It measured moisture [1].", {"action": "answer", "mode": "transform"},
        )]
        rework = ask(client, home, content="Shorten that.", knowledgeBaseIds=[base_id])
        assert rework["citations"] and rework["context"]["rework"]
        fake.replies[:] = [agent_replies(
            "", framed={"clear": False, "ask_user": ["Which trial?"]},
        )]
        asking = ask(client, home, skill="research", knowledgeBaseIds=[base_id])
        assert asking["context"]["research"]["state"] == "asking"
        monkeypatch.setattr(online_search.TavilySearch, "_post", lambda self, body: httpx.Response(
            200, json={"results": [{
                "url": "https://example.org/soil", "title": "Soil report", "content": FACT,
            }]},
        ))
        fake.replies[:] = [agent_replies(
            "A trial measured soil moisture [1].",
            {"action": "search_web", "query": "soil moisture"},
            {"action": "answer"}, checked=checks("supported"),
        )]
        web = ask(client, create_session(client, base_id), web=True)
        assert web["citations"][0]["kind"] == "web" and web["keyResults"] is None
        assert not worker.run_once(ai=True)
        client.post(
            f"/api/sessions/{home}/file", headers=SIDECAR, json={"knowledgeBaseId": base_id},
        ).raise_for_status()
        with worker.sessions() as session:
            [job] = session.scalars(select(KeyResultJob)).all()
            assert (job.origin_id, job.state) == (origin["id"], "queued")
        assert job_state(client, base_id, origin)["state"] == "queued"


def test_settings_resume_needs_model_and_retry_a_checker_failure(tmp_path: Path):
    fake = FakeProvider(reply())
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        service = client.app.state.knowledge_service
        analysis = service.models.for_role("analysis")[0].ref

        def set_analysis(ref):
            client.put("/api/settings/model-roles", headers=SIDECAR, json={
                "roles": {"analysis": {"model": ref, "effort": "low"}},
            }).raise_for_status()

        set_analysis(None)
        base_id, _, worker = setup(client)
        origin = ask(client, create_session(client, base_id))
        assert worker.run_once(ai=True)
        state = job_state(client, base_id, origin)
        assert state["state"] == "needs_model"
        activity_id = state["id"]

        def activity():
            items = client.get("/api/activity", headers=SIDECAR).raise_for_status().json()["items"]
            return next(row for row in items if row["id"] == activity_id)

        assert activity()["title"] == "Key results need an Analysis model"
        assert not worker.run_once(ai=True)
        set_analysis(analysis)
        assert job_state(client, base_id, origin)["state"] == "queued"
        fake.replies[:] = [finding(), ModelError("The Checker was unavailable")]
        assert worker.run_once(ai=True)
        failed = job_state(client, base_id, origin)
        assert failed["state"] == "failed" and failed["keptCount"] == 0
        assert activity()["kind"] == "key_results"
        assert activity()["state"] == "failed" and activity()["error"] == failed["error"]
        assert not worker.run_once(ai=True)
        retry = client.post(
            f"{results_path(base_id)}/jobs/{activity_id}/retry", headers=SIDECAR,
        ).raise_for_status().json()
        assert retry["state"] == "queued" and retry["error"] is None
        fake.replies[:] = [finding(), json.dumps(checks("supported"))]
        assert worker.run_once(ai=True)
        assert job_state(client, base_id, origin)["keptCount"] == 1


def test_trusted_saved_answer_distills_once_and_preserves_its_artifact_seal(tmp_path: Path):
    fake = FakeProvider(finding(), json.dumps(checks("supported")))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, sources, worker = setup(client)
        answer = old_answer(client, worker, base_id, list(sources.values()), enqueue=False)
        unit_id = client.post(
            f"/api/sessions/{answer['sessionId']}/messages/{answer['id']}/proposal",
            headers=SIDECAR, json={"title": "Trial result"},
        ).raise_for_status().json()["knowledgeUnitId"]
        outline = {"title": "Trial", "sections": [{"heading": "Summary"}]}
        artifact = saved_output(
            client, base_id, outline, ["## Summary\n\nSee the methods section."],
            scope={"mode": "selection", "unitIds": [unit_id]},
        )
        assert artifact["unitSnapshots"][0]["unitId"] == unit_id
        with worker.sessions() as session:
            unit = session.get(KnowledgeUnit, unit_id)
            revision_id = unit.head_revision_id
            assert unit.status == "trusted"
            assert session.scalar(select(KeyResultJob)) is None
        assert worker.run_once(ai=True)
        [result] = list_results(client, base_id)
        assert (result["origin"]["kind"], result["origin"]["id"]) == ("saved_answer", unit_id)
        assert job_state(client, base_id, {"id": unit_id}, kind="saved_answer")["keptCount"] == 1
        assert not worker.run_once(ai=True)
        sealed = client.get(route(base_id, artifact["id"]), headers=workspace(client))
        assert sealed.raise_for_status().json() == artifact
        with worker.sessions() as session:
            assert session.get(KnowledgeUnit, unit_id).head_revision_id == revision_id


def test_pending_saved_answers_show_trusted_head_content_until_distilled(tmp_path: Path):
    fake = FakeProvider()
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, sources, worker = setup(client)

        def save_answer(title: str) -> dict:
            answer = old_answer(client, worker, base_id, list(sources.values()), enqueue=False)
            return client.post(
                f"/api/sessions/{answer['sessionId']}/messages/{answer['id']}/proposal",
                headers=SIDECAR, json={"title": title},
            ).raise_for_status().json()

        title = "Trial result"
        trusted = save_answer(title)["knowledgeUnitId"]
        retired = save_answer("Withdrawn result")
        client.patch(
            f"/api/proposals/{retired['id']}", headers=SIDECAR, json={"status": "rejected"},
        ).raise_for_status()
        head_text = "The trial recorded soil moisture [1]."
        with worker.sessions.begin() as session:
            unit = session.get(KnowledgeUnit, trusted)
            revision = session.get(KnowledgeUnitRevision, unit.head_revision_id)
            revision.content = head_text

        path = f"{results_path(base_id)}/pending-saved-answers"
        expected = [{"id": trusted, "title": title, "content": head_text}]
        assert client.get(path, headers=SIDECAR).raise_for_status().json() == expected

        def distill(_request):
            assert client.get(path, headers=SIDECAR).raise_for_status().json() == expected
            return finding()

        fake.replies[:] = [distill, json.dumps(checks("supported"))]
        assert worker.run_once(ai=True)
        state = job_state(client, base_id, {"id": trusted}, kind="saved_answer")
        assert (state["state"], state["keptCount"]) == ("done", 1)
        assert client.get(path, headers=SIDECAR).raise_for_status().json() == []


def test_a_running_job_blocks_its_library_and_expired_ownership_cannot_publish(tmp_path: Path):
    with app_for(tmp_path, FakeProvider(), ai_summaries="off") as client:
        first, _, worker = setup(client, {})
        other, _ = library(client, title="Other observations", notes={})
        with worker.sessions.begin() as session:
            key_results.enqueue(session, first, "answer", "first")
            key_results.enqueue(session, first, "answer", "second")
            key_results.enqueue(session, other, "answer", "other")
            held, _ = key_results.claim(session, configured=False, lease=JOB_LEASE)
            independent, _ = key_results.claim(session, configured=False, lease=JOB_LEASE)
            assert (held.origin_id, independent.origin_id) == ("first", "other")
            assert key_results.claim(session, configured=False, lease=JOB_LEASE) == (None, [])
        with worker.sessions.begin() as session:
            row = session.get(KeyResultJob, held.id)
            row.updated_at = utc_now() - JOB_LEASE - timedelta(seconds=1)
        with worker.sessions.begin() as session:
            next_job, expired = key_results.claim(session, configured=False, lease=JOB_LEASE)
            assert next_job.origin_id == "second"
            assert [(j.id, j.state) for j in expired] == [(held.id, "failed")]
            assert session.scalar(select(KeyResultJob).where(
                key_results.owned(held, utc_now(), JOB_LEASE),
            )) is None
