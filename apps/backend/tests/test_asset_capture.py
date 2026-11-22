import asyncio
import io
import wave
import zlib
from codecs import BOM_UTF8
from pathlib import Path
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import select

from gunther import asset_service
from gunther.config import Settings
from gunther.database import session_scope
from gunther.documents import text_document
from gunther.main import create_app
from gunther.models import ProcessingJob, Source
from gunther.ocr import OcrError, OcrPage, OcrProvider, OcrRegion, UnavailableOcrProvider
from gunther.schemas import CreateSourceInput


class FakeOcrProvider(OcrProvider):
    name = "fake_local"

    def __init__(self) -> None:
        self.image_calls: list[tuple[Path, int]] = []
        self.pdf_calls: list[tuple[Path, int]] = []

    @property
    def available(self) -> bool:
        return True

    @staticmethod
    def _page(page_number: int) -> OcrPage:
        return OcrPage(
            page_number=page_number,
            provider_name="fake_local",
            regions=(
                OcrRegion(
                    page_number=page_number,
                    left_ppm=100_000,
                    top_ppm=200_000,
                    width_ppm=500_000,
                    height_ppm=50_000,
                    text="Genome -> contains -> genes",
                    confidence=0.98,
                ),
            ),
        )

    def recognize_image(
        self,
        path: Path,
        *,
        page_number: int = 1,
        timeout_seconds: int = 30,
    ) -> OcrPage:
        del timeout_seconds
        self.image_calls.append((path, page_number))
        return self._page(page_number)

    def recognize_pdf_page(
        self,
        path: Path,
        *,
        page_number: int,
        timeout_seconds: int = 30,
    ) -> OcrPage:
        del timeout_seconds
        self.pdf_calls.append((path, page_number))
        return self._page(page_number)


def make_client(tmp_path: Path, ocr_provider: OcrProvider | None = None) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
                assets_dir=tmp_path / "assets",
                recordings_dir=tmp_path / "recordings",
                deepseek_api_key=None,
                stt_provider="compatible",
                processing_worker_enabled=False,
            ),
            ocr_provider=ocr_provider,
        )
    )


