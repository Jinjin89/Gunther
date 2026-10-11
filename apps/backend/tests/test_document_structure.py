"""Genre-independent structure, visual ownership and metadata provenance."""

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from gunther.content import ParsedBlock
from gunther.document_parser import docling_document
from gunther.document_structure import organize_document, pdf_metadata
from gunther.documents import ParsedDocument, reference_data
from gunther.papers import pdf_facts
from gunther.processing import reconcile_fallback


@pytest.mark.parametrize(
    "titles",
    [
        ["Scope and ownership", "Retention policy", "Escalation paths"],
        ["Executive overview", "Operating costs", "Risks and decisions"],
        ["背景", "方案比较", "后续安排"],
    ],
)
def test_document_owns_its_section_names(titles):
    document = docling_document(
        {
            "body": {"children": [{"$ref": f"#/texts/{i}"} for i in range(6)]},
            "texts": [
                item
                for title in titles
                for item in (
                    {"label": "section_header", "text": title, "level": 2},
                    {"label": "text", "text": "Source paragraph under this heading."},
                )
            ],
        }
    )
    assert [e.text for e in document.elements if e.kind == "heading"] == titles
    assert [e.parent for e in document.elements if e.kind == "paragraph"] == [0, 2, 4]
    assert document.manifest["structure"]["inferredSectionNames"] is False


def test_unheaded_document_does_not_acquire_an_invented_outline():
    document = ParsedDocument(
        [
            ParsedBlock("First paragraph."),
            ParsedBlock("Copyright restrictions are discussed in this paragraph."),
        ]
    )
    organize_document(document)
    assert all(e.kind == "paragraph" for e in document.elements)
    assert document.manifest["outline"] == []
    assert document.manifest["structure"]["unheadedContent"] is True


def test_separate_printed_reference_markers_survive_and_resolve_body_mentions():
    document = docling_document(
        {
            "body": {"children": [{"$ref": f"#/texts/{i}"} for i in range(3)]},
            "texts": [
                {"label": "text", "text": "An original statement [7]."},
                {"label": "section_header", "text": "References", "level": 2},
                {
                    "label": "list_item",
                    "marker": "7.",
                    "enumerated": True,
                    "text": "Example, A. An observed result. 2024.",
                },
            ],
        }
    )
    entries, mentions = reference_data(document.elements)
    assert entries[0]["label"] == "7"
    assert entries[0]["raw"] == "7. Example, A. An observed result. 2024."
    assert document.elements[2].text == "Example, A. An observed result. 2024."
    assert mentions[0]["status"] == "resolved" and mentions[0]["targets"] == [0]


def test_trailing_publication_notices_are_not_bibliography_entries():
    document = docling_document(
        {
            "body": {"children": [{"$ref": f"#/texts/{i}"} for i in range(5)]},
            "texts": [
                {"label": "section_header", "text": "References", "level": 2},
                {
                    "label": "list_item",
                    "marker": "1.",
                    "enumerated": True,
                    "text": "Example, A. An observed result. 2024.",
                },
                {
                    "label": "text",
                    "text": "Publisher's note: territorial descriptions follow the source.",
                },
                {
                    "label": "text",
                    "text": "Open Access. This document is distributed under an explicit licence.",
                },
                {"label": "text", "text": "© The authors 2024"},
            ],
        }
    )
    entries, _ = reference_data(document.elements)
    assert [e["label"] for e in entries] == ["1"]
    assert all(e.kind == "metadata" for e in document.elements[2:])
    assert len(document.manifest["metadata"]["rights"]) == 3


def test_mentioning_an_author_does_not_hide_a_body_paragraph():
    document = ParsedDocument(
        [
            ParsedBlock("Alice Example 1; Bob Example 2", anchor={"page": 1}),
            ParsedBlock(
                "Alice Example describes why the operating model needs a separate review process.",
                anchor={"page": 1},
            ),
        ]
    )
    organize_document(
        document, {"authors": [{"value": "Alice Example; Bob Example", "origin": "xmp:creator"}]}
    )
    assert document.elements[0].kind == "metadata"
    assert document.elements[1].kind == "paragraph"


def provenance(x, y, w, h):
    return [
        {"page_no": 1, "bbox": {"l": x, "t": y, "r": x + w, "b": y + h, "coord_origin": "TOPLEFT"}}
    ]


def visual_document(with_prose=False):
    texts = [
        {"label": "text", "text": "Revenue 2024 0.1 0.2 0.3", "prov": provenance(12, 22, 10, 10)},
        {
            "label": "caption",
            "text": "Figure 1. Two measured series with their original labels and values.",
            "prov": provenance(10, 65, 80, 10),
        },
    ]
    children = [{"$ref": "#/pictures/0"}, {"$ref": "#/pictures/1"}, {"$ref": "#/texts/1"}]
    if with_prose:
        texts.append(
            {
                "label": "text",
                "text": "This is independent body prose between the diagrams. " * 4,
                "prov": provenance(36, 26, 20, 10),
            }
        )
        children.insert(1, {"$ref": "#/texts/2"})
    return docling_document(
        {
            "body": {"children": children},
            "texts": texts,
            "pictures": [
                {
                    "label": "picture",
                    "prov": provenance(10, 20, 25, 40),
                    "children": [{"$ref": "#/texts/0"}],
                },
                {"label": "picture", "prov": provenance(60, 20, 25, 40)},
            ],
            "pages": {"1": {"size": {"width": 100, "height": 100}}},
        }
    )


