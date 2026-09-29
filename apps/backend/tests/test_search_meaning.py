"""Home search finds a source from a whole sentence, not only from every one of its words."""

from pathlib import Path

from fastapi.testclient import TestClient

from gunther.config import Settings
from gunther.main import create_app

SIDECAR_TOKEN = "sidecar-token-with-at-least-256-bits-000000000000000000000000"
SIDECAR = {"X-Gunther-Token": SIDECAR_TOKEN}


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
        deepseek_api_key=None,
        stt_provider="compatible",
        processing_worker_enabled=False,
        semantic_search=False,
        auth_token=SIDECAR_TOKEN,
    )
    return TestClient(create_app(settings))


def library(client: TestClient, title: str) -> str:
    return client.post(
        "/api/knowledge-bases",
        headers=SIDECAR,
        json={"title": title, "question": f"{title}?", "description": title},
    ).json()["id"]


def test_a_question_finds_the_source_that_answers_it(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        cells = library(client, "Cells")
        other = library(client, "Other")
        source_id = client.post(
            "/api/sources",
            headers=SIDECAR,
            json={
                "title": "Markers",
                "kind": "note",
                "knowledgeBaseId": cells,
                "content": "CD3D is a marker of T cells.",
            },
        ).json()["source"]["id"]

        def search(query: str, *scope: str) -> list[dict]:
            scoped = "".join(f"&knowledgeBaseId={item}" for item in scope)
            response = client.get(f"/api/search?q={query}{scoped}", headers=SIDECAR)
            assert response.status_code == 200
            return [item for item in response.json() if item["kind"] == "source"]

        # Not every word is in the note, but most are.
        found = search("Which marker identifies T cells?")
        assert [item["id"] for item in found] == [source_id]
        assert found[0]["knowledgeBaseId"] == cells
        assert "related by" in found[0]["meta"]
        assert "CD3D is a marker" in found[0]["snippet"]

        # An exact match is not labelled as a guess, and is not listed twice.
        exact = search("CD3D")
        assert [item["id"] for item in exact] == [source_id]
        assert "related by" not in exact[0]["meta"]

        # Scope holds: another library does not see it, the right one does.
        assert search("Which marker identifies T cells?", other) == []
        assert [item["id"] for item in search("Which marker identifies T cells?", cells)] == [
            source_id
        ]
        assert search("Which planet has rings?") == []