def create_base(client: TestClient, title: str) -> str:
    response = client.post(
        "/api/knowledge-bases",
        json={
            "title": title,
            "question": f"What belongs in {title}?",
            "description": f"A durable boundary for {title}.",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def read_capture(client: TestClient, receipt: dict) -> dict:
    source_id = receipt["importResult"]["source"]["id"]
    assert client.app.state.processing_worker.run_once(ai=False)
    return client.get(f"/api/sources/{source_id}/document").raise_for_status().json()


def assert_queued_reading_creates_no_assertions(client: TestClient, source_id: str) -> None:
    while client.app.state.processing_worker.run_once():
        pass
    assert client.get(f"/api/sources/{source_id}").raise_for_status().json()["assertions"] == []


def docx_bytes(text: str) -> bytes:
    output = io.BytesIO()
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    return output.getvalue()


def epub_bytes(*sections: str) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        for index, section in enumerate(sections, start=1):
            archive.writestr(
                f"OEBPS/chapter-{index}.xhtml",
                f"<html><body><p>{section}</p></body></html>",
            )
    return output.getvalue()


def epub_with_spine(chapters: list[str | None], *, names: list[str] | None = None) -> bytes:
    output = io.BytesIO()
    manifest, spine = [], []
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="book.opf"/></rootfiles></container>',
        )
        for index, chapter in enumerate(chapters):
            name = names[index] if names else f"chapter-{index}.xhtml"
            href = quote(name, safe="")
            manifest.append(
                f'<item id="c{index}" href="{href}" media-type="application/xhtml+xml"/>',
            )
            spine.append(f'<itemref idref="c{index}"/>')
            if chapter is not None:
                archive.writestr(name, f"<p>{chapter}</p>")
        archive.writestr(
            "book.opf",
            '<package xmlns="http://www.idpf.org/2007/opf">'
            f'<manifest>{"".join(manifest)}</manifest><spine>{"".join(spine)}</spine></package>',
        )
    return output.getvalue()


def pdf_bytes(text: str) -> bytes:
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b">>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, content in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode())
        result.extend(content)
        result.extend(b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(result)


def compressed_pdf_bytes(content: bytes) -> bytes:
    stream = zlib.compress(content)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> /Contents 4 0 R >>",
        b"<< /Filter /FlateDecode /Length "
        + str(len(stream)).encode()
        + b">>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode())
        result.extend(value)
        result.extend(b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(result)


def blank_pdf_bytes(page_count: int) -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    writer.write(output)
    return output.getvalue()


def test_original_file_is_preserved_downloadable_and_visible_in_inbox(tmp_path: Path) -> None:
    original = b"CD3D -> marker_of -> T cell\n"
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/captures/assets",
            params={"title": "PBMC markers", "fileName": "markers.txt", "kind": "file"},
            headers={"Content-Type": "text/plain"},
            content=original,
        )
        assert response.status_code == 201
        captured = response.json()
        asset = captured["asset"]
        source = captured["importResult"]["source"]
        assert asset["originalName"] == "markers.txt"
        assert asset["sizeBytes"] == len(original)
        assert source["asset"]["id"] == asset["id"]
        assert source["processing"]["state"] == "queued"
        assert source["processing"]["jobId"]
        assert source["processing"]["revisionId"] is None

        downloaded = client.get(asset["downloadUrl"])
        assert downloaded.status_code == 200
        assert downloaded.content == original
        inbox = client.get("/api/inbox").json()
        assert any(
            item["sourceId"] == source["id"] and item["state"] == "unfiled" for item in inbox
        )
        document = read_capture(client, captured)
        assert original.decode().strip() in [e["content"] for e in document["elements"]]
        assert_queued_reading_creates_no_assertions(client, source["id"])

    stored = [path for path in (tmp_path / "assets").rglob("*") if path.is_file()]
    assert len(stored) == 1
    assert stored[0].read_bytes() == original


def test_asset_download_rejects_same_length_file_tampering(tmp_path: Path) -> None:
    original = b"trusted-original-bytes"
    with make_client(tmp_path) as client:
        captured = client.post(
            "/api/captures/assets",
            params={"title": "Trusted", "fileName": "trusted.bin", "kind": "file"},
            headers={"Content-Type": "application/octet-stream"},
            content=original,
        )
        assert captured.status_code == 201
        download_url = captured.json()["asset"]["downloadUrl"]

        stored = [path for path in (tmp_path / "assets").rglob("*") if path.is_file()]
        assert len(stored) == 1
        tampered = b"X" + original[1:]
        assert len(tampered) == len(original)
        stored[0].write_bytes(tampered)

        downloaded = client.get(download_url)
        assert downloaded.status_code == 404
        assert "integrity" in downloaded.json()["detail"].lower()


def test_same_asset_is_deduplicated_but_can_be_filed_to_two_libraries(tmp_path: Path) -> None:
    original = docx_bytes("Evidence -> improves -> decisions")
    with make_client(tmp_path) as client:
        first_base = create_base(client, "Decision Science")
        second_base = create_base(client, "Research Practice")
        common = {
            "title": "Evidence notes",
            "fileName": "evidence.docx",
            "kind": "paper",
        }
        docx_media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        first = client.post(
            "/api/captures/assets",
            params={**common, "knowledgeBaseId": first_base},
            headers={"Content-Type": docx_media},
            content=original,
        )
        second = client.post(
            "/api/captures/assets",
            params={**common, "knowledgeBaseId": second_base},
            headers={"Content-Type": docx_media},
            content=original,
        )
        assert first.status_code == second.status_code == 201
        first_body = first.json()
        second_body = second.json()
        assert first_body["asset"]["id"] == second_body["asset"]["id"]
        assert (
            first_body["importResult"]["source"]["id"]
            == second_body["importResult"]["source"]["id"]
        )
        assert first_body["importResult"]["duplicate"] is False
        assert second_body["importResult"]["duplicate"] is True
        assert len(client.get(f"/api/knowledge-bases/{first_base}/sources").json()) == 1
        assert len(client.get(f"/api/knowledge-bases/{second_base}/sources").json()) == 1


@pytest.mark.parametrize(("field", "value"), [
    ("title", "Meeting attachment"),
    ("kind", "paper"),
    ("notes", "Read the results"),
    ("fileName", "attachment.txt"),
])
def test_one_asset_can_back_two_distinct_source_contexts(tmp_path: Path, field, value) -> None:
    original = b"Identical original bytes with two user-visible meanings."
    params = {"title": "Course handout", "fileName": "shared.txt", "kind": "file",
              "notes": "Read the methods"}
    with make_client(tmp_path) as client:
        first = client.post(
            "/api/captures/assets",
            params=params,
            headers={"Content-Type": "text/plain"},
            content=original,
        ).json()
        second = client.post(
            "/api/captures/assets",
            params={**params, field: value},
            headers={"Content-Type": "text/plain"},
            content=original,
        ).json()

        assert first["asset"]["id"] == second["asset"]["id"]
        assert first["importResult"]["source"]["id"] != second["importResult"]["source"]["id"]
        assert first["importResult"]["duplicate"] is False
        assert second["importResult"]["duplicate"] is False


def test_a_receipt_lost_before_restart_reuses_the_source_asset_and_job(tmp_path: Path) -> None:
    request = {
        "params": {"title": "Reading", "fileName": "reading.txt", "notes": "For review"},
        "headers": {"Content-Type": "text/plain"},
        "content": b"An original paragraph with stable upload identity.",
    }
    with make_client(tmp_path) as client:
        first = client.post("/api/captures/assets", **request).raise_for_status().json()
    source = first["importResult"]["source"]

    with make_client(tmp_path) as client:
        replay = client.post("/api/captures/assets", **request).raise_for_status().json()
        assert replay["asset"]["id"] == first["asset"]["id"]
        assert replay["importResult"]["duplicate"] is True
        assert replay["importResult"]["source"] == source
        document = read_capture(client, replay)
        assert document["processing"]["state"] == "ready"
        assert document["processing"]["jobId"] == source["processing"]["jobId"]
        with session_scope(client.app.state.processing_worker.sessions) as session:
            jobs = session.scalars(select(ProcessingJob.id).where(
                ProcessingJob.source_id == source["id"], ProcessingJob.kind == "parse_asset",
            )).all()
        assert jobs == [source["processing"]["jobId"]]


@pytest.mark.parametrize("earlier_queued", [True, False], ids=["queued", "inline"])
def test_v1_capture_replay_requires_a_stored_request_identity(tmp_path: Path, earlier_queued: bool):
    original = b" \n"
    notes = "Keep the original for reference."
    with make_client(tmp_path) as client:
        state = client.app.state
        asset = asyncio.run(state.processing_worker.assets.preserve_bytes(
            original, file_name="reading.txt", media_type="text/plain",
        ))
        original_base = create_base(client, "Original library")
        target_base = create_base(client, "New library")
        content = (
            "# Original file\n\nFile: reading.txt\nMedia type: text/plain\n"
            f"Size: {len(original)} bytes\nSHA-256: {asset.content_hash}\n\n"
            f"## Your context\n\n{notes}"
        )
        service = state.knowledge_service
        earlier = service.import_source(
            CreateSourceInput(title="Reading", kind="file", content=content,
                              knowledge_base_id=original_base),
            asset_id=asset.id if earlier_queued else None,
        ).source
        if not earlier_queued:
            with session_scope(service.sessions) as session:
                session.get(Source, earlier.id).asset_id = asset.id
        # Both a queued upload and a later re-read of an inline upload can have a parse job.
        client.post(f"/api/sources/{earlier.id}/reprocess").raise_for_status()
        existing = client.get(f"/api/sources/{earlier.id}").raise_for_status().json()
        if not earlier_queued:
            assert existing["processing"]["revisionId"]

    request = {
        "params": {"title": "Reading", "fileName": "reading.txt", "kind": "file",
                   "notes": notes, "knowledgeBaseId": target_base},
        "headers": {"Content-Type": "text/plain"},
        "content": original,
    }
    with make_client(tmp_path) as client:
        receipt = client.post("/api/captures/assets", **request).raise_for_status().json()
        source = receipt["importResult"]["source"]
        assert receipt["asset"]["id"] == asset.id
        assert receipt["importResult"]["duplicate"] is earlier_queued
        if earlier_queued:
            assert source["id"] == earlier.id
            assert source["processing"]["jobId"] == existing["processing"]["jobId"]
        else:
            assert source["id"] != earlier.id
            assert client.get(f"/api/sources/{earlier.id}").json() == existing
            assert source["processing"]["jobId"] != existing["processing"]["jobId"]
        replay = client.post("/api/captures/assets", **request).raise_for_status().json()
        assert replay["importResult"]["duplicate"] is True
        assert replay["importResult"]["source"] == source
        assert client.get(replay["asset"]["downloadUrl"]).content == original
        library_sources = client.get(f"/api/knowledge-bases/{target_base}/sources").json()
        assert [item["id"] for item in library_sources] == [source["id"]]


def test_pdf_extraction_preserves_page_locator_for_evidence(tmp_path: Path) -> None:
    original = pdf_bytes("Genome -> contains -> genes")
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/captures/assets",
            params={"title": "Genome paper", "fileName": "genome.png", "kind": "paper"},
            headers={"Content-Type": "image/png"},
            content=original,
        )
        assert response.status_code == 201
        document = read_capture(client, response.json())
        [evidence] = [e for e in document["elements"] if "Genome -> contains" in e["content"]]
        assert evidence["anchor"]["page"] == 1
        assert evidence["locator"].startswith("Page 1")


@pytest.mark.parametrize(("file_name", "media_type", "encode"), [
    ("notes.epub", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
     docx_bytes),
    ("notes.docx", "application/octet-stream", epub_bytes),
])
def test_queued_archives_keep_text_and_the_original(tmp_path, file_name, media_type, encode):
    text = "An original paragraph from a portable document."
    original = encode(text)
    with make_client(tmp_path) as client:
        receipt = client.post(
            "/api/captures/assets",
            params={"title": "Notes", "fileName": file_name},
            headers={"Content-Type": media_type}, content=original,
        ).raise_for_status().json()
        document = read_capture(client, receipt)
        assert text in [element["content"] for element in document["elements"]]
        assert client.get(receipt["asset"]["downloadUrl"]).content == original


@pytest.mark.parametrize(("file_name", "media_type", "original", "readable", "method"), [
    ("notes.md", "application/octet-stream", "# Notes\n\nRésumé du chantier.".encode("utf-16"),
     ["Notes", "Résumé du chantier."], "local-text"),
    ("survey.csv", "application/vnd.ms-excel", b"Date,Total\n2025-06-01,14\n",
     ["Date,Total", "2025-06-01,14"], "local-text"),
    ("handout.html", "application/octet-stream", b"<!-- saved page --><h1>Field notes</h1>",
     ["Field notes"], "local-html"),
])
def test_queued_text_recovers_format_and_encoding(
    tmp_path, file_name, media_type, original, readable, method,
):
    with make_client(tmp_path) as client:
        receipt = client.post(
            "/api/captures/assets", params={"title": "Handout", "fileName": file_name},
            headers={"Content-Type": media_type}, content=original,
        ).raise_for_status().json()
        document = read_capture(client, receipt)
        assert document["manifest"]["method"] == method
        assert [part["content"] for part in document["elements"]] == readable
        assert client.get(receipt["asset"]["downloadUrl"]).content == original


@pytest.mark.parametrize("missing", [True, False], ids=["missing member", "chapter limit"])
def test_epub_keeps_readable_chapters_after_a_partial_read(tmp_path, missing):
    cap = asset_service.MAX_EPUB_DOCUMENTS
    if missing:
        chapters = ["Opening chapter.", None, "Closing chapter."]
        expected = ["Opening chapter.", "Closing chapter."]
        warning = "missing"
    else:
        chapters = [f"Chapter {index + 1} text." for index in range(cap + 1)]
        expected = chapters[:cap]
        warning = "chapter limit"
    original = epub_with_spine(chapters)
    with make_client(tmp_path) as client:
        receipt = client.post(
            "/api/captures/assets", params={"title": "Book", "fileName": "book.epub"},
            headers={"Content-Type": "application/epub+zip"}, content=original,
        ).raise_for_status().json()
        document = read_capture(client, receipt)
        elements = document["elements"]
        source_id = receipt["importResult"]["source"]["id"]
        while document["nextOffset"] is not None:
            document = client.get(
                f"/api/sources/{source_id}/document",
                params={"offset": document["nextOffset"], "limit": 500},
            ).raise_for_status().json()
            elements.extend(document["elements"])
        assert [part["content"] for part in elements] == expected
        assert warning in document["processing"]["warning"]
        assert client.get(receipt["asset"]["downloadUrl"]).content == original


def test_epub_decodes_manifest_paths_before_lookup_and_validation(tmp_path):
    original = epub_with_spine(
        ["First chapter.", "Second chapter.", "Outside the archive root."],
        names=["chapter one.xhtml", "chapter-two.xhtml", "../outside.xhtml"],
    )
    with make_client(tmp_path) as client:
        receipt = client.post(
            "/api/captures/assets", params={"title": "Book", "fileName": "book.epub"},
            headers={"Content-Type": "application/epub+zip"}, content=original,
        ).raise_for_status().json()
        document = read_capture(client, receipt)
        assert [part["content"] for part in document["elements"]] == [
            "First chapter.", "Second chapter.",
        ]
        assert "invalid spine reference" in document["processing"]["warning"]
        assert client.get(receipt["asset"]["downloadUrl"]).content == original


def test_windows_1252_csv_keeps_rows_and_punctuation(tmp_path):
    rows = ["Area,Status", "North – West,Open"]
    original = "\n".join(rows).encode("windows-1252")
    with make_client(tmp_path) as client:
        receipt = client.post(
            "/api/captures/assets", params={"title": "Survey", "fileName": "survey.csv"},
            headers={"Content-Type": "application/octet-stream"}, content=original,
        ).raise_for_status().json()
        document = read_capture(client, receipt)
        assert [part["content"] for part in document["elements"]] == rows
        assert client.get(receipt["asset"]["downloadUrl"]).content == original


@pytest.mark.parametrize(("file_name", "media_type"), [
    ("recording.wav", "audio/wav"),
    ("recording.bin", "application/octet-stream"),
])
def test_audio_upload_with_no_text_evidence_stays_original_only(tmp_path, file_name, media_type):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 16)
    original = output.getvalue()

    with make_client(tmp_path) as client:
        receipt = client.post(
            "/api/captures/assets", params={"title": "Recording", "fileName": file_name},
            headers={"Content-Type": media_type}, content=original,
        ).raise_for_status().json()
        document = read_capture(client, receipt)
        assert not document["elements"]
        assert "original is preserved" in document["processing"]["warning"].lower()
        assert client.get(receipt["asset"]["downloadUrl"]).content == original