def test_panels_share_a_crop_and_chart_labels_are_not_body_prose():
    document = visual_document()
    figure = next(e for e in document.elements if e.kind == "figure")
    assert len([e for e in document.elements if e.kind == "figure"]) == 1
    assert figure.anchor["bboxPpm"] == [98_000, 198_000, 754_000, 404_000]
    label = next(e for e in document.elements if e.text.startswith("Revenue"))
    assert label.kind == "figure_text" and label.payload["noEmbed"] is True
    assert label.payload["visualOwner"] == figure.payload["documentRef"]
    assert len(figure.payload["panelRefs"]) == 2
    assert any(b.payload.get("noEmbed") for b in document.chunks() if b.kind == "figure_text")


def test_nearby_prose_prevents_combining_unrelated_regions():
    document = visual_document(with_prose=True)
    assert len([e for e in document.elements if e.kind == "figure"]) == 2
    assert (
        next(e for e in document.elements if e.text.startswith("This is independent")).kind
        == "paragraph"
    )


def test_furniture_keeps_source_locations_without_becoming_body_evidence():
    document = docling_document(
        {
            "body": {"children": [{"$ref": "#/texts/0"}]},
            "furniture": {"children": [{"$ref": "#/texts/1"}, {"$ref": "#/texts/2"}]},
            "texts": [
                {"label": "text", "text": "A complete body paragraph with evidence."},
                {
                    "label": "page_header",
                    "text": "Organization quarterly review",
                    "prov": provenance(10, 2, 80, 5),
                },
                {
                    "label": "page_footer",
                    "text": "Confidential — version 3",
                    "prov": provenance(10, 90, 80, 5),
                },
            ],
            "pages": {"1": {"size": {"width": 100, "height": 100}}},
        }
    )
    assert [e.kind for e in document.elements] == ["paragraph", "page_header", "page_footer"]
    assert all(b.payload.get("noEmbed") for b in document.chunks() if b.kind.startswith("page_"))
    assert document.elements[-1].anchor["page"] == 1


def test_native_fallback_cannot_reintroduce_labels_on_a_layout_page():
    document = visual_document()
    fallback = ParsedDocument(
        [
            ParsedBlock("Revenue 2024 0.1 0.2 0.3. Repeating page header.", anchor={"page": 1}),
            ParsedBlock(
                "A genuine missing page is retained with uncertain structure.", anchor={"page": 2}
            ),
        ]
    )
    recovered = reconcile_fallback(document, fallback)
    assert [e.anchor["page"] for e in recovered] == [2]
    assert recovered[0].payload["extraction"] == "fallback-gap"


def test_pdf_metadata_distinguishes_publisher_dates_and_file_software(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_metadata(
        {
            "/Title": "An operations handbook",
            "/Creator": "A PDF editor",
            "/CreationDate": "D:20200102030405Z",
        }
    )
    xmp = DecodedStreamObject()
    xmp.set_data(
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/">'
        b"<dc:publisher><rdf:Bag><rdf:li>Example Press</rdf:li></rdf:Bag></dc:publisher>"
        b"</rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    xmp.update(
        {NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")}
    )
    writer.root_object[NameObject("/Metadata")] = writer._add_object(xmp)
    path = tmp_path / "handbook.pdf"
    writer.write(path)
    document = ParsedDocument(
        [
            ParsedBlock("Published: 3 May 2024", anchor={"page": 1}),
            ParsedBlock("Accepted: xx xx xxxx", anchor={"page": 1}),
            ParsedBlock("The policy is accepted by all teams.", anchor={"page": 1}),
        ]
    )
    organize_document(document, pdf_metadata(path))
    metadata = document.manifest["metadata"]
    assert pdf_facts(path).year is None
    assert metadata["publisher"][0]["value"] == "Example Press"
    assert metadata["fileCreator"][0]["value"] == "A PDF editor"
    assert metadata["published"][0]["value"] == "3 May 2024"
    assert metadata["fileCreated"][0]["value"].startswith("D:2020")
    assert "accepted" not in metadata
    assert document.elements[1].payload["status"] == "unresolved"
    assert document.elements[2].kind == "paragraph"


def test_partial_bookmarks_calibrate_heading_styles_without_section_names(tmp_path):
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica-Bold"),
        }
    )
    font_ref = writer._add_object(font)
    for title in ("Ownership boundaries", "Data retention"):
        page = writer.add_blank_page(width=300, height=400)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 14 Tf 20 350 Td ({title}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(content)
    writer.add_outline_item("Ownership boundaries", 0)
    path = tmp_path / "policies.pdf"
    writer.write(path)
    document = ParsedDocument(
        [
            ParsedBlock(
                "Ownership boundaries", "heading", anchor={"page": 1}, payload={"level": 1}
            ),
            ParsedBlock("Data retention", "heading", anchor={"page": 2}, payload={"level": 6}),
            ParsedBlock("Retention paragraph.", anchor={"page": 2}),
        ]
    )
    organize_document(document, pdf_metadata(path, document))
    assert document.elements[1].payload["level"] == 1
    assert document.elements[1].payload["structureSource"] == "bookmark-calibrated-typography"
    assert document.elements[1].parent is None
    assert document.elements[2].parent == 1
    assert document.elements[2].headings == ["Data retention"]
