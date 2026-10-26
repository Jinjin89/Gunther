"""PDF fidelity from extraction to the reader's API, on documents designed here.

Each test builds a fixture PDF in code (``pdf_documents``), replays the layout
parser's recorded read of it (``pdf_replay``) and runs everything after the layout
model as the app does: the native glyph, form and formula checks, the structure,
assembly, crop checks, the extraction report, storage, retrieval passages, the
paper card and the document API. No real document is involved.
"""

from __future__ import annotations

import io
import re

import pytest
from sqlalchemy import select

pytest.importorskip("pypdfium2")

import numpy as np  # noqa: E402
from pdf_documents import RUNNING_HEAD, TITLE, form_pdf, paper_pdf  # noqa: E402
from pdf_fixtures import PdfBuilder, TrueType, find_font  # noqa: E402
from pdf_replay import parser, recorded, replay  # noqa: E402
from PIL import Image  # noqa: E402

from gunther.database import session_scope  # noqa: E402
from gunther.document_model import (  # noqa: E402
    Block,
    Evidence,
    Figure,
    Plan,
    Section,
    StructureStatus,
    assemble,
)
from gunther.figure_crops import complete_region  # noqa: E402
from gunther.models import ContentBlock, SourceRevision  # noqa: E402

# Characters no faithful text layer leaves in reading text: controls, private use.
FORBIDDEN = re.compile(r"[\x00-\x08\x0b-\x1f-�]")


def build(tmp_path, monkeypatch, name: str, edit=None):
    pdf = tmp_path / f"{name}.pdf"
    if (paper_pdf if name == "paper" else form_pdf)(pdf) is None:
        pytest.skip("This machine lacks the fixture fonts")
    layout = recorded(name)
    if edit:
        edit(layout)
    replay(monkeypatch, layout)
    return pdf


def parsed_paper(tmp_path, monkeypatch, edit=None):
    pdf = build(tmp_path, monkeypatch, "paper", edit)
    return parser(tmp_path).parse(pdf)


def of_kind(parsed, kind: str) -> list:
    return [element for element in parsed.elements if element.kind == kind]


def containing(parsed, text: str):
    return next(element for element in parsed.elements if text in element.text)


# F01: a misnamed font's values are read from their printed shapes -----------------


def test_values_set_in_a_misnamed_font_read_as_printed_and_are_marked(tmp_path, monkeypatch):
    parsed = parsed_paper(tmp_path, monkeypatch)
    sentence = containing(parsed, "mean change")
    assert "was −0.42 (n = 12) per day." in sentence.text
    marks = [s for s in sentence.payload["inlineSpans"] if s.get("mark") == "decoded"]
    # Each corrected glyph says what the text layer named it.
    assert {(sentence.text[m["start"]:m["end"]], m["from"]) for m in marks} == {
        ("−", "2"), ("(", "ð"), ("=", "¼"), (")", "Þ"),
    }
    every = "\n".join(element.text for element in parsed.elements)
    assert "20.42" not in every and not re.search("[ðÞþ¼]", every)
    assert not FORBIDDEN.search(every)
    # Search reads the same text as the reader.
    assert any("−0.42 (n = 12)" in chunk.text for chunk in parsed.chunks())
    warnings = {w["kind"]: w for w in parsed.manifest["extraction"]["warnings"]}
    assert warnings["glyphs-decoded"]["pages"] == [1, 2]
    assert parsed.manifest["extraction"]["state"] == "partial"


def test_glyphs_of_honest_fonts_are_never_doubted(tmp_path):
    """Every character a body font prints, named correctly, passes the glyph check."""
    from gunther.pdf_native import enrich_native_pdf

    fonts = [find_font(kind) for kind in ("sans", "serif", "serif-bold")]
    if not all(fonts):
        pytest.skip("This machine lacks the fixture fonts")
    sample = "0123456789 abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ ()[]+=<>/.,:;%−×±≤≥"
    builder = PdfBuilder()
    page = builder.page()
    for row, path in enumerate(fonts):
        font = builder.truetype(path)
        shown = "".join(c for c in sample if c == " " or ord(c) in TrueType(path).cmap)
        page.paragraph(40, 60 + row * 90, 520, shown, font, 11)
    pdf = tmp_path / "honest.pdf"
    builder.build(pdf)
    document = {"texts": [], "pages": {"1": {"size": {"width": 612, "height": 792}}}}
    enrich_native_pdf(document, str(pdf))
    check = document["gunther_glyphs"]
    assert check["checkedPairs"] > 150
    assert (check["fontsMisnaming"], check["decoded"], check["uncertain"]) == (0, [], [])


