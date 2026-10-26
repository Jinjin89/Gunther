"""Optional Docling process boundary. No cloud calls or implicit model downloads.

Layout parsing is offline. Only the structure reviser a caller passes in may call
a model, to check how the document is organized.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from gunther.content import ParsedBlock
from gunther.document_structure import (
    body_font_size,
    group_visuals,
    in_reference_section,
    organize_document,
    pdf_facts,
    pdf_metadata,
    recover_figure_regions,
)
from gunther.documents import DocumentAsset, ParsedDocument, split_reference_entries

logger = logging.getLogger(__name__)

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
    cpu_seconds = int(sys.argv[6])
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
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
options.document_timeout = float(sys.argv[5])
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
    try:
        from gunther.pdf_native import enrich_native_pdf
        enrich_native_pdf(data, sys.argv[1])
    except Exception as error:
        data['gunther_native_warning'] = type(error).__name__
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
    try:
        from gunther.pdf_native import render_visual_pages
        sharp = render_visual_pages(data, sys.argv[1], Path(sys.argv[3]).parent)
    except Exception as error:
        sharp = {}
        data['gunther_render_warning'] = type(error).__name__
    pages = []
    for number, page in result.document.pages.items():
        if number in sharp:
            pages.append({'page': number, 'file': sharp[number]})
        elif page.image is not None:
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
            "page_number": "page_number",
            "sidebar": "sidebar",
            "margin_note": "margin_note",
        }.get(label, "paragraph")
        text = str(item.get("text") or item.get("orig") or "")
        payload: dict[str, object] = {"documentRef": ref}
        if item.get("gunther_font_size"):
            payload["fontSize"] = item["gunther_font_size"]
        if native := item.get("gunther_native"):
            payload.update(sourceText=text, textSource="pdf-native-glyphs")
            text = native["text"]
            payload["inlineSpans"] = native["scripts"]
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
        if kind in {"figure", "table"}:
            caption_refs = [c["$ref"] for c in item.get("captions", []) if c.get("$ref")]
            payload["captionRefs"] = caption_refs
            if len(caption_refs) == 1:
                payload["captionRef"] = caption_refs[0]
            captions = [objects.get(c, {}).get("text", "") for c in caption_refs]
            payload["caption"] = "\n".join(c for c in captions if c)
        if kind == "figure":
            text = text or "Figure"
            payload["imageKey"] = ref
        if kind == "equation":
            # Formula recognition is optional; an empty transcription must never
            # discard a detected equation. Render its original region instead.
            payload.update(imageKey=ref, transcription="available" if text else "unavailable")
            if not text:
                payload["noEmbed"] = True
        new_parent = parent
        if text or kind == "equation":
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
            if kind in {"paragraph", "list_item"} and in_reference_section(headings):
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
    if recover_figure_regions(parsed):
        # Visual labels first classified as headings must not remain ancestors of
        # later prose. Structural parents (lists, visuals) stay as parsed.
        headings_stack.clear()
        demoted = set()
        for index, element in enumerate(parsed.elements):
            if element.kind == "heading":
                level = int(element.payload.get("level", 2))
                while headings_stack and headings_stack[-1][0] >= level:
                    headings_stack.pop()
                element.parent = headings_stack[-1][1] if headings_stack else None
                headings_stack.append((level, index, element.text))
            else:
                if element.payload.get("sourceKind") == "heading":
                    demoted.add(index)
                parent = element.parent
                if parent is None or parent in demoted or parsed.elements[parent].kind == "heading":
                    element.parent = headings_stack[-1][1] if headings_stack else None
            element.headings = [entry[2] for entry in headings_stack]
    organize_document(parsed)
    parsed.validate()
    return parsed


def structured_document(
    document: dict, metadata: dict, reviser=None, *, source_id: str | None = None
) -> ParsedDocument:
    """Evidence, the rule draft, a model's correction when one is set up, then assembly.

    See docs/DOCUMENT_STRUCTURE_PLAN.md. Imported here rather than at the top: the
    offline layout helper imports this module for its program and has no model client.
    """
    from gunther.document_evidence import build_evidence
    from gunther.document_model import StructureStatus, assemble, check
    from gunther.structure_draft import compute_clues, draft_plan
    from gunther.structure_model import StructureError, StructureTooLong

    evidence = build_evidence(document, metadata)
    compute_clues(evidence)
    draft = draft_plan(evidence)
    plan, status = draft, StructureStatus("needs-key")
    if reviser is not None and any(block.placeable for block in evidence.blocks):
        try:
            plan, status = reviser.revise(evidence, draft, source_id=source_id)
        except StructureTooLong:
            status = StructureStatus("too-long")
        except StructureError as error:
            status = StructureStatus("failed", error=str(error))
    if plan is draft:
        status.violations = [v for v in check(draft, evidence) if v.severity == "warning"]
    parsed = assemble(plan, evidence, status)
    parsed.manifest.update(
        layout="structured",
        conversionStatus=document.get("gunther_status", "unknown"),
        # Pages the layout read, including those whose every block was dropped, so
        # native text never brings back what the plan removed.
        layoutPages=sorted(
            {region["page"] for block in evidence.blocks for region in block.regions}
            | {block.page for block in evidence.blocks if block.page is not None}
        ),
    )
    return parsed


class DoclingError(RuntimeError):
    """A safe, actionable failure from the offline parser boundary."""


def attach_document_images(parsed: ParsedDocument, document: dict, directory: Path) -> None:
    """Render detected equations and complete visual regions from the page pixels.

    Each crop is checked against its page image: ink joined to the visual that runs
    past the detected box is taken in, and a crop that still cuts ink (other text
    touching it) is marked ``clipped`` (``figure_crops``).
    """
    import numpy as np
    from PIL import Image

    from gunther.figure_crops import complete_region

    pictures = {entry["ref"]: entry["file"] for entry in document.get("gunther_images", [])}
    pages = {entry["page"]: entry["file"] for entry in document.get("gunther_page_images", [])}

    def local_file(name):
        path = directory / name
        if path.parent != directory or path.is_symlink():
            raise ValueError("Invalid exported image path")
        return path

    def visual(element) -> bool:
        # A table printed as a picture has no cells; its image is the table.
        picture_table = element.kind == "table" and not element.payload.get("table")
        return element.kind in {"figure", "embedded_image", "equation"} or picture_table

    total = 0
    for element in parsed.elements:
        if not visual(element):
            continue
        key = element.payload.get("imageKey")
        number = element.anchor.get("page")
        page = pages.get(number)
        box = element.anchor.get("bboxPpm")
        image_file = pictures.get(key)
        if page and box:
            with Image.open(local_file(page)) as image:
                if image.width * image.height > 20_000_000:
                    raise ValueError("Oversized page image")
                x, y, width, height = box
                pad = 2_000 if element.kind == "equation" else 0
                crop = (
                    max(0, int((x - pad) / 1e6 * image.width)),
                    max(0, int((y - pad) / 1e6 * image.height)),
                    min(image.width, int((x + width + pad) / 1e6 * image.width + 1)),
                    min(image.height, int((y + height + pad) / 1e6 * image.height + 1)),
                )
                if crop[2] <= crop[0] or crop[3] <= crop[1]:
                    continue
                gray = np.asarray(image.convert("L"))
                others = [
                    _pixels(region, image.width, image.height)
                    for region in _other_text(parsed, element, number)
                ]
                crop, check = complete_region(gray, crop, others)
                element.payload["cropCheck"] = {**check, "source": "page-pixels"}
                if check["state"] == "extended":
                    left, top, right, bottom = crop
                    element.anchor["bboxPpm"] = [
                        round(left / image.width * 1e6), round(top / image.height * 1e6),
                        round((right - left) / image.width * 1e6),
                        round((bottom - top) / image.height * 1e6),
                    ]
                output = io.BytesIO()
                image.crop(crop).save(output, "PNG")
                data = output.getvalue()
        elif image_file:
            data = local_file(image_file).read_bytes()
            element.payload["cropCheck"] = {"state": "unchecked", "source": "layout-image"}
        else:
            continue
        box = element.anchor.get("bboxPpm")
        size = document.get("pages", {}).get(str(element.anchor.get("page")), {}).get("size", {})
        if box and size.get("width"):
            # Physical width lets the reader print a formula at the text's own size.
            element.payload["widthPt"] = round(box[2] / 1e6 * float(size["width"]), 1)
        total += len(data)
        if total > 128 * 1024 * 1024:
            raise ValueError("Document images exceed the processing budget")
        parsed.assets.append(DocumentAsset(key, data, name=f"region-{len(parsed.assets)}.png"))
    keys = {asset.key for asset in parsed.assets}
    missing = sum(
        e.kind in {"figure", "equation"} and e.payload.get("imageKey") not in keys
        for e in parsed.elements
    )
    clipped = sum(
        (e.payload.get("cropCheck") or {}).get("state") == "clipped" for e in parsed.elements
    )
    parsed.manifest.update(
        missingImages=missing, clippedImages=clipped,
        images="partial" if missing or clipped else "preserved",
    )
    if body := body_font_size(parsed):
        parsed.manifest["bodyFontSize"] = body


# Elements whose regions are pictures, not text a crop must stop at.
_PICTURE_KINDS = frozenset({"figure_text", "figure_panel"})


def _other_text(parsed: ParsedDocument, element, page) -> list[list[int]]:
    """Regions on ``page`` a visual's crop must not take in: every other element's, and
    the visual's own caption and notes."""
    regions = []
    for other in parsed.elements:
        if other.kind in _PICTURE_KINDS:
            continue
        own = other is element
        spans = other.anchor.get("spans") or (
            [{"page": other.anchor.get("page"), "bboxPpm": other.anchor.get("bboxPpm")}]
        )
        for span in spans:
            if span.get("page") != page or not span.get("bboxPpm"):
                continue
            if own and span.get("role") not in {"caption", "footnote"}:
                continue
            regions.append(span["bboxPpm"])
        if not own and other.kind in {"figure", "equation", "embedded_image"} and (
            other.anchor.get("page") == page and other.anchor.get("bboxPpm")
        ):
            regions.append(other.anchor["bboxPpm"])
    return regions


