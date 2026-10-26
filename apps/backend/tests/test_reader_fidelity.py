"""Readable output from independent layout, glyph and image evidence."""

from PIL import Image

from gunther.content import ParsedBlock
from gunther.document_parser import attach_document_images, docling_document
from gunther.document_structure import evidence_eligible, organize_document
from gunther.documents import ParsedDocument
from gunther.pdf_native import SYMBOL_ENCODING, _native_text, _owned


def region(x, y, width, height, page=1):
    return [{"page_no": page, "bbox": {
        "l": x, "t": y, "r": x + width, "b": y + height, "coord_origin": "TOPLEFT",
    }}]


def text(label, content, size, box):
    return {"label": label, "text": content, "gunther_font_size": size, "prov": region(*box)}


def test_formula_without_transcription_survives_and_renders_from_page(tmp_path):
    raw = {
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {"label": "formula", "text": "", "orig": "", "prov": region(10, 20, 50, 10)},
            {"label": "formula", "text": "", "orig": "x = y", "prov": region(10, 40, 50, 10)},
        ],
        "pages": {"1": {"size": {"width": 100, "height": 100}}},
        "gunther_page_images": [{"page": 1, "file": "page.png"}],
    }
    Image.new("RGB", (200, 200), "white").save(tmp_path / "page.png")
    document = docling_document(raw)
    attach_document_images(document, raw, tmp_path)
    assert [e.kind for e in document.elements] == ["equation", "equation"]
    assert [e.text for e in document.elements] == ["", "x = y"]
    assert document.elements[0].payload["noEmbed"] is True
    assert len(document.assets) == 2
    assert document.manifest["missingImages"] == 0
    assert document.assets[0].data.startswith(b"\x89PNG")


def test_caption_and_typography_recover_lower_panels_without_eating_body():
    raw = {
        "body": {"children": [
            {"$ref": "#/pictures/0"}, *({"$ref": f"#/texts/{i}"} for i in range(5)),
        ]},
        "pictures": [{"label": "picture", "prov": region(10, 10, 80, 20)}],
        "texts": [
            text("text", "b", 7, (10, 35, 4, 3)),
            text("section_header", "Evaluation map", 7, (20, 40, 40, 4)),
            text("text", "Figure 9 | Comparison across both panels.", 7, (10, 65, 38, 5)),
            text(
                "text",
                "The second panel uses the same units and the same observation interval.",
                7,
                (52, 65, 38, 10),
            ),
            text("text", "This is the next genuine body paragraph. " * 7, 10, (10, 80, 80, 15)),
        ],
        "pages": {"1": {"size": {"width": 100, "height": 100}}},
    }
    document = docling_document(raw)
    visual = document.elements[0]
    assert visual.anchor["bboxPpm"][3] > 500_000
    labels = document.elements[1:3]
    assert all(e.kind == "figure_text" and not evidence_eligible(e.kind, e.payload) for e in labels)
    assert document.elements[-1].kind == "paragraph"
    assert document.elements[-1].headings == []
    assert "same observation interval" in visual.payload["caption"]
    assert "Evaluation map" not in [e["title"] for e in document.manifest["outline"]]


def test_native_scripts_and_discretionary_hyphens_keep_exact_offsets():
    chars = [{"text": c, "size": 10, "y": 20} for c in "Read x"]
    chars += [{"text": "i", "size": 6, "y": 17}]
    chars += [{"text": c, "size": 10, "y": 20} for c in " and R"]
    chars += [{"text": "2", "size": 6, "y": 24}]
    chars += [{"text": c, "size": 10, "y": 20} for c in " per"]
    chars += [{"text": "\ufffe", "size": 10, "y": 20, "hyphen": True}]
    chars += [{"text": "\n", "size": 0, "y": 20}]
    chars += [{"text": c, "size": 10, "y": 8} for c in "formed well."]
    result = _native_text(chars)
    assert result["text"] == "Read xi and R2 performed well."
    assert [(result["text"][s["start"]:s["end"]], s["script"]) for s in result["scripts"]] == [
        ("i", "sub"), ("2", "super"),
    ]