def test_bars_are_told_apart_by_their_length(tmp_path):
    """A font that names its hyphen, minus and em dash with letters: each reads as the
    bar its printed length makes it."""
    from gunther.pdf_native import enrich_native_pdf

    sans = find_font("sans")
    if sans is None:
        pytest.skip("This machine lacks the fixture fonts")
    builder = PdfBuilder()
    serif = builder.truetype(find_font("serif") or sans)
    wrong = builder.truetype(
        sans, claims={"-": "h", "−": "m", "—": "e", "(": "ð", ")": "Þ", "<": "\ue001"}
    )
    page = builder.page()
    x = 40.0
    for word in ("ab", "-", "cd", "−", "ef", "—", "gh", "(", "ij", ")", "kl", "<"):
        x += page.text(x, 80, word, serif if word.isalpha() else wrong, 12) + 4
    pdf = tmp_path / "bars.pdf"
    builder.build(pdf)
    document = {"texts": [], "pages": {"1": {"size": {"width": 612, "height": 792}}}}
    enrich_native_pdf(document, str(pdf))
    decoded = {pair["from"]: pair["to"] for pair in document["gunther_glyphs"]["decoded"]}
    # A private-use code, a stray glyph in a word, is read from its shape too.
    assert decoded == {"h": "-", "m": "−", "e": "—", "ð": "(", "Þ": ")", "\ue001": "<"}


# F02: equations -----------------------------------------------------------------


def test_equations_keep_no_text_that_differs_from_their_image(tmp_path, monkeypatch):
    parsed = parsed_paper(tmp_path, monkeypatch)
    keys = {asset.key for asset in parsed.assets}
    fraction, line = of_kind(parsed, "equation")
    # Printed in two dimensions: the picture is the formula, its glyphs only a text layer.
    assert fraction.text == "" and fraction.payload["transcription"] == "unreliable"
    assert fraction.payload["equationNumber"] == "(1)"
    assert fraction.payload["imageKey"] in keys
    # A one-line formula keeps its text, with the misnamed operators read as printed.
    assert line.text == "y = −2x + 1" and line.payload["transcription"] == "available"
    assert (line.payload["equationNumber"], line.payload["glyphCheck"]) == ("(2)", "decoded")
    assert line.payload["imageKey"] in keys
    image = next(asset for asset in parsed.assets if asset.key == fraction.payload["imageKey"])
    # The fraction's rule starts left of the layout box; the crop took it in.
    assert fraction.payload["cropCheck"]["state"] in {"complete", "extended"}
    assert Image.open(io.BytesIO(image.data)).width > 20


# F03: figure crops ---------------------------------------------------------------


def _picture_box_to_the_raster_only(layout: dict) -> None:
    """A layout parser that boxed only the embedded image, not the vector axes, ticks,
    labels and legend printed around it."""
    picture = layout["pictures"][0]
    box = picture["prov"][0]["bbox"]
    assert box["coord_origin"] == "BOTTOMLEFT"
    height = box["t"] - box["b"]
    # The fixture's raster is 300 × 160 pt at x = 130, under the box's top edge.
    box.update(l=130.0, r=430.0, t=box["t"] - 6, b=box["t"] - 6 - 160)
    assert height > 160


def test_a_crop_cut_inside_a_figure_is_completed_from_the_page(tmp_path, monkeypatch):
    parsed = parsed_paper(tmp_path, monkeypatch, _picture_box_to_the_raster_only)
    (figure,) = of_kind(parsed, "figure")
    check = figure.payload["cropCheck"]
    assert check["state"] == "extended" and check["source"] == "page-pixels"
    left, top, width, height = figure.anchor["bboxPpm"]
    page_width, page_height = 612, 792
    legend = next(t for t in recorded("paper")["texts"] if t["text"] == "Unit S2")
    hours = next(t for t in recorded("paper")["texts"] if t["text"] == "Hours")
    # The axis line at 124 pt, the legend right of the bars, "Hours" under the ticks.
    assert left / 1e6 * page_width <= 125
    assert (left + width) / 1e6 * page_width >= legend["prov"][0]["bbox"]["r"] - 0.5
    assert (top + height) / 1e6 * page_height >= 792 - hours["prov"][0]["bbox"]["b"] - 0.5
    asset = next(a for a in parsed.assets if a.key == figure.payload["imageKey"])
    image = Image.open(io.BytesIO(asset.data))
    assert image.height / (height / 1e6 * page_height) > 1  # rendered, not empty
    assert parsed.manifest["images"] == "preserved"
    # The caption under the figure stays out of the picture.
    assert (top + height) / 1e6 < next(
        span["bboxPpm"][1] for span in figure.anchor["spans"] if span.get("role") == "caption"
    ) / 1e6