@pytest.mark.parametrize("malformed", [True, False], ids=["bad byte", "literal replacement"])
def test_bom_text_keeps_surrounding_paragraphs_and_reports_actual_replacements(tmp_path, malformed):
    marker = b"\xff" if malformed else "\ufffd".encode("utf-8")
    original = (
        BOM_UTF8 + b"Opening paragraph.\n\nIn the middle: " + marker + b".\n\nClosing paragraph."
    )

    with make_client(tmp_path) as client:
        receipt = client.post(
            "/api/captures/assets", params={"title": "Notes", "fileName": "notes.bin"},
            headers={"Content-Type": "application/octet-stream"}, content=original,
        ).raise_for_status().json()
        document = read_capture(client, receipt)
        assert [part["content"] for part in document["elements"]] == [
            "Opening paragraph.", "In the middle: \ufffd.", "Closing paragraph.",
        ]
        warning = document["processing"]["warning"]
        assert warning == document["manifest"]["warning"]
        if malformed:
            assert "replaced" in warning
        else:
            assert warning is None
        assert client.get(receipt["asset"]["downloadUrl"]).content == original


def test_bigtiff_reaches_ocr_by_verified_format(tmp_path):
    provider = FakeOcrProvider()
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="TIFF", big_tiff=True)
    original = output.getvalue()
    assert original[:4] in (b"II+\x00", b"MM\x00+")
    with make_client(tmp_path, provider) as client:
        receipt = client.post(
            "/api/captures/assets", params={"title": "Scan", "fileName": "scan.tif"},
            headers={"Content-Type": "image/tiff"}, content=original,
        ).raise_for_status().json()
        document = read_capture(client, receipt)
        assert document["manifest"]["ocrStatus"] == "completed"
        assert [page for _, page in provider.image_calls] == [1]
        assert client.get(receipt["asset"]["downloadUrl"]).content == original


