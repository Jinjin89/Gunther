from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gunther.asset_service import AssetTextExtraction
from gunther.config import Settings
from gunther.content import ParsedBlock, parse_content
from gunther.database import session_scope
from gunther.document_parser import docling_blocks
from gunther.main import create_app
from gunther.models import (
    Assertion,
    EvidenceLink,
    Fragment,
    ProcessingJob,
    Source,
    SourceRevision,
    utc_now,
)


def client_for(path: Path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                database_url=f"sqlite+pysqlite:///{path / 'db.sqlite'}",
                assets_dir=path / "assets",
                recordings_dir=path / "recordings",
                processing_worker_enabled=False,
                ocr_provider="disabled",
                seed_demo=False,
                deepseek_api_key=None,
                stt_provider="openai",
            )
        )
    )


def base(client: TestClient, title: str = "Biology") -> str:
    response = client.post(
        "/api/knowledge-bases",
        json={
            "title": title,
            "question": title,
            "description": "Test knowledge base",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def source(client: TestClient, base_id: str, content: str) -> str:
    response = client.post(
        "/api/sources",
        json={
            "title": "Research notes",
            "kind": "note",
            "content": content,
            "knowledgeBaseId": base_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["source"]["id"]


def ask(client: TestClient, base_id: str, question: str, topic: str | None = None):
    conversation = client.post(
        f"/api/knowledge-bases/{base_id}/sessions",
        json={
            "focusChapterId": topic,
        },
    ).json()
    response = client.post(
        f"/api/sessions/{conversation['id']}/messages", json={"content": question}
    )
    assert response.status_code == 200, response.text
    return response.json()["assistantMessage"]


def capture(client: TestClient, base_id: str):
    response = client.post(
        "/api/captures/assets",
        params={
            "title": "Reading",
            "fileName": "reading.md",
            "kind": "file",
            "knowledgeBaseId": base_id,
            "deferProcessing": "true",
        },
        content=b"# Genomics\n\nGenome quality affects clustering accuracy.",
        headers={"content-type": "text/markdown"},
    )
    assert response.status_code == 201, response.text
    return response.json()["importResult"]["source"]


def test_blocks_preserve_headings_pages_regions_times_and_exact_offsets():
    body = (
        "# Biology\n## Genomics\n<!-- gunther:page=8 -->\n"
        "<!-- gunther:ocr-region=1,2,3,4 unit=ppm provider=vision confidence=0.8 -->\n"
        "Genome analysis needs careful controls.\n[01:02:03] Lecture evidence follows.\n"
    )
    blocks = parse_content(body)
    assert blocks[1].parent == 0
    assert blocks[2].parent == 1
    assert blocks[2].anchor["page"] == 8
    assert blocks[2].anchor["bboxPpm"] == [1, 2, 3, 4]
    assert blocks[-1].anchor["startSeconds"] == 3723
    for block in blocks[2:]:
        assert body[block.anchor["charStart"] : block.anchor["charEnd"]] == block.text
    long_text = "字" * 5000
    assert "".join(block.text for block in parse_content(long_text)) == long_text


def test_docling_adapter_preserves_layout_tree_and_table_cells():
    table = {"table_cells": [{"text": "Gene"}, {"text": "CD3D"}], "num_rows": 2}
    document = {
        "body": {"children": [{"$ref": "#/texts/0"}]},
        "texts": [
            {
                "label": "section_header",
                "text": "Methods",
                "children": [
                    {"$ref": "#/tables/0"},
                ],
            }
        ],
        "tables": [
            {
                "label": "table",
                "data": table,
                "prov": [{"page_no": 8, "bbox": {"l": 10, "t": 20, "r": 80, "b": 90}}],
            }
        ],
    }
    blocks = docling_blocks(document)
    assert blocks[1].parent == 0
    assert blocks[1].headings == ["Methods"]
    assert blocks[1].anchor["page"] == 8
    assert blocks[1].payload["table"] == table
    assert "CD3D" in blocks[1].text


def test_docling_long_paragraphs_are_bounded_without_losing_text():
    text = "Genome quality evidence. " * 200
    blocks = docling_blocks(
        {
            "body": {"children": [{"$ref": "#/texts/0"}]},
            "texts": [{"label": "text", "text": text}],
        }
    )
    assert "".join(block.text for block in blocks) == text
    assert all(len(block.text) <= 1200 for block in blocks)
    assert blocks[-1].anchor["documentCharEnd"] == len(text)


def test_pdf_figure_only_pages_retain_ocr_fallback_text(tmp_path, monkeypatch):
    class FigureParser:
        version = "test-layout"

        def parse(self, path):
            return [
                ParsedBlock("Methods text.", "paragraph", None, [], {"page": 1}, "Page 1"),
                ParsedBlock("Figure", "figure", None, [], {"page": 2}, "Page 2"),
            ]

    monkeypatch.setattr(
        "gunther.processing.extract_asset_content",
        lambda *args: AssetTextExtraction(
            "<!-- gunther:page=2 -->\nGenome quality from scanned page."
        ),
    )
    with client_for(tmp_path) as client:
        saved = client.post(
            "/api/captures/assets",
            params={
                "title": "Scan",
                "fileName": "scan.pdf",
                "kind": "file",
                "knowledgeBaseId": base(client),
                "deferProcessing": "true",
            },
            content=b"%PDF-test-fixture",
            headers={"content-type": "application/pdf"},
        ).json()
        worker = client.app.state.processing_worker
        worker.document_parser = FigureParser()
        assert worker.run_once()
        blocks = client.get(
            f"/api/sources/{saved['importResult']['source']['id']}/structure"
        ).json()["blocks"]
        assert any("Genome quality from scanned page" in block["content"] for block in blocks)


def test_fts_handles_chinese_scope_and_hostile_query(tmp_path):
    with client_for(tmp_path) as client:
        biology, other = base(client), base(client, "Other")
        kept = source(client, biology, "猫咪需要均衡饮食。猫咪饮食需要足够的水分。")
        source(client, other, "猫咪饮食包含绝密信息。")
        answer = ask(client, biology, "猫咪饮食")
        assert answer["citations"]
        assert {c["sourceId"] for c in answer["citations"]} == {kept}
        assert all(c["blockId"] and c["sourceRevisionId"] for c in answer["citations"])
        assert ask(client, biology, '" OR * : NEAR ( )')["citations"] == []


def test_raw_competing_evidence_is_not_hidden_by_an_assertion(tmp_path):
    with client_for(tmp_path) as client:
        biology = base(client)
        a = source(client, biology, "Exercise improves memory.")
        b = source(client, biology, "Our exercise trial found no memory improvement.")
        cited = ask(client, biology, "Does exercise improve memory?")["citations"]
        assert {c["sourceId"] for c in cited} == {a, b}


def test_background_capture_resume_and_idempotent_reprocessing(tmp_path):
    with client_for(tmp_path) as client:
        biology = base(client)
        saved = capture(client, biology)
        assert saved["processing"]["state"] == "queued"
        assert capture(client, biology)["id"] == saved["id"]
        assert client.get(f"/api/assets/{saved['asset']['id']}").status_code == 200
    # New app instance, same persisted job and original.
    with client_for(tmp_path) as client:
        worker = client.app.state.processing_worker
        assert worker.run_once()
        result = client.get(f"/api/sources/{saved['id']}/structure").json()
        assert result["processing"]["state"] == "ready"
        assert any("Genome quality" in b["content"] for b in result["blocks"])
        before = result["revisionId"]
        client.post(f"/api/sources/{saved['id']}/reprocess")
        assert worker.run_once()
        assert client.get(f"/api/sources/{saved['id']}/structure").json()["revisionId"] == before
        assert ask(client, biology, "How does genome quality affect clustering?")["citations"]


def test_cancelled_or_lost_lease_cannot_publish_and_retry_recovers(tmp_path):
    with client_for(tmp_path) as client:
        saved = capture(client, base(client))
        worker = client.app.state.processing_worker
        claimed = worker.claim()
        assert claimed
        assert client.post(f"/api/sources/{saved['id']}/processing/cancel").status_code == 200
        worker.process(*claimed)
        result = client.get(f"/api/sources/{saved['id']}/structure").json()
        assert result["revisionId"] is None
        assert result["processing"]["state"] == "cancelled"
        client.post(f"/api/sources/{saved['id']}/reprocess")
        expired = worker.claim()
        with session_scope(worker.sessions) as session:
            session.get(ProcessingJob, expired[0]).lease_until = utc_now() - timedelta(seconds=1)
        reclaimed = worker.claim()
        assert reclaimed[0] == expired[0] and reclaimed[1] != expired[1]
        worker.process(*expired)
        assert client.get(f"/api/sources/{saved['id']}/structure").json()["revisionId"] is None
        worker.process(*reclaimed)
        assert client.get(f"/api/sources/{saved['id']}/structure").json()["revisionId"]


def test_concurrent_workers_claim_job_once(tmp_path):
    with client_for(tmp_path) as client:
        capture(client, base(client))
        worker = client.app.state.processing_worker
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda _: worker.claim(), range(4)))
        assert len([claim for claim in claims if claim]) == 1


def test_topics_reject_cycles_cross_library_links_and_lost_updates(tmp_path):
    with client_for(tmp_path) as client:
        biology, other = base(client), base(client, "Other")
        a = client.post(f"/api/knowledge-bases/{biology}/topics", json={"title": "Genomics"}).json()
        b = client.post(
            f"/api/knowledge-bases/{biology}/topics",
            json={
                "title": "Quality",
                "parentId": a["id"],
            },
        ).json()
        route = f"/api/knowledge-bases/{biology}/topics/{a['id']}"
        assert (
            client.patch(
                route, json={"title": "Genomics", "version": 1, "parentId": b["id"]}
            ).status_code
            == 422
        )
        assert client.patch(route, json={"title": "Genetics", "version": 1}).status_code == 200
        assert client.patch(route, json={"title": "Stale", "version": 1}).status_code == 409
        assert (
            client.post(
                f"/api/knowledge-bases/{other}/topics",
                json={
                    "title": "Invalid",
                    "parentId": a["id"],
                },
            ).status_code
            == 422
        )
        private = source(client, other, "Genome quality is confidential.")
        block = client.get(f"/api/sources/{private}/structure").json()["blocks"][0]
        assert client.post(route + "/evidence", json={"blockId": block["id"]}).status_code == 422


def test_topic_subtree_limits_ask_and_historical_citations_remain_addressable(tmp_path):
    with client_for(tmp_path) as client:
        biology = base(client)
        src = source(
            client,
            biology,
            "# Quality\nRNA counts need quality control.\n"
            "# Clustering\nRNA clustering uses another method.",
        )
        structured = client.get(f"/api/sources/{src}/structure").json()
        root = client.post(
            f"/api/knowledge-bases/{biology}/topics", json={"title": "Methods"}
        ).json()
        child = client.post(
            f"/api/knowledge-bases/{biology}/topics",
            json={
                "title": "Quality",
                "parentId": root["id"],
            },
        ).json()
        block = structured["blocks"][1]
        endpoint = f"/api/knowledge-bases/{biology}/topics/{child['id']}/evidence"
        assert client.post(endpoint, json={"blockId": block["id"]}).status_code == 200
        assert client.post(endpoint, json={"blockId": block["id"]}).json()["blockIds"] == [
            block["id"]
        ]
        answer = ask(client, biology, "RNA quality control", root["id"])
        assert [c["blockId"] for c in answer["citations"]] == [block["id"]]
        index = client.app.state.knowledge_service.index
        with session_scope(index.sessions) as session:
            index.publish(
                session, session.get(Source, src), parse_content("Updated source content.")
            )
        old = client.get(
            f"/api/sources/{src}/structure",
            params={
                "revision_id": structured["revisionId"],
                "block_id": block["id"],
            },
        ).json()
        assert old["blocks"][0]["content"] == block["content"]
        pinned = ask(client, biology, "RNA quality control", root["id"])
        assert pinned["citations"][0]["blockId"] == block["id"]
        assert client.get(f"/api/sources/{src}").json()["content"].startswith("# Quality")


def test_topic_assertions_choose_scoped_evidence_and_its_pinned_revision(tmp_path):
    with client_for(tmp_path) as client:
        biology = base(client)
        src = source(
            client, biology, "Exercise improves memory.\nClinical exercise supports memory."
        )
        # A distinct source contains the same words, but is not filed under this topic.
        source(client, biology, "Clinical exercise supports memory.\nUnrelated study.")
        structured = client.get(f"/api/sources/{src}/structure").json()
        block = next(b for b in structured["blocks"] if b["content"].startswith("Clinical"))
        topic = client.post(
            f"/api/knowledge-bases/{biology}/topics", json={"title": "Exercise"}
        ).json()
        client.post(
            f"/api/knowledge-bases/{biology}/topics/{topic['id']}/evidence",
            json={"blockId": block["id"]},
        )
        index = client.app.state.knowledge_service.index
        with session_scope(index.sessions) as session:
            assertion = session.scalar(
                select(Assertion).where(
                    Assertion.source_id == src, Assertion.predicate == "improves"
                )
            )
            fragment = session.scalar(
                select(Fragment).where(
                    Fragment.source_id == src, Fragment.content == block["content"]
                )
            )
            assertion_id = assertion.id
            session.add(
                EvidenceLink(id="evd_extra", assertion_id=assertion.id, fragment_id=fragment.id)
            )
            index.publish(session, session.get(Source, src), parse_content("Newly parsed content."))
        citations = ask(client, biology, "exercise memory", topic["id"])["citations"]
        assert citations
        assert all(c["sourceId"] == src and c["blockId"] == block["id"] for c in citations)
        assert any(c["assertionId"] == assertion_id for c in citations)


def test_embedding_backfill_after_enabling_model_pins_revision(tmp_path):
    with client_for(tmp_path) as client:
        index = client.app.state.knowledge_service.index
        src = source(client, base(client), "Felines eat fish.")
        old_revision = client.get(f"/api/sources/{src}/structure").json()["revisionId"]
        index.embedder = FakeEmbedder()
        index.backfill()
        with session_scope(index.sessions) as session:
            job = session.scalar(select(ProcessingJob).where(ProcessingJob.kind == "embed"))
            assert job.revision_id == old_revision
            index.publish(session, session.get(Source, src), parse_content("Updated feline notes."))
        worker = client.app.state.processing_worker
        assert worker.run_once()
        assert worker.run_once()
        diagnostics = client.get("/api/retrieval/status").json()
        assert diagnostics["embeddedBlocks"] == 2


class FakeEmbedder:
    model_id = "test-embedding-v1"

    def encode(self, texts, *, query=False):
        return [[1.0, 0.0] for _ in texts]


def test_optional_semantic_retrieval_is_scoped_and_falls_back_safely(tmp_path):
    with client_for(tmp_path) as client:
        index = client.app.state.knowledge_service.index
        index.embedder = FakeEmbedder()
        biology, other = base(client), base(client, "Other")
        kept = source(client, biology, "Felines eat fish.")
        source(client, other, "Classified dietary information.")
        worker = client.app.state.processing_worker
        while worker.run_once():
            pass
        citations = ask(client, biology, "cat nutrition")["citations"]
        assert {c["sourceId"] for c in citations} == {kept}
        with session_scope(index.sessions) as session:
            assert session.scalar(select(func.count()).select_from(SourceRevision)) == 2

        class BrokenEmbedder(FakeEmbedder):
            def encode(self, texts, *, query=False):
                raise RuntimeError("secret provider details must not leak")

        index.embedder = BrokenEmbedder()
        assert ask(client, biology, "Felines eat fish")["citations"]
        diagnostics = client.get("/api/retrieval/status").json()
        assert "secret" not in diagnostics["warning"]
        assert "unavailable" in diagnostics["warning"]


def test_unavailable_worker_backs_off_logs_sparingly_and_stops_promptly(
    tmp_path, monkeypatch, caplog
):
    import asyncio
    import logging

    from gunther import processing

    with client_for(tmp_path) as client:
        worker = client.app.state.processing_worker
        attempts = {"count": 0}

        def unavailable(_limit: int) -> None:
            attempts["count"] += 1
            if attempts["count"] >= 40:
                worker.stopping.set()
            raise RuntimeError("database is locked")

        pauses: list[float] = []

        async def record_pause(seconds: float) -> None:
            pauses.append(seconds)

        monkeypatch.setattr(worker.index, "backfill", unavailable)
        monkeypatch.setattr(worker, "_pause", record_pause)
        with caplog.at_level(logging.WARNING, logger="gunther.processing"):
            asyncio.run(asyncio.wait_for(worker.run(), timeout=5))

    assert attempts["count"] == 40
    assert pauses[:4] == [2.0, 4.0, 8.0, 16.0]
    assert max(pauses) == processing.MAX_RETRY_SECONDS
    warnings = [record for record in caplog.records if "unavailable" in record.getMessage()]
    assert len(warnings) == 2  # the first failure and the 30th, not forty lines
    assert processing.retry_delay_seconds(1) == 2.0
    assert processing.retry_delay_seconds(100) == processing.MAX_RETRY_SECONDS


def test_worker_pause_wakes_when_stopping(tmp_path):
    import asyncio
    import time

    with client_for(tmp_path) as client:
        worker = client.app.state.processing_worker
        worker.stopping.set()
        started = time.monotonic()
        asyncio.run(worker._pause(30))
        assert time.monotonic() - started < 1