def test_a_crop_that_text_cuts_is_reported_clipped():
    page = np.full((400, 400), 255, np.uint8)
    page[100:300, 100:300] = 0  # a figure
    page[150:160, 300:340] = 0  # its ink runs on into …
    text = (300, 140, 380, 170)  # … a paragraph's box beside it
    box, check = complete_region(page, (100, 100, 300, 300), [text])
    assert check == {"state": "clipped", "sides": ["right"]}
    assert box == (100, 100, 300, 300)
    page[150:160, 300:340] = 255
    page[300:330, 100:300] = 0  # a lower panel the box missed
    box, check = complete_region(page, (100, 100, 300, 300), [])
    assert check["state"] == "extended" and box[3] >= 330
    assert complete_region(page, box, [])[1] == {"state": "complete"}


# F05: headings and the bibliography ---------------------------------------------


def _running_head_read_into_the_heading(layout: dict) -> None:
    """A layout parser that read page 2's running head and the heading below it as one
    heading, as happens when the two are close."""
    texts = layout["texts"]
    head = next(i for i, t in enumerate(texts)
                if t["text"] == RUNNING_HEAD and t["prov"][0]["page_no"] == 2)
    heading = next(t for t in texts if t["text"] == "2 Methods")
    top, bottom = texts[head]["prov"][0]["bbox"], heading["prov"][0]["bbox"]
    heading["text"] = heading["orig"] = f"{RUNNING_HEAD} 2 Methods"
    bottom.update(l=min(top["l"], bottom["l"]), t=max(top["t"], bottom["t"]),
                  r=max(top["r"], bottom["r"]))
    heading["prov"][0]["charspan"] = [0, len(heading["text"])]
    ref = f"#/texts/{head}"
    layout["furniture"]["children"] = [
        c for c in layout["furniture"]["children"] if c.get("$ref") != ref
    ]
    texts[head]["prov"] = []
    texts[head]["text"] = texts[head]["orig"] = ""


def test_headings_follow_the_print_and_the_bibliography_has_one_heading(tmp_path, monkeypatch):
    parsed = parsed_paper(tmp_path, monkeypatch, _running_head_read_into_the_heading)
    headings = [e.text for e in of_kind(parsed, "heading")]
    assert headings == [TITLE, "Abstract", "1 Introduction", "2 Methods", "3 Results",
                        "References"]
    assert [entry["title"] for entry in parsed.manifest["outline"]] == headings[1:]
    references = of_kind(parsed, "reference")
    assert [r.text[:3] for r in references] == ["[1]", "[2]", "[3]", "[4]", "[5]"]
    omitted = {o["reason"]: o for o in parsed.manifest["omissions"]}
    repeated = omitted["repeated references heading"]
    assert (repeated["text"], repeated["page"]) == ("References", 4)
    running = [o for o in parsed.manifest["omissions"] if o["text"] == RUNNING_HEAD]
    # The running head cut out of page 2's heading is dropped as what it is, on its page.
    assert 2 in {o["page"] for o in running}
    assert all(o["reason"] == "page furniture" for o in running)


# F06, F07, F08, F04 through storage, the API and the paper card --------------------


