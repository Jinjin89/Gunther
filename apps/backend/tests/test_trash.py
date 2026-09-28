from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from gunther.config import Settings
from gunther.database import session_scope
from gunther.main import create_app
from gunther.models import (
    Artifact,
    Asset,
    KnowledgeBaseSource,
    KnowledgeProposal,
    KnowledgeSession,
    KnowledgeUnit,
    RecordingSession,
    Source,
    TopicNode,
    utc_now,
)

RECORDING_ID = "rec_" + "a" * 24


@pytest.fixture
def storage(tmp_path: Path) -> Path:
    for name in ("assets", "recordings"):
        (tmp_path / name).mkdir()
    return tmp_path


def make_client(storage: Path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                database_url=f"sqlite+pysqlite:///{storage / 'gunther.sqlite'}",
                assets_dir=storage / "assets",
                recordings_dir=storage / "recordings",
                seed_demo=False,
                deepseek_api_key=None,
                processing_worker_enabled=False,
            )
        )
    )


def create_base(client: TestClient, title: str) -> str:
    created = client.post(
        "/api/knowledge-bases",
        json={"title": title, "question": f"What belongs in {title}?", "description": title},
    )
    assert created.status_code == 201
    return created.json()["id"]


def add_source(
    client: TestClient, title: str, content: str, base_id: str | None = None
) -> str:
    payload = {"title": title, "kind": "note", "content": content}
    if base_id:
        payload["knowledgeBaseId"] = base_id
    created = client.post("/api/sources", json=payload)
    assert created.status_code == 201
    return created.json()["source"]["id"]


def sessions_of(client: TestClient):
    return client.app.state.knowledge_service.sessions


def inbox_ids(client: TestClient) -> set[str]:
    return {item["id"] for item in client.get("/api/inbox").json()}


def search_ids(client: TestClient, query: str) -> set[str]:
    return {item["id"] for item in client.get("/api/search", params={"q": query}).json()}


def test_trashed_source_leaves_every_list_and_restores_exactly(storage: Path) -> None:
    with make_client(storage) as client:
        base_id = create_base(client, "Zebrafish")
        filed = add_source(client, "Fin regeneration", "Zebrafish fins regrow quickly.", base_id)
        loose = add_source(client, "Loose thought", "Zebrafish larvae are transparent.")
        assert loose in inbox_ids(client)

        trashed = client.post(f"/api/sources/{loose}/trash")
        assert trashed.status_code == 200
        assert trashed.json()["kind"] == "source"
        assert loose not in inbox_ids(client)
        assert loose not in search_ids(client, "zebrafish")
        assert client.get(f"/api/sources/{loose}").json()["trashedAt"]

        client.post(f"/api/sources/{filed}/trash")
        library = next(b for b in client.get("/api/knowledge-bases").json() if b["id"] == base_id)
        assert library["sourceCount"] == 0
        assert client.get(f"/api/knowledge-bases/{base_id}/sources").json() == []
        assert search_ids(client, "zebrafish") == set()
        assert [item["id"] for item in client.get("/api/trash").json()] == [filed, loose]

        restored = client.post(f"/api/trash/source/{filed}/restore")
        assert restored.status_code == 200
        assert [s["id"] for s in client.get(f"/api/knowledge-bases/{base_id}/sources").json()] == [
            filed
        ]
        assert filed in search_ids(client, "zebrafish")
        assert client.get(f"/api/sources/{filed}").json()["trashedAt"] is None
        assert [item["id"] for item in client.get("/api/trash").json()] == [loose]