def test_image_ocr_creates_traceable_text_and_region_evidence(tmp_path: Path) -> None:
    provider = FakeOcrProvider()
    original = png_bytes()
    with make_client(tmp_path, provider) as client:
        response = client.post(
            "/api/captures/assets",
            params={"title": "Whiteboard", "fileName": "board.png", "kind": "image"},
            headers={"Content-Type": "image/png"},
            content=original,
        )

        assert response.status_code == 201
        captured = response.json()
        assert captured["importResult"]["source"]["processing"]["state"] == "queued"
        assert provider.image_calls == []
        document = read_capture(client, captured)
        assert document["manifest"]["ocrStatus"] == "completed"
        assert document["manifest"]["ocrProvider"] == "fake_local"
        [evidence] = document["elements"]
        assert evidence["content"] == "Genome -> contains -> genes"
        assert evidence["anchor"]["page"] == 1
        assert evidence["anchor"]["bboxPpm"] == [100_000, 200_000, 500_000, 50_000]
        assert evidence["anchor"]["provider"] == "fake_local"
        assert evidence["anchor"]["confidence"] == 0.98
        assert client.get(captured["asset"]["downloadUrl"]).content == original
        assert provider.image_calls and provider.image_calls[0][1] == 1
        assert_queued_reading_creates_no_assertions(
            client, captured["importResult"]["source"]["id"],
        )


