"""Optional Docling process boundary. No cloud calls or implicit model downloads."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from gunther.content import ParsedBlock

# Run in an explicitly configured Python environment. The desktop sidecar stays
# small and does not import PyTorch, download models, or execute document code.
_PROGRAM = """
import json, sys, resource
from importlib.metadata import version
resource.setrlimit(resource.RLIMIT_FSIZE, (33554432, 33554432))
resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
options = PdfPipelineOptions(artifacts_path=sys.argv[2])
options.do_ocr = False
converter = DocumentConverter(format_options={
    InputFormat.PDF: PdfFormatOption(pipeline_options=options)
})
result = converter.convert(sys.argv[1], max_num_pages=500, max_file_size=67108864)
with open(sys.argv[3], 'w', encoding='utf-8') as output:
    data = result.document.export_to_dict()
    data['gunther_parser'] = 'docling:' + version('docling')
    json.dump(data, output, ensure_ascii=False)
"""


def docling_blocks(document: dict) -> list[ParsedBlock]:
    """Walk reading order, preserving hierarchy, table cells and original geometry."""
    objects = {}
    for key in ("texts", "tables", "pictures", "groups"):
        for index, item in enumerate(document.get(key, [])):
            objects[f"#/{key}/{index}"] = item
    blocks: list[ParsedBlock] = []
    visited: set[str] = set()
    characters = 0

    def walk(ref: str, parent: int | None, headings: list[str], depth: int):
        nonlocal characters
        if ref in visited or depth > 64 or len(blocks) > 10_000:
            raise ValueError("Invalid or oversized document hierarchy")
        visited.add(ref)
        item = objects.get(ref)
        if not item:
            raise ValueError("Document contains an unresolved content reference")
        label = item.get("label", "paragraph")
        kind = {
            "section_header": "heading",
            "title": "heading",
            "picture": "figure",
            "table": "table",
            "formula": "equation",
        }.get(label, "paragraph")
        text = str(item.get("text", ""))
        payload: dict[str, object] = {}
        if kind == "table":
            table = item.get("data", {})
            payload["table"] = table
            text = "\n".join(str(cell.get("text", "")) for cell in table.get("table_cells", []))
        if kind == "figure":
            text = text or "Figure"
        new_parent = parent
        if text:
            prov = item.get("prov", [])
            anchor: dict[str, object] = {"documentRef": ref}
            if prov:
                anchor["page"] = prov[0].get("page_no")
                anchor["bbox"] = prov[0].get("bbox")
                anchor["provenance"] = prov
            headings = [*headings, text] if kind == "heading" else headings
            locator = f"Page {anchor['page']}" if anchor.get("page") else " / ".join(headings)
            characters += len(text)
            if characters > 950_000:
                raise ValueError("Document exceeds the structured text budget")
            # Retain every character while bounding retrieval prompt size. Table
            # cells stay on the first block; subsequent slices point to the same
            # document object, rather than duplicating a potentially large payload.
            new_parent = len(blocks)
            if kind == "heading" and len(text) > 1200:
                raise ValueError("Oversized document heading")
            for start in range(0, len(text), 1200):
                end = min(start + 1200, len(text))
                piece_anchor = {**anchor, "documentCharStart": start, "documentCharEnd": end}
                blocks.append(
                    ParsedBlock(
                        text[start:end],
                        kind,
                        parent,
                        headings,
                        piece_anchor,
                        locator,
                        payload if start == 0 else {"documentRef": ref},
                    )
                )
        for child in item.get("children", []):
            walk(child["$ref"], new_parent, headings, depth + 1)

    for child in document.get("body", {}).get("children", []):
        walk(child["$ref"], None, [], 0)
    return blocks


class DoclingParser:
    def __init__(self, python: Path, artifacts: Path):
        self.python, self.artifacts = python, artifacts
        self.version = "docling-adapter-v1"

    def parse(self, path: Path) -> list[ParsedBlock]:
        if not self.python.is_file() or not self.artifacts.is_dir():
            raise ValueError("Docling runtime and offline model artifacts must be configured")
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("PDF exceeds the document parsing budget")
        with tempfile.TemporaryDirectory(prefix="gunther-docling-") as directory:
            output = Path(directory) / "document.json"
            environment = {
                **os.environ,
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
            }
            subprocess.run(
                [str(self.python), "-c", _PROGRAM, str(path), str(self.artifacts), str(output)],
                env=environment,
                check=True,
                timeout=240,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if output.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("Document representation exceeds the processing budget")
            document = json.loads(output.read_text(encoding="utf-8"))
            self.version = str(document.get("gunther_parser", "docling-adapter-v1"))[:120]
            return docling_blocks(document)