def test_the_stored_document_reads_the_same_everywhere(tmp_path, monkeypatch):
    from test_structured_knowledge import base, client_for

    pdf = build(tmp_path, monkeypatch, "paper")
    with client_for(tmp_path) as client:
        response = client.post(
            "/api/captures/assets",
            params={"title": "Paper", "fileName": "paper.pdf", "kind": "paper",
                    "knowledgeBaseId": base(client), "deferProcessing": "true"},
            content=pdf.read_bytes(), headers={"content-type": "application/pdf"},
        )
        assert response.status_code == 201, response.text
        saved = response.json()["importResult"]["source"]["id"]
        worker = client.app.state.processing_worker
        worker.document_parser = parser(tmp_path)
        while worker.run_once():
            pass
        document = client.get(
            f"/api/sources/{saved}/document", params={"reading": "true", "limit": 500}
        ).json()
        card = client.get(f"/api/sources/{saved}/paper").json()
        with session_scope(worker.sessions) as session:
            passages = [
                b.content for b in session.scalars(
                    select(ContentBlock).where(ContentBlock.revision_id == document["revisionId"])
                )
            ]

    manifest = document["manifest"]
    assert manifest["schema"] == 2 and manifest["pageCount"] == 5
    elements = document["elements"]
    # F04: every numbered mention names its entry, and every entry is cited.
    labels = {r["id"]: r["label"] for r in document["references"]}
    assert sorted(labels.values()) == ["1", "2", "3", "4", "5"]
    cited = {
        e["content"][e_m["start"]:e_m["end"]]: [labels[t] for t in e_m["targets"]]
        for e in elements for e_m in e["mentions"]
    }
    assert cited["[1]"] == ["1"] and cited["[2, 3]"] == ["2", "3"] and cited["4"] == ["4"]
    assert {label for targets in cited.values() for label in targets} == set(labels.values())
    assert all(m["status"] == "resolved" for e in elements for m in e["mentions"])
    figure = next(e for e in elements if e["kind"] == "figure")
    # The exponent in "10³" is not a citation; the bracketed "[1]" in the caption is.
    assert [figure["content"][m["start"]:m["end"]] for m in figure["mentions"]] == ["[1]"]
    # F10: the caption's sub- and superscripts reach the reader.
    scripts = {(figure["content"][s["start"]:s["end"]], s["script"])
               for s in figure["payload"]["inlineSpans"] if "script" in s}
    assert scripts == {("2", "sub"), ("3", "super")}
    # F06: printed dates are the publication dates; file timestamps stay file facts.
    metadata = manifest["metadata"]
    printed = {name: [c["value"] for c in metadata[name]]
               for name in ("received", "accepted", "published")}
    assert printed == {"received": ["3 March 2024"], "accepted": ["9 May 2024"],
                       "published": ["2 June 2024"]}
    assert metadata["published"][0]["anchor"]["page"] == 1
    assert metadata["doi"][0]["value"] == "10.5555/sensors.2024.17"
    # Kept as the file states it, with where it came from; the reader formats it.
    assert metadata["fileCreated"] == [{"value": "D:20250115093000+02'00'",
                                        "origin": "pdf-info:/CreationDate",
                                        "status": "observed"}]
    assert metadata["fileProducer"][0]["value"] == "Example PDF Library 9.0"
    assert "publisher" not in metadata
    assert card["year"] == 2024
    # The printed byline names three authors; the file's metadata only two.
    assert card["authors"] == "Ana Kovač, Ben Ortiz, Chen Li"
    assert card["revisionId"] == document["revisionId"]
    # F08: the card's outline is the document's, whole.
    assert card["outline"] == ["1 Introduction", "2 Methods", "3 Results"]
    # Retrieval passages carry the same faithful text.
    assert any("−0.42 (n = 12)" in p for p in passages)
    assert not any(FORBIDDEN.search(p) or "20.42" in p for p in passages)
    # The reader gets the corrected text with each correction marked.
    sentence = next(e for e in elements if "mean change" in e["content"])
    assert "−0.42 (n = 12)" in sentence["content"]
    assert sum(s.get("mark") == "decoded" for s in sentence["payload"]["inlineSpans"]) == 4


# F09: forms ---------------------------------------------------------------------


def test_form_answers_keep_their_states_and_what_they_answer(tmp_path, monkeypatch):
    pdf = build(tmp_path, monkeypatch, "form")
    parsed = parser(tmp_path).parse(pdf)
    (table,) = of_kind(parsed, "table")
    cells = {
        (c["start_row_offset_idx"], c["start_col_offset_idx"]): c["text"]
        for c in table.payload["table"]["table_cells"]
    }
    grid = [[cells.get((row, column), "") for column in range(4)] for row in range(4)]
    assert grid == [
        ["Item", "Yes", "No", "N/A"],
        ["Exits marked", "☒", "☐", "☐"],
        ["Extinguisher present", "☐", "☒", "☐"],
        ["Signage visible", "☐", "☐", "☒"],
    ]
    # Search reads each answer on its item's line.
    assert "Extinguisher present | ☐ | ☒ | ☐" in table.text
    confirm = containing(parsed, "I confirm")
    follow = containing(parsed, "follow-up")
    assert confirm is not follow
    assert confirm.text.startswith("☒ ") and follow.text.startswith("☐ ")
    marks = [s for s in confirm.payload["inlineSpans"] if s.get("mark") == "checkbox"]
    assert marks == [{"mark": "checkbox", "kind": "checkbox", "source": "form-field",
                      "checked": True, "field": "confirm", "start": 0, "end": 1}]
    dingbats = containing(parsed, "Photos attached")
    assert "❏ Photos attached" in dingbats.text and "✔ Report signed" in dingbats.text
    assert parsed.manifest["accounting"]["unaccounted"] == 0
    assert parsed.manifest["pageCount"] == 1