def test_scanned_pdf_uses_ocr_and_preserves_pdf_page_region(tmp_path: Path) -> None:
    provider = FakeOcrProvider()
    original = pdf_bytes("")
    with make_client(tmp_path, provider) as client:
        response = client.post(
            "/api/captures/assets",
            params={"title": "Scanned paper", "fileName": "scan.pdf", "kind": "paper"},
            headers={"Content-Type": "application/pdf"},
            content=original,
        )

        assert response.status_code == 201
        document = read_capture(client, response.json())
        assert document["manifest"]["ocrStatus"] == "completed"
        assert document["manifest"]["ocrProvider"] == "fake_local"
        [evidence] = document["elements"]
        assert evidence["content"] == "Genome -> contains -> genes"
        assert evidence["anchor"]["page"] == 1
        assert evidence["anchor"]["bboxPpm"] == [100_000, 200_000, 500_000, 50_000]
        assert provider.pdf_calls and provider.pdf_calls[0][1] == 1


def test_scanned_pdf_keeps_partial_ocr_when_one_page_fails(tmp_path: Path) -> None:
    class FailFirstProvider(FakeOcrProvider):
        def recognize_pdf_page(
            self,
            path: Path,
            *,
            page_number: int,
            timeout_seconds: int = 30,
        ) -> OcrPage:
            self.pdf_calls.append((path, page_number))
            if page_number == 1:
                raise OcrError("synthetic page failure")
            return self._page(page_number)

    provider = FailFirstProvider()
    with make_client(tmp_path, provider) as client:
        response = client.post(
            "/api/captures/assets",
            params={"title": "Partial scan", "fileName": "partial.pdf", "kind": "paper"},
            headers={"Content-Type": "application/pdf"},
            content=blank_pdf_bytes(2),
        )

        assert response.status_code == 201
        document = read_capture(client, response.json())
        assert document["manifest"]["ocrStatus"] == "degraded"
        assert "page 1" in document["manifest"]["warning"]
        [evidence] = document["elements"]
        assert evidence["anchor"]["page"] == 2
        assert [page for _, page in provider.pdf_calls] == [1, 2]


