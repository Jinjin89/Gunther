"""Optional Docling process boundary. No cloud calls or implicit model downloads."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from gunther.content import ParsedBlock
from gunther.document_structure import group_visuals, organize_document, pdf_metadata
from gunther.documents import DocumentAsset, ParsedDocument, split_reference_entries
from gunther.pdf_text import REFERENCE_SECTIONS, section_name

# Run in an explicitly configured Python environment. The desktop sidecar stays
# small and does not import PyTorch, download models, or execute document code.
_PROGRAM = """
import json, sys
from importlib.metadata import version, PackageNotFoundError
import socket
def offline(*args, **kwargs):
    raise OSError('The document parser is offline; include missing model artifacts in the bundle')
socket.socket.connect = offline
socket.socket.connect_ex = offline
socket.create_connection = offline
socket.getaddrinfo = offline
try:
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (33554432, 33554432))
    resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
except (ImportError, OSError, ValueError):
    pass
from pathlib import Path
from docling_core.types.doc import PictureItem
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    LayoutObjectDetectionOptions, PdfPipelineOptions, RapidOcrOptions,
    TableFormerMode, TableStructureOptions,
)
from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
options = PdfPipelineOptions(artifacts_path=sys.argv[2])
# Pin the existing model choices so packaging can omit unused alternatives.
options.layout_options = LayoutObjectDetectionOptions.from_preset('layout_heron_default')
options.table_structure_options = TableStructureOptions(mode=TableFormerMode.ACCURATE)
options.do_ocr = True
options.ocr_options = RapidOcrOptions(backend='onnxruntime')
options.accelerator_options = AcceleratorOptions(device=sys.argv[4], num_threads=4)
options.document_timeout = 180
options.enable_remote_services = False
options.allow_external_plugins = False
options.generate_page_images = True
options.generate_picture_images = True
options.generate_parsed_pages = True
options.heading_hierarchy_options.enabled = True
options.images_scale = 1.5
converter = DocumentConverter(format_options={
    InputFormat.PDF: PdfFormatOption(pipeline_options=options)
})
result = converter.convert(sys.argv[1], max_num_pages=500, max_file_size=67108864)
with open(sys.argv[3], 'w', encoding='utf-8') as output:
    data = result.document.export_to_dict()
    # A native text layer can contain a placeholder underneath a printed image.
    # Check only unresolved, explicitly labelled metadata against the page pixels.
    from gunther.document_structure import DATE_LABEL, DATE_VALUE
    import numpy
    notices = [item for item in list(data.get('texts', []))
               if DATE_LABEL.fullmatch(item.get('text', '').strip())
               and not DATE_VALUE.search(item.get('text', ''))]
    reader = next((getattr(getattr(p, 'ocr_model', None), 'reader', None)
                   for p in converter.initialized_pipelines.values()), None)
    for notice in notices[:16]:
        try:
            prov = notice['prov'][0]
            page = result.document.pages[prov['page_no']]
            image = page.image.pil_image
            box = prov['bbox']
            left, top, right, bottom = [box[k] for k in ('l', 't', 'r', 'b')]
            if box.get('coord_origin') == 'BOTTOMLEFT':
                top, bottom = page.size.height - top, page.size.height - bottom
            sx, sy = image.width / page.size.width, image.height / page.size.height
            pad = max(10, (right - left) / 2)
            region = (max(0, (left-pad)*sx), max(0, (top-3)*sy),
                      min(image.width, (right+pad)*sx), min(image.height, (bottom+3)*sy))
            crop = image.crop(region)
            crop = crop.resize((crop.width*2, crop.height*2))
            read = reader(numpy.array(crop)) if reader else None
            if read is None or read.txts is None: continue
            text = ' '.join(read.txts)
            if not DATE_LABEL.fullmatch(text.strip()) or not DATE_VALUE.search(text): continue
            confidence = min(float(score) for score in read.scores)
            if confidence < 0.75: continue
            ref = '#/texts/' + str(len(data['texts']))
            provenance = [{'page_no': prov['page_no'], 'bbox': {
                'l': region[0]/sx, 't': region[1]/sy,
                'r': region[2]/sx, 'b': region[3]/sy, 'coord_origin': 'TOPLEFT'}}]
            data['texts'].append({'self_ref': ref, 'label': 'text', 'text': text,
                                 'prov': provenance, 'gunther_ocr': {
                                     'confidence': confidence, 'nativeRef': notice['self_ref']}})
            data['body']['children'].append({'$ref': ref})
        except Exception:
            # Preserve the uncertain native declaration if this optional check fails.
            pass
    try:
        parser_version = version('docling-slim')
    except PackageNotFoundError:
        parser_version = version('docling')
    data['gunther_parser'] = 'docling:' + parser_version
    data['gunther_status'] = result.status.value
    images = []
    for element, _ in result.document.iterate_items():
        if isinstance(element, PictureItem):
            image = element.get_image(result.document)
            if image is not None:
                name = 'figure-' + str(len(images)) + '.png'
                image.save(Path(sys.argv[3]).parent / name, 'PNG')
                images.append({'ref': element.self_ref, 'file': name})
    data['gunther_images'] = images
    pages = []
    for number, page in result.document.pages.items():
        if page.image is not None:
            name = 'page-' + str(number) + '.png'
            page.image.pil_image.save(Path(sys.argv[3]).parent / name, 'PNG')
            pages.append({'page': number, 'file': name})
    data['gunther_page_images'] = pages
    # Images travel as bounded local files, not duplicated base64 in the JSON.
    def remove_images(value):
        if isinstance(value, dict):
            value.pop('image', None)
            for child in value.values(): remove_images(child)
        elif isinstance(value, list):
            for child in value: remove_images(child)
    remove_images(data)
    json.dump(data, output, ensure_ascii=False)