# F11: every block is accounted for, with its pages ---------------------------------


def test_every_block_is_placed_or_dropped_with_its_reason(tmp_path, monkeypatch):
    parsed = parsed_paper(tmp_path, monkeypatch)
    accounting = parsed.manifest["accounting"]
    assert accounting["unaccounted"] == 0
    assert accounting["blocks"] == (
        accounting["placed"] + accounting["insideFigures"] + accounting["omitted"]
    )
    omissions = parsed.manifest["omissions"]
    assert len(omissions) == accounting["omitted"]
    for omission in omissions:
        assert omission["reason"] and omission["documentRef"].startswith("#/texts/")
        assert omission["page"] in range(1, 6) and len(omission["bboxPpm"]) == 4
    assert {o["reason"] for o in omissions} >= {"page furniture", "repeated references heading"}
    report = parsed.manifest["extraction"]
    assert report["omitted"] == dict(
        __import__("collections").Counter(o["reason"] for o in omissions)
    )


def test_a_caption_continued_on_the_next_page_keeps_both_pages():
    evidence = Evidence(
        [
            Block("b0", "picture", page=1, box=(100_000, 500_000, 900_000, 850_000),
                  regions=[{"page": 1, "bboxPpm": [100_000, 500_000, 800_000, 350_000]}]),
            Block("b1", "caption", "Figure 2. Drift of every unit over the", page=1,
                  regions=[{"page": 1, "bboxPpm": [100_000, 860_000, 800_000, 30_000]}],
                  extra={"documentRef": "#/texts/1"}),
            Block("b2", "text", "hundred days, one line per unit.", page=2,
                  regions=[{"page": 2, "bboxPpm": [100_000, 60_000, 800_000, 30_000]}],
                  extra={"documentRef": "#/texts/2"}),
        ],
        pages={1: {"width": 612, "height": 792}, 2: {"width": 612, "height": 792}},
    )
    plan = Plan(body=[Section(blocks=["F1"])],
                figures=[Figure(id="F1", number="2", images=["b0"], caption=["b1", "b2"])])
    (figure,) = assemble(plan, evidence, StructureStatus("checked")).elements
    assert figure.text == ("Figure 2. Drift of every unit over the hundred days, "
                           "one line per unit.")
    captions = [s for s in figure.anchor["spans"] if s.get("role") == "caption"]
    assert [s["page"] for s in captions] == [1, 2]
    for span in captions:
        part = figure.text[span["textStart"]:span["textEnd"]]
        assert part and part in figure.text
    assert figure.text[captions[1]["textStart"]:captions[1]["textEnd"]] == (
        "hundred days, one line per unit."
    )


def test_a_reprocessed_paper_keeps_its_old_revision_whole(tmp_path, monkeypatch):
    """Requirement 9: a new revision replaces what the reader shows; the old revision's
    citations still point at its own bibliography."""
    from test_structured_knowledge import base, client_for

    pdf = build(tmp_path, monkeypatch, "paper")
    with client_for(tmp_path) as client:
        saved = client.post(
            "/api/captures/assets",
            params={"title": "Paper", "fileName": "paper.pdf", "kind": "paper",
                    "knowledgeBaseId": base(client), "deferProcessing": "true"},
            content=pdf.read_bytes(), headers={"content-type": "application/pdf"},
        ).json()["importResult"]["source"]["id"]
        worker = client.app.state.processing_worker
        worker.document_parser = parser(tmp_path)
        while worker.run_once():
            pass
        first = client.get(f"/api/sources/{saved}/document", params={"limit": 500}).json()
        assert client.post(f"/api/sources/{saved}/reprocess").status_code == 202
        while worker.run_once():
            pass
        head = client.get(f"/api/sources/{saved}/document", params={"limit": 500}).json()
        old = client.get(
            f"/api/sources/{saved}/document",
            params={"limit": 500, "revision_id": first["revisionId"]},
        ).json()
        card = client.get(f"/api/sources/{saved}/paper").json()
        with session_scope(worker.sessions) as session:
            revisions = session.scalars(
                select(SourceRevision.id).where(SourceRevision.source_id == saved)
            ).all()

    assert len(revisions) >= 2
    assert card["revisionId"] == head["revisionId"]
    for document in (head, old):
        ids = {r["id"] for r in document["references"]}
        targets = {t for e in document["elements"] for m in e["mentions"] for t in m["targets"]}
        assert targets and targets <= ids
        assert [e["content"] for e in document["elements"]] == [
            e["content"] for e in first["elements"]
        ]


