"""Evidence from Docling-like JSON, measured clues, and a rule draft that check() accepts."""

import random

from gunther.document_evidence import build_evidence
from gunther.document_model import Evidence, Plan, StructureStatus, assemble, check, expand
from gunther.structure_draft import compute_clues, draft_plan

LONG = (
    "Synthetic documents let us test the rules on layouts we control completely, from the "
    "running head to the last reference, without depending on any publisher's template. "
)


class Doc:
    """A Docling-like document built in reading order; a page is 1000 × 1000 points."""

    def __init__(self, pages: int = 1, width: float = 1000, height: float = 1000):
        self.raw: dict = {
            "body": {"self_ref": "#/body", "children": []},
            "furniture": {"self_ref": "#/furniture", "children": []},
            "texts": [],
            "pictures": [],
            "tables": [],
            "groups": [],
            "pages": {
                str(n): {"size": {"width": width, "height": height}} for n in range(1, pages + 1)
            },
        }

    def add(self, collection: str, item: dict, parent: dict | None = None) -> dict:
        ref = f"#/{collection}/{len(self.raw[collection])}"
        item = {"self_ref": ref, "children": [], **item}
        self.raw[collection].append(item)
        (parent or self.raw["body"])["children"].append({"$ref": ref})
        return item

    def text(self, label, content, size, box, page=1, parent=None, **extra) -> dict:
        x, y, width, height = box
        prov = [{"page_no": page, "bbox": {
            "l": x, "t": y, "r": x + width, "b": y + height, "coord_origin": "TOPLEFT",
        }}]
        item = {"label": label, "text": content, "orig": content, "prov": prov, **extra}
        if size is not None:
            item["gunther_font_size"] = size
        return self.add("texts", item, parent)

    def stitched(self, label, parts, size=10, native=True, parent=None) -> dict:
        """One item the parser read over several printed regions.

        Each part: text, box, page, and optionally its own type size, the parser's text
        for it (``source``) and whether a hyphen-broken word goes on (``joins``).
        """
        prov, pieces, start = [], [], 0
        for part in parts:
            x, y, width, height = part["box"]
            piece = part.get("source", part["text"])
            prov.append({"page_no": part["page"], "charspan": [start, start + len(piece)], "bbox": {
                "l": x, "t": y, "r": x + width, "b": y + height, "coord_origin": "TOPLEFT",
            }})
            pieces.append(piece)
            start += len(piece) + 1
        content = " ".join(pieces)
        item = {"label": label, "text": content, "orig": content, "prov": prov,
                "gunther_font_size": size}
        if native:
            item["gunther_native"] = {
                "text": " ".join(part["text"] for part in parts), "scripts": [], "fontSize": size,
                "regions": [
                    {"text": part["text"], "scripts": [], "joinsNext": part.get("joins", False),
                     "fontSize": part.get("size", size)}
                    for part in parts
                ],
            }
        return self.add("texts", item, parent)

    def picture(self, box, page=1, captions=()) -> dict:
        x, y, width, height = box
        prov = [{"page_no": page, "bbox": {
            "l": x, "t": y, "r": x + width, "b": y + height, "coord_origin": "TOPLEFT",
        }}]
        return self.add("pictures", {
            "label": "picture", "prov": prov,
            "captions": [{"$ref": c["self_ref"]} for c in captions],
        })

    def table(self, box, page=1, captions=(), cells=("a", "b")) -> dict:
        x, y, width, height = box
        prov = [{"page_no": page, "bbox": {
            "l": x, "t": y, "r": x + width, "b": y + height, "coord_origin": "TOPLEFT",
        }}]
        return self.add("tables", {
            "label": "table", "prov": prov,
            "captions": [{"$ref": c["self_ref"]} for c in captions],
            "data": {"table_cells": [{"text": cell} for cell in cells], "num_rows": 1},
        })

    def evidence(self, metadata: dict | None = None) -> Evidence:
        return build_evidence(self.raw, metadata)


def block_of(evidence: Evidence, item: dict):
    return next(b for b in evidence.blocks if b.extra["documentRef"] == item["self_ref"])