def test_pdf_ocr_document_budget_degrades_without_starting_more_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeOcrProvider()
    path = tmp_path / "budget.pdf"
    path.write_bytes(blank_pdf_bytes(2))
    clock = iter((0.0, 61.0, 62.0))
    monkeypatch.setattr(asset_service.time, "monotonic", lambda: next(clock))

    result = asset_service.read_initial_asset(
        path,
        "application/pdf",
        provider,
    )

    assert result.document.manifest["ocrStatus"] == "degraded"
    assert provider.pdf_calls == []
    assert result.warning is not None
    assert "60-second" in result.warning
    assert "2 additional blank pages" in result.warning


def test_unavailable_ocr_is_explicitly_degraded_and_keeps_original(tmp_path: Path) -> None:
    provider = UnavailableOcrProvider("OCR engine deliberately absent in this test.")
    original = png_bytes()
    with make_client(tmp_path, provider) as client:
        response = client.post(
            "/api/captures/assets",
            params={"title": "Offline scan", "fileName": "scan.png", "kind": "image"},
            headers={"Content-Type": "image/png"},
            content=original,
        )

        assert response.status_code == 201
        captured = response.json()
        document = read_capture(client, captured)
        assert document["elements"] == []
        assert document["manifest"]["ocrStatus"] == "degraded"
        assert document["manifest"]["ocrProvider"] is None
        warning = document["manifest"]["warning"]
        assert "deliberately absent" in warning
        assert "complete original remains available" in warning
        assert document["processing"]["warning"] == warning
        assert client.get(captured["asset"]["downloadUrl"]).content == original
        health = client.get("/api/health").json()
        assert health["ocrMode"] == "not_configured"
        assert health["ocrProvider"] == "none"


