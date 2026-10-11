"""Bounded local HTML/DOCX/EPUB normalization; no scripts or external file reads."""

from __future__ import annotations

import posixpath
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from gunther.content import ParsedBlock
from gunther.documents import DocumentAsset, ParsedDocument, split_reference_entries
from gunther.pdf_text import REFERENCE_SECTIONS, section_name

IMAGE_LIMIT = 16 * 1024 * 1024
IMAGE_TOTAL = 32 * 1024 * 1024


def image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class HtmlDocument(HTMLParser):
    def __init__(self, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.elements: list[ParsedBlock] = []
        self.parts: list[str] = []
        self.kind = "paragraph"
        self.html_id = ""
        self.ignored: list[str] = []
        self.headings: list[tuple[int, int, str]] = []
        self.figure: int | None = None
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.sup_start: int | None = None
        self.sup_ranges: list[tuple[int, int]] = []

    def flush(self) -> None:
        raw = "".join(self.parts)
        text = re.sub(r"\s+", " ", raw).strip()
        self.parts = []
        if not text:
            return
        payload: dict[str, object] = {"htmlId": self.html_id} if self.html_id else {}
        markers = []
        for start, stop in self.sup_ranges:
            marker = raw[start:stop].strip()
            if not re.fullmatch(r"\d+(?:\s*[,;–—-]\s*\d+)*", marker):
                continue
            left = len(re.sub(r"\s+", " ", raw[:start]).lstrip())
            normalized = re.sub(r"\s+", " ", marker)
            if text[left : left + len(normalized)] == normalized:
                markers.append({"start": left, "end": left + len(normalized), "raw": normalized})
        self.sup_ranges, self.sup_start = [], None
        if markers:
            payload["citationMarkers"] = markers
        kind = self.kind
        if kind == "heading":
            level = getattr(self, "level", 2)
            while self.headings and self.headings[-1][0] >= level:
                self.headings.pop()
            payload["level"] = level
            self.headings.append((level, len(self.elements), text))
        elif any(section_name(h[2]) in REFERENCE_SECTIONS for h in self.headings):
            kind = "reference"
            payload.update(reference=True, noEmbed=True)
        parent = self.headings[-1][1] if self.headings else None
        if kind == "heading":
            parent = self.headings[-2][1] if len(self.headings) > 1 else None
        self.elements.append(
            ParsedBlock(text, kind, parent, [h[2] for h in self.headings], {}, "Article", payload)
        )
        if kind == "caption" and self.figure is not None:
            self.elements[self.figure].payload["caption"] = text
        self.html_id = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"script", "style", "noscript", "svg", "head", "nav", "footer", "form"}:
            self.ignored.append(tag)
        if self.ignored:
            return
        if tag == "table":
            self.flush()
            self.table = []
        elif tag == "sup" and self.table is None:
            self.sup_start = len("".join(self.parts))
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"td", "th"} and self.table is not None:
            self.cell = []
        elif tag == "img":
            self.flush()
            src = attributes.get("src") or attributes.get("data-src") or ""
            if src:
                self.figure = len(self.elements)
                self.elements.append(
                    ParsedBlock(
                        attributes.get("alt") or "Figure",
                        "figure",
                        self.headings[-1][1] if self.headings else None,
                        [h[2] for h in self.headings],
                        {},
                        "Article",
                        {
                            "imageUrl": urljoin(self.base_url, src),
                            "caption": attributes.get("alt") or "",
                        },
                    )
                )
        elif tag in {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "figcaption"}:
            self.flush()
            self.kind = (
                "heading"
                if re.fullmatch(r"h[1-6]", tag)
                else (
                    "list_item"
                    if tag == "li"
                    else "caption"
                    if tag == "figcaption"
                    else "paragraph"
                )
            )
            self.level = int(tag[1]) if self.kind == "heading" else 2
            self.html_id = attributes.get("id") or ""
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if tag == "sup" and self.sup_start is not None:
            self.sup_ranges.append((self.sup_start, len("".join(self.parts))))
            self.sup_start = None
        elif tag in {"td", "th"} and self.cell is not None:
            if self.row is not None:
                self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.table is not None:
                if len(self.row) > 40 or len(self.table) >= 200:
                    raise ValueError("HTML table exceeds the structured extraction budget")
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            rows = self.table
            cells = [
                {
                    "text": text,
                    "start_row_offset_idx": r,
                    "end_row_offset_idx": r + 1,
                    "start_col_offset_idx": c,
                    "end_col_offset_idx": c + 1,
                    "column_header": r == 0,
                }
                for r, row in enumerate(rows)
                for c, text in enumerate(row)
            ]
            self.elements.append(
                ParsedBlock(
                    "\n".join(" | ".join(row) for row in rows),
                    "table",
                    self.headings[-1][1] if self.headings else None,
                    [h[2] for h in self.headings],
                    {},
                    "Article",
                    {
                        "table": {
                            "num_rows": len(rows),
                            "num_cols": max(map(len, rows), default=0),
                            "table_cells": cells,
                        }
                    },
                )
            )
            self.table = None
        elif tag in {
            "p",
            "li",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "figcaption",
            "div",
            "section",
            "article",
        }:
            self.flush()
            self.kind = "paragraph"

    def handle_data(self, data: str) -> None:
        if self.ignored:
            return
        if self.cell is not None:
            self.cell.append(data)
        elif self.table is None:
            self.parts.append(data)