def regions_of(evidence: Evidence, item: dict) -> list:
    return [b for b in evidence.blocks if b.extra["documentRef"] == item["self_ref"]]


def ids(evidence: Evidence, *items: dict) -> list[str]:
    return [block_of(evidence, item).id for item in items]


def errors(plan: Plan, evidence: Evidence) -> list:
    return [v for v in check(plan, evidence) if v.severity == "error"]


def placed(plan: Plan, evidence: Evidence, items: list[str]) -> list[str]:
    return [i for i in expand(items, evidence) if not (b := evidence.get(i)) or b.placeable]


def drop_reason(plan: Plan, evidence: Evidence, block_id: str) -> str | None:
    return next(
        (d.reason for d in plan.drop if block_id in expand(d.blocks, evidence)), None
    )


def two_page_paper():
    doc = Doc(pages=2)
    head1 = doc.text("text", "Journal of Examples", 8, (100, 20, 300, 10))
    title = doc.text("section_header", "A study of synthetic documents", 20, (100, 80, 800, 30))
    authors = doc.text(
        "text", "Ada Lovelace1, Alan Turing2 & Grace Hopper1", 11, (100, 120, 800, 15)
    )
    affiliation = doc.text(
        "text", "1 Department of Computing, University of Somewhere, Exampletown", 8,
        (100, 140, 800, 10),
    )
    abstract = doc.text("text", LONG * 2, 10, (100, 170, 800, 100))
    intro = doc.text("section_header", "Introduction", 12, (100, 290, 400, 15))
    first = doc.text("text", LONG * 2, 10, (100, 310, 800, 150))
    picture = doc.picture((100, 480, 800, 300))
    panel = doc.text("text", "a", 9, (93, 482, 5, 8))
    caption = doc.text(
        "caption", "Figure 1. Results of the synthetic experiment.", 8, (100, 790, 800, 20)
    )
    broken = doc.text(
        "text", LONG + "and this sentence goes on after the page break where", 10,
        (100, 820, 800, 120),
    )
    number1 = doc.text("text", "1", 8, (490, 960, 20, 10))
    head2 = doc.text("text", "Journal of Examples", 8, (100, 20, 300, 10), page=2)
    rest = doc.text(
        "text", "it ends with a full stop, as every sentence should. " + LONG, 10,
        (100, 60, 800, 80), page=2,
    )
    competing = doc.text("section_header", "Competing interests", 12, (100, 160, 400, 15), page=2)
    declared = doc.text(
        "text", "The authors declare no competing interests.", 10, (100, 180, 800, 15), page=2
    )
    references = doc.text("section_header", "References", 12, (100, 230, 400, 15), page=2)
    entries = [
        doc.text(
            "list_item", f"{n}. Lovelace, A. Notes on engine number {n}. J. Ex. {n}, 1–10 (1843).",
            8, (100, 250 + 20 * n, 800, 15), page=2, marker=f"{n}.",
        )
        for n in range(1, 6)
    ]
    number2 = doc.text("page_footer", "2", 8, (490, 960, 20, 10), page=2)
    return doc, locals()


def test_evidence_keeps_reading_order_native_text_and_page_geometry():
    doc = Doc(height=500)
    doc.text(
        "paragraph", "Docling text", 10, (100, 50, 400, 50),
        gunther_native={"text": "Native glyph text", "scripts": [], "fontSize": 10},
    )
    picture = doc.picture((100, 200, 400, 100))
    doc.text("text", "inside", 7, (120, 210, 50, 10), parent=picture)
    caption = doc.text("caption", "Figure 1. A picture.", 8, (100, 310, 400, 10), parent=picture)
    picture["captions"] = [{"$ref": caption["self_ref"]}]
    bottom_left = doc.text("text", "Low", 9, (0, 0, 10, 10))
    bottom_left["prov"][0]["bbox"] = {
        "l": 0, "t": 500, "r": 100, "b": 450, "coord_origin": "BOTTOMLEFT",
    }
    doc.add("texts", {"label": "page_footer", "text": "7", "prov": []}, doc.raw["furniture"])

    evidence = doc.evidence({"title": [{"value": "T"}]})

    assert [b.id for b in evidence.blocks] == ["b0", "b1", "b2", "b3", "b4", "b5"]
    assert [b.label for b in evidence.blocks] == [
        "text", "picture", "text", "caption", "text", "page_footer",
    ]
    first, picture_block, inside, caption_block, low, _ = evidence.blocks
    assert first.text == "Native glyph text"
    assert first.extra["sourceText"] == "Docling text"
    assert first.box == (100_000, 100_000, 500_000, 200_000)
    assert first.regions == [{"page": 1, "bboxPpm": [100_000, 100_000, 400_000, 100_000]}]
    assert picture_block.image_key == "#/pictures/0"
    assert picture_block.extra["captionRefs"] == ["#/texts/2"]
    assert inside.inside == "b1" and not inside.placeable
    assert caption_block.inside is None  # a caption is linked to its picture, not printed in it
    assert low.box == (0, 0, 100_000, 100_000)
    assert evidence.metadata == {"title": [{"value": "T"}]}
    assert evidence.body_font_size is None  # no running prose of 200+ characters


