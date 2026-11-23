import json
import logging
from pathlib import Path

from fake_models import FakeProvider, agent_replies
from test_agent import SIDECAR, app_for, ready_session

from evals.sse import ask_turn
from gunther import trace


def ask(client, session_id: str, question: str = "What marks T cells?") -> dict:
    return ask_turn(client, session_id, {"content": question}, headers=SIDECAR)["assistantMessage"]


def kinds(steps: list[dict]) -> list[str]:
    return [step["kind"] for step in steps]


def test_answers_keep_how_they_were_made_only_while_asked(tmp_path: Path) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
        )
    )
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        assert client.get("/api/settings/developer", headers=SIDECAR).json() == {"traces": False}
        turned_on = client.put("/api/settings/developer", headers=SIDECAR, json={"traces": True})
        assert turned_on.json() == {"traces": True}
        first = ask(client, session_id)
        found = client.get(
            f"/api/sessions/{session_id}/messages/{first['id']}/trace", headers=SIDECAR
        )
        # Another conversation cannot read it.
        other = client.get(f"/api/sessions/elsewhere/messages/{first['id']}/trace", headers=SIDECAR)

        client.put("/api/settings/developer", headers=SIDECAR, json={"traces": False})
        second = ask(client, session_id, "And CD4?")
        missing = client.get(
            f"/api/sessions/{session_id}/messages/{second['id']}/trace", headers=SIDECAR
        )
    assert found.status_code == 200 and other.status_code == 404
    assert missing.status_code == 404 and "Settings → Developer" in missing.json()["detail"]
    kept = found.json()
    steps = kept["steps"]
    assert kept["version"] == 1 and kept["totalMs"] >= 0
    assert kinds(steps)[:2] == ["plan", "search"]
    assert "write" in kinds(steps) and kinds(steps)[-1] == "cite"
    plan = steps[0]
    assert plan["action"] == "search_library" and plan["query"] == "CD3D"
    [planning_call] = plan["children"]
    assert planning_call["kind"] == "model" and "JSON" in planning_call["system"]
    assert planning_call["messages"][-1]["role"] == "user" and planning_call["reply"]
    search = steps[1]
    assert search["query"] == "CD3D" and search["results"][0]["title"] == "Markers"
    write = next(step for step in steps if step["kind"] == "write")
    [writing] = write["children"]
    assert writing["reply"] == "CD3D marks T cells [1]." and writing["ms"] >= 0
    assert steps[-1]["sources"] == ["[1] Markers (pool 1)"]


def test_trace_text_is_clipped_and_nothing_is_kept_without_a_trace() -> None:
    assert trace.note("plan", "nothing is kept") is None
    with trace.step("plan", "no trace") as entry:
        trace.update(entry, action="answer")
    assert entry == {}
    kept = trace.Trace()
    with trace.tracing(kept), trace.step("write", "long") as entry:
        trace.update(entry, reply="x" * (trace.MAX_TEXT + 10))
    [step] = kept.out()["steps"]
    assert step["reply"].endswith("10 more characters not kept")
    assert trace.current() is None


def test_recent_log_lines_and_background_jobs_are_shown(tmp_path: Path) -> None:
    fake = FakeProvider("unused")
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        base_id = client.get(
            f"/api/sessions/{session_id}", headers=SIDECAR,
        ).raise_for_status().json()["knowledgeBaseId"]
        overview = client.post(
            f"/api/knowledge-bases/{base_id}/overview", headers=SIDECAR,
        ).raise_for_status().json()
        assert overview["state"] == "writing"
        logging.getLogger("gunther.agent").info("The planning step failed: example")
        logging.getLogger("gunther.processing").warning("Knowledge job failed (RuntimeError)")
        everything = client.get("/api/developer/logs", headers=SIDECAR).json()["lines"]
        warnings = client.get("/api/developer/logs?level=warning", headers=SIDECAR).json()["lines"]
        jobs = client.get("/api/developer/jobs", headers=SIDECAR).json()["jobs"]
        phone = client.get("/api/developer/logs")
    messages = [line["message"] for line in everything]
    # Info lines were dropped before: nothing configured logging.
    assert "The planning step failed: example" in messages
    assert [line["message"] for line in warnings][0] == "Knowledge job failed (RuntimeError)"
    assert all(line["level"] in ("warning", "error") for line in warnings)
    source_job = next(job for job in jobs if job["sourceId"])
    assert source_job["sourceTitle"] == "Markers"
    assert source_job["state"] in ("queued", "completed")
    assert source_job["knowledgeBaseId"] is None and source_job["knowledgeBaseTitle"] is None
    [library_job] = [job for job in jobs if job["kind"] == "library_overview"]
    assert library_job["sourceId"] is None and library_job["sourceTitle"] is None
    assert (library_job["knowledgeBaseId"], library_job["knowledgeBaseTitle"]) == (base_id, "Cells")
    assert phone.status_code in (401, 403)
    json.dumps(jobs)
