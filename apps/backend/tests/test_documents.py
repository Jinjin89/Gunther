"""Document fidelity, immutable revisions and retained image ownership."""

import asyncio
import json
import struct
import zlib
from datetime import timedelta
from zipfile import ZipFile

from sqlalchemy import func, select
from test_structured_knowledge import base, client_for, source

from gunther.asset_service import AssetTextExtraction
from gunther.content import ParsedBlock
from gunther.database import session_scope
from gunther.document_parser import docling_document
from gunther.documents import DocumentAsset, ParsedDocument, reference_data, text_document
from gunther.models import (
    Asset,
    BlockElementMap,
    ElementAsset,
    ProcessingAsset,
    ProcessingJob,
    SourceIndexHead,
    SourceRevision,
    utc_now,
)
from gunther.portable_documents import archive_document, html_document, pdf_raster_images
from gunther.processing import reconcile_fallback


def png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\x00\xff\x00"))
        + chunk(b"IEND", b"")
    )


def test_canonical_paragraph_and_list_slices_map_to_exact_unicode_text():
    paragraph = "中文😀 evidence. " * 180
    item = "- " + "A long list item with evidence. " * 100
    document = text_document(f"# Methods\n\n{paragraph}\n\n{item}\n\nNext paragraph.")
    assert len(document.elements) == 4
    assert document.elements[1].text == paragraph.rstrip()
    assert document.elements[2].kind == "list_item"
    assert document.elements[2].text == item.rstrip()
    for block in document.chunks():
        element = document.elements[block.payload["elementOrdinal"]]
        assert (
            element.text[block.anchor["elementCharStart"] : block.anchor["elementCharEnd"]]
            == block.text
        )


def test_grouped_references_and_ambiguous_author_year_keep_raw_strings():
    document = text_document(
        "# Findings\n😀 Evidence [1,2–3]. Smith (2020) disagrees. Unknown [9].\n"
        "# References\n[1] Smith, A. First result. 2020. [2] Jones, B. 2021. "
        "[3] Smith, A. Another result. 2020."
    )
    entries, mentions = reference_data(document.elements)
    assert [entry["label"] for entry in entries] == ["1", "2", "3"]
    ranged = next(mention for mention in mentions if mention["raw"] == "[1,2–3]")
    assert ranged["status"] == "resolved" and ranged["targets"] == [0, 1, 2]
    assert next(m for m in mentions if m["method"] == "author-year")["status"] == "ambiguous"
    assert next(m for m in mentions if m["raw"] == "[9]")["status"] == "unresolved"
    for mention in mentions:
        text = document.elements[mention["element"]].text
        assert text[mention["start"] : mention["end"]] == mention["raw"]


def test_html_superscript_markers_use_document_references_not_guessed_numbers():
    document = html_document(
        "<h1>Results</h1><p>😀 Benefit<sup>1,2</sup>; x<sup>9</sup>.</p>"
        "<h2>References</h2><p>[1] Smith 2020.</p><p>[2] Jones 2021.</p>"
    )
    _, mentions = reference_data(document.elements)
    assert [(m["raw"], m["status"]) for m in mentions] == [("1,2", "resolved"), ("9", "unresolved")]
    assert all(m["method"] == "numeric-superscript" for m in mentions)


def test_docling_sibling_headings_figures_captions_and_bottom_left_geometry():
    document = docling_document(
        {
            "body": {
                "children": [
                    {"$ref": "#/texts/0"},
                    {"$ref": "#/texts/1"},
                    {"$ref": "#/pictures/0"},
                    {"$ref": "#/texts/2"},
                ]
            },
            "texts": [
                {"label": "section_header", "text": "Methods", "level": 2},
                {"label": "text", "text": "Paragraph under the sibling heading."},
                {"label": "caption", "text": "Figure 1. The measured result."},
            ],
            "pictures": [
                {
                    "label": "picture",
                    "captions": [{"$ref": "#/texts/2"}],
                    "prov": [
                        {
                            "page_no": 2,
                            "bbox": {
                                "l": 10,
                                "t": 80,
                                "r": 70,
                                "b": 20,
                                "coord_origin": "BOTTOMLEFT",
                            },
                        }
                    ],
                }
            ],
            "pages": {"2": {"size": {"width": 100, "height": 100}}},
        }
    )
    assert document.elements[1].parent == 0
    figure = document.elements[2]
    assert figure.headings == ["Methods"]
    assert figure.payload["caption"].startswith("Figure 1.")
    assert figure.anchor["bboxPpm"] == [100_000, 200_000, 600_000, 600_000]