@pytest.mark.parametrize(
    ("file_name", "media_type", "original", "visible", "hidden"),
    [
        (
            "large.pdf",
            "text/plain",
            b"visible-text\n" + (b"x" * 160) + b"hidden-text",
            "visible-text",
            "hidden-text",
        ),
        (
            "large.bin",
            "Text/HTML; charset=utf-8",
            b"<p>visible-html</p>" + (b" " * 160) + b"<p>hidden-html</p>",
            "visible-html",
            "hidden-html",
        ),
    ],
)
def test_text_and_html_only_read_a_bounded_prefix_but_keep_the_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_name: str,
    media_type: str,
    original: bytes,
    visible: str,
    hidden: str,
) -> None:
    monkeypatch.setattr(asset_service, "MAX_TEXT_INPUT_BYTES", 64)
    read_prefix = asset_service._read_text_prefix
    reads = []

    def count_read(path):
        reads.append(path)
        return read_prefix(path)

    monkeypatch.setattr(asset_service, "_read_text_prefix", count_read)
    with make_client(tmp_path) as client:
        captured = client.post(
            "/api/captures/assets",
            params={"title": "Bounded text", "fileName": file_name, "kind": "file"},
            headers={"Content-Type": media_type},
            content=original,
        )
        assert captured.status_code == 201
        body = captured.json()
        document = read_capture(client, body)
        assert len(reads) == 1
        content = "\n".join(e["content"] for e in document["elements"])
        assert visible in content
        assert hidden not in content
        assert "limited to the first 64 bytes" in document["processing"]["warning"]
        downloaded = client.get(body["asset"]["downloadUrl"])
        assert downloaded.content == original


def test_character_limit_is_applied_during_html_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asset_service, "MAX_EXTRACTED_CHARACTERS", 40)
    path = tmp_path / "long.html"
    path.write_bytes(b"<p>" + (b"A" * 200) + b"</p>")

    result = asset_service.read_initial_asset(path, "text/html", UnavailableOcrProvider())

    assert [element.text for element in result.document.elements] == ["A" * 40]
    assert result.warning is not None
    assert "limited to 40 characters" in result.warning


def test_docx_compression_bomb_limit_degrades_to_downloadable_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asset_service, "MAX_ARCHIVE_MEMBER_BYTES", 256)
    original = docx_bytes("A" * 4_096)
    with make_client(tmp_path) as client:
        captured = client.post(
            "/api/captures/assets",
            params={"title": "Oversized DOCX", "fileName": "oversized.docx", "kind": "file"},
            headers={
                "Content-Type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
            },
            content=original,
        )
        assert captured.status_code == 201
        body = captured.json()
        document = read_capture(client, body)
        assert "skipped to protect device resources" in document["processing"]["warning"]
        assert "archive member exceeds" in document["processing"]["warning"]
        assert not document["elements"]
        assert client.get(body["asset"]["downloadUrl"]).content == original


def test_epub_cumulative_decompression_limit_preserves_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asset_service, "MAX_ARCHIVE_TOTAL_EXTRACTED_BYTES", 256)
    original = epub_bytes("B" * 180, "C" * 180)
    with make_client(tmp_path) as client:
        captured = client.post(
            "/api/captures/assets",
            params={"title": "Oversized EPUB", "fileName": "oversized.epub", "kind": "file"},
            headers={"Content-Type": "application/epub+zip"},
            content=original,
        )
        assert captured.status_code == 201
        body = captured.json()
        document = read_capture(client, body)
        assert "cumulative decompression budget" in document["processing"]["warning"]
        assert [element["content"] for element in document["elements"]] == ["B" * 180]
        assert client.get(body["asset"]["downloadUrl"]).content == original


def test_pdf_input_limit_skips_extraction_without_rejecting_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = pdf_bytes("Safe original remains downloadable")
    monkeypatch.setattr(asset_service, "MAX_PDF_INPUT_BYTES", len(original) - 1)
    with make_client(tmp_path) as client:
        captured = client.post(
            "/api/captures/assets",
            params={"title": "Large PDF", "fileName": "large.pdf", "kind": "paper"},
            headers={"Content-Type": "application/pdf"},
            content=original,
        )
        assert captured.status_code == 201
        body = captured.json()
        document = read_capture(client, body)
        assert "PDF exceeds" in document["manifest"]["warning"]
        assert "complete original remains available" in document["manifest"]["warning"]
        assert client.get(body["asset"]["downloadUrl"]).content == original


