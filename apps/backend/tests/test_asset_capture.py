import io
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from gunther import asset_service
from gunther.config import Settings
from gunther.main import create_app
from gunther.ocr import OcrError, OcrPage, OcrProvider, OcrRegion, UnavailableOcrProvider


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
                seed_demo=False,
                deepseek_api_key=None,
                stt_provider="compatible",
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
        assert source["assertionCount"] == 1

        downloaded = client.get(asset["downloadUrl"])
        assert downloaded.status_code == 200
        assert downloaded.content == original
        inbox = client.get("/api/inbox").json()
        assert any(
            item["sourceId"] == source["id"] and item["state"] == "unfiled" for item in inbox
        )

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


def test_one_asset_can_back_two_distinct_source_contexts(tmp_path: Path) -> None:
    original = b"Identical original bytes with two user-visible meanings."
    with make_client(tmp_path) as client:
        first = client.post(
            "/api/captures/assets",
            params={"title": "Course handout", "fileName": "shared.txt", "kind": "file"},
            headers={"Content-Type": "text/plain"},
            content=original,
        ).json()
        second = client.post(
            "/api/captures/assets",
            params={"title": "Meeting attachment", "fileName": "shared.txt", "kind": "paper"},
            headers={"Content-Type": "text/plain"},
            content=original,
        ).json()

        assert first["asset"]["id"] == second["asset"]["id"]
        assert first["importResult"]["source"]["id"] != second["importResult"]["source"]["id"]
        assert first["importResult"]["duplicate"] is False
        assert second["importResult"]["duplicate"] is False


def test_pdf_extraction_preserves_page_locator_for_evidence(tmp_path: Path) -> None:
    original = pdf_bytes("Genome -> contains -> genes")
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/captures/assets",
            params={"title": "Genome paper", "fileName": "genome.pdf", "kind": "paper"},
            headers={"Content-Type": "application/pdf"},
            content=original,
        )
        assert response.status_code == 201
        source_id = response.json()["importResult"]["source"]["id"]
        source = client.get(f"/api/sources/{source_id}").json()
        assert "<!-- gunther:page=1 -->" in source["content"]
        assert source["assertions"][0]["evidence"][0]["locator"].startswith("page 1")


def test_image_ocr_creates_traceable_text_and_region_evidence(tmp_path: Path) -> None:
    provider = FakeOcrProvider()
    original = b"bounded fake image bytes"
    with make_client(tmp_path, provider) as client:
        response = client.post(
            "/api/captures/assets",
            params={"title": "Whiteboard", "fileName": "board.png", "kind": "image"},
            headers={"Content-Type": "image/png"},
            content=original,
        )

        assert response.status_code == 201
        captured = response.json()
        assert captured["processing"] == {
            "ocrStatus": "completed",
            "ocrProvider": "fake_local",
            "note": None,
        }
        source_id = captured["importResult"]["source"]["id"]
        source = client.get(f"/api/sources/{source_id}").json()
        assert "OCR status: completed" in source["content"]
        assert "OCR provider: fake_local" in source["content"]
        assert "<!-- gunther:page=1 -->" in source["content"]
        assert (
            "<!-- gunther:ocr-region=100000,200000,500000,50000 "
            "unit=ppm provider=fake_local confidence=0.980 -->"
        ) in source["content"]
        evidence = source["assertions"][0]["evidence"][0]
        assert evidence["quote"] == "Genome -> contains -> genes"
        assert evidence["locator"].startswith("page 1 · region 100000,200000,500000,50000 ppm")
        assert client.get(captured["asset"]["downloadUrl"]).content == original
        assert provider.image_calls and provider.image_calls[0][1] == 1


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
        captured = response.json()
        assert captured["processing"]["ocrStatus"] == "completed"
        assert captured["processing"]["ocrProvider"] == "fake_local"
        source = client.get(f"/api/sources/{captured['importResult']['source']['id']}").json()
        assert "Genome -> contains -> genes" in source["content"]
        assert source["assertions"][0]["evidence"][0]["locator"].startswith(
            "page 1 · region 100000,200000,500000,50000 ppm"
        )
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
        captured = response.json()
        assert captured["processing"]["ocrStatus"] == "degraded"
        assert "page 1" in captured["processing"]["note"]
        source = client.get(f"/api/sources/{captured['importResult']['source']['id']}").json()
        assert "<!-- gunther:page=2 -->" in source["content"]
        assert source["assertions"][0]["evidence"][0]["locator"].startswith("page 2")
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

    result = asset_service.extract_asset_content(
        path,
        path.name,
        "application/pdf",
        provider,
    )

    assert result.ocr_status == "degraded"
    assert provider.pdf_calls == []
    assert result.limitation is not None
    assert "60-second" in result.limitation
    assert "2 additional blank pages" in result.limitation