def test_partial_page_fallback_recovers_missing_text_without_repeating_known_text():
    known = "This structured paragraph is already present and sufficiently long."
    missing = "This scanned paragraph was omitted by layout parsing and must be retained."
    structured = ParsedDocument([ParsedBlock(known, anchor={"page": 1})])
    fallback = ParsedDocument([ParsedBlock(known + " " + missing, anchor={"page": 1})])
    recovered = reconcile_fallback(structured, fallback)
    assert len(recovered) == 1 and recovered[0].text == missing
    assert recovered[0].anchor["page"] == 1
    assert "bboxPpm" not in recovered[0].anchor
    assert (
        reconcile_fallback(structured, ParsedDocument([ParsedBlock(known, anchor={"page": 1})]))
        == []
    )


def test_epub_uses_spine_order_and_preserves_relative_embedded_image(tmp_path):
    path = tmp_path / "book.epub"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            "<rootfiles>"
            '<rootfile full-path="OPS/book.opf"/>'
            "</rootfiles>"
            "</container>",
        )
        archive.writestr(
            "OPS/book.opf",
            '<package xmlns="http://www.idpf.org/2007/opf">'
            "<manifest>"
            '<item id="first" href="z.html"/>'
            '<item id="last" href="a.html"/>'
            "</manifest>"
            "<spine>"
            '<itemref idref="first"/>'
            '<itemref idref="last"/>'
            "</spine>"
            "</package>",
        )
        archive.writestr(
            "OPS/z.html", '<h1>First</h1><p>Opening.</p><img src="images/chart.png" alt="Chart"/>'
        )
        archive.writestr("OPS/a.html", "<h1>Last</h1><p>Conclusion.</p>")
        archive.writestr("OPS/images/chart.png", png())
    document = archive_document(path, ".epub")
    assert [e.text for e in document.elements if e.kind == "heading"] == ["First", "Last"]
    assert document.assets[0].data == png()
    assert (
        next(e for e in document.elements if e.kind == "figure").payload["imageKey"]
        == "OPS/images/chart.png"
    )


def test_docx_preserves_paragraphs_and_image_relationship(tmp_path):
    path = tmp_path / "notes.docx"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<w:body>"
            "<w:p>"
            "<w:pPr>"
            '<w:pStyle w:val="Heading1"/>'
            "</w:pPr>"
            "<w:r>"
            "<w:t>Methods</w:t>"
            "</w:r>"
            "</w:p>"
            "<w:p>"
            "<w:r>"
            "<w:t>Paragraph one.</w:t>"
            "</w:r>"
            "</w:p>"
            "<w:p>"
            "<w:r>"
            "<w:t>Paragraph two.</w:t>"
            "<w:drawing>"
            '<a:blip r:embed="rId1"/>'
            "</w:drawing>"
            "</w:r>"
            "</w:p>"
            "</w:body>"
            "</w:document>",
        )
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="media/figure.png"/>'
            "</Relationships>",
        )
        archive.writestr("word/media/figure.png", png())
    document = archive_document(path, ".docx")
    assert [e.text for e in document.elements if e.kind == "paragraph"] == [
        "Paragraph one.",
        "Paragraph two.",
    ]
    assert document.assets[0].data == png()
    assert all(e.parent == 0 for e in document.elements[1:])


def test_native_pdf_fallback_exports_an_actual_embedded_raster(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    image = DecodedStreamObject()
    image.set_data(b"\xff\x00\x00\x00\xff\x00")
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(2),
            NameObject("/Height"): NumberObject(1),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): writer._add_object(image)})}
    )
    path = tmp_path / "figure.pdf"
    writer.write(path)
    document = text_document("<!-- gunther:page=1 -->\n## Methods\nA paragraph.")
    pdf_raster_images(path, document)
    assert document.assets and document.assets[0].media_type == "image/png"
    assert [e.kind for e in document.elements] == ["heading", "paragraph", "embedded_image"]
    assert document.elements[-1].payload["captionStatus"] == "unresolved"
    assert document.manifest["images"] == "partial"


def capture_pdf(client, library):
    response = client.post(
        "/api/captures/assets",
        params={
            "title": "Paper",
            "fileName": "paper.pdf",
            "kind": "paper",
            "knowledgeBaseId": library,
            "deferProcessing": "true",
        },
        content=b"%PDF-fixture",
        headers={"content-type": "application/pdf"},
    )
    assert response.status_code == 201, response.text
    return response.json()["importResult"]["source"]["id"]