def test_trashing_a_library_takes_only_its_own_sources_and_restores_them_together(
    storage: Path,
) -> None:
    with make_client(storage) as client:
        cells = create_base(client, "Cells")
        genes = create_base(client, "Genes")
        shared = add_source(client, "Shared marker", "CD3E marks T cells.", cells)
        client.post(f"/api/sources/{shared}/file", json={"knowledgeBaseId": genes})
        only_here = add_source(client, "Only in cells", "Monocytes express CD14.", cells)
        note = client.post("/api/notes", json={"title": "Idea", "content": "Macrophages adapt."})
        filed_note = client.post(
            f"/api/notes/{note.json()['id']}/file", json={"knowledgeBaseId": cells}
        ).json()
        note_source = filed_note["importResult"]["source"]["id"]

        entry = client.post(f"/api/knowledge-bases/{cells}/trash").json()
        assert (entry["kind"], entry["itemCount"]) == ("library", 2)
        assert cells not in {b["id"] for b in client.get("/api/knowledge-bases").json()}
        assert {only_here, note_source}.isdisjoint(inbox_ids(client))
        assert client.get(f"/api/sources/{only_here}").json()["trashedAt"]
        shared_detail = client.get(f"/api/sources/{shared}").json()
        assert shared_detail["trashedAt"] is None
        assert [base["id"] for base in shared_detail["knowledgeBases"]] == [genes]
        assert filed_note["note"]["id"] not in {
            n["id"] for n in client.get("/api/notes").json()
        }
        assert [item["kind"] for item in client.get("/api/trash").json()] == ["library"]

        client.post(f"/api/trash/library/{cells}/restore")
        assert cells in {b["id"] for b in client.get("/api/knowledge-bases").json()}
        assert {s["id"] for s in client.get(f"/api/knowledge-bases/{cells}/sources").json()} == {
            shared,
            only_here,
            note_source,
        }
        assert filed_note["note"]["id"] in {n["id"] for n in client.get("/api/notes").json()}
        assert client.get("/api/trash").json() == []


def test_delete_forever_removes_rows_index_and_only_unused_originals(storage: Path) -> None:
    with make_client(storage) as client:
        base_id = create_base(client, "Recordings")
        recording_source = add_source(
            client,
            "Lecture 3",
            f"Duration: 10:00 · Captured: Today · Local recording: {RECORDING_ID}\n"
            "Clustering groups similar cells.",
            base_id,
        )
        kept_asset = storage / "assets" / "aa" / "shared.pdf"
        kept_asset.parent.mkdir()
        kept_asset.write_bytes(b"%PDF shared")
        audio = storage / "recordings" / f"{RECORDING_ID}.webm"
        audio.write_bytes(b"audio")
        with session_scope(sessions_of(client)) as session:
            session.add(
                RecordingSession(
                    id=RECORDING_ID,
                    title="Lecture 3",
                    status="completed",
                    file_name=audio.name,
                    knowledge_base_id=base_id,
                )
            )
            session.add(
                Asset(
                    id="ast_shared",
                    content_hash="b" * 64,
                    original_name="paper.pdf",
                    media_type="application/pdf",
                    size_bytes=11,
                    relative_path="aa/shared.pdf",
                )
            )
        first_pdf = add_source(client, "Paper, first copy", "Methods compare clustering.")
        second_pdf = add_source(client, "Paper, second copy", "Methods compare clustering again.")
        with session_scope(sessions_of(client)) as session:
            for source_id in (first_pdf, second_pdf):
                session.get(Source, source_id).asset_id = "ast_shared"

        for source_id in (recording_source, first_pdf):
            client.post(f"/api/sources/{source_id}/trash")
        with session_scope(sessions_of(client)) as session:
            indexed = session.execute(
                text("SELECT COUNT(*) FROM knowledge_fts WHERE source_id IN (:a, :b)"),
                {"a": recording_source, "b": first_pdf},
            ).scalar_one()
        assert indexed > 0, "Trash keeps the index so Restore is exact"
        assert client.delete(f"/api/trash/source/{recording_source}").status_code == 200
        assert client.delete(f"/api/trash/source/{first_pdf}").status_code == 200

        assert client.get(f"/api/sources/{recording_source}").status_code == 404
        assert not audio.exists()
        assert kept_asset.exists(), "another source still uses this original"
        with session_scope(sessions_of(client)) as session:
            assert session.get(RecordingSession, RECORDING_ID) is None
            assert session.execute(
                text("SELECT COUNT(*) FROM knowledge_fts WHERE source_id IN (:a, :b)"),
                {"a": recording_source, "b": first_pdf},
            ).scalar_one() == 0
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []

        client.post(f"/api/sources/{second_pdf}/trash")
        client.delete(f"/api/trash/source/{second_pdf}")
        assert not kept_asset.exists(), "the last source using it is gone"
        with session_scope(sessions_of(client)) as session:
            assert session.get(Asset, "ast_shared") is None


