"""Replay a recorded layout read through the real parser boundary.

``layouts/*.json`` hold what Docling read from the fixtures in ``pdf_documents``
(see ``layouts/record.py``). The replay stands in for the layout subprocess only:
the native PDF checks, page renders, structure, assembly, crops and everything
after them run as in the app, on the fixture PDF the test builds.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

LAYOUTS = Path(__file__).parent / "layouts"
# Docling's page image scale in the parser program.
PAGE_SCALE = 1.5


def recorded(name: str) -> dict:
    return json.loads((LAYOUTS / f"{name}.json").read_text(encoding="utf-8"))


def replay(monkeypatch, layout: dict) -> None:
    """Make the layout subprocess return ``layout`` enriched from the PDF it is given."""
    import pypdfium2 as pdfium

    from gunther.pdf_native import enrich_native_pdf, render_visual_pages

    class Process:
        returncode = None

        def __init__(self, command, **kwargs):
            pdf, output = command[-6], Path(command[-4])
            data = copy.deepcopy(layout)
            enrich_native_pdf(data, pdf)
            sharp = render_visual_pages(data, pdf, output.parent)
            document = pdfium.PdfDocument(pdf)
            pages = []
            try:
                for number in range(1, len(document) + 1):
                    if number in sharp:
                        pages.append({"page": number, "file": sharp[number]})
                        continue
                    name = f"page-{number}.png"
                    page = document[number - 1]
                    page.render(scale=PAGE_SCALE).to_pil().save(output.parent / name)
                    page.close()
                    pages.append({"page": number, "file": name})
            finally:
                document.close()
            data.update(gunther_page_images=pages, gunther_images=[],
                        gunther_parser="docling:recorded")
            output.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.returncode = 0

    monkeypatch.setattr("gunther.document_parser.subprocess.Popen", Process)


def parser(tmp_path: Path):
    """A layout parser whose runtime is the replay."""
    from gunther.document_parser import DoclingParser

    runtime = tmp_path / "runtime" / "python"
    runtime.parent.mkdir(exist_ok=True)
    runtime.touch()
    return DoclingParser(runtime, runtime.parent)