"""


def docling_blocks(document: dict) -> list[ParsedBlock]:
    """Compatibility adapter for callers that still request evidence chunks."""
    blocks = docling_document(document).chunks()
    for block in blocks:
        block.anchor["documentCharStart"] = block.anchor["elementCharStart"]
        block.anchor["documentCharEnd"] = block.anchor["elementCharEnd"]
    return blocks


def docling_document(document: dict) -> ParsedDocument:
    """Normalize the reading order without splitting canonical paragraphs."""
    objects = {}
    for key in ("texts", "tables", "pictures", "groups"):
        for index, item in enumerate(document.get(key, [])):
            objects[f"#/{key}/{index}"] = item
    blocks: list[ParsedBlock] = []
    visited: set[str] = set()
    characters = 0
    headings_stack: list[tuple[int, int, str]] = []

    def walk(
        ref: str,
        parent: int | None,
        headings: list[str],
        depth: int,
        visual_owner: str | None = None,
    ):
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
            "caption": "caption",
            "list_item": "list_item",
            "footnote": "footnote",
            "reference": "reference",
            "page_header": "page_header",
            "page_footer": "page_footer",
        }.get(label, "paragraph")
        text = str(item.get("text", ""))
        payload: dict[str, object] = {"documentRef": ref}
        if item.get("marker"):
            payload["marker"] = str(item["marker"])
            payload["enumerated"] = bool(item.get("enumerated"))
        if item.get("gunther_ocr"):
            payload.update(extraction="rapidocr-metadata-check", **item["gunther_ocr"])
        if visual_owner:
            payload["visualOwner"] = visual_owner
            if kind not in {"table", "figure", "caption"}:
                kind = "figure_text"
                payload["noEmbed"] = True
        if kind == "table":
            table = item.get("data", {})
            payload["table"] = table
            text = "\n".join(str(cell.get("text", "")) for cell in table.get("table_cells", []))
        if kind == "figure":
            text = text or "Figure"
            captions = [
                objects.get(c.get("$ref"), {}).get("text", "") for c in item.get("captions", [])
            ]
            payload["caption"] = "\n".join(c for c in captions if c)
            payload["imageKey"] = ref
        new_parent = parent
        if text:
            prov = item.get("prov", [])
            anchor: dict[str, object] = {"documentRef": ref}
            if prov:
                anchor["page"] = prov[0].get("page_no")
                anchor["bbox"] = prov[0].get("bbox")
                anchor["provenance"] = prov
            if kind == "heading":
                level = int(item.get("level") or (1 if label == "title" else 2))
                while headings_stack and headings_stack[-1][0] >= level:
                    headings_stack.pop()
                parent = headings_stack[-1][1] if headings_stack else parent
                headings_stack.append((level, len(blocks), text))
                payload["level"] = level
                payload["structureSource"] = "layout/bookmarks/typography"
                if label == "title":
                    payload["role"] = "title"
            else:
                parent = headings_stack[-1][1] if headings_stack else parent
            headings = [entry[2] for entry in headings_stack]
            if kind in {"paragraph", "list_item"} and any(
                section_name(h) in REFERENCE_SECTIONS for h in headings
            ):
                kind = "reference"
            if kind == "reference":
                payload.update(reference=True, noEmbed=True)
            spans = []
            for provenance in prov:
                page_no = provenance.get("page_no")
                page = document.get("pages", {}).get(str(page_no), {})
                size = page.get("size", {})
                bbox = provenance.get("bbox", {})
                width, height = size.get("width"), size.get("height")
                span = {"page": page_no, "raw": provenance}
                if width and height and all(k in bbox for k in ("l", "t", "r", "b")):
                    left, top, right, bottom = (float(bbox[k]) for k in ("l", "t", "r", "b"))
                    if str(bbox.get("coord_origin", "TOPLEFT")).upper() in {
                        "BOTTOMLEFT",
                        "BOTTOM_LEFT",
                    }:
                        top, bottom = height - top, height - bottom
                    top, bottom = min(top, bottom), max(top, bottom)
                    span["bboxPpm"] = [
                        round(left / width * 1_000_000),
                        round(top / height * 1_000_000),
                        round((right - left) / width * 1_000_000),
                        round((bottom - top) / height * 1_000_000),
                    ]
                spans.append(span)
            if spans:
                anchor["spans"] = spans
                if "bboxPpm" in spans[0]:
                    anchor["bboxPpm"] = spans[0]["bboxPpm"]
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
            blocks.append(ParsedBlock(text, kind, parent, headings, anchor, locator, payload))
        for child in item.get("children", []):
            walk(
                child["$ref"],
                new_parent,
                headings,
                depth + 1,
                ref if kind == "figure" else visual_owner,
            )

    for child in document.get("body", {}).get("children", []):
        walk(child["$ref"], None, [], 0)
    for child in document.get("furniture", {}).get("children", []):
        if child["$ref"] not in visited:
            walk(child["$ref"], None, [], 0)
    split_reference_entries(blocks)
    parsed = ParsedDocument(
        blocks,
        manifest={
            "schema": 1,
            "method": "docling",
            "images": "pending",
            "layout": "structured",
            "pageCount": len(document.get("pages", {})),
            "conversionStatus": document.get("gunther_status", "unknown"),
        },
    )
    group_visuals(parsed)
    organize_document(parsed)
    parsed.validate()
    return parsed


class DoclingParser:
    def __init__(self, python: Path, artifacts: Path, *, frozen: bool = False, device: str = "cpu"):
        self.python, self.artifacts = python, artifacts
        self.frozen, self.device = frozen, device
        self.version = "docling-adapter-v3"
        self.bundle_metadata: dict[str, object] = {}

    @classmethod
    def bundled(cls) -> DoclingParser | None:
        development = False
        root = (
            Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "document-parser"
        )
        if not root.is_dir() and not getattr(sys, "frozen", False):
            # The development app uses the same pinned bundle as the release builder.
            project = Path(__file__).resolve().parents[3]
            candidate = project / "tools" / "document-parser" / ".build" / "bundle"
            if (project / "tools" / "document-parser" / "uv.lock").is_file():
                root = candidate
                development = True
        executable = (
            root / "runtime" / ("gunther-docling.exe" if os.name == "nt" else "gunther-docling")
        )
        artifacts = root / "models"
        if not executable.is_file() or not artifacts.is_dir():
            return None
        parser = cls(executable, artifacts, frozen=True)
        if development:
            # Run current pipeline code in the pinned helper environment during
            # development, rather than using a stale compiled copy of _PROGRAM.
            python = (
                project
                / "tools"
                / "document-parser"
                / ".venv"
                / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )
            if python.is_file():
                parser = cls(python, artifacts)
        manifest = root / "manifest.json"
        if manifest.is_file() and manifest.stat().st_size <= 2 * 1024 * 1024:
            raw = manifest.read_bytes()
            try:
                data = json.loads(raw)
                parser.bundle_metadata = {
                    "manifestHash": hashlib.sha256(raw).hexdigest(),
                    "lockHash": data.get("lockHash"),
                    "platform": data.get("platform"),
                    "architecture": data.get("architecture"),
                    "packages": {
                        name: value
                        for name, value in data.get("packages", {}).items()
                        if name.casefold()
                        in {"docling-slim", "docling-core", "rapidocr", "onnxruntime", "torch"}
                    },
                }
            except (ValueError, AttributeError):
                return None
        return parser

    def parse(self, path: Path, *, stopping: Callable[[], bool] | None = None) -> ParsedDocument:
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
            deadline = time.monotonic() + 240
            for device in dict.fromkeys((self.device, "cpu")):
                command = [str(self.python)] if self.frozen else [str(self.python), "-c", _PROGRAM]
                command += [str(path), str(self.artifacts), str(output), device]
                process = subprocess.Popen(
                    command,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    while process.poll() is None:
                        if (stopping and stopping()) or time.monotonic() >= deadline:
                            raise RuntimeError("Document parsing cancelled or timed out")
                        with suppress(subprocess.TimeoutExpired):
                            process.wait(timeout=0.25)
                    if process.returncode == 0:
                        break
                    if device == "cpu":
                        raise ValueError("The offline document parser failed")
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=1)
            if output.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("Document representation exceeds the processing budget")
            document = json.loads(output.read_text(encoding="utf-8"))
            self.version = (str(document.get("gunther_parser", "docling")) + "/adapter-v3")[:120]
            parsed = docling_document(document)
            try:
                metadata = pdf_metadata(path, parsed)
            except Exception as error:
                metadata = {}
                parsed.manifest["metadataWarning"] = (
                    f"Some PDF metadata could not be read ({type(error).__name__})."
                )
            organize_document(parsed, metadata)
            total = 0
            figures = {
                element.payload.get("imageKey"): element
                for element in parsed.elements
                if element.kind in {"figure", "embedded_image"}
            }
            page_images = {
                entry["page"]: entry["file"] for entry in document.get("gunther_page_images", [])
            }
            for entry in document.get("gunther_images", []):
                if entry["ref"] not in figures:
                    continue
                element = figures[entry["ref"]]
                image_path = Path(directory) / entry["file"]
                if image_path.parent != Path(directory) or image_path.is_symlink():
                    raise ValueError("Invalid exported image path")
                if element.anchor.get("cropSource"):
                    from PIL import Image

                    page_path = Path(directory) / page_images[element.anchor["page"]]
                    if page_path.parent != Path(directory) or page_path.is_symlink():
                        raise ValueError("Invalid exported page path")
                    with Image.open(page_path) as page_image:
                        if page_image.width * page_image.height > 20_000_000:
                            raise ValueError("Oversized page image")
                        x, y, width, height = element.anchor["bboxPpm"]
                        crop = (
                            max(0, x / 1e6 * page_image.width),
                            max(0, y / 1e6 * page_image.height),
                            min(page_image.width, (x + width) / 1e6 * page_image.width),
                            min(page_image.height, (y + height) / 1e6 * page_image.height),
                        )
                        page_image.crop(crop).save(image_path, "PNG")
                total += image_path.stat().st_size
                if total > 128 * 1024 * 1024:
                    raise ValueError("Figure images exceed the processing budget")
                parsed.assets.append(
                    DocumentAsset(entry["ref"], image_path.read_bytes(), name=image_path.name)
                )
            parsed.manifest["parser"] = self.version
            parsed.manifest.update(device=device, ocr="rapidocr-onnxruntime", offline=True)
            parsed.manifest.update(
                options={
                    "imageScale": 1.5,
                    "threads": 4,
                    "adapterSchema": 3,
                    "headingHierarchy": True,
                },
                modelBundle=self.bundle_metadata,
            )
            missing = sum(
                element.kind == "figure"
                and not any(asset.key == element.payload.get("imageKey") for asset in parsed.assets)
                for element in parsed.elements
            )
            parsed.manifest.update(
                missingImages=missing, images="partial" if missing else "preserved"
            )
            return parsed
