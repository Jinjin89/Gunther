"""Record the layout parser's read of the fidelity fixtures, for the replay tests.

The tests replay this read and run everything after it (the native PDF checks, the
structure, assembly, crops, persistence, retrieval) on the fixture PDF they build.
Only Docling's own output is kept: Gunther's additions are left out, so they are
always computed by the code under test. Run again after changing a fixture:

    tools/document-parser/.venv/bin/python apps/backend/tests/layouts/record.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path[:0] = [str(BACKEND), str(HERE.parent)]

from pdf_documents import form_pdf, paper_pdf  # noqa: E402

from gunther.document_parser import _PROGRAM, DoclingParser  # noqa: E402

# The parser program without Gunther's steps.
LAYOUT_ONLY = (
    _PROGRAM.replace("enrich_native_pdf(data, sys.argv[1])", "pass")
    .replace("sharp = render_visual_pages(data, sys.argv[1], Path(sys.argv[3]).parent)",
             "sharp = {}")
)


def main() -> None:
    parser = DoclingParser.bundled()
    if parser is None:
        raise SystemExit("No document parser bundle: run the parser build first")
    assert LAYOUT_ONLY.count("pass") > _PROGRAM.count("pass")
    for name, build in (("paper", paper_pdf), ("form", form_pdf)):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / f"{name}.pdf"
            if build(pdf) is None:
                raise SystemExit("This machine lacks the fixture fonts")
            output = Path(folder) / "document.json"
            subprocess.run(
                [str(parser.python), "-c", LAYOUT_ONLY, str(pdf), str(parser.artifacts),
                 str(output), "cpu", "600", "2400"],
                check=True,
                env={**os.environ, "PYTHONPATH": str(BACKEND), "HF_HUB_OFFLINE": "1"},
            )
            data = json.loads(output.read_text())
        for key in [key for key in data if key.startswith("gunther_") and key != "gunther_status"]:
            data.pop(key)
        data["origin"] = {"mimetype": "application/pdf", "filename": f"{name}.pdf"}
        (HERE / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=None, separators=(",", ":")) + "\n"
        )
        print(name, len(data.get("texts", [])), "texts")


if __name__ == "__main__":
    main()
