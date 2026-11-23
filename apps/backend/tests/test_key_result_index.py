import json
import sqlite3
from contextlib import closing
from pathlib import Path

from fake_models import FakeProvider
from sqlalchemy import select, text
from test_key_results import (
    EXTRA,
    FACT,
    checks,
    finding,
    job_state,
    list_results,
    old_answer,
    prompt,
    results_path,
    setup,
)
from test_outputs import SIDECAR, app_for, library

from gunther import key_result_index, key_results
from gunther.documents import text_document
from gunther.models import (
    Asset,
    ContentBlock,
    KeyResult,
    KeyResultEvidence,
    Source,
    SourceIndexHead,
)
from gunther.source_identity import source_fingerprint


def test_pinned_file_passage_survives_docling_but_results_from_an_edited_note_do_not(tmp_path):
    fake = FakeProvider(
        json.dumps({"items": [
            {"text": FACT, "passages": [1]}, {"text": EXTRA, "passages": [2]},
        ]}), json.dumps(checks("supported", "supported")),
    )
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, sources, worker = setup(client, {"Paper": FACT, "Field note": EXTRA})
        paper_id, note_id = sources.values()
        with worker.sessions.begin() as session:
            session.add(Asset(
                id="original_pdf", content_hash="a" * 64, original_name="paper.pdf",
                media_type="application/pdf", size_bytes=16, relative_path="aa/paper.pdf",
            ))
            session.flush()
            paper = session.get(Source, paper_id)
            paper.asset_id, paper.kind = "original_pdf", "paper"
        origin = old_answer(client, worker, base_id, [paper_id, note_id])
        assert worker.run_once(ai=True)
        results = {result["text"]: result for result in list_results(client, base_id)}
        assert results.keys() == {FACT, EXTRA}

        delayed = old_answer(client, worker, base_id, [note_id])
        with worker.sessions.begin() as session:
            paper = session.get(Source, paper_id)
            note = session.get(Source, note_id)
            paper.content = "Soil moisture was measured in the trial."
            note.content = "The field log records air temperature."
            for source in (paper, note):
                source.content_hash = source_fingerprint(source.title, source.kind, source.content)
            reading = text_document(paper.content)
            revision = worker.index.publish(
                session, paper, reading.chunks(), parser="docling", document=reading,
            )
            assert revision.id != results[FACT]["evidence"][0]["revisionId"]
            worker.index.add_source(session, note)

        request_count = len(fake.requests)
        assert worker.run_once(ai=True)
        assert job_state(client, base_id, delayed)["state"] == "done"
        assert len(fake.requests) == request_count
        assert list_results(client, base_id) == [results[FACT]]
        assert job_state(client, base_id, origin)["keptIds"] == [results[FACT]["id"]]
        with worker.sessions.begin() as session:
            original_block = session.get(ContentBlock, results[FACT]["evidence"][0]["blockId"])
            original_block.kind = "page_footer"
        assert list_results(client, base_id) == []


