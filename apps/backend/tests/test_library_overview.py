import json
from datetime import timedelta

import openai
import pytest
from fake_models import FakeProvider, api_error
from sqlalchemy import select
from test_outputs import app_for, library, workspace
from test_papers import make_pdf

from gunther.library_overview import KIND
from gunther.models import LibraryOverview, ProcessingJob, SourceDigest, SourceIndexHead, utc_now


def capture(client, headers, library_id, title, kind, content):
    return client.post("/api/sources?deferAnalysis=true", headers=headers, json={
        "knowledgeBaseId": library_id, "title": title, "kind": kind, "content": content,
    }).raise_for_status().json()["source"]["id"]


def request(client, headers, library_id, *, retry=False):
    return client.post(
        f"/api/knowledge-bases/{library_id}/overview", headers=headers, params={"retry": retry},
    ).raise_for_status().json()


def input_rows(call):
    return {row["kind"]: row["text"] for row in json.loads(call["messages"][-1]["content"])}


def reply(summary="The materials cover three ways to monitor rainfall."):
    return json.dumps({"summary": summary, "suggested_questions": ["How is rainfall measured?"]})


def setup(client):
    # Leave other Analysis work alone while exercising the real library job path.
    client.app.state.knowledge_service.extractor = None
    worker = client.app.state.processing_worker
    worker.document_parser = None
    worker.structure_reviser = None
    return workspace(client), worker


def test_analysis_combines_pdf_abstract_note_digest_and_recording_passage(tmp_path):
    during = []

    def analyze(_call):
        during.append((
            request(client, headers, base_id)["state"],
            client.get("/api/activity", headers=headers).raise_for_status().json()["items"],
        ))
        return reply()

    fake = FakeProvider(analyze)
    with app_for(tmp_path, fake, ai_summaries="off", ocr_provider="disabled") as client:
        headers, worker = setup(client)
        base_id, _ = library(client, "Field measurements", notes={})
        pdf = make_pdf([
            (20, "Measuring rainfall"),
            (11, "A. Reader"),
            (12, "Abstract"),
            (11, "Rain gauges recorded twelve days of precipitation."),
            (12, "1 Introduction"),
            (11, "This introduction gives background on the equipment."),
        ])
        client.post("/api/captures/assets", headers={
            **headers, "Content-Type": "application/pdf",
        }, params={
            "title": "Measuring rainfall", "fileName": "rain.pdf", "kind": "paper",
            "knowledgeBaseId": base_id,
        }, content=pdf).raise_for_status()
        while worker.run_once(ai=False):
            pass
        note = capture(client, headers, base_id, "Field notes", "note", "A note on checking rain.")
        capture(client, headers, base_id, "Audio", "recording",
                "The recording describes where to place the gauge.")
        with worker.sessions.begin() as session:
            revision = session.get(SourceIndexHead, note).revision_id
            session.add(SourceDigest(
                source_id=note, revision_id=revision,
                overview="The field notes describe retaining gauge readings each morning.",
            ))

        assert request(client, headers, base_id)["state"] == "writing"
        assert request(client, headers, base_id)["state"] == "writing"
        worker.claim(ai=False)
        with worker.sessions() as session:
            job = session.scalar(select(ProcessingJob).where(ProcessingJob.kind == KIND))
            assert job.state == "queued"
        assert worker.run_once(ai=True)
        assert not worker.run_once(ai=True)
        assert request(client, headers, base_id) == {
            "state": "ready", "summary": "The materials cover three ways to monitor rainfall.",
            "suggestedQuestions": ["How is rainfall measured?"], "error": None,
        }
        [item] = client.get("/api/activity", headers=headers).raise_for_status().json()["items"]
        assert (item["kind"], item["state"]) == ("overview", "done")
        with worker.sessions() as session:
            assert len(list(session.scalars(select(ProcessingJob).where(
                ProcessingJob.knowledge_base_id == base_id, ProcessingJob.kind == KIND,
            )))) == 1
        [(state, [running])] = during
        assert (state, running["kind"], running["state"], running["open"]["libraryId"]) == (
            "writing", "overview", "running", base_id,
        )
        [call] = fake.requests
        assert input_rows(call) == {
            "paper": "Rain gauges recorded twelve days of precipitation.",
            "note": "The field notes describe retaining gauge readings each morning.",
            "recording": "The recording describes where to place the gauge.",
        }


def test_empty_library_has_no_job_and_unconfigured_analysis_returns_needs_model(tmp_path):
    fake = FakeProvider()
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        headers, worker = setup(client)
        base_id, _ = library(client, notes={})
        assert request(client, headers, base_id)["state"] == "empty"
        service = client.app.state.knowledge_service
        service.models = None
        capture(client, headers, base_id, "Logs", "note", "The rain gauge was emptied.")
        assert request(client, headers, base_id)["state"] == "needs_model"
        with worker.sessions() as session:
            assert session.get(LibraryOverview, base_id) is None
            assert session.scalar(select(ProcessingJob).where(ProcessingJob.kind == KIND)) is None
        assert not fake.requests