def test_clues_name_the_measured_facts():
    doc, items = two_page_paper()
    evidence = doc.evidence()
    compute_clues(evidence)
    clues = {key: block_of(evidence, item).clues for key, item in items.items()
             if isinstance(item, dict) and "self_ref" in item}

    assert evidence.body_font_size == 10
    assert "repeats:top" in clues["head1"] and "repeats:top" in clues["head2"]
    assert "page-number" in clues["number1"]
    assert "small-type" in clues["affiliation"] and "large-type" in clues["title"]
    assert "caption:figure:1" in clues["caption"]
    assert "stray-glyphs" in clues["panel"]
    assert "heading:back-matter" in clues["competing"]
    assert "heading:references" in clues["references"]
    assert all("reference-entry" in block_of(evidence, e).clues for e in items["entries"])
    assert f"continues:{block_of(evidence, items['broken']).id}" in clues["rest"]
    assert "first-page" in clues["title"] and "first-page" not in clues["rest"]
    assert not any(c.startswith("continues:") for c in clues["first"])
    compute_clues(evidence)  # recomputing replaces, never duplicates
    assert block_of(evidence, items["head1"]).clues.count("repeats:top") == 1


def test_paper_draft_places_every_block_once():
    doc, items = two_page_paper()
    evidence = doc.evidence()
    plan = draft_plan(evidence)
    block = {key: block_of(evidence, item).id for key, item in items.items()
             if isinstance(item, dict) and "self_ref" in item}

    assert errors(plan, evidence) == []
    assert plan.type == "paper" and plan.by == "rules"
    assert plan.title == [block["title"]]
    assert plan.authors == [block["authors"]]
    assert [(p.part, placed(plan, evidence, p.blocks)) for p in plan.front] == [
        ("abstract", [block["abstract"]])
    ]
    [section] = plan.body
    assert section.heading == block["intro"]
    assert placed(plan, evidence, section.blocks) == [
        block["first"], "F1", block["broken"], block["rest"],
    ]
    [figure] = plan.figures
    assert (figure.number, figure.images) == ("1", [block["picture"]])
    assert figure.caption == [block["caption"]]
    assert figure.labels == [block["panel"]]
    assert plan.joins == [[block["broken"], block["rest"]]]
    [references] = plan.back
    assert references.part == "references" and references.heading == block["references"]
    assert placed(plan, evidence, references.blocks) == ids(evidence, *items["entries"])
    for key in ("head1", "head2", "number1", "number2"):
        assert drop_reason(plan, evidence, block[key]) == "page furniture"
    assert drop_reason(plan, evidence, block["affiliation"]) == "affiliations"
    assert drop_reason(plan, evidence, block["competing"]) == "back matter: competing interests"
    assert drop_reason(plan, evidence, block["declared"]) == "back matter: competing interests"
    assert [v.code for v in check(plan, evidence)] == []


