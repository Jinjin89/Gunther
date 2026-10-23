"""Part A: native publication, bounded layout upgrades and live worker leases."""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event

import pytest
from pypdf import PdfWriter
from sqlalchemy import select
from test_documents import FigureParser, capture_pdf
from test_structured_knowledge import base, client_for

from gunther.asset_service import AssetTextExtraction
from gunther.database import session_scope
from gunther.document_parser import DoclingError, DoclingParser, docling_budget
from gunther.models import ProcessingJob, utc_now


def pdf(path, pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    writer.write(path)
    return path


@pytest.mark.parametrize("pages,wall", [(1, 210), (12, 540), (500, 1800)])
def test_layout_budgets_scale_and_cap_both_wall_and_cpu(tmp_path, pages, wall):
    assert docling_budget(pdf(tmp_path / "input.pdf", pages)) == (wall, wall * 4)


def test_unknown_page_count_gets_conservative_bounded_budget(tmp_path):
    path = tmp_path / "damaged.pdf"
    path.write_bytes(b"%PDF-damaged")
    assert docling_budget(path) == (1800, 7200)


def test_native_revision_is_readable_during_layout_and_lease_is_renewed(tmp_path, monkeypatch):
    entered, finish, renewed = Event(), Event(), Event()
    monkeypatch.setattr("gunther.processing.HEARTBEAT_SECONDS", 0.02)
    monkeypatch.setattr(
        "gunther.processing.extract_asset_content",
        lambda *args: AssetTextExtraction("Native evidence is available immediately."),
    )
    with client_for(tmp_path) as client:
        saved = capture_pdf(client, base(client))
        worker = client.app.state.processing_worker
        original_heartbeat = worker.heartbeat

        def heartbeat(*args):
            result = original_heartbeat(*args)
            renewed.set()
            return result

        monkeypatch.setattr(worker, "heartbeat", heartbeat)

        class SlowParser(FigureParser):
            def parse(self, path):
                entered.set()
                assert finish.wait(10)
                return super().parse(path)

        worker.document_parser = SlowParser()
        job_id, token = worker.claim()
        with session_scope(worker.sessions) as session:
            session.get(ProcessingJob, job_id).lease_until = utc_now() + timedelta(seconds=2)
        with ThreadPoolExecutor() as pool:
            task = pool.submit(worker.process, job_id, token)
            try:
                assert entered.wait(10)
                initial = client.get(f"/api/sources/{saved}/document").json()
                assert initial["processing"]["state"] == "running"
                assert any("Native evidence" in e["content"] for e in initial["elements"])
                assert renewed.wait(3)
                with session_scope(worker.sessions) as session:
                    job = session.get(ProcessingJob, job_id)
                    assert job.lease_until > utc_now() + timedelta(seconds=100)
                    assert job.attempts == 1
            finally:
                finish.set()
            task.result(timeout=10)
        upgraded = client.get(f"/api/sources/{saved}/document").json()
        assert upgraded["revisionId"] != initial["revisionId"]
        assert upgraded["manifest"]["method"] == "docling"
        assert upgraded["processing"]["state"] in {"ready", "partial"}
        previous = client.get(
            f"/api/sources/{saved}/document", params={"revision_id": initial["revisionId"]}
        ).json()
        assert previous["elements"] == initial["elements"]


@pytest.mark.parametrize("error", [RuntimeError("secret /local/path"), TimeoutError(), None])
def test_failed_upgrade_keeps_native_revision_and_can_retry(tmp_path, monkeypatch, error):
    monkeypatch.setattr(
        "gunther.processing.extract_asset_content",
        lambda *args: AssetTextExtraction("Readable native evidence."),
    )
    with client_for(tmp_path) as client:
        saved = capture_pdf(client, base(client))
        worker = client.app.state.processing_worker
        native = []

        class FailedParser:
            def parse(self, path):
                native.append(client.get(f"/api/sources/{saved}/document").json()["revisionId"])
                raise error

        worker.document_parser = FailedParser() if error else None
        worker.run_once()
        failed = client.get(f"/api/sources/{saved}/document").json()
        assert failed["processing"]["state"] == "failed"
        assert failed["processing"]["warning"].startswith("Layout parsing failed:")
        assert "secret" not in failed["processing"]["warning"]
        assert "retained" in failed["processing"]["warning"]
        assert any("Readable native" in e["content"] for e in failed["elements"])
        if native:
            assert failed["revisionId"] == native[0]
        worker.document_parser = FigureParser()
        assert client.post(f"/api/sources/{saved}/reprocess").status_code == 202
        while worker.run_once():
            pass
        upgraded = client.get(f"/api/sources/{saved}/document").json()
        assert upgraded["manifest"]["method"] == "docling"
        assert upgraded["processing"]["state"] in {"ready", "partial"}


def test_layout_helper_receives_scaled_budgets_and_survives_old_wall_limit(tmp_path, monkeypatch):
    path = pdf(tmp_path / "input.pdf", 12)
    runtime = tmp_path / "python"
    runtime.touch()
    now = [0.0]
    commands = []

    class Process:
        returncode = None

        def __init__(self, command, **kwargs):
            commands.append(command)
            Path(command[-4]).write_text(json.dumps({"body": {"children": []}}))

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            if now[0] == 0:
                now[0] = 300.0
                raise subprocess.TimeoutExpired("fixture", timeout)
            self.returncode = 0

    monkeypatch.setattr("gunther.document_parser.subprocess.Popen", Process)
    monkeypatch.setattr("gunther.document_parser.time.monotonic", lambda: now[0])
    DoclingParser(runtime, tmp_path).parse(path)
    assert float(commands[0][-2]) == 540
    assert int(commands[0][-1]) == 2160


@pytest.mark.parametrize("cancelled", [False, True])
def test_layout_subprocess_is_terminated_on_timeout_or_cancellation(
    tmp_path, monkeypatch, cancelled,
):
    path = pdf(tmp_path / "input.pdf", 1)
    runtime = tmp_path / "python"
    runtime.touch()
    now = [0.0]
    terminated = []

    class Process:
        returncode = None

        def __init__(self, *args, **kwargs):
            pass

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            now[0] = 1000.0
            if self.returncode is None:
                raise subprocess.TimeoutExpired("fixture", timeout)

        def terminate(self):
            terminated.append(True)
            self.returncode = -15

    monkeypatch.setattr("gunther.document_parser.subprocess.Popen", Process)
    monkeypatch.setattr("gunther.document_parser.time.monotonic", lambda: now[0])
    with pytest.raises(DoclingError if cancelled else TimeoutError):
        DoclingParser(runtime, tmp_path).parse(path, stopping=lambda: cancelled)
    assert terminated == [True]


def test_synchronous_pdf_capture_queues_layout_upgrade(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gunther.asset_service.extract_asset_content",
        lambda *args: AssetTextExtraction("Native capture."),
    )
    with client_for(tmp_path) as client:
        response = client.post(
            "/api/captures/assets",
            params={"title": "Report", "fileName": "report.pdf", "kind": "paper"},
            content=b"%PDF-fixture", headers={"content-type": "application/pdf"},
        )
        assert response.status_code == 201, response.text
        saved = response.json()["importResult"]["source"]["id"]
        with session_scope(client.app.state.processing_worker.sessions) as session:
            job = session.scalar(select(ProcessingJob).where(
                ProcessingJob.source_id == saved, ProcessingJob.kind == "parse_asset",
            ))
            assert job is not None and job.state == "queued"


def test_failed_conversion_with_residual_content_does_not_replace_native(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gunther.processing.extract_asset_content",
        lambda *args: AssetTextExtraction("Complete native paragraph."),
    )
    with client_for(tmp_path) as client:
        saved = capture_pdf(client, base(client))
        worker = client.app.state.processing_worker

        class FailedConversion(FigureParser):
            def parse(self, path):
                result = super().parse(path)
                result.manifest["conversionStatus"] = "failure"
                return result

        worker.document_parser = FailedConversion()
        worker.run_once()
        retained = client.get(f"/api/sources/{saved}/document").json()
        assert retained["processing"]["state"] == "failed"
        assert retained["manifest"].get("method") != "docling"
        assert any(e["content"] == "Complete native paragraph." for e in retained["elements"])


def test_shutdown_leaves_layout_resumable_without_losing_native_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gunther.processing.extract_asset_content",
        lambda *args: AssetTextExtraction("Readable across a restart."),
    )
    with client_for(tmp_path) as client:
        saved = capture_pdf(client, base(client))
        worker = client.app.state.processing_worker

        class StoppedParser:
            def parse(self, path):
                worker.stopping.set()
                raise DoclingError("Layout parsing was interrupted")

        worker.document_parser = StoppedParser()
        worker.run_once()
        initial = client.get(f"/api/sources/{saved}/document").json()
        assert initial["processing"]["state"] == "running"
        with session_scope(worker.sessions) as session:
            session.get(ProcessingJob, initial["processing"]["jobId"]).lease_until = (
                utc_now() - timedelta(seconds=1)
            )
        worker.stopping.clear()
        worker.document_parser = FigureParser()
        worker.run_once()
        resumed = client.get(f"/api/sources/{saved}/document").json()
        assert resumed["manifest"]["method"] == "docling"
        assert resumed["revisionId"] != initial["revisionId"]
        with session_scope(worker.sessions) as session:
            job = session.get(ProcessingJob, initial["processing"]["jobId"])
            assert job.state == "completed" and job.attempts == 2
