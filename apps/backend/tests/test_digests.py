"""Summaries of captures: written by a model after reading, citing passages, per kind."""

from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
from fake_models import FakeProvider, gateway, model
from fastapi.testclient import TestClient
from sqlalchemy import select

from gunther import digest
from gunther.config import Settings
from gunther.database import session_scope
from gunther.digest import DigestError, DigestRequest, ModelDigestWriter, Passage
from gunther.main import create_app
from gunther.models import ProcessingJob, Source

LECTURE = (
    "Today we covered how T cells recognise antigens through the T cell receptor. "
    "The receptor binds peptides presented by MHC molecules on other cells. "
    "Helper T cells carry CD4 and cytotoxic T cells carry CD8. "
    "Thymic selection removes T cells whose receptors bind self peptides too strongly. "
    "For homework you should read chapter four on thymic selection before Friday. "
    "Why do some autoreactive T cells escape thymic selection? "
    "Next week we compare T cell receptors with B cell receptors and antibodies."
)


def parsed(**values):
    return digest._Digest(**{
        "title": "T cell recognition",
        "overview": "How T cells recognise antigen.",
        "key_points": [
            digest._Point(text="T cells bind peptides on MHC.", passages=[1]),
        ],
        **values,
    })


def model_writer(*outcomes: object, vision: bool = False) -> ModelDigestWriter:
    """A writer whose model answers ``outcomes`` in turn (replies or errors)."""

    replies = [
        outcome.model_dump_json() if isinstance(outcome, digest._Digest) else outcome
        for outcome in outcomes or (parsed(),)
    ]
    fake = FakeProvider(*replies)
    chosen = model("gpt-test", kind="openai", vision=vision)
    writer = ModelDigestWriter(
        gateway(fake, chosen), (chosen, "low"), (chosen, "low") if vision else None
    )
    writer.fake = fake  # type: ignore[attr-defined]
    return writer


def asked(writer: ModelDigestWriter, index: int = 0) -> tuple[str, object]:
    """The instructions and the question of the ``index``-th request."""

    messages = writer.fake.requests[index]["messages"]  # type: ignore[attr-defined]
    return messages[0]["content"], messages[-1]["content"]


OFFLINE = openai.APIConnectionError(request=httpx.Request("POST", "https://model.test"))


def client_for(path: Path, **overrides) -> TestClient:
    settings = {
        "database_url": f"sqlite+pysqlite:///{path / 'db.sqlite'}",
        "assets_dir": path / "assets",
        "recordings_dir": path / "recordings",
        "processing_worker_enabled": False,
        "ocr_provider": "disabled",
        "seed_demo": False,
        "deepseek_api_key": None,
        "openai_api_key": None,
        "stt_provider": "openai",
        **overrides,
    }
    return TestClient(create_app(Settings(**settings)))


def use_model(client: TestClient, writer: ModelDigestWriter) -> ModelDigestWriter:
    """Stand in for a configured key: summaries are written by ``writer``."""

    index = client.app.state.knowledge_service.index
    index.digest_method, index.digest_off_reason = writer.method, None
    client.app.state.processing_worker.digest_writer = writer
    return writer