class FigureParser:
    version = "fixture-docling-v1"

    def parse(self, _path):
        return ParsedDocument(
            [
                ParsedBlock("Results", "heading", payload={"level": 1}),
                ParsedBlock("Evidence [1].", parent=0),
                ParsedBlock(
                    "Figure",
                    "figure",
                    0,
                    payload={"imageKey": "figure", "caption": "Figure 1. Evidence."},
                ),
                ParsedBlock("[1] Smith 2020.", "reference", payload={"reference": True}),
            ],
            [DocumentAsset("figure", png())],
            {"schema": 1, "method": "docling"},
        )


def test_reparse_is_idempotent_and_figures_references_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gunther.processing.extract_asset_content",
        lambda *args: AssetTextExtraction("Evidence [1]."),
    )
    with client_for(tmp_path) as client:
        saved = capture_pdf(client, base(client))
        worker = client.app.state.processing_worker
        worker.document_parser = FigureParser()
        assert worker.run_once()
        document = client.get(f"/api/sources/{saved}/document").json()
        image_id = next(e for e in document["elements"] if e["kind"] == "figure")["payload"][
            "imageAssetId"
        ]
        assert client.get(f"/api/assets/{image_id}").content == png()
        assert document["references"][0]["raw"] == "[1] Smith 2020."
        assert client.post(f"/api/sources/{saved}/reprocess").status_code == 202
        while worker.run_once():
            pass
        with session_scope(worker.sessions) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(SourceRevision)
                    .where(SourceRevision.source_id == saved)
                )
                == 1
            )
            assert session.scalar(select(func.count()).select_from(ElementAsset)) == 1
            assert session.scalar(select(func.count()).select_from(ProcessingAsset)) == 0
            assert session.scalar(select(func.count()).select_from(BlockElementMap)) > 0
    with client_for(tmp_path) as client:
        document = client.get(f"/api/sources/{saved}/document").json()
        assert document["revisionId"]
        assert client.get(f"/api/assets/{image_id}").content == png()
        other = source(client, base(client), "Another document.")
        assert (
            client.get(
                f"/api/sources/{other}/document", params={"revision_id": document["revisionId"]}
            ).status_code
            == 404
        )


def test_cancelled_import_releases_derived_assets_and_cannot_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gunther.processing.extract_asset_content", lambda *args: AssetTextExtraction("Evidence.")
    )
    with client_for(tmp_path) as client:
        saved = capture_pdf(client, base(client))
        worker = client.app.state.processing_worker

        class CancelParser(FigureParser):
            def parse(self, path):
                result = super().parse(path)
                client.post(f"/api/sources/{saved}/processing/cancel")
                return result

        worker.document_parser = CancelParser()
        assert worker.run_once()
        with session_scope(worker.sessions) as session:
            assert session.scalar(select(func.count()).select_from(Asset)) == 1
            assert session.scalar(select(func.count()).select_from(ProcessingAsset)) == 0
            assert session.get(SourceIndexHead, saved) is None


def test_reclaimed_lease_collects_abandoned_images_safely(tmp_path):
    with client_for(tmp_path) as client:
        capture_pdf(client, base(client))
        worker = client.app.state.processing_worker
        job_id, token = worker.claim()
        image = asyncio.run(
            worker.assets.preserve_bytes(
                png(), file_name="staged.png", media_type="image/png", staging=(job_id, token)
            )
        )
        with session_scope(worker.sessions) as session:
            session.get(ProcessingJob, job_id).lease_until = utc_now() - timedelta(seconds=1)
        assert worker.claim()[1] != token
        with session_scope(worker.sessions) as session:
            assert session.get(Asset, image.id) is None
            assert session.scalar(select(func.count()).select_from(ProcessingAsset)) == 0


def test_parser_failure_retains_a_richer_existing_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gunther.processing.extract_asset_content",
        lambda *args: AssetTextExtraction("Evidence [1]."),
    )
    with client_for(tmp_path) as client:
        saved = capture_pdf(client, base(client))
        worker = client.app.state.processing_worker
        worker.document_parser = FigureParser()
        worker.run_once()
        first = client.get(f"/api/sources/{saved}/document").json()["revisionId"]

        class FailedParser:
            version = "failed-parser"

            def parse(self, _path):
                raise RuntimeError("Fixture failure")

        worker.document_parser = FailedParser()
        client.post(f"/api/sources/{saved}/reprocess")
        while worker.run_once():
            pass
        assert client.get(f"/api/sources/{saved}/document").json()["revisionId"] == first
        with session_scope(worker.sessions) as session:
            job = session.scalar(select(ProcessingJob).where(ProcessingJob.kind == "parse_asset"))
            assert job.state == "failed" and "retained" in job.error
            assert (
                json.loads(session.get(SourceRevision, first).manifest_json)["method"] == "docling"
            )