def test_a_line_end_outside_the_layout_box_still_separates_the_words():
    def glyph(index, text, left):
        return {"index": index, "text": text, "size": 10, "y": 20, "left": left,
                "right": left + 5, "cy": 20, "page": 1}

    # "frustrated" ends a line; its space and break lie past the box's right edge.
    chars = [glyph(0, "e", 90), glyph(1, "d", 95), glyph(2, " ", 101),
             {**glyph(3, "\r", 104), "y": 20}, {**glyph(4, "\n", 104), "y": 20},
             {**glyph(5, "c", 10), "y": 8, "cy": 8}, {**glyph(6, "o", 15), "y": 8, "cy": 8}]
    owner = {(1, index): (1.0, 0, 0) for index in (0, 1, 5, 6)}
    owned = _owned({1: chars}, owner)
    assert _native_text(owned[(0, 0)])["text"] == "ed co"
    # Whitespace after a glyph that belongs to no region stays out.
    assert _owned({1: [glyph(0, "x", 0), glyph(1, " ", 5)]}, {}) == {}


def test_accents_badges_and_line_starts_are_read_from_the_glyphs():
    def glyph(text, left, y=20, **extra):
        return {"text": text, "size": 10, "y": y, "left": left, "right": left + 5,
                "cy": y, "x": left + 2.5, "page": 1, **extra}

    # A name, then on the next line an ORCID badge's glyphs and an accent drawn
    # as its own glyph over the letter before it ("Ba¨r").
    chars = [glyph(c, i * 5) for i, c in enumerate("Ada Lovelace")]
    chars += [{"text": "\n", "size": 10, "y": 20, "left": 70, "right": 70, "cy": 20,
               "x": 70, "page": 1}]
    chars += [glyph("I", 0, 8, icon=True), glyph("D", 5, 8, icon=True), glyph(" ", 10, 8)]
    chars += [glyph("B", 15, 8), glyph("a", 20, 8), glyph("¨", 20.5, 8), glyph("r", 25, 8)]
    native = _native_text(chars)
    assert native["text"] == "Ada Lovelace ID Bär"
    assert native["lineStarts"] == [13]
    assert native["icons"] == [{"start": 13, "end": 15}]


def test_symbol_font_private_codes_read_as_their_characters():
    assert [SYMBOL_ENCODING[code] for code in (0x61, 0x63, 0x68, 0x6D, 0xB1, 0xE5, 0xF2)] == [
        "α", "χ", "η", "μ", "±", "∑", "∫",
    ]
    assert SYMBOL_ENCODING[0x20] == " " and len(SYMBOL_ENCODING) == 189


def test_cross_page_sentence_continuation_requires_matching_structure_and_geometry():
    first = ParsedBlock("The results support the following explanation. " * 3 + "We", anchor={
        "page": 1, "bboxPpm": [500_000, 850_000, 400_000, 100_000],
    }, payload={"documentRef": "first"})
    second = ParsedBlock("attribute this to the measured change.", anchor={
        "page": 2, "bboxPpm": [50_000, 60_000, 400_000, 100_000],
    }, payload={"documentRef": "second"})
    document = ParsedDocument([first, ParsedBlock("Chart labels", "figure_text"), second])
    organize_document(document)
    assert second.payload["continuesDocumentRef"] == "first"
    first.text += "."
    organize_document(document)
    assert "continuesDocumentRef" not in second.payload


def test_continuation_skips_visuals_but_stops_at_an_equation():
    def part(text, page, box, ref):
        anchor = {"page": page, "bboxPpm": box}
        return ParsedBlock(text, anchor=anchor, payload={"documentRef": ref})

    lead = "The values follow from the relation " * 4
    first = part(lead, 1, [50_000, 850_000, 400_000, 100_000], "a")
    second = part("where the constant is measured.", 2, [50_000, 60_000, 400_000, 100_000], "b")
    figure = ParsedBlock("Figure", "figure", payload={"documentRef": "f"})
    document = ParsedDocument([first, figure, second])
    organize_document(document)
    assert second.payload["continuesDocumentRef"] == "a"
    equation = ParsedBlock("E = mc2", "equation", payload={"documentRef": "e"})
    document = ParsedDocument([first, equation, second])
    organize_document(document)
    assert "continuesDocumentRef" not in second.payload