def test_unknown_type_keeps_uncaptioned_pictures_notes_and_numbered_levels():
    doc = Doc(pages=2)
    doc.text("title", "Field notes", 18, (100, 50, 600, 30))
    logo1 = doc.picture((850, 20, 60, 30))
    top = doc.text("section_header", "1 Overview", 12, (100, 100, 400, 15))
    body = doc.text("text", LONG * 2, 10, (100, 120, 800, 150))
    photo = doc.picture((100, 300, 800, 400))
    scope = doc.text("section_header", "1.1 Scope", 11, (100, 720, 400, 15))
    more = doc.text("text", LONG * 2 + "The end.", 10, (100, 740, 800, 120))
    note = doc.text("footnote", "* A note at the foot of the page.", 7, (100, 900, 800, 12))
    logo2 = doc.picture((850, 20, 60, 30), page=2)
    last = doc.text("text", LONG * 2, 10, (100, 100, 800, 150), page=2)
    evidence = doc.evidence()
    plan = draft_plan(evidence)

    assert errors(plan, evidence) == []
    assert plan.type == "unknown"
    assert [(s.heading, s.level) for s in plan.body] == [
        (block_of(evidence, top).id, 1), (block_of(evidence, scope).id, 2),
    ]
    assert placed(plan, evidence, plan.body[0].blocks) == [block_of(evidence, body).id, "F1"]
    [figure] = plan.figures
    assert figure.number is None and figure.images == ids(evidence, photo)
    [kept] = plan.notes
    assert (kept.marker, kept.blocks) == ("*", ids(evidence, note))
    for logo in (logo1, logo2):
        assert "repeats-image" in block_of(evidence, logo).clues
        assert drop_reason(plan, evidence, block_of(evidence, logo).id) == "decoration"
    assert block_of(evidence, last).id in placed(plan, evidence, plan.body[1].blocks)
    assert block_of(evidence, more).id in placed(plan, evidence, plan.body[1].blocks)


def test_caption_claims_a_full_page_plate_on_the_next_page():
    doc = Doc(pages=2)
    doc.text("text", LONG * 2, 10, (100, 100, 800, 600))
    caption = doc.text(
        "caption", "Figure 2. A plate printed on the following page.", 8, (100, 850, 800, 20)
    )
    plate = doc.picture((50, 50, 900, 900), page=2)
    evidence = doc.evidence()
    plan = draft_plan(evidence)

    assert errors(plan, evidence) == []
    [figure] = plan.figures
    assert (figure.number, figure.images) == ("2", ids(evidence, plate))
    assert figure.caption == ids(evidence, caption)


def test_caption_cut_by_the_page_end_goes_on_at_the_top_of_the_next_page():
    doc = Doc(pages=2)
    doc.text("text", LONG * 2, 10, (100, 60, 800, 300))
    picture = doc.picture((100, 400, 800, 400))
    caption = doc.text(
        "caption", "Figure 4. Cells at rest (a) and after stimulation (b), where the", 8,
        (100, 810, 800, 150),
    )
    doc.text("page_footer", "1", 8, (490, 970, 20, 10))
    rest = doc.text("text", "(arrowheads) mark dividing cells.", 8, (100, 60, 800, 20), page=2)
    body = doc.text("text", LONG * 2, 10, (100, 100, 800, 300), page=2)
    evidence = doc.evidence()
    plan = draft_plan(evidence)

    assert errors(plan, evidence) == []
    [figure] = plan.figures
    assert figure.images == ids(evidence, picture)
    assert figure.caption == ids(evidence, caption, rest)
    assert block_of(evidence, body).id in placed(plan, evidence, plan.body[0].blocks)


def test_panel_letters_at_page_edges_stay_with_their_figure():
    doc = Doc(pages=2)
    labels = []
    for page in (1, 2):
        labels.append(doc.text("text", "a", 9, (95, 30, 5, 8), page=page))
        doc.picture((100, 30, 800, 400), page=page)
        doc.text("caption", f"Figure {page}. Panels.", 8, (100, 440, 800, 15), page=page)
        doc.text("text", LONG * 2, 10, (100, 480, 800, 300), page=page)
        doc.text("page_footer", str(page), 8, (490, 970, 20, 10), page=page)
    evidence = doc.evidence()
    plan = draft_plan(evidence)

    assert errors(plan, evidence) == []
    assert [f.labels for f in plan.figures] == [[i] for i in ids(evidence, *labels)]
    clues = block_of(evidence, labels[0]).clues
    assert not any(c.startswith(("repeats:", "page-number")) for c in clues)