def test_a_recording_still_capturing_cannot_move_to_trash(storage: Path) -> None:
    with make_client(storage) as client:
        base_id = create_base(client, "Live")
        source_id = add_source(
            client,
            "Live lecture",
            f"Duration: 01:00 · Captured: Now · Local recording: {RECORDING_ID}",
            base_id,
        )
        with session_scope(sessions_of(client)) as session:
            session.add(
                RecordingSession(
                    id=RECORDING_ID,
                    title="Live lecture",
                    status="capturing",
                    file_name=f"{RECORDING_ID}.webm",
                    knowledge_base_id=base_id,
                )
            )
        assert client.post(f"/api/sources/{source_id}/trash").status_code == 409
        assert client.post(f"/api/knowledge-bases/{base_id}/trash").status_code == 409
        assert client.get("/api/trash").json() == []


def test_capturing_the_same_content_again_brings_it_back_from_trash(storage: Path) -> None:
    with make_client(storage) as client:
        source_id = add_source(client, "Marker list", "CD19 marks B cells.")
        client.post(f"/api/sources/{source_id}/trash")
        again = client.post(
            "/api/sources",
            json={"title": "Marker list", "kind": "note", "content": "CD19 marks B cells."},
        ).json()
        assert (again["source"]["id"], again["duplicate"]) == (source_id, True)
        assert source_id in inbox_ids(client)
        assert client.get("/api/trash").json() == []


def test_expired_items_are_purged_and_empty_trash_deletes_the_rest(storage: Path) -> None:
    with make_client(storage) as client:
        old = add_source(client, "Old", "An old capture.")
        recent = add_source(client, "Recent", "A recent capture.")
        note = client.post("/api/notes", json={"title": "Scratch", "content": "Draft"}).json()
        for route in (f"/api/sources/{old}/trash", f"/api/notes/{note['id']}/trash"):
            client.post(route)
        trash = client.app.state.trash_service
        assert trash.purge_expired(utc_now() + timedelta(days=29)) == 0
        client.post(f"/api/sources/{recent}/trash")
        with session_scope(sessions_of(client)) as session:
            session.get(Source, recent).trashed_at = utc_now() + timedelta(days=5)

        assert trash.purge_expired(utc_now() + timedelta(days=31)) == 2
        assert [item["id"] for item in client.get("/api/trash").json()] == [recent]
        assert client.get(f"/api/sources/{old}").status_code == 404

        assert client.delete("/api/trash").json() == {"deleted": 1}
        assert client.get("/api/trash").json() == []
        assert client.get(f"/api/sources/{recent}").status_code == 404