class LocalEmbedder:
    model_id = "test-key-result-embedding"

    def __init__(self, database: Path):
        self.database = database
        self.writes = 0

    def encode(self, texts, *, query=False):
        with closing(sqlite3.connect(self.database, timeout=0)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        self.writes += 1
        return [[1.0, 0.0] for _ in texts]


def indexed(worker) -> tuple[set[str], set[str]]:
    with worker.sessions() as session:
        return (
            set(session.scalars(text("SELECT result_id FROM key_results_fts"))),
            set(session.scalars(text("SELECT result_id FROM key_result_vectors_2"))),
        )


def test_scope_keeps_only_complete_origins_and_permanent_trash_purges_the_lost_group(tmp_path):
    fake = FakeProvider(
        json.dumps({"items": [
            {"text": FACT, "passages": [1, 2]}, {"text": EXTRA, "passages": [2]},
        ]}), json.dumps(checks("supported", "supported")),
    )
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, sources, worker = setup(client, {"Trial log": FACT, "Field log": EXTRA})
        first, second = sources.values()
        combined = old_answer(client, worker, base_id, [first, second])
        assert worker.run_once(ai=True)
        ids = {result["text"]: result["id"] for result in list_results(client, base_id)}

        def selected(*source_ids):
            return client.post(
                results_path(base_id), headers=SIDECAR, json={"sourceIds": list(source_ids)},
            ).raise_for_status().json()

        assert selected(first) == selected() == []
        assert {result["id"] for result in selected(first, second)} == set(ids.values())

        alternate = old_answer(client, worker, base_id, [first])
        fake.replies[:] = [
            json.dumps({"items": [{"same_as": ids[FACT], "passages": [1]}]}),
            json.dumps(checks("supported")),
        ]
        assert worker.run_once(ai=True)
        assert job_state(client, base_id, alternate)["keptIds"] == [ids[FACT]]
        [one] = selected(first)
        assert one["id"] == ids[FACT]
        assert [link["sourceId"] for link in one["evidence"]] == [first]

        embedder = LocalEmbedder(tmp_path / "gunther.sqlite")
        worker.index.embedder = embedder
        while worker.run_once(ai=False):
            pass
        assert indexed(worker) == (set(ids.values()), set(ids.values()))
        assert embedder.writes > 0

        def trash_second():
            return client.post(
                f"/api/sources/{second}/trash", headers=SIDECAR,
            ).raise_for_status().json()["id"]

        batch = trash_second()
        assert list_results(client, base_id) == [one]
        client.post(f"/api/trash/source/{batch}/restore", headers=SIDECAR).raise_for_status()
        assert {result["id"] for result in list_results(client, base_id)} == set(ids.values())
        batch = trash_second()
        client.delete(f"/api/trash/source/{batch}", headers=SIDECAR).raise_for_status()
        assert list_results(client, base_id) == [one]
        assert job_state(client, base_id, combined)["keptIds"] == []
        assert indexed(worker) == ({ids[FACT]}, set())
        with worker.sessions() as session:
            assert session.get(KeyResult, ids[EXTRA]) is None
            assert list(session.scalars(select(KeyResultEvidence.origin_id))) == [alternate["id"]]
        assert worker.run_once(ai=False)
        assert indexed(worker) == ({ids[FACT]}, {ids[FACT]})

        path = f"{results_path(base_id)}/{ids[FACT]}"
        stamp = client.post(
            f"{path}/remove", params={"withUndo": True}, headers=SIDECAR,
        ).raise_for_status().json()["removedAt"]
        client.post(
            f"{path}/restore", params={"removedAt": stamp}, headers=SIDECAR,
        ).raise_for_status()
        assert list_results(client, base_id) == [one]

        library_batch = client.post(
            f"/api/knowledge-bases/{base_id}/trash", headers=SIDECAR,
        ).raise_for_status().json()["id"]
        client.delete(f"/api/trash/library/{library_batch}", headers=SIDECAR).raise_for_status()
        assert indexed(worker) == (set(), set())


def test_distillation_ranks_fts_with_scope_applied_before_the_limit(tmp_path):
    fake = FakeProvider()
    with app_for(tmp_path, fake, ai_summaries="off", semantic_search=False) as client:
        base_id, sources, worker = setup(client, {"Trial log": FACT, "Unrelated": EXTRA})
        other_id, _ = library(client, title="Shared trial", notes={})
        main, other_source = sources.values()
        client.post(
            f"/api/sources/{main}/file", headers=SIDECAR, json={"knowledgeBaseId": other_id},
        ).raise_for_status()

        with worker.sessions.begin() as session:
            def seed(result_id, library_id, source_id, sentence, status="kept"):
                source = session.get(Source, source_id)
                head = session.get(SourceIndexHead, source_id)
                block = session.scalar(select(ContentBlock).where(
                    ContentBlock.revision_id == head.revision_id,
                ).order_by(ContentBlock.ordinal).limit(1))
                session.add(KeyResult(
                    id=result_id, knowledge_base_id=library_id, text=sentence, status=status,
                    created_origin_kind="answer", created_origin_id=result_id,
                ))
                session.flush()
                session.add(KeyResultEvidence(
                    id=f"evidence_{result_id}", key_result_id=result_id, source_id=source_id,
                    revision_id=head.revision_id, block_id=block.id, quote=block.content,
                    source_content_hash=source.content_hash,
                    origin_kind="answer", origin_id=result_id,
                ))
                if status == "kept":
                    key_result_index.write_fts(session, result_id, sentence)

            seed("related", base_id, main, FACT)
            seed("suppressed", base_id, main, "Soil moisture was measured.", "removed")
            for n in range(65):
                seed(f"off_source_{n:02}", base_id, other_source, FACT)
                seed(f"off_library_{n:02}", other_id, main, FACT)
                seed(f"ignored_{n:02}", base_id, main, f"Air temperature was {n} degrees.")

        def distill(request):
            assert prompt(request)["existing"] == [
                {"id": "related", "text": FACT, "status": "kept"},
                {"id": "suppressed", "text": "Soil moisture was measured.", "status": "removed"},
            ]
            return json.dumps({"items": []})

        origin = old_answer(client, worker, base_id, [main])
        fake.replies[:] = [distill]
        assert worker.run_once(ai=True)
        assert job_state(client, base_id, origin)["state"] == "done"
        with worker.sessions() as session:
            assert session.scalar(text(
                "SELECT count(*) FROM sqlite_master WHERE name GLOB 'key_result_vectors_*'"
            )) == 0


def test_kept_and_removed_semantic_matches_follow_remove_restore_and_undo(tmp_path):
    fake = FakeProvider(finding(), json.dumps(checks("supported")))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, sources, worker = setup(client)
        source_ids = list(sources.values())
        origin = old_answer(client, worker, base_id, source_ids)
        assert worker.run_once(ai=True)
        [result] = list_results(client, base_id)
        result_id = result["id"]
        embedder = LocalEmbedder(tmp_path / "gunther.sqlite")
        worker.index.embedder = embedder

        def matches():
            with worker.sessions() as session:
                removed = key_results.related_removed(session, base_id, source_ids)
            query = key_result_index.prepare_query("Hydrology observations.", embedder, removed)
            with worker.sessions(info={key_result_index.QUERY_INFO: query}) as session:
                return [(row.id, row.status) for row in key_results.related_results(
                    session, base_id, source_ids,
                )]

        while worker.run_once(ai=False):
            pass
        assert indexed(worker) == ({result_id}, {result_id})
        assert matches() == [(result_id, "kept")]

        root = f"{results_path(base_id)}/{result_id}"
        stamp = client.post(
            f"{root}/remove", params={"withUndo": True}, headers=SIDECAR,
        ).raise_for_status().json()["removedAt"]
        assert indexed(worker) == (set(), set())
        assert matches() == [(result_id, "removed")]
        client.post(
            f"{root}/restore", params={"removedAt": stamp}, headers=SIDECAR,
        ).raise_for_status()
        assert worker.run_once(ai=False)
        assert indexed(worker) == ({result_id}, {result_id})
        client.post(
            f"{results_path(base_id)}/origins/answer/{origin['id']}/undo", headers=SIDECAR,
        ).raise_for_status()
        assert indexed(worker) == (set(), set())
        assert matches() == []


def test_folder_mirror_reads_the_same_kept_usable_results_as_the_workbook(tmp_path, monkeypatch):
    root = tmp_path / "Library root"
    fake = FakeProvider(
        json.dumps({"items": [
            {"text": FACT, "passages": [1]}, {"text": EXTRA, "passages": [2]},
        ]}), json.dumps(checks("supported", "supported")),
    )
    with app_for(tmp_path, fake, ai_summaries="off", library_root=root) as client:
        folders = client.app.state.library_folders
        folders.stop()
        base_id, sources, worker = setup(client, {"Trial log": FACT, "Field log": EXTRA})
        # Sync immediately in the change callback to verify it observes committed rows.
        monkeypatch.setattr(folders, "request_sync", folders.sync)
        old_answer(client, worker, base_id, list(sources.values()))
        assert worker.run_once(ai=True)
        [mirror] = (root / "Libraries").glob("*/Key results.md")

        def verify_mirror():
            kept = list_results(client, base_id)
            lines = [
                f"- {item['text']} · {item['evidence'][0]['sourceTitle']}, "
                f"{item['evidence'][0]['locator']}"
                for item in kept
            ]
            assert mirror.read_text().splitlines() == ["# Key results", "", *lines]
            return kept

        kept = verify_mirror()
        assert {result["text"] for result in kept} == {FACT, EXTRA}
        extra = next(item for item in kept if item["text"] == EXTRA)
        client.post(
            f"{results_path(base_id)}/{extra['id']}/remove", headers=SIDECAR,
        ).raise_for_status()
        [one] = verify_mirror()
        assert one["text"] == FACT
        batch = client.post(
            f"/api/sources/{sources['Trial log']}/trash", headers=SIDECAR,
        ).raise_for_status().json()["id"]
        assert verify_mirror() == []
        client.post(f"/api/trash/source/{batch}/restore", headers=SIDECAR).raise_for_status()
        assert verify_mirror() == [one]