def test_figure_caption_may_show_its_figure_as_a_table_grid():
    doc = Doc()
    doc.text("text", LONG * 2, 10, (100, 100, 800, 200))
    caption = doc.text("caption", "Fig. 3 Comparison grid of methods.", 8, (100, 320, 800, 15))
    grid = doc.table((100, 340, 800, 300), captions=[caption])
    labelled = doc.text("caption", "Table 1: Measured values.", 8, (100, 660, 800, 15))
    values = doc.table((100, 680, 800, 200), captions=[labelled])
    evidence = doc.evidence()
    plan = draft_plan(evidence)

    assert errors(plan, evidence) == []
    [figure] = plan.figures
    assert (figure.number, figure.images) == ("3", ids(evidence, grid))
    [table] = plan.tables
    assert (table.number, table.table, table.caption) == (
        "1", ids(evidence, values), ids(evidence, labelled)
    )


def test_margin_column_and_dates_beside_the_body_are_dropped_in_a_paper():
    doc = Doc(pages=2)
    received = doc.text("text", "Received: 1 May 2020", 7, (40, 300, 200, 10))
    editor = doc.text("text", "Editor: Someone Else, Some Place", 7, (40, 320, 200, 20))
    doc.text("section_header", "Rules beside the column", 20, (300, 60, 600, 30))
    doc.text("text", "Abstract " + LONG * 2, 10, (300, 110, 600, 150))
    doc.text("text", LONG * 2, 10, (300, 280, 600, 200))
    doc.text("section_header", "References", 12, (300, 500, 400, 15))
    for n in range(1, 4):
        doc.text("list_item", f"[{n}] Turing, A. On computable numbers. P. Lond. (1936).", 8,
                 (300, 520 + 20 * n, 600, 15))
    evidence = doc.evidence()
    plan = draft_plan(evidence)

    assert errors(plan, evidence) == []
    assert plan.type == "paper"
    assert "margin-column" in block_of(evidence, editor).clues
    assert drop_reason(plan, evidence, block_of(evidence, editor).id) == "margin column"
    assert drop_reason(plan, evidence, block_of(evidence, received).id) in {
        "margin column", "dates",
    }
    assert [p.part for p in plan.front] == ["abstract"]


def test_one_reference_list_continues_past_end_matter_set_beside_it():
    doc = Doc(pages=2)
    doc.text("section_header", "A paper with end matter in two columns", 20, (100, 60, 800, 30))
    doc.text("text", "Abstract " + LONG * 2, 10, (100, 110, 800, 150))
    doc.text("text", LONG * 2, 10, (100, 280, 800, 200))
    heading = doc.text("section_header", "References", 12, (60, 500, 400, 15))
    left = [
        doc.text("list_item", f"Author{n}, B. A title. Journal {n}, 10–20 (2001).", 8,
                 (60, 520 + 30 * n, 400, 25))
        for n in range(3)
    ]
    funding = doc.text("section_header", "Funding", 12, (520, 500, 400, 15))
    paid = doc.text("text", "Paid for by a fund.", 10, (520, 520, 400, 15))
    right = [
        doc.text("list_item", f"Writer{n}, C. Another title. Journal {n}, 30–40 (2002).", 8,
                 (520, 560 + 30 * n, 400, 25))
        for n in range(3)
    ]
    evidence = doc.evidence()
    plan = draft_plan(evidence)

    assert errors(plan, evidence) == []
    [references] = [p for p in plan.back if p.part == "references"]
    assert references.heading == block_of(evidence, heading).id
    assert placed(plan, evidence, references.blocks) == ids(evidence, *left, *right)
    assert drop_reason(plan, evidence, block_of(evidence, funding).id) == "back matter: funding"
    assert drop_reason(plan, evidence, block_of(evidence, paid).id) == "back matter: funding"


