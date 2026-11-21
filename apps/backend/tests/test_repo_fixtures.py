import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from gunther.config import Settings
from gunther.main import create_app

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "scripts/fixtures"


def workspace_client(workspace: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{workspace / 'gunther.sqlite'}",
        assets_dir=workspace / "assets",
        recordings_dir=workspace / "recordings",
        speech_dir=workspace / "speech",
        llm_api_key=None,
        tavily_api_key=None,
        ocr_provider="disabled",
        processing_worker_enabled=False,
        semantic_search=False,
    )
    return TestClient(create_app(settings))


def run_repo_command(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO / "apps/backend"),
            "GUNTHER_DESKTOP_SIDECAR": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_workspace_starts_empty_even_with_retired_seed_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("SEED_DEMO", "true")

    with workspace_client(tmp_path) as client:
        for route in ("health", "knowledge-bases", "sources", "assertions"):
            response = client.get(f"/api/{route}")
            assert response.status_code == 200
            if route != "health":
                assert response.json() == []


def test_repo_seed_loads_explicit_fixture_and_can_be_replayed(tmp_path):
    fixture_path = FIXTURES / "demo.json"
    seed = (
        REPO / "scripts/seed_demo.py",
        tmp_path / "gunther.sqlite",
        fixture_path,
    )
    result = run_repo_command(*seed)
    assert result.returncode == 0, result.stderr
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    with workspace_client(tmp_path) as client:
        libraries = client.get("/api/knowledge-bases").json()
        assert {row["id"]: row["title"] for row in libraries} == {
            row["id"]: row["title"] for row in fixture["knowledge_bases"]
        }
        [source] = client.get("/api/sources").json()
        assert source["title"] == fixture["source"]["title"]
        assertions = client.get("/api/assertions").json()
        assert len(assertions) == len(fixture["source"]["content"].splitlines())
        assert {item["status"] for item in assertions} == {"verified"}

    result = run_repo_command(*seed)
    assert result.returncode == 0, result.stderr
    with workspace_client(tmp_path) as client:
        assert client.get("/api/sources").json() == [source]
        assert client.get("/api/assertions").json() == assertions


def test_sidecar_smoke_checks_the_explicit_deck(tmp_path):
    command = ("-m", "gunther.desktop_server", "--studio-smoke")
    fixture = FIXTURES / "studio-package.md"
    result = run_repo_command(*command, fixture)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["problems"] == 0
    assert report["layouts"] == ["title", "statement"]
    assert "gunther.studio.pipeline" in report["modules"]

    bad_deck = tmp_path / "missing-notes.md"
    bad_deck.write_text(
        fixture.read_text(encoding="utf-8").replace("Note: why.", ""),
        encoding="utf-8",
    )
    result = run_repo_command(*command, bad_deck)
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert not report["ok"] and report["problems"] == 1