def test_membership_and_contribution_changes_invalidate_the_overview(tmp_path):
    fake = FakeProvider(reply("First."), reply("Second."), reply("Third."), reply("Fourth."))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        headers, worker = setup(client)
        base_id, _ = library(client, notes={})
        original = capture(client, headers, base_id, "Records", "note", "Records are kept weekly.")

        def refresh():
            assert request(client, headers, base_id)["state"] == "writing"
            assert worker.run_once(ai=True)
            assert request(client, headers, base_id)["state"] == "ready"
            with worker.sessions() as session:
                return session.get(LibraryOverview, base_id).source_fingerprint

        first = refresh()
        extra = capture(
            client, headers, base_id, "Page", "link", "The page gives the full schedule.",
        )
        second = refresh()
        client.post(f"/api/sources/{extra}/trash", headers=headers).raise_for_status()
        third = refresh()
        with worker.sessions.begin() as session:
            session.add(SourceDigest(
                source_id=original, revision_id=session.get(SourceIndexHead, original).revision_id,
                overview="There are new records each week.",
            ))
        fourth = refresh()
        assert first == third and len({first, second, fourth}) == 3
        assert input_rows(fake.requests[1])["link"] == "The page gives the full schedule."
        assert "link" not in input_rows(fake.requests[2])
        assert input_rows(fake.requests[3])["note"] == "There are new records each week."


def test_failure_is_saved_with_no_summary_until_retry(tmp_path):
    fake = FakeProvider(api_error(openai.InternalServerError, 500, "offline"), reply())
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        headers, worker = setup(client)
        base_id, _ = library(client, notes={"Log": "The first trial used a rain gauge."})
        assert request(client, headers, base_id)["state"] == "writing"
        assert worker.run_once(ai=True)
        failure = request(client, headers, base_id)
        assert failure["state"] == "failed" and failure["error"]
        assert failure["summary"] == "" and failure["suggestedQuestions"] == []
        with worker.sessions() as session:
            row = session.get(LibraryOverview, base_id)
            assert row.error == failure["error"] and not row.summary
        [item] = client.get("/api/activity", headers=headers).raise_for_status().json()["items"]
        assert (item["state"], item["error"]) == ("failed", failure["error"])
        assert not worker.run_once(ai=True)
        assert request(client, headers, base_id, retry=True)["state"] == "writing"
        assert worker.run_once(ai=True)
        assert request(client, headers, base_id)["state"] == "ready"


@pytest.mark.parametrize("retry", [False, True], ids=["changed material", "explicit retry"])
def test_exhausted_refresh_stays_failed_for_the_material_it_attempted(tmp_path, retry):
    class Interrupted(BaseException):
        pass

    def crash(_call):
        # Simulate a process exiting before it can publish a model failure.
        raise Interrupted()

    fake = FakeProvider(reply("Saved overview."), crash, crash, crash, reply("Refreshed overview."))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        headers, worker = setup(client)
        base_id, _ = library(client, notes={"Log": "The first trial used a gauge."})
        assert request(client, headers, base_id)["state"] == "writing"
        assert worker.run_once(ai=True)
        assert request(client, headers, base_id)["summary"] == "Saved overview."

        capture(client, headers, base_id, "Second log", "note", "The next trial used a sensor.")
        assert request(client, headers, base_id)["state"] == "writing"
        capture(client, headers, base_id, "Plans", "note", "A third trial is planned.")
        for attempt in range(1, 4):
            with pytest.raises(Interrupted):
                worker.run_once(ai=True)
            with worker.sessions.begin() as session:
                job = session.scalar(select(ProcessingJob).where(ProcessingJob.kind == KIND))
                assert (job.state, job.attempts) == ("running", attempt)
                job.lease_until = utc_now() - timedelta(seconds=1)

        assert not worker.run_once(ai=True)
        failure = request(client, headers, base_id)
        assert failure["state"] == "failed" and "Retry" in failure["error"]
        with worker.sessions() as session:
            job = session.scalar(select(ProcessingJob).where(ProcessingJob.kind == KIND))
            assert (job.state, job.attempts, job.error) == ("failed", 3, failure["error"])
            assert session.get(LibraryOverview, base_id).summary == "Saved overview."
        assert request(client, headers, base_id) == failure
        assert not worker.run_once(ai=True)
        assert len(fake.requests) == 4

        if not retry:
            capture(client, headers, base_id, "Results", "note", "The third trial finished.")
        assert request(client, headers, base_id, retry=retry)["state"] == "writing"
        assert worker.run_once(ai=True)
        assert request(client, headers, base_id)["summary"] == "Refreshed overview."


def test_source_change_during_generation_requeues_before_publishing(tmp_path):
    def change_sources(_call):
        capture(client, headers, base_id, "Second log", "note", "The second trial used a sensor.")
        return reply("The first trial used a gauge.")

    fake = FakeProvider(change_sources, reply("The trials used a gauge and a sensor."))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        headers, worker = setup(client)
        base_id, _ = library(client, notes={"First log": "The first trial used a rain gauge."})
        assert request(client, headers, base_id)["state"] == "writing"
        assert worker.run_once(ai=True)
        with worker.sessions() as session:
            assert session.get(LibraryOverview, base_id) is None
        assert request(client, headers, base_id)["state"] == "writing"
        assert worker.run_once(ai=True)
        result = request(client, headers, base_id)
        assert result["summary"] == "The trials used a gauge and a sensor."