def test_slides_get_one_section_per_page():
    doc = Doc(pages=2, width=1000, height=560)
    for page in (1, 2):
        doc.text("section_header", f"Slide {page} heading", 28, (60, 40, 800, 60), page=page)
        doc.text("text", LONG * 2, 18, (60, 140, 880, 300), page=page)
    evidence = doc.evidence()
    plan = draft_plan(evidence)

    assert errors(plan, evidence) == []
    assert plan.type == "slides"
    first, second = (b for b in evidence.blocks if b.label == "section_header")
    assert plan.title == [first.id]  # the opening slide's heading is the deck's title
    assert [(s.heading, len(placed(plan, evidence, s.blocks))) for s in plan.body] == [
        (None, 1), (second.id, 1),
    ]


def references_on(doc: Doc, page: int, top: int) -> None:
    doc.text("section_header", "References", 12, (100, top, 400, 15), page=page)
    for n in range(1, 6):
        doc.text("list_item", f"{n}. Lovelace, A. Notes {n}. J. Ex. {n}, 1–10 (1843).", 8,
                 (100, top + 20 * n, 800, 15), page=page, marker=f"{n}.")


def test_a_stitched_item_becomes_one_block_per_printed_region():
    doc = Doc(pages=2)
    doc.text("text", LONG, 10, (100, 100, 800, 100))
    item = doc.stitched("text", [
        {"text": LONG + "the word com", "source": LONG + "the word com-",
         "box": (100, 800, 800, 150), "page": 1, "joins": True},
        {"text": "Journal of Examples", "box": (100, 20, 300, 10), "page": 2, "size": 8},
        {"text": "putation goes on. " + LONG, "box": (100, 60, 800, 100), "page": 2},
    ])
    after = doc.text("text", LONG, 10, (100, 200, 800, 100), page=2)
    evidence = doc.evidence()
    compute_clues(evidence)

    first, head, rest = regions_of(evidence, item)
    assert [first.id, head.id, rest.id, block_of(evidence, after).id] == ["b1", "b2", "b3", "b4"]
    assert first.text.endswith("the word com") and first.extra["sourceText"].endswith("com-")
    assert (head.text, head.page, head.font_size) == ("Journal of Examples", 2, 8)
    assert rest.regions == [{"page": 2, "bboxPpm": [100_000, 60_000, 800_000, 100_000]}]
    assert first.extra["joinsNext"] is True and "joinsNext" not in head.extra
    assert (head.extra["sameItem"], rest.extra["sameItem"]) == (first.id, head.id)
    assert f"same-item:{first.id}" in head.clues and "sameItem" not in first.extra


def test_without_native_text_the_regions_are_cut_at_the_parser_spans():
    doc = Doc(pages=2)
    item = doc.stitched("text", [
        {"text": LONG + "a hyphen-", "box": (100, 800, 800, 150), "page": 1},
        {"text": "ated word.", "box": (100, 60, 800, 20), "page": 2},
    ], native=False)
    evidence = doc.evidence()

    first, second = regions_of(evidence, item)
    assert (first.text, second.text) == (LONG + "a hyphen-", "ated word.")
    assert first.extra["joinsNext"] is True and "textSource" not in first.extra
    assert (first.page, second.page) == (1, 2)


def test_regions_that_cannot_be_told_apart_keep_the_item_whole():
    doc = Doc(pages=2)
    parts = [
        {"text": LONG, "box": (100, 800, 800, 150), "page": 1},
        {"text": "goes on.", "box": (100, 60, 800, 20), "page": 2},
    ]
    overlapping = doc.stitched("text", parts, native=False)
    overlapping["prov"][1]["charspan"] = [10, len(overlapping["text"])]
    empty = doc.stitched("text", parts)
    empty["gunther_native"]["regions"][1]["text"] = ""
    heading = doc.stitched("section_header", parts)
    evidence = doc.evidence()

    for item in (overlapping, empty, heading):
        [whole] = regions_of(evidence, item)
        assert len(whole.regions) == 2
    assert block_of(evidence, empty).text == empty["gunther_native"]["text"]


