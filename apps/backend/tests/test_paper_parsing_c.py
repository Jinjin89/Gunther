"""Part C: source caption links, title evidence and bounded reference sections."""

import json

import pytest
from pypdf import PdfWriter

from gunther.content import ParsedBlock
from gunther.document_parser import docling_document
from gunther.document_structure import group_visuals
from gunther.documents import ParsedDocument, reference_data
from gunther.models import ContentBlock
from gunther.papers import PdfFacts, pdf_facts, read_paper


def element(text, kind, ref, box, **payload):
    return ParsedBlock(
        text, kind, anchor={"page": 1, "bboxPpm": box},
        payload={"documentRef": ref, **payload},
    )


def test_existing_caption_is_not_replaced_or_merged_by_geometry():
    figure = element(
        "Figure", "figure", "picture", [50_000, 100_000, 400_000, 300_000],
        caption="Figure 4. The source caption.", captionRefs=["linked"],
    )
    caption = element(
        "Figure 4. The source caption.", "caption", "linked",
        [50_000, 420_000, 400_000, 50_000],
    )
    distractor = element(
        "Figure 5. An unrelated caption that happens to be nearer.", "caption", "other",
        [50_000, 405_000, 400_000, 10_000],
    )
    original_anchor = figure.anchor.copy()
    group_visuals(ParsedDocument([figure, distractor, caption]))
    assert figure.payload["caption"] == caption.text
    assert figure.anchor == original_anchor
    assert "visualOwner" not in distractor.payload


@pytest.mark.parametrize("caption_kind", ["Figure", "Table"])
def test_numbered_caption_cannot_cross_columns(caption_kind):
    visual = element(
        "Figure", "figure" if caption_kind == "Figure" else "table", "visual",
        [50_000, 100_000, 400_000, 300_000],
    )
    caption = element(
        f"{caption_kind} 8. Measurements shown in the right column only.",
        "caption", "caption", [550_000, 410_000, 400_000, 50_000],
    )
    group_visuals(ParsedDocument([visual, caption]))
    assert not visual.payload.get("caption")
    assert "visualOwner" not in caption.payload


def test_figure_cannot_take_table_caption_even_in_same_column():
    figure = element("Figure", "figure", "figure", [50_000, 100_000, 400_000, 300_000])
    caption = element(
        "Table 7. Numeric measurements from a separate experiment.", "caption", "caption",
        [50_000, 410_000, 400_000, 50_000],
    )
    group_visuals(ParsedDocument([figure, caption]))
    assert "caption" not in figure.payload


def title_block(text, page, kind="heading", **payload):
    return ContentBlock(
        id=f"{page}-{text}", kind=kind, content=text,
        anchor_json=json.dumps({"page": page}), payload_json=json.dumps(payload),
    )