def add(client: TestClient, title: str, content: str, kind: str = "note", **extra) -> str:
    response = client.post(
        "/api/sources", json={"title": title, "kind": kind, "content": content, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()["source"]["id"]


def drain(client: TestClient, ai: bool | None = None) -> None:
    worker = client.app.state.processing_worker
    while worker.run_once(ai):
        pass


def fail_now(client: TestClient) -> None:
    """Skip the retry wait of queued summary jobs."""

    with session_scope(client.app.state.knowledge_service.sessions) as session:
        for job in session.scalars(select(ProcessingJob).where(ProcessingJob.kind == "digest")):
            job.available_at = job.created_at


def test_a_capture_is_summarized_after_it_is_read_and_its_points_cite_passages(tmp_path):
    with client_for(tmp_path) as client:
        writer = use_model(client, model_writer(parsed(
            action_items=["Read chapter four before Friday"],
            open_questions=["Why do some autoreactive T cells escape?"],
            terms=["MHC", "CD4"],
        )))
        source_id = add(client, "Immunology lecture 3", LECTURE)
        assert client.get(f"/api/sources/{source_id}/digest").json()["state"] == "writing"

        drain(client)

        state = client.get(f"/api/sources/{source_id}/digest").json()
        assert state["state"] == "ready" and not state["stale"] and state["error"] is None
        summary = state["digest"]
        assert summary["engine"] == "OpenAI" and summary["model"] == "gpt-test"
        assert summary["profile"] == "note"
        assert summary["overview"] == "How T cells recognise antigen."
        [point] = summary["keyPoints"]
        [citation] = point["citations"]
        assert citation["number"] == 1 and citation["blockId"].startswith("blk_")
        assert citation["quote"].startswith("Today we covered")
        assert summary["actionItems"] == ["Read chapter four before Friday"]
        assert "## Key points" in summary["markdown"] and "OpenAI gpt-test" in summary["markdown"]
        instructions, question = asked(writer)
        assert "personal note" in instructions and "[1]" in question


def test_without_a_key_there_are_no_summaries_and_it_says_why(tmp_path):
    with client_for(tmp_path) as client:
        assert client.get("/api/health").json()["digestMode"] == "off"
        assert client.get("/api/health").json()["summaryMode"] == "off"
        source_id = add(client, "Lecture", LECTURE)
        drain(client)
        state = client.get(f"/api/sources/{source_id}/digest").json()
        assert state["state"] == "off" and state["offReason"] == "no_key"
        assert state["digest"] is None
        again = client.post(f"/api/sources/{source_id}/digest")
        assert again.status_code == 409 and "API key" in again.json()["detail"]
        with session_scope(client.app.state.knowledge_service.sessions) as session:
            assert "digest" not in set(session.scalars(select(ProcessingJob.kind)))

    (tmp_path / "off").mkdir()
    with client_for(tmp_path / "off", ai_summaries="off") as client:
        source_id = add(client, "Lecture", LECTURE)
        state = client.get(f"/api/sources/{source_id}/digest").json()
        assert state["state"] == "off" and state["offReason"] == "setting"


def test_a_failing_model_is_retried_then_shown_as_failed_never_replaced(tmp_path):
    with client_for(tmp_path) as client:
        use_model(client, model_writer(OFFLINE))
        source_id = add(client, "Lecture", LECTURE)
        for _ in range(3):
            drain(client)
            fail_now(client)

        state = client.get(f"/api/sources/{source_id}/digest").json()
        assert state["state"] == "failed" and state["digest"] is None
        assert state["error"] == (
            "The model could not write the summary: Could not reach model.test."
        )

        # Trying again once the model answers writes it.
        use_model(client, model_writer(parsed()))
        assert client.post(f"/api/sources/{source_id}/digest").status_code == 202
        drain(client)
        assert client.get(f"/api/sources/{source_id}/digest").json()["state"] == "ready"


def test_an_empty_answer_is_an_error_not_a_summary():
    request = DigestRequest("src", "rev", "Lecture", "lecture", [Passage(1, "b", LECTURE, "")])
    writer = model_writer(parsed(overview="  "))
    try:
        writer.write(request)
    except DigestError:
        pass
    else:
        raise AssertionError("an empty summary must not be kept")


def test_short_notes_are_their_own_summary_but_can_be_summarized_on_request(tmp_path):
    with client_for(tmp_path) as client:
        use_model(client, model_writer())
        short = add(client, "Reminder", "Buy reagents for Friday.")
        drain(client)
        assert client.get(f"/api/sources/{short}/digest").json()["state"] == "none"
        assert client.post(f"/api/sources/{short}/digest").status_code == 202
        drain(client)
        assert client.get(f"/api/sources/{short}/digest").json()["state"] == "ready"


def test_writing_again_replaces_the_summary_and_is_never_queued_twice(tmp_path):
    with client_for(tmp_path) as client:
        use_model(client, model_writer())
        source_id = add(client, "Immunology lecture 3", LECTURE)
        drain(client)
        first = client.get(f"/api/sources/{source_id}/digest").json()["digest"]["updatedAt"]

        again = client.post(f"/api/sources/{source_id}/digest")
        assert again.status_code == 202 and again.json()["state"] == "writing"
        client.post(f"/api/sources/{source_id}/digest")
        with session_scope(client.app.state.knowledge_service.sessions) as session:
            waiting = session.scalars(
                select(ProcessingJob).where(
                    ProcessingJob.kind == "digest", ProcessingJob.state == "queued"
                )
            ).all()
        assert len(waiting) == 1

        drain(client)
        state = client.get(f"/api/sources/{source_id}/digest").json()
        assert state["state"] == "ready" and state["digest"]["updatedAt"] >= first


def test_model_summaries_run_in_their_own_lane(tmp_path):
    with client_for(tmp_path) as client:
        use_model(client, model_writer())
        add(client, "Immunology lecture 3", LECTURE)
        drain(client, ai=False)
        with session_scope(client.app.state.knowledge_service.sessions) as session:
            left = {job.kind: job.state for job in session.scalars(select(ProcessingJob))}
        # Reading and indexing are done; only the summary waits for its lane.
        assert left["digest"] == "queued"
        assert all(state == "completed" for kind, state in left.items() if kind != "digest")
        drain(client, ai=True)
        with session_scope(client.app.state.knowledge_service.sessions) as session:
            assert session.scalar(
                select(ProcessingJob.state).where(ProcessingJob.kind == "digest")
            ) == "completed"


def test_a_models_citations_are_checked_against_the_passages_it_was_given():
    request = DigestRequest(
        source_id="src",
        revision_id="rev",
        title="Lecture",
        profile="lecture",
        passages=[Passage(1, "blk_1", LECTURE[:200], ""), Passage(2, "blk_2", LECTURE[200:], "")],
    )
    writer = model_writer(parsed(key_points=[
        digest._Point(text="T cells bind peptides on MHC.", passages=[1, 7]),
        digest._Point(text="Selection removes strong binders.", passages=[2]),
        digest._Point(text="  ", passages=[1]),
    ], action_items=["Read chapter four"], terms=["MHC", ""]))

    result = writer.write(request)

    assert result.method == "OpenAI:gpt-test" and result.title == "T cell recognition"
    assert [(p.text, p.passages) for p in result.key_points] == [
        ("T cells bind peptides on MHC.", [1]),
        ("Selection removes strong binders.", [2]),
    ]
    assert result.terms == ["MHC"]
    instructions, question = asked(writer)
    assert "recorded lecture" in instructions
    assert "[1]" in question and "[2]" in question


def test_a_photo_is_shown_to_a_vision_model(tmp_path):
    image = tmp_path / "board.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    request = DigestRequest(
        source_id="src", revision_id="rev", title="Whiteboard", profile="image",
        passages=[], image_path=image, image_type="image/png",
    )
    writer = model_writer(parsed(overview="A whiteboard sketch of the T cell receptor."),
                          vision=True)
    result = writer.write(request)

    assert result.overview.startswith("A whiteboard sketch")
    _, content = asked(writer)
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    # A model that cannot see is never sent the picture; without text there is nothing to do.
    blind = model("deepseek-v4-pro")
    unseeing = ModelDigestWriter(gateway(FakeProvider(), blind), (blind, "low"), (blind, "low"))
    assert not unseeing.vision and unseeing.write(request) is None


def test_a_table_is_described_to_the_model_by_its_columns():
    table = "gene\tcluster\tlog2fc\nCD3D\tT cell\t2.5\nCD3E\tT cell\t2.1\nMS4A1\tB cell\t3.4\n"
    shape, facts = digest.table_facts(table)
    assert shape == "3 rows × 3 columns: gene, cluster, log2fc"
    assert "log2fc: 2.1 to 3.4, mean 2.67" in facts
    assert any(fact.startswith("cluster: 2 values, most often T cell (2)") for fact in facts)

    request = DigestRequest(
        source_id="src", revision_id="rev", title="Markers", profile="table",
        passages=[Passage(1, "blk_1", table, "")], table=table,
    )
    writer = model_writer()
    writer.write(request)
    _, prompt = asked(writer)
    assert "Column statistics:" in prompt and "log2fc: 2.1 to 3.4" in prompt


def test_each_kind_of_capture_gets_its_own_kind_of_summary():
    def source(kind: str) -> Source:
        return Source(id="src", title="x", kind=kind, content="")

    image = SimpleNamespace(media_type="image/jpeg")
    pdf = SimpleNamespace(media_type="application/pdf")
    assert digest.profile_for(source("recording"), None, "meeting") == "meeting"
    assert digest.profile_for(source("recording"), None, "memo") == "memo"
    assert digest.profile_for(source("course"), None, None) == "lecture"
    assert digest.profile_for(source("file"), image, None) == "image"
    assert digest.profile_for(source("paper"), pdf, None) == "paper"
    assert digest.profile_for(source("file"), pdf, None) == "document"
    assert digest.profile_for(source("link"), None, None) == "web"
    assert digest.profile_for(source("table"), None, None) == "table"
    assert digest.profile_for(source("note"), None, None) == "note"


def test_the_summary_is_written_into_the_library_folder(tmp_path):
    root = tmp_path / "Gunther"
    with client_for(tmp_path, library_root=root) as client:
        use_model(client, model_writer())
        base = client.post(
            "/api/knowledge-bases",
            json={"title": "Immunology", "question": "How do T cells work?",
                  "description": "Lectures"},
        ).json()["id"]
        add(client, "Lecture 3", LECTURE, knowledgeBaseId=base)
        drain(client)
        client.app.state.library_folders.sync()

        folder = root / "Libraries" / "Immunology"
        summary = next(folder.glob("Sources/*Lecture 3/summary.md")).read_text()
        assert summary.startswith("# Lecture 3\n\nHow T cells recognise antigen.")
        assert "## Passages cited" in summary and "[1]" in summary
        assert "summary.md" in (folder / "Overview.md").read_text()


def test_a_placeholder_title_gives_way_to_the_written_one():
    for title, file_name in [
        ("Untitled source", None),
        ("Lecture · 29/09/2026", None),
        ("Voice memo · 9/29/2026", None),
        ("IMG_2041", "IMG_2041.jpg"),
        ("Screenshot 2026-09-29 at 10.02.11", None),
        ("handout", "handout.pdf"),
    ]:
        assert digest.placeholder_title(title, file_name), title
    for title in ["Immunology lecture 3", "Meeting with Dana", "Imaging pipeline notes",
                  "Photo of the lab whiteboard"]:
        assert not digest.placeholder_title(title, None), title


def test_a_recording_saved_under_its_default_name_takes_the_summarys_title(tmp_path):
    with client_for(tmp_path) as client:
        use_model(client, model_writer())
        source_id = add(client, "Lecture · 29/09/2026", LECTURE, kind="recording")
        chosen = add(client, "Immunology lecture 3", LECTURE + " Again.", kind="recording")
        drain(client)

        assert client.get(f"/api/sources/{source_id}").json()["title"] == "T cell recognition"
        assert client.get(f"/api/sources/{chosen}").json()["title"] == "Immunology lecture 3"
        state = client.get(f"/api/sources/{source_id}/digest").json()
        assert state["digest"]["profile"] == "lecture"