def test_a_paragraph_split_by_the_page_break_is_joined_around_its_running_head():
    doc = Doc(pages=2)
    doc.text("text", "Journal of Examples", 8, (100, 20, 300, 10))
    doc.text("section_header", "A paper the parser stitched", 20, (100, 60, 800, 30))
    doc.text("text", "Abstract " + LONG * 2, 10, (100, 110, 800, 150))
    doc.text("section_header", "Introduction", 12, (100, 280, 400, 15))
    item = doc.stitched("text", [
        {"text": LONG * 2 + "where the com", "box": (100, 300, 800, 600), "page": 1,
         "joins": True},
        {"text": "Journal of Examples", "box": (100, 20, 300, 10), "page": 2, "size": 8},
        {"text": "putation ends. " + LONG, "box": (100, 60, 800, 100), "page": 2},
    ])
    references_on(doc, 2, 200)
    evidence = doc.evidence()
    plan = draft_plan(evidence)
    first, head, rest = regions_of(evidence, item)

    assert errors(plan, evidence) == []
    assert "repeats:top" in head.clues
    assert drop_reason(plan, evidence, head.id) == "page furniture"
    assert [first.id, rest.id] in plan.joins
    [section] = plan.body
    assert placed(plan, evidence, section.blocks) == [first.id, rest.id]
    parsed = assemble(plan, evidence, StructureStatus("needs-key"))
    [paragraph] = [e for e in parsed.elements if first.id in e.payload.get("blockIds", [])]
    assert paragraph.payload["blockIds"] == [first.id, rest.id]
    assert "computation ends." in paragraph.text and "Journal" not in paragraph.text


def test_an_availability_statement_inside_a_paragraph_goes_to_the_back():
    doc = Doc(pages=2)
    doc.text("section_header", "Loans and savings", 20, (300, 60, 600, 30))
    doc.text("text", "Abstract " + LONG * 2, 10, (300, 110, 600, 200))
    doc.text("section_header", "Introduction", 12, (300, 400, 400, 15))
    item = doc.stitched("text", [
        {"text": LONG * 2 + "such as small loans,", "box": (300, 600, 600, 300), "page": 1},
        {"text": "Data Availability Statement: All data are in a public repository.",
         "box": (40, 60, 200, 40), "page": 2, "size": 7},
    ])
    rest = doc.text("text", "insurance and savings. " + LONG, 10, (300, 60, 600, 200), page=2)
    references_on(doc, 2, 300)
    evidence = doc.evidence()
    plan = draft_plan(evidence)
    first, statement = regions_of(evidence, item)

    assert errors(plan, evidence) == []
    [availability] = [p for p in plan.back if p.part == "availability"]
    assert placed(plan, evidence, availability.blocks) == [statement.id]
    assert [first.id, block_of(evidence, rest).id] in plan.joins