def test_declared_pdf_title_wins_over_large_type(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_metadata({"/Title": "Measurements of a Changing System"})
    writer.write(path)
    monkeypatch.setattr("gunther.papers.title_by_font", lambda reader: "Journal of Measurements")
    facts = pdf_facts(path)
    assert facts.title == "Measurements of a Changing System"
    assert facts.title_origin == "metadata"


@pytest.mark.parametrize("kind", ["heading", "page_header", "page_footer"])
def test_first_page_title_beats_repeated_venue_or_font_guess(kind):
    blocks = [title_block("Journal of Measurements", page, kind) for page in range(1, 5)]
    blocks.insert(1, title_block("Measurements of a Changing System", 1, role="title"))
    facts = read_paper(
        "download", blocks,
        PdfFacts(title="Journal of Measurements", title_origin="typography"),
    )
    assert facts.title == "Measurements of a Changing System"
    assert facts.title_from_document


def test_repeated_title_label_without_a_real_title_keeps_source_name():
    blocks = [title_block("Journal of Measurements", page, role="title") for page in range(1, 5)]
    facts = read_paper("User supplied name", blocks)
    assert facts.title == "User supplied name"
    assert not facts.title_from_document


def test_pdf_metadata_title_wins_over_layout_title():
    blocks = [title_block("A different layout title", 1, role="title")]
    facts = read_paper("download", blocks, PdfFacts(title="Measurements of a Changing System"))
    assert facts.title == "Measurements of a Changing System"


def test_font_title_remains_available_without_structured_title():
    facts = read_paper(
        "download", [],
        PdfFacts(title="Measurements of a Changing System", title_origin="typography"),
    )
    assert facts.title == "Measurements of a Changing System"


def test_unlinked_visual_cannot_claim_a_source_linked_caption_inside_its_box():
    linked = element(
        "Figure", "figure", "linked", [50_000, 100_000, 400_000, 250_000],
        caption="Figure 2. Caption linked to the first picture only.", captionRefs=["caption"],
    )
    unrelated = element("Figure", "figure", "other", [50_000, 360_000, 400_000, 200_000])
    caption = element(
        linked.payload["caption"], "caption", "caption",
        [50_000, 400_000, 400_000, 40_000],
    )
    group_visuals(ParsedDocument([linked, unrelated, caption]))
    assert caption.kind == "caption"
    assert not unrelated.payload.get("caption")
    assert caption.payload["visualOwner"] == "linked"


@pytest.mark.parametrize("kind,label", [("figure", "Figure"), ("table", "Table")])
def test_geometry_still_fills_an_unlinked_caption_in_its_column(kind, label):
    visual = element("Visual", kind, "visual", [50_000, 100_000, 400_000, 300_000])
    caption = element(
        f"{label} 1. Source description for the measurements above.", "caption", "caption",
        [50_000, 410_000, 400_000, 50_000],
    )
    group_visuals(ParsedDocument([visual, caption]))
    assert visual.payload["caption"] == caption.text
    assert visual.payload["captionRef"] == "caption"
    assert caption.payload["visualOwner"] == "visual"


@pytest.mark.parametrize("heading", ["References", "7. References", "Bibliography", "参考文献"])
def test_reference_span_ends_at_any_next_heading(heading):
    from gunther.document_structure import in_reference_section

    assert not in_reference_section([])
    assert in_reference_section(["Document title", heading])
    assert not in_reference_section(["Document title", heading, "Additional observations"])
    assert in_reference_section(["Document title", heading, "Appendix", "References"])


@pytest.mark.parametrize("level", [1, 2, 3, 5])
def test_next_real_heading_ends_references_even_at_deeper_level(level):
    texts = [
        {"label": "section_header", "text": "References", "level": 2},
        {"label": "list_item", "marker": "1.", "text": "Example, A. Observations. 2024."},
        {"label": "section_header", "text": "Further observations", "level": level},
        {"label": "text", "text": "Original appendix evidence remains searchable."},
        {"label": "list_item", "marker": "1.", "text": "An appendix list item."},
        {"label": "reference", "text": "Explicitly labeled reference remains a reference."},
    ]
    parsed = docling_document({
        "body": {"children": [{"$ref": f"#/texts/{i}"} for i in range(len(texts))]},
        "texts": texts,
    })
    assert [e.kind for e in parsed.elements] == [
        "heading", "reference", "heading", "paragraph", "list_item", "reference"
    ]
    assert not parsed.elements[3].payload.get("noEmbed")
    assert not parsed.elements[4].payload.get("reference")
    assert len(reference_data(parsed.elements)[0]) == 2
    assert [e.text for e in parsed.elements] == [t["text"] for t in texts]


@pytest.mark.parametrize("kind,collection", [("picture", "pictures"), ("table", "tables")])
def test_source_link_survives_without_caption_geometry(kind, collection):
    visual = {
        "label": kind,
        "captions": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}],
        "data": {"table_cells": [{"text": "42"}]},
    }
    parsed = docling_document({
        "body": {"children": [
            {"$ref": f"#/{collection}/0"}, {"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}
        ]},
        collection: [visual],
        "texts": [
            {"label": "caption", "text": "Original caption."},
            {"label": "caption", "text": "Second caption line."},
        ],
    })
    assert parsed.elements[0].payload["captionRefs"] == ["#/texts/0", "#/texts/1"]
    assert parsed.elements[0].payload["caption"] == "Original caption.\nSecond caption line."
    assert all(
        e.kind == "caption" and e.payload["visualOwner"] == f"#/{collection}/0"
        for e in parsed.elements[1:]
    )


@pytest.mark.parametrize("caption_label,owner_kind", [("Figure", "table"), ("Table", "figure")])
def test_contradictory_source_link_is_retained_as_provenance_and_reassociated(
    caption_label, owner_kind
):
    correct_kind = "figure" if caption_label == "Figure" else "table"
    caption = element(
        f"{caption_label} 3. Measured values from the experiment.", "caption", "caption",
        [100_000, 320_000, 800_000, 40_000],
    )
    correct = element(
        "Correct visual", correct_kind, "correct", [100_000, 100_000, 800_000, 200_000]
    )
    wrong = element(
        "Other visual", owner_kind, "wrong", [100_000, 500_000, 400_000, 200_000],
        caption=caption.text, captionRef="caption", captionRefs=["caption"],
    )
    caption.payload["visualOwner"] = "wrong"
    document = ParsedDocument([correct, wrong, caption])
    group_visuals(document)
    assert correct.payload["caption"] == caption.text
    assert caption.payload["visualOwner"] == "correct"
    assert caption.payload["sourceVisualOwner"] == "wrong"
    assert not wrong.payload.get("caption")
    assert not wrong.payload.get("captionRefs")
    assert "captionRef" not in wrong.payload
    assert wrong.payload["sourceCaptionRefs"] == ["caption"]
    assert wrong.payload["sourceCaption"] == caption.text
    assert wrong.payload["captionLinkIssues"] == [
        {"captionRef": "caption", "reason": "caption-type-mismatch"}
    ]
    assert [e.text for e in document.elements] == ["Correct visual", "Other visual", caption.text]


def panel_document(*, both_linked=False, caption_box=None, left_box=None, prose=False):
    caption = element(
        "Figure 8. Two views of the same measured phenomenon.", "caption", "caption",
        caption_box or [100_000, 450_000, 800_000, 40_000],
    )
    left = element("Figure", "figure", "left", left_box or [100_000, 100_000, 300_000, 300_000])
    right = element(
        "Figure", "figure", "right", [600_000, 100_000, 300_000, 300_000],
        caption=caption.text, captionRef="caption", captionRefs=["caption"],
    )
    if both_linked:
        left.payload.update(caption=caption.text, captionRef="caption", captionRefs=["caption"])
    annotation = element(
        "Panel annotation", "figure_text", "annotation", [610_000, 410_000, 200_000, 20_000],
        visualOwner="right", noEmbed=True,
    )
    elements = [left, right, annotation, caption]
    if prose:
        elements.insert(1, element(
            "This is independent body prose between the diagrams. " * 4,
            "paragraph", "prose", [420_000, 150_000, 160_000, 200_000],
        ))
    return ParsedDocument(elements)


@pytest.mark.parametrize("both_linked", [False, True])
def test_shared_caption_belongs_to_panel_group_even_if_only_one_panel_was_linked(both_linked):
    document = panel_document(both_linked=both_linked)
    group_visuals(document)
    left, right, annotation, caption = document.elements
    assert left.kind == "figure" and right.kind == "figure_panel"
    assert left.payload["caption"] == caption.text
    assert left.payload["captionRefs"] == ["caption"]
    assert set(left.payload["panelRefs"]) == {"left", "right"}
    assert left.anchor["bboxPpm"] == [98_000, 98_000, 804_000, 334_000]
    assert all(e.payload["visualOwner"] == "left" for e in (right, annotation, caption))
    assert "caption" not in right.payload and "captionRefs" not in right.payload
    assert right.payload["sourceCaptionRefs"] == ["caption"]
    assert right.payload["sourceCaption"] == caption.text


@pytest.mark.parametrize("kwargs", [
    {"caption_box": [550_000, 450_000, 400_000, 40_000]},
    {"left_box": [100_000, 10_000, 300_000, 60_000]},
    {"prose": True},
])
def test_linked_caption_does_not_absorb_another_column_row_or_intervening_prose(kwargs):
    document = panel_document(**kwargs)
    group_visuals(document)
    pictures = [e for e in document.elements if e.kind == "figure"]
    assert len(pictures) == 2
    assert not pictures[0].payload.get("caption")
    assert document.elements[-1].payload["visualOwner"] == "right"


def test_panel_with_its_own_caption_is_not_merged_into_a_nearby_group():
    document = panel_document()
    left_caption = element(
        "Figure 7. An independent observation.", "caption", "left-caption",
        [100_000, 410_000, 300_000, 30_000],
    )
    document.elements[0].payload.update(
        caption=left_caption.text, captionRefs=["left-caption"], captionRef="left-caption",
    )
    document.elements.append(left_caption)
    group_visuals(document)
    assert len([e for e in document.elements if e.kind == "figure"]) == 2
    assert document.elements[0].payload["caption"] == left_caption.text
    assert document.elements[1].payload["captionRef"] == "caption"