# F06, F08 on the card's own reading -------------------------------------------------


def test_the_card_lists_every_section_and_one_byline():
    import json

    from gunther.models import ContentBlock as Passage
    from gunther.papers import read_paper, reconcile_authors

    sections = [f"{n} Section {n}" for n in range(1, 56)]
    manifest = {
        "outline": [{"title": "Abstract"}, *({"title": t} for t in sections),
                    {"title": "References"}],
        "metadata": {
            "published": [{"value": "2 June 2024", "origin": "document"}],
            "fileCreated": [{"value": "D:20250115093000Z", "origin": "pdf-info:/CreationDate"}],
        },
    }
    blocks = [
        Passage(id="t", kind="heading", content="Measuring drift", anchor_json='{"page": 1}',
                payload_json=json.dumps({"role": "title", "part": "front"})),
        Passage(id="a", kind="metadata", content="Ana Kovač, Ben Ortiz and Chen Li",
                anchor_json='{"page": 1}', payload_json=json.dumps({"role": "authors"})),
    ]
    facts = read_paper("paper.pdf", blocks, manifest=manifest)
    # Every section, none cut at a fixed count; the abstract and bibliography are not sections.
    assert facts.outline == sections
    assert facts.year == 2024
    # A file's partial author list does not replace the printed byline …
    assert reconcile_authors("Ana Kovač, Ben Ortiz and Chen Li", "Kovač, A.; Ortiz, B.") == (
        "Ana Kovač, Ben Ortiz, Chen Li"
    )
    # … nor is one author listed twice for two spellings; a fuller declaration wins.
    assert reconcile_authors("Ana Kovač", "Kovač, A.; Ortiz, B.; Li, C.") == (
        "Kovač, A., Ortiz, B., Li, C."
    )
    without_printed_date = {"metadata": {"fileCreated": manifest["metadata"]["fileCreated"]}}
    assert read_paper("paper.pdf", blocks, manifest=without_printed_date).year is None


def test_a_listing_in_fixed_pitch_type_stays_whole_and_proportional_lines_are_text(tmp_path):
    """The layout parser can call stacked short lines "code". Fixed-pitch type is a listing
    and keeps its blank line; proportional lines set apart by a gap are separate texts."""
    from pathlib import Path

    from pdf_fixtures import layout

    from gunther.document_evidence import build_evidence
    from gunther.pdf_native import enrich_native_pdf

    mono = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
    serif_path = find_font("serif")
    if not mono.is_file() or serif_path is None:
        pytest.skip("This machine lacks the fixture fonts")
    builder = PdfBuilder()
    fixed, serif = builder.truetype(mono), builder.truetype(serif_path)
    page = builder.page()
    listing = ["def drift(error, hours):", "    return error / hours", "",
               "def mean(values):", "    return sum(values) / len(values)"]
    page.lines(72, 100, listing, fixed, 9, leading=12)
    page.place("code", (70, 90, 300, 152), "\n".join(line for line in listing if line))
    page.text(72, 200, "1 Institute of Example Studies, Lakeside", serif, 8)
    page.text(72, 210, "2 Department of Imaginary Physics, Hilltown", serif, 8)
    page.text(72, 234, "Received: 3 March 2024; Accepted: 9 May 2024", serif, 8)
    page.place("code", (70, 192, 300, 237), (
        "1 Institute of Example Studies, Lakeside 2 Department of Imaginary Physics, "
        "Hilltown Received: 3 March 2024; Accepted: 9 May 2024"
    ))
    pdf = tmp_path / "code.pdf"
    builder.build(pdf)
    document = layout(builder)
    enrich_native_pdf(document, str(pdf))
    assert [t["gunther_native"]["pitch"] for t in document["texts"]] == ["fixed", "proportional"]
    blocks = build_evidence(document, {}).blocks
    assert [(b.label, b.text[:24]) for b in blocks] == [
        ("code", "def drift(error, hours):"),
        ("text", "1 Institute of Example S"),
        ("text", "Received: 3 March 2024; "),
    ]
    assert blocks[1].extra["layoutLabel"] == "code"
    assert blocks[2].extra["splitFrom"] == "#/texts/1"
