"""The structure step inside the layout read: evidence, draft, model check, assembly."""

import json
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from gunther.document_model import StructureStatus
from gunther.document_parser import DoclingParser, structured_document
from gunther.structure_model import StructureError, StructureTooLong


def region(x, y, width, height, page=1):
    return [{"page_no": page, "bbox": {
        "l": x, "t": y, "r": x + width, "b": y + height, "coord_origin": "TOPLEFT",
    }}]


def text(label, content, size, box, page=1):
    return {"label": label, "text": content, "gunther_font_size": size,
            "prov": region(*box, page=page)}


BODY = "Cells respond to perturbations in ways that depend on their type and state. " * 6


def paper() -> dict:
    """Two pages: title, names, abstract, a section, a figure, references, running heads."""
    texts = [
        text("page_header", "Journal of Examples 12 (2026)", 7, (10, 2, 50, 2)),
        text("title", "Predicting single-cell responses", 16, (10, 8, 80, 5)),
        text("text", "Ada Lovelace and Grace Hopper", 10, (10, 15, 60, 3)),
        text("text", BODY, 10, (10, 20, 80, 20)),
        text("section_header", "Introduction", 12, (10, 42, 40, 3)),
        text("text", BODY, 10, (10, 46, 80, 20)),
        text("caption", "Figure 1 | Overview of the method.", 9, (10, 92, 80, 3)),
        text("page_header", "Journal of Examples 12 (2026)", 7, (10, 2, 50, 2), page=2),
        text("section_header", "References", 12, (10, 8, 40, 3), page=2),
        text("text", "1. Smith, J. Cell responses. Nature 1, 1-10 (2020).", 8,
             (10, 12, 80, 3), page=2),
        text("text", "2. Jones, K. Perturbation atlases. Cell 2, 11-20 (2021).", 8,
             (10, 16, 80, 3), page=2),
    ]
    order = [0, 1, 2, 3, 4, 5]
    return {
        "body": {"children": [
            *({"$ref": f"#/texts/{i}"} for i in order),
            {"$ref": "#/pictures/0"},
            *({"$ref": f"#/texts/{i}"} for i in (6, 8, 9, 10)),
        ]},
        "furniture": {"children": [{"$ref": "#/texts/7"}]},
        "texts": texts,
        "pictures": [{
            "label": "picture", "prov": region(10, 68, 80, 22),
            "captions": [{"$ref": "#/texts/6"}],
        }],
        "pages": {str(page): {"size": {"width": 100, "height": 100}} for page in (1, 2)},
        "gunther_status": "success",
    }


class Reviser:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    def revise(self, evidence, draft, *, source_id=None):
        self.calls.append((evidence, draft, source_id))
        if self.error:
            raise self.error
        plan = draft.model_copy(update={"by": "deepseek:deepseek-flash"})
        return plan, StructureStatus("checked", by=plan.by)


def texts_of(document):
    return [element.text for element in document.elements]


def test_without_a_model_the_rule_draft_is_used_and_says_it_needs_one():
    document = structured_document(paper(), {})
    manifest = document.manifest
    assert manifest["schema"] == 2
    assert manifest["structure"]["state"] == "needs-key"
    assert manifest["layout"] == "structured" and manifest["conversionStatus"] == "success"
    assert manifest["layoutPages"] == [1, 2]
    # The running head is gone, not hidden; nothing else is lost.
    assert not any("Journal of Examples" in value for value in texts_of(document))
    assert any(value.startswith("Cells respond") for value in texts_of(document))
    assert document.elements[0].payload.get("role") == "title"


def test_a_checked_plan_comes_from_the_model_with_the_source_named():
    reviser = Reviser()
    document = structured_document(paper(), {}, reviser, source_id="source-1")
    assert reviser.calls[0][2] == "source-1"
    assert document.manifest["structure"]["state"] == "checked"
    assert document.manifest["structure"]["by"] == "deepseek:deepseek-flash"


@pytest.mark.parametrize(
    "error,state",
    [(StructureError("The model's plan still left b3 unplaced."), "failed"),
     (StructureTooLong("Too long"), "too-long")],
)
def test_a_failed_or_impossible_check_keeps_the_draft_and_says_why(error, state):
    document = structured_document(paper(), {}, Reviser(error))
    structure = document.manifest["structure"]
    assert structure["state"] == state and structure["by"] == "rules"
    if state == "failed":
        assert structure["error"] == "The model's plan still left b3 unplaced."
    assert not any("Journal of Examples" in value for value in texts_of(document))


def test_an_empty_document_never_asks_the_model():
    reviser = Reviser()
    raw = {"body": {"children": []}, "pages": {}}
    document = structured_document(raw, {}, reviser)
    assert reviser.calls == [] and document.elements == []


def blank_pdf(path: Path) -> Path:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(path)
    return path


def fake_layout(monkeypatch, raw: dict, page_image: bool = False):
    class Process:
        returncode = None

        def __init__(self, command, **kwargs):
            output = Path(command[-4])
            if page_image:
                Image.new("RGB", (200, 200), "white").save(output.parent / "page-1.png")
                raw["gunther_page_images"] = [{"page": 1, "file": "page-1.png"}]
            output.write_text(json.dumps(raw))

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.returncode = 0

    monkeypatch.setattr("gunther.document_parser.subprocess.Popen", Process)


def test_the_layout_read_organizes_the_document_and_crops_its_figure(tmp_path, monkeypatch):
    fake_layout(monkeypatch, paper(), page_image=True)
    runtime = tmp_path / "python"
    runtime.touch()
    reviser = Reviser()
    parsed = DoclingParser(runtime, tmp_path).parse(
        blank_pdf(tmp_path / "input.pdf"), reviser=reviser, source_id="source-2"
    )
    assert reviser.calls[0][2] == "source-2"
    assert parsed.manifest["schema"] == 2 and parsed.manifest["structure"]["state"] == "checked"
    figures = [e for e in parsed.elements if e.kind == "figure"]
    assert len(figures) == 1 and figures[0].text.startswith("Figure 1")
    assert figures[0].payload["imageKey"] in {asset.key for asset in parsed.assets}


def test_a_fault_in_the_structure_step_keeps_the_older_layout_and_says_so(
    tmp_path, monkeypatch
):
    fake_layout(monkeypatch, paper())

    def broken(*args, **kwargs):
        raise KeyError("bug")

    monkeypatch.setattr("gunther.document_parser.structured_document", broken)
    runtime = tmp_path / "python"
    runtime.touch()
    parsed = DoclingParser(runtime, tmp_path).parse(blank_pdf(tmp_path / "input.pdf"))
    assert parsed.manifest["schema"] == 1
    assert "could not be organized (KeyError)" in parsed.manifest["structureWarning"]
    assert any(value.startswith("Cells respond") for value in texts_of(parsed))