def test_continuation_below_a_figure_needs_both_column_ends():
    def part(text, page, box, ref, kind="paragraph"):
        anchor = {"page": page, "bboxPpm": box}
        return ParsedBlock(text, kind, anchor=anchor, payload={"documentRef": ref})

    lead = "Short models were competitive with the mean " * 3
    first = part(lead, 1, [50_000, 60_000, 400_000, 90_000], "a")
    figure = part("Figure", 2, [50_000, 50_000, 900_000, 600_000], "f", "figure")
    second = part("baselines in each scenario.", 2, [50_000, 700_000, 400_000, 50_000], "b")
    organize_document(ParsedDocument([first, figure, second]))
    assert second.payload["continuesDocumentRef"] == "a"
    # Prose below the first part in the same column means it did not end there.
    box = [50_000, 300_000, 400_000, 50_000]
    below = part("Another complete paragraph.", 1, box, "c", "list_item")
    organize_document(ParsedDocument([first, below, figure, second]))
    assert "continuesDocumentRef" not in second.payload


def test_panel_letter_touching_a_visual_edge_joins_the_visual():
    raw = {
        "body": {"children": [
            {"$ref": "#/texts/0"}, {"$ref": "#/pictures/0"}, {"$ref": "#/texts/1"},
        ]},
        "pictures": [{"label": "picture", "prov": region(12, 10, 70, 50), "captions": [
            {"$ref": "#/texts/1"},
        ]}],
        "texts": [
            text("text", "a", 10, (11, 9, 1, 1)),
            text("text", "Figure 2 | Results across panels.", 8, (12, 62, 70, 4)),
        ],
        "pages": {"1": {"size": {"width": 100, "height": 100}}},
    }
    document = docling_document(raw)
    assert document.elements[0].kind == "figure_text"


def test_small_type_closing_a_page_does_not_break_a_sentence():
    def part(text, page, box, ref, size):
        anchor = {"page": page, "bboxPpm": box}
        return ParsedBlock(text, anchor=anchor, payload={"documentRef": ref, "fontSize": size})

    prose = "Running prose is set in the body size of this document. " * 5
    opening = part(prose, 1, [50_000, 100_000, 400_000, 600_000], "o", 8.2)
    lead = prose + "and the challenge lies in"
    first = part(lead, 1, [500_000, 700_000, 400_000, 100_000], "a", 8.2)
    note = part("1 Institute of Example Studies, Example City. " * 3, 1,
                [50_000, 860_000, 900_000, 60_000], "n", 7.5)
    second = part("representing each state well.", 2, [50_000, 60_000, 400_000, 100_000], "b", 8.2)
    organize_document(ParsedDocument([opening, first, note, second]))
    assert second.payload["continuesDocumentRef"] == "a"
    assert "continuesDocumentRef" not in note.payload


def test_caption_parts_split_by_columns_rejoin_as_one_sentence():
    raw = {
        "body": {"children": [
            {"$ref": "#/pictures/0"}, *({"$ref": f"#/texts/{i}"} for i in range(3)),
        ]},
        "pictures": [{"label": "picture", "prov": region(10, 10, 80, 40)}],
        "texts": [
            text("text", "Figure 5 | Results. c, Error plot for models under", 7, (10, 55, 38, 5)),
            text(
                "text", "two training settings. d, Waterfall plot of gains in each setting.", 7,
                (52, 55, 38, 5),
            ),
            text("text", "Running body prose that follows the figure. " * 6, 10, (10, 70, 80, 20)),
        ],
        "pages": {"1": {"size": {"width": 100, "height": 100}}},
    }
    visual = docling_document(raw).elements[0]
    assert "models under two training settings" in visual.payload["caption"]


def test_another_floats_caption_bounds_a_figure_crop():
    raw = {
        "body": {"children": [
            {"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}, {"$ref": "#/pictures/0"},
            {"$ref": "#/texts/2"}, {"$ref": "#/texts/3"},
        ]},
        "pictures": [{"label": "picture", "prov": region(15, 35, 70, 25)}],
        "texts": [
            text("text", "Body prose above both floats on a page. " * 6, 10, (10, 5, 80, 15)),
            text("caption", "Table 1. Architectures used in every experiment.", 8, (10, 25, 80, 4)),
            text("text", "x", 7, (12, 31, 2, 2)),
            text("text", "Figure 4. Training curves for both model families.", 8, (10, 62, 80, 4)),
        ],
        "pages": {"1": {"size": {"width": 100, "height": 100}}},
    }
    document = docling_document(raw)
    figure = next(e for e in document.elements if e.kind == "figure")
    assert figure.anchor["bboxPpm"][1] >= 290_000
    assert next(e for e in document.elements if e.text.startswith("Table 1")).kind == "caption"