def html_document(html: str, base_url: str = "") -> ParsedDocument:
    parser = HtmlDocument(base_url)
    parser.feed(html[: 8 * 1024 * 1024])
    parser.flush()
    split_reference_entries(parser.elements)
    document = ParsedDocument(
        parser.elements, manifest={"schema": 1, "method": "local-html", "images": "pending"}
    )
    document.validate()
    return document


def epub_order(archive: ZipFile) -> list[str]:
    from gunther.asset_service import _read_archive_member

    container = ET.fromstring(_read_archive_member(archive, "META-INF/container.xml", IMAGE_TOTAL))
    root = next(node for node in container.iter() if node.tag.endswith("}rootfile"))
    package_path = root.attrib["full-path"]
    package = ET.fromstring(_read_archive_member(archive, package_path, IMAGE_TOTAL))
    manifest = {
        node.attrib.get("id"): node.attrib.get("href", "")
        for node in package.iter()
        if node.tag.endswith("}item")
    }
    names = [
        posixpath.normpath(
            posixpath.join(posixpath.dirname(package_path), manifest[node.attrib["idref"]])
        )
        for node in package.iter()
        if node.tag.endswith("}itemref") and node.attrib.get("idref") in manifest
    ]
    if not names or any(name.startswith(("../", "/")) for name in names):
        raise ValueError("Invalid EPUB reading order")
    return list(dict.fromkeys(names))


def archive_document(path: Path, suffix: str) -> ParsedDocument:
    from gunther.asset_service import _preflight_archive, _read_archive_member

    _preflight_archive(path)
    document = ParsedDocument(
        [], manifest={"schema": 1, "method": "local" + suffix, "images": "preserved"}
    )
    used = 0
    with ZipFile(path) as archive:

        def member(name: str) -> bytes:
            nonlocal used
            data = _read_archive_member(archive, name, IMAGE_TOTAL - used)
            used += len(data)
            return data

        def image(name: str) -> str | None:
            if name not in archive.namelist() or name.startswith(("../", "/")):
                return None
            if any(asset.key == name for asset in document.assets):
                return name
            data = member(name)
            media = image_type(data)
            if not media or len(data) > IMAGE_LIMIT:
                return None
            document.assets.append(DocumentAsset(name, data, media, Path(name).name))
            return name

        if suffix == ".epub":
            for name in epub_order(archive)[:512]:
                parsed = html_document(member(name).decode("utf-8", errors="replace"), name)
                offset = len(document.elements)
                for element in parsed.elements:
                    if element.parent is not None:
                        element.parent += offset
                    if element.kind == "figure":
                        key = image(posixpath.normpath(str(element.payload["imageUrl"])))
                        if key:
                            element.payload["imageKey"] = key
                    document.elements.append(element)
        else:
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
            relations = {}
            if "word/_rels/document.xml.rels" in archive.namelist():
                relations = {
                    node.attrib["Id"]: posixpath.normpath("word/" + node.attrib["Target"])
                    for node in ET.fromstring(member("word/_rels/document.xml.rels"))
                    if node.attrib.get("TargetMode") != "External"
                }
            root = ET.fromstring(member("word/document.xml"))
            headings: list[tuple[int, int, str]] = []
            body = root.find(f"{ns}body")
            if body is None:
                raise ValueError("DOCX has no document body")
            for node in body:
                if node.tag == f"{ns}p":
                    text = "".join(part.text or "" for part in node.iter(f"{ns}t"))
                    style = node.find(f".//{ns}pStyle")
                    name = style.attrib.get(f"{ns}val", "") if style is not None else ""
                    match = re.match(r"Heading([1-6])", name, re.I)
                    kind = "heading" if match else "paragraph"
                    if match:
                        level = int(match[1])
                        while headings and headings[-1][0] >= level:
                            headings.pop()
                    parent = headings[-1][1] if headings else None
                    if text:
                        if match:
                            headings.append((level, len(document.elements), text))
                        payload = {"level": level} if match else {}
                        if kind != "heading" and any(
                            section_name(h[2]) in REFERENCE_SECTIONS for h in headings
                        ):
                            kind, payload = "reference", {"reference": True, "noEmbed": True}
                        document.elements.append(
                            ParsedBlock(
                                text,
                                kind,
                                parent,
                                [h[2] for h in headings],
                                {},
                                "Document",
                                payload,
                            )
                        )
                    for part in node.iter():
                        target = relations.get(part.attrib.get(f"{rel_ns}embed", ""))
                        key = image(target) if target else None
                        if key:
                            document.elements.append(
                                ParsedBlock(
                                    "Figure",
                                    "figure",
                                    parent,
                                    [h[2] for h in headings],
                                    {},
                                    "Document",
                                    {"imageKey": key},
                                )
                            )
                elif node.tag == f"{ns}tbl":
                    rows = [
                        [
                            "".join(t.text or "" for t in cell.iter(f"{ns}t"))
                            for cell in row.findall(f"{ns}tc")
                        ]
                        for row in node.findall(f"{ns}tr")
                    ]
                    if len(rows) > 200 or any(len(row) > 40 for row in rows):
                        raise ValueError("DOCX table exceeds the structured extraction budget")
                    cells = [
                        {
                            "text": text,
                            "start_row_offset_idx": r,
                            "end_row_offset_idx": r + 1,
                            "start_col_offset_idx": c,
                            "end_col_offset_idx": c + 1,
                            "column_header": r == 0,
                        }
                        for r, row in enumerate(rows)
                        for c, text in enumerate(row[:40])
                    ]
                    document.elements.append(
                        ParsedBlock(
                            "\n".join(" | ".join(row) for row in rows),
                            "table",
                            None,
                            [h[2] for h in headings],
                            {},
                            "Document",
                            {
                                "table": {
                                    "num_rows": len(rows),
                                    "num_cols": max(map(len, rows), default=0),
                                    "table_cells": cells,
                                }
                            },
                        )
                    )
    split_reference_entries(document.elements)
    document.validate()
    return document