def _pixels(region: list[int], width: int, height: int) -> tuple[int, int, int, int]:
    left, top, w, h = region
    return (
        int(left / 1e6 * width), int(top / 1e6 * height),
        int((left + w) / 1e6 * width + 1), int((top + h) / 1e6 * height + 1),
    )


def docling_budget(path: Path) -> tuple[int, int]:
    """Bound elapsed time and aggregate CPU time for a four-thread layout worker."""
    from pypdf import PdfReader

    try:
        pages = len(PdfReader(path, strict=False).pages)
    except Exception:
        # Uncertain page counts must not starve an otherwise readable document.
        pages = 500
    wall_seconds = min(1800, 180 + 30 * max(1, pages))
    return wall_seconds, wall_seconds * 4


class DoclingParser:
    def __init__(self, python: Path, artifacts: Path, *, frozen: bool = False, device: str = "cpu"):
        self.python, self.artifacts = python, artifacts
        self.frozen, self.device = frozen, device
        self.version = "docling-adapter-v4"
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

    def parse(
        self,
        path: Path,
        *,
        stopping: Callable[[], bool] | None = None,
        reviser=None,
        source_id: str | None = None,
    ) -> ParsedDocument:
        """The stored document; ``reviser`` is the model that checks its structure, if any.

        Structure is decided here, not later, because figure crops come from the page
        images in the parser's temporary folder.
        """
        if not self.python.is_file() or not self.artifacts.is_dir():
            raise DoclingError("The offline layout runtime or model artifacts are unavailable")
        if path.stat().st_size > 64 * 1024 * 1024:
            raise DoclingError("PDF exceeds the 64 MiB layout parsing budget")
        with tempfile.TemporaryDirectory(prefix="gunther-docling-") as directory:
            output = Path(directory) / "document.json"
            environment = {
                **os.environ,
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
            }
            if not self.frozen:
                # The inline program imports Gunther's own structure helpers; the
                # frozen helper bundles them, a development runtime finds them here.
                backend = str(Path(__file__).resolve().parents[1])
                environment["PYTHONPATH"] = os.pathsep.join(
                    filter(None, [backend, os.environ.get("PYTHONPATH")])
                )
            wall_seconds, cpu_seconds = docling_budget(path)
            deadline = time.monotonic() + wall_seconds
            for device in dict.fromkeys((self.device, "cpu")):
                command = [str(self.python)] if self.frozen else [str(self.python), "-c", _PROGRAM]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Layout parsing exceeded its time budget")
                command += [
                    str(path), str(self.artifacts), str(output), device,
                    str(remaining), str(cpu_seconds),
                ]
                process = subprocess.Popen(
                    command,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    while process.poll() is None:
                        if stopping and stopping():
                            raise DoclingError("Layout parsing was interrupted")
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Layout parsing exceeded its time budget")
                        with suppress(subprocess.TimeoutExpired):
                            process.wait(timeout=0.25)
                    if process.returncode == 0:
                        break
                    if device == "cpu":
                        if process.returncode == -getattr(signal, "SIGXCPU", 24):
                            raise DoclingError("Layout parsing exceeded its CPU budget")
                        raise DoclingError(
                            "The offline layout parser exited unsuccessfully "
                            f"({process.returncode})"
                        )
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
            self.version = (str(document.get("gunther_parser", "docling")) + "/adapter-v4")[:120]
            try:
                facts = pdf_facts(path)
            except Exception:
                facts = {}
            # The file's own page count, whatever the layout parser read or kept.
            if facts.get("pageCount") and not document.get("gunther_page_count"):
                document["gunther_page_count"] = facts["pageCount"]
            if facts.get("bookmarks"):
                document["gunther_bookmarks"] = facts["bookmarks"]
            metadata_warning = None
            try:
                metadata = pdf_metadata(path)
            except Exception as error:
                metadata = {}
                metadata_warning = (
                    f"Some PDF metadata could not be read ({type(error).__name__})."
                )
            try:
                parsed = structured_document(
                    document, metadata, reviser, source_id=source_id
                )
            except Exception as error:
                # A fault in the structure step must not cost the layout read: keep the
                # older organized version and say so.
                logger.exception("Document structure step failed; using the older layout")
                parsed = docling_document(document)
                with suppress(Exception):
                    pdf_metadata(path, parsed)
                organize_document(parsed, metadata)
                parsed.manifest["structureWarning"] = (
                    f"The document's structure could not be organized ({type(error).__name__}); "
                    "showing the older layout version."
                )
            if metadata_warning:
                parsed.manifest["metadataWarning"] = metadata_warning
            attach_document_images(parsed, document, Path(directory))
            if parsed.manifest.get("schema") == 2:
                from gunther.document_model import extraction_report

                parsed.manifest["extraction"] = extraction_report(parsed)
            parsed.manifest["parser"] = self.version
            parsed.manifest.update(device=device, ocr="rapidocr-onnxruntime", offline=True)
            parsed.manifest.update(
                options={
                    "imageScale": 1.5,
                    "threads": 4,
                    "adapterSchema": 4,
                    "headingHierarchy": True,
                },
                modelBundle=self.bundle_metadata,
            )
            return parsed