def test_author_grid_licence_notice_and_run_in_acknowledgements():
    doc = Doc()
    notice = doc.text(
        "text", "Provided proper attribution is given, the owner hereby grants permission to "
        "reproduce the tables and figures in this paper.", 12, (200, 30, 600, 30),
    )
    doc.text("section_header", "Engines that compute", 18, (300, 80, 400, 25))
    people = [("Ada Lovelace", "ada"), ("Charles Babbage", "cb"), ("Mary Somerville", "ms"),
              ("Augustus De Morgan", "adm"), ("Grace Hopper", "gh")]
    cells = [
        doc.text("text", f"{name}∗ Analytical Engines {mail}@engine.org", 10,
                 (100 + 200 * (n % 4), 130 + 50 * (n // 4), 180, 40))
        for n, (name, mail) in enumerate(people)
    ]
    doc.text("section_header", "Abstract", 12, (450, 250, 100, 15))
    doc.text("text", LONG * 2, 10, (150, 270, 700, 150))
    doc.text("section_header", "1 Introduction", 12, (100, 440, 400, 15))
    doc.text("text", LONG * 2, 10, (100, 460, 800, 150))
    thanks = doc.text(
        "text", "Acknowledgements We thank the reviewers for their comments.", 10,
        (100, 620, 800, 15),
    )
    references_on(doc, 1, 650)
    evidence = doc.evidence()
    plan = draft_plan(evidence)

    assert errors(plan, evidence) == []
    assert plan.authors == ids(evidence, *cells)
    assert drop_reason(plan, evidence, block_of(evidence, notice).id) == "licence"
    assert drop_reason(plan, evidence, block_of(evidence, thanks).id) == "back matter"


def test_lines_on_a_figure_page_label_it_and_floats_keep_page_order():
    doc = Doc(pages=3)
    doc.text("section_header", "Pictures after the references", 20, (100, 60, 800, 30))
    doc.text("text", "Abstract " + LONG * 2, 10, (100, 110, 800, 150))
    doc.text("text", LONG * 2, 10, (100, 280, 800, 200))
    references_on(doc, 1, 500)
    # The parser read page 3 before page 2.
    title = doc.text("section_header", "Input-Input Layer5", 16, (150, 100, 300, 25), page=3)
    late = doc.picture((150, 200, 700, 500), page=3)
    doc.text("caption", "Figure 2: Attention in layer five.", 10, (150, 720, 700, 20), page=3)
    early = doc.picture((150, 100, 700, 500), page=2)
    doc.text("caption", "Figure 1: Attention over a sentence.", 10, (150, 620, 700, 20), page=2)
    evidence = doc.evidence()
    plan = draft_plan(evidence)

    assert errors(plan, evidence) == []
    assert [(f.id, f.number, f.images) for f in plan.figures] == [
        ("F1", "1", ids(evidence, early)), ("F2", "2", ids(evidence, late)),
    ]
    assert plan.figures[1].labels == ids(evidence, title)
    [appendix] = [p for p in plan.back if p.part == "appendix"]
    assert appendix.blocks == ["F1", "F2"]


def test_random_layouts_always_give_a_plan_without_errors():
    words = [
        "Figure 1. A caption.", "Fig. 2 | Panels.", "Table 1: Values.", "References",
        "Acknowledgements", "Abstract", "Abstract We report results " + LONG, "Funding",
        "1. Smith, J. A paper. J. Ex. 3, 1–2 (2010).", "a", "×", "Data availability",
        "2.1 Methods", "it continues here " + LONG, LONG, "* a note", "Appendix A",
        "Received: 1 May 2020", "x@y.org", "1 University of Somewhere, Town",
    ]
    labels = [
        "text", "text", "text", "section_header", "list_item", "caption", "footnote",
        "page_header", "page_footer", "formula", "title", "code",
    ]
    for seed in range(40):
        rng = random.Random(seed)
        pages = rng.randint(1, 3)
        doc = Doc(pages=pages)
        pictures: list[dict] = []
        for _ in range(rng.randint(1, 40)):
            page = rng.randint(1, pages)
            box = (
                rng.randint(0, 900), rng.randint(0, 950), rng.randint(5, 600), rng.randint(5, 300)
            )
            roll = rng.random()
            if roll < 0.12:
                pictures.append(doc.picture(box, page=page))
            elif roll < 0.18:
                doc.table(box, page=page)
            elif roll < 0.28:
                parts = [
                    {"text": rng.choice(words), "page": rng.randint(1, pages),
                     "box": (rng.randint(0, 900), rng.randint(0, 950), rng.randint(5, 600),
                             rng.randint(5, 300)),
                     "size": rng.choice([7, 10]), "joins": rng.random() < 0.3}
                    for _ in range(rng.randint(2, 3))
                ]
                item = doc.stitched(
                    rng.choice(labels), parts, size=rng.choice([None, 10]),
                    native=rng.random() < 0.6,
                )
                if rng.random() < 0.2:
                    item["prov"][-1]["charspan"] = [0, 3]
            else:
                parent = rng.choice(pictures) if pictures and rng.random() < 0.15 else None
                size = rng.choice([None, 7, 10, 14])
                item = doc.text(
                    rng.choice(labels), rng.choice(words), size, box, page=page, parent=parent
                )
                if pictures and item["label"] == "caption" and rng.random() < 0.5:
                    rng.choice(pictures)["captions"].append({"$ref": item["self_ref"]})
        evidence = doc.evidence()
        plan = draft_plan(evidence)
        assert errors(plan, evidence) == [], seed