def pdf_raster_images(path: Path, document: ParsedDocument) -> None:
    """Portable CPU fallback: original embedded rasters, without guessed geometry/captions."""
    from pypdf import PdfReader

    from gunther.asset_service import _limited_pdf_filters

    if path.stat().st_size > 64 * 1024 * 1024:
        return
    used = 0
    figures: list[ParsedBlock] = []
    with _limited_pdf_filters():
        reader = PdfReader(path, strict=False)
        for page_number, page in enumerate(reader.pages[:500], 1):
            resources = page.get("/Resources", {})
            resources = resources.get_object() if hasattr(resources, "get_object") else resources
            objects = resources.get("/XObject", {})
            objects = objects.get_object() if hasattr(objects, "get_object") else objects
            for name, reference in list(objects.items())[:100]:
                item = reference.get_object()
                if item.get("/Subtype") != "/Image":
                    continue
                if int(item.get("/Width", 0)) * int(item.get("/Height", 0)) > 20_000_000:
                    continue
                exported = page.images[name]
                data = exported.data
                media = image_type(data)
                if not media or len(data) > IMAGE_LIMIT or used + len(data) > IMAGE_TOTAL:
                    continue
                used += len(data)
                key = f"page-{page_number}:{name}"
                document.assets.append(DocumentAsset(key, data, media, exported.name))
                figures.append(
                    ParsedBlock(
                        f"Embedded image on page {page_number}",
                        "embedded_image",
                        None,
                        [],
                        {"page": page_number},
                        f"Page {page_number}",
                        {"imageKey": key, "captionStatus": "unresolved", "noEmbed": True},
                    )
                )
    document.elements.extend(figures)
    # Figures follow the text on their page; rebase any heading parents.
    ordered = sorted(
        enumerate(document.elements), key=lambda pair: (pair[1].anchor.get("page") or 0, pair[0])
    )
    positions = {old: new for new, (old, _) in enumerate(ordered)}
    for new, (_, element) in enumerate(ordered):
        parent = positions.get(element.parent) if element.parent is not None else None
        element.parent = parent if parent is not None and parent < new else None
    document.elements = [element for _, element in ordered]
    document.manifest.update(
        method="native-pdf",
        images="partial" if figures else "unavailable",
        layout="fallback",
        captionMatching="unresolved",
    )