def test_pdf_decompression_limit_skips_suspicious_page_and_keeps_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asset_service, "MAX_PDF_DECOMPRESSED_STREAM_BYTES", 256)
    original = compressed_pdf_bytes(b"q\n" + (b" " * 8_192) + b"\nQ")
    with make_client(tmp_path) as client:
        captured = client.post(
            "/api/captures/assets",
            params={"title": "Compressed PDF", "fileName": "compressed.pdf", "kind": "paper"},
            headers={"Content-Type": "application/pdf"},
            content=original,
        )
        assert captured.status_code == 201
        body = captured.json()
        document = read_capture(client, body)
        assert "could not be safely extracted from 1 PDF page" in document["manifest"]["warning"]
        assert client.get(body["asset"]["downloadUrl"]).content == original


def test_corrupt_archive_fails_closed_for_extraction_but_not_capture(tmp_path: Path) -> None:
    original = b"PK\x03\x04truncated archive"
    with make_client(tmp_path) as client:
        captured = client.post(
            "/api/captures/assets",
            params={"title": "Corrupt document", "fileName": "corrupt.docx", "kind": "file"},
            headers={
                "Content-Type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
            },
            content=original,
        )
        assert captured.status_code == 201
        body = captured.json()
        document = read_capture(client, body)
        assert "Text extraction failed safely" in document["processing"]["warning"]
        assert client.get(body["asset"]["downloadUrl"]).content == original


def test_failed_text_reparse_keeps_the_last_reading(tmp_path, monkeypatch):
    with make_client(tmp_path) as client:
        receipt = client.post(
            "/api/captures/assets",
            params={"title": "Notes", "fileName": "notes.txt"},
            headers={"Content-Type": "text/plain"},
            content=b"An existing reading from the uploaded file.",
        ).raise_for_status().json()
        first = read_capture(client, receipt)
        source_id = receipt["importResult"]["source"]["id"]

        def failed_read(_path):
            raise OSError("Read temporarily unavailable")

        monkeypatch.setattr(asset_service, "_read_text_prefix", failed_read)
        client.post(f"/api/sources/{source_id}/reprocess").raise_for_status()
        retried = read_capture(client, receipt)
        assert retried["revisionId"] == first["revisionId"]
        assert retried["elements"] == first["elements"]
        assert "previous document revision was retained" in retried["processing"]["warning"]


@pytest.mark.parametrize("failed_state", ["failed", "needs-key", None])
def test_read_again_keeps_a_checked_revision_when_structure_review_does_not_succeed(
    tmp_path, failed_state,
):
    class Layout:
        version = "docling/test"
        state = "needs-key"

        def parse(self, _path):
            document = text_document(f"An organized reading with structure state {self.state}.")
            document.manifest.update(method="docling", schema=2)
            if self.state is None:
                document.manifest.update(schema=1, structureWarning="Structure could not be read.")
            else:
                document.manifest["structure"] = {"state": self.state}
            return document

    with make_client(tmp_path) as client:
        layout = Layout()
        client.app.state.processing_worker.document_parser = layout
        receipt = client.post(
            "/api/captures/assets",
            params={"title": "Lecture handout", "fileName": "handout.pdf"},
            headers={"Content-Type": "application/pdf"},
            content=blank_pdf_bytes(1),
        ).raise_for_status().json()
        source_id = receipt["importResult"]["source"]["id"]
        first = read_capture(client, receipt)
        assert first["manifest"]["structure"]["state"] == "needs-key"

        layout.state = "checked"
        client.post(f"/api/sources/{source_id}/reprocess").raise_for_status()
        checked = read_capture(client, receipt)
        assert checked["manifest"]["structure"]["state"] == "checked"
        assert checked["revisionId"] != first["revisionId"]

        layout.state = failed_state
        client.post(f"/api/sources/{source_id}/reprocess").raise_for_status()
        retried = read_capture(client, receipt)
        assert retried["revisionId"] == checked["revisionId"]
        assert retried["elements"] == checked["elements"]
        assert "previous document revision was retained" in retried["processing"]["warning"]