def test_deleting_a_library_forever_clears_everything_it_owned(storage: Path) -> None:
    with make_client(storage) as client:
        base_id = create_base(client, "Biology")
        other_id = create_base(client, "Chemistry")
        shared = add_source(client, "Shared", "CD3D is a useful T-cell marker.", base_id)
        client.post(f"/api/sources/{shared}/file", json={"knowledgeBaseId": other_id})
        parent = client.post(f"/api/knowledge-bases/{base_id}/topics", json={"title": "Cells"})
        client.post(
            f"/api/knowledge-bases/{base_id}/topics",
            json={"title": "T cells", "parentId": parent.json()["id"]},
        )
        knowledge_session = client.post(f"/api/knowledge-bases/{base_id}/sessions", json={}).json()
        turn = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages",
            json={"content": "Which marker identifies T cells?"},
        ).json()
        proposal = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages/"
            f"{turn['assistantMessage']['id']}/proposal",
            json={"title": "T-cell identity"},
        ).json()
        unit_id = client.patch(
            f"/api/proposals/{proposal['id']}", json={"status": "accepted", "reason": "Reviewed"}
        ).json()["knowledgeUnitId"]
        workspace_id = client.get("/api/workspace/bootstrap").json()["workspaceId"]
        headers = {"X-Gunther-Workspace-Id": workspace_id}
        first = client.post(
            f"/api/knowledge-bases/{base_id}/artifacts",
            headers=headers,
            json={
                "clientRequestId": "trash_request_0001",
                "format": "field_guide",
                "audience": "scientist",
                "acceptedUnitIds": [unit_id],
            },
        ).json()
        second = client.post(
            f"/api/knowledge-bases/{base_id}/artifacts",
            headers=headers,
            json={
                "clientRequestId": "trash_request_0002",
                "format": "field_guide",
                "audience": "scientist",
                "acceptedUnitIds": [unit_id],
                "supersedesArtifactId": first["id"],
            },
        )
        assert second.status_code == 201

        client.post(f"/api/knowledge-bases/{base_id}/trash")
        assert client.delete(f"/api/trash/library/{base_id}").status_code == 200

        with session_scope(sessions_of(client)) as session:
            for model, column in (
                (TopicNode, TopicNode.knowledge_base_id),
                (KnowledgeSession, KnowledgeSession.knowledge_base_id),
                (KnowledgeProposal, KnowledgeProposal.knowledge_base_id),
                (KnowledgeUnit, KnowledgeUnit.knowledge_base_id),
                (Artifact, Artifact.knowledge_base_id),
                (KnowledgeBaseSource, KnowledgeBaseSource.knowledge_base_id),
            ):
                count = session.scalar(
                    select(func.count()).select_from(model).where(column == base_id)
                )
                assert count == 0, model.__tablename__
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
        assert client.get(f"/api/sources/{shared}").json()["knowledgeBases"] == [
            {"id": other_id, "title": "Chemistry"}
        ]
        assert client.get("/api/trash").json() == []


def test_ask_leaves_sources_in_trash_out_of_its_answers(storage: Path) -> None:
    with make_client(storage) as client:
        base_id = create_base(client, "Immunology")
        add_source(client, "T cells", "CD3D -> marker_of -> T cell", base_id)
        b_cells = add_source(client, "B cells", "CD19 -> marker_of -> B cell", base_id)
        client.post(f"/api/sources/{b_cells}/trash")
        knowledge_session = client.post(f"/api/knowledge-bases/{base_id}/sessions", json={}).json()
        turn = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages",
            json={"content": "Which marker identifies B cells?"},
        ).json()
        quotes = " ".join(c["quote"] for c in turn["assistantMessage"]["citations"])
        assert "CD19" not in quotes

        client.post(f"/api/knowledge-bases/{base_id}/trash")
        assert (
            client.post(
                f"/api/sessions/{knowledge_session['id']}/messages",
                json={"content": "Anything else?"},
            ).status_code
            == 404
        )


def test_the_desktop_app_may_send_trash_deletions_across_origins(storage: Path) -> None:
    # The desktop web layer calls the service cross-origin; Delete forever and
    # Empty Trash are DELETE requests, so the preflight must allow them.
    with make_client(storage) as client:
        preflight = client.options(
            "/api/trash",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "DELETE",
            },
        )
        assert preflight.status_code == 200
        assert "DELETE" in preflight.headers["access-control-allow-methods"]