def test_unavailable_ocr_is_explicitly_degraded_and_keeps_original(tmp_path: Path) -> None:
    provider = UnavailableOcrProvider("OCR engine deliberately absent in this test.")
    original = b"unreadable but immutable image"
    with make_client(tmp_path, provider) as client:
        response = client.post(
            "/api/captures/assets",
            params={"title": "Offline scan", "fileName": "scan.png", "kind": "image"},
            headers={"Content-Type": "image/png"},
            content=original,
        )

        assert response.status_code == 201
        captured = response.json()
        assert captured["processing"]["ocrStatus"] == "degraded"
        assert captured["processing"]["ocrProvider"] is None
        assert "deliberately absent" in captured["processing"]["note"]
        source = client.get(f"/api/sources/{captured['importResult']['source']['id']}").json()
        assert "OCR status: degraded" in source["content"]
        assert "complete original remains available" in source["content"]
        assert client.get(captured["asset"]["downloadUrl"]).content == original
        health = client.get("/api/health").json()
        assert health["ocrMode"] == "not_configured"
        assert health["ocrProvider"] == "none"


@pytest.mark.parametrize(
    ("file_name", "media_type", "original", "visible", "hidden"),
    [
        (
            "large.txt",
            "text/plain",
            b"visible-text\n" + (b"x" * 160) + b"hidden-text",
            "visible-text",
            "hidden-text",
        ),
        (
            "large.html",
            "text/html",
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
    with make_client(tmp_path) as client:
        captured = client.post(
            "/api/captures/assets",
            params={"title": "Bounded text", "fileName": file_name, "kind": "file"},
            headers={"Content-Type": media_type},
            content=original,
        )
        assert captured.status_code == 201
        body = captured.json()
        source = client.get(f"/api/sources/{body['importResult']['source']['id']}").json()

        assert visible in source["content"]
        assert hidden not in source["content"]
        assert "limited to the first 64 bytes" in source["content"]
        downloaded = client.get(body["asset"]["downloadUrl"])
        assert downloaded.content == original


def test_character_limit_is_applied_during_html_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asset_service, "MAX_EXTRACTED_CHARACTERS", 40)
    path = tmp_path / "long.html"
    path.write_bytes(b"<p>" + (b"A" * 200) + b"</p>")

    text, limitation = asset_service.extract_asset_text(path, path.name, "text/html")

    assert len(text) == 40
    assert limitation is not None
    assert "limited to 40 characters" in limitation


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
        source = client.get(f"/api/sources/{body['importResult']['source']['id']}").json()

        assert "skipped to protect device resources" in source["content"]
        assert "archive member exceeds" in source["content"]
        assert "A" * 200 not in source["content"]
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
        source = client.get(f"/api/sources/{body['importResult']['source']['id']}").json()

        assert "skipped to protect device resources" in source["content"]
        assert "cumulative decompression budget" in source["content"]
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
        source = client.get(f"/api/sources/{body['importResult']['source']['id']}").json()

        assert "PDF exceeds" in source["content"]
        assert "complete original remains available" in source["content"]
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
        source = client.get(f"/api/sources/{body['importResult']['source']['id']}").json()

        assert "could not be safely extracted from 1 PDF page" in source["content"]
        assert client.get(body["asset"]["downloadUrl"]).content == original


def test_corrupt_archive_fails_closed_for_extraction_but_not_capture(tmp_path: Path) -> None:
    original = b"this is not a zip archive"
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
        source = client.get(f"/api/sources/{body['importResult']['source']['id']}").json()

        assert "Text extraction failed safely" in source["content"]
        assert client.get(body["asset"]["downloadUrl"]).content == original
