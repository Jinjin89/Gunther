"""Source-defined structure and metadata, independent of document genre.

Layout labels describe roles, not a required outline. Unheaded text stays
unheaded. Visual annotations and page furniture remain inspectable, without
being interleaved with the document's prose or embedded as body evidence.
"""

from __future__ import annotations

import html
import re
from collections import Counter, defaultdict
from contextlib import suppress
from pathlib import Path
from xml.etree import ElementTree as ET

from gunther.documents import ParsedDocument

NON_BODY = {
    "page_header",
    "page_footer",
    "metadata",
    "figure_text",
    "figure_panel",
    "embedded_image",
}
DATE_LABEL = re.compile(
    r"^(received|accepted|published(?: online)?|publication date|issued|updated|"
    r"出版日期|发布日期|接收日期|修订日期)\s*[:：]\s*(.+)$",
    re.I,
)
DATE_VALUE = re.compile(r"\b(?:19|20)\d{2}\b")
CAPTION_LABEL = re.compile(
    r"^(?:fig(?:ure)?\.?|table|图|表|abbildung|tabelle)\s*(?:\d+|[IVX]+)\b", re.I
)
PUBLICATION_NOTICE = re.compile(
    r"^(?:©|copyright\b|(?:publisher|editor)(?:['’]s)?\s+note\b|"
    r"open access\b|licen[cs]e\b|版权|许可声明|出版者说明)",
    re.I,
)


def normalized(value: str) -> str:
    return "".join(c for c in value.casefold() if c.isalnum())


def _clean(value: object) -> str:
    text = str(value or "")
    # Some PDF generators escape XML numeric entities a second time.
    text = re.sub(r"amp#(x[0-9a-f]+|\d+);", r"&#\1;", text, flags=re.I)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()[:2000]


def _field(metadata: dict, name: str, value: object, origin: str, anchor: dict | None = None):
    text = _clean(value)
    if not text:
        return
    evidence = {"value": text, "origin": origin, "status": "observed"}
    if anchor:
        evidence["anchor"] = anchor
    choices = metadata.setdefault(name, [])
    existing = next((c for c in choices if c["value"] == text and c["origin"] == origin), None)
    if existing is not None:
        return existing
    choices.append(evidence)
    return evidence


def pdf_metadata(path: Path, document: ParsedDocument | None = None) -> dict:
    """Read declarations only; a PDF producer is not its publisher."""
    from pypdf import PdfReader

    from gunther.asset_service import _limited_pdf_filters

    metadata: dict = {}
    with _limited_pdf_filters():
        reader = PdfReader(path, strict=False)
        info = reader.metadata or {}
        for key, name in {
            "/Title": "title",
            "/Author": "authors",
            "/Subject": "subject",
            "/Keywords": "keywords",
            "/Publisher": "publisher",
            "/CreationDate": "fileCreated",
            "/ModDate": "fileModified",
            "/Creator": "fileCreator",
            "/Producer": "fileProducer",
            "/doi": "doi",
        }.items():
            _field(metadata, name, info.get(key), "pdf-info:" + key)
        xmp = reader.xmp_metadata
        if xmp is not None and len(xmp.stream.get_data()) <= 2 * 1024 * 1024:
            tree = ET.fromstring(xmp.stream.get_data())
            # Standard namespaces prevent unrelated custom fields from overriding metadata.
            for item in tree.iter():
                namespace, _, local = item.tag.lstrip("{").partition("}")
                if namespace == "http://purl.org/dc/elements/1.1/":
                    name = {
                        "title": "title",
                        "creator": "authors",
                        "publisher": "publisher",
                        "source": "source",
                        "description": "subject",
                        "date": "issued",
                        "identifier": "identifier",
                        "rights": "rights",
                    }.get(local)
                elif namespace.startswith("http://prismstandard.org/namespaces/basic/"):
                    name = {
                        "doi": "doi",
                        "publicationName": "publication",
                        "publicationDate": "published",
                        "coverDate": "coverDate",
                        "url": "source",
                        "issn": "issn",
                        "isbn": "isbn",
                        "copyright": "rights",
                    }.get(local)
                else:
                    name = None
                if name:
                    values = [child.text for child in item.iter() if not len(child) and child.text]
                    _field(metadata, name, "; ".join(values), "xmp:" + local)
        if document is not None:
            _native_hierarchy(reader, document)
    return metadata


def _native_hierarchy(reader, document: ParsedDocument) -> None:
    """Partial bookmarks calibrate matching font styles on the remaining pages.

    This avoids ranking a later chapter below unrelated fonts in a supplementary
    document. No semantic section-name vocabulary participates in this step.
    """
    bookmarks: dict[tuple[int, str], int] = {}

    def visit_outline(nodes, level=1):
        for node in nodes:
            if isinstance(node, list):
                visit_outline(node, level + 1)
            else:
                page = reader.get_destination_page_number(node)
                if page is not None:
                    bookmarks[(page + 1, normalized(node.title))] = level

    with suppress(Exception):
        # Broken optional bookmarks cannot invalidate the document.
        visit_outline(reader.outline)
    page_headings = defaultdict(list)
    for element in document.elements:
        if element.kind == "heading" and isinstance(element.anchor.get("page"), int):
            page_headings[element.anchor["page"]].append(element)
    anchored_styles = defaultdict(Counter)
    styles = {}
    for number, headings in page_headings.items():
        runs = []
        length = 0

        def visit_text(text, cm, tm, font, size, runs=runs):
            nonlocal length
            key = normalized(text)
            if not key or not font or abs(tm[1]) > 0.01 or abs(tm[2]) > 0.01:
                return
            name = re.sub(r"^/?[A-Z]{6}\+", "", str(font.get("/BaseFont", "")))
            actual_size = round(abs(float(size) * tm[3] * cm[3]) * 2) / 2
            runs.append((length, length + len(key), key, (name, actual_size)))
            length += len(key)

        try:
            reader.pages[number - 1].extract_text(visitor_text=visit_text)
        except Exception:
            continue
        text = "".join(run[2] for run in runs)
        for element in headings:
            key = normalized(element.text)
            start = text.find(key)
            if start >= 0:
                stop = start + len(key)
                weights = Counter()
                for left, right, _, style in runs:
                    weights[style] += max(0, min(right, stop) - max(left, start))
                if weights and weights.most_common(1)[0][1] > 0:
                    styles[id(element)] = weights.most_common(1)[0][0]
            if (level := bookmarks.get((number, key))) is not None:
                element.payload.update(level=level, structureSource="pdf-bookmark")
                if id(element) in styles:
                    anchored_styles[styles[id(element)]][level] += 1
    for element in document.elements:
        levels = anchored_styles.get(styles.get(id(element)))
        if (
            element.kind == "heading"
            and element.payload.get("structureSource") != "pdf-bookmark"
            and levels
            and len(levels) == 1
        ):
            element.payload.update(
                level=next(iter(levels)), structureSource="bookmark-calibrated-typography"
            )
    # Changing a heading level must update parent links and evidence paths too.
    stack = []
    refs = {e.payload.get("documentRef"): i for i, e in enumerate(document.elements)}
    for index, element in enumerate(document.elements):
        if element.kind == "heading":
            level = int(element.payload.get("level", 2))
            while stack and stack[-1][0] >= level:
                stack.pop()
            element.parent = stack[-1][1] if stack else None
            stack.append((level, index, element.text))
        elif element.kind in {"page_header", "page_footer", "metadata"}:
            element.parent = None
            element.headings = []
            continue
        else:
            parent = refs.get(element.payload.get("visualOwner"))
            element.parent = (
                parent
                if parent is not None and parent < index
                else (stack[-1][1] if stack else None)
            )
        element.headings = [h[2] for h in stack]


def organize_document(document: ParsedDocument, metadata: dict | None = None) -> None:
    """Retain observed roles and make section hierarchy available to every consumer."""
    metadata = metadata if metadata is not None else document.manifest.get("metadata", {})
    title_keys = {normalized(c["value"]) for c in metadata.get("title", [])}
    author_keys = [
        normalized(name) for c in metadata.get("authors", []) for name in c["value"].split(";")
    ]
    outline = []
    for ordinal, element in enumerate(document.elements):
        notice = PUBLICATION_NOTICE.match(element.text.strip())
        notice_context = (
            element.kind == "reference"
            or element.text.lstrip().startswith("©")
            or (notice and element.text.strip()[notice.end() :].lstrip().startswith((":", "：")))
        )
        if (
            element.kind in {"paragraph", "reference", "metadata"}
            and not element.payload.get("marker")
            and notice
            and (notice_context or element.payload.get("role") == "rights")
        ):
            # A trailing licence or publisher notice does not become a reference
            # merely because it follows the bibliography without another heading.
            element.kind = "metadata"
            element.payload.update(role="rights", noEmbed=True)
            _field(metadata, "rights", element.text, "document", element.anchor)
        if element.kind in NON_BODY:
            element.payload["noEmbed"] = True
            element.payload.pop("reference", None)
        if element.kind == "heading":
            if normalized(element.text) in title_keys or element.payload.get("role") == "title":
                element.payload["role"] = "title"
                _field(metadata, "title", element.text, "document", element.anchor)
            else:
                outline.append(
                    {
                        "ordinal": ordinal,
                        "title": element.text,
                        "level": element.payload.get("level", 2),
                        "anchor": element.anchor,
                        "status": "observed"
                        if element.payload.get("structureSource") == "pdf-bookmark"
                        else "inferred",
                    }
                )
        if element.kind not in {"paragraph", "metadata", "page_header", "page_footer"}:
            continue
        if match := DATE_LABEL.fullmatch(element.text.strip()):
            if DATE_VALUE.search(match[2]):
                key = match[1].casefold().replace(" ", "")
                key = {
                    "publishedonline": "published",
                    "publicationdate": "published",
                    "出版日期": "published",
                    "发布日期": "published",
                    "接收日期": "accepted",
                    "修订日期": "updated",
                }.get(key, key)
                evidence = _field(
                    metadata,
                    key,
                    match[2],
                    str(element.payload.get("extraction", "document")),
                    element.anchor,
                )
                if evidence is not None and isinstance(
                    element.payload.get("confidence"), (int, float)
                ):
                    evidence["confidence"] = element.payload["confidence"]
                element.kind = "metadata"
                element.payload.update(role=key, noEmbed=True)
            else:
                # A placeholder stays visible in details, never becomes a claimed date.
                element.kind = "metadata"
                element.payload.update(role="date-notice", noEmbed=True, status="unresolved")
        elif element.kind == "paragraph" and element.anchor.get("page") == 1:
            text_key = re.sub(r"\d", "", normalized(element.text))
            remaining = text_key
            for key in sorted(set(author_keys), key=len, reverse=True):
                if key:
                    remaining = remaining.replace(key, "")
            # Mentioning a declared author in prose does not make it an author line.
            named_fraction = 1 - len(remaining) / max(1, len(text_key))
            if len(text_key) > 12 and named_fraction >= 0.7:
                element.kind = "metadata"
                element.payload.update(role="authors", noEmbed=True)
        if element.kind == "paragraph":
            for name in ("doi", "identifier"):
                if any(
                    c["value"] in element.text
                    and len(element.text.replace(c["value"], "").strip()) < 40
                    for c in metadata.get(name, [])
                ):
                    element.kind = "metadata"
                    element.payload.update(role="identifier", noEmbed=True)
    document.manifest.update(
        metadata=metadata,
        outline=outline,
        structure={
            "policy": "source-defined",
            "headings": len(outline),
            "unheadedContent": not bool(outline),
            "inferredSectionNames": False,
        },
    )
    document.validate()


def _box(element) -> tuple[int, int, int, int] | None:
    box = element.anchor.get("bboxPpm")
    if not isinstance(box, list) or len(box) != 4:
        return None
    boxes = [
        box,
        *[
            span["bboxPpm"]
            for span in element.anchor.get("spans", [])
            if span.get("page") == element.anchor.get("page") and "bboxPpm" in span
        ],
    ]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[0] + b[2] for b in boxes),
        max(b[1] + b[3] for b in boxes),
    )


def _contains(outer: tuple, inner: tuple) -> bool:
    overlap = max(0, min(outer[2], inner[2]) - max(outer[0], inner[0])) * max(
        0, min(outer[3], inner[3]) - max(outer[1], inner[1])
    )
    area = max(1, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return overlap / area >= 0.85


def group_visuals(document: ParsedDocument) -> None:
    """Join panels only when an external caption and geometry support ownership.

    Keep panel text and structured tables as children. A chart's numeric labels
    never become reconstructed table values or body paragraphs.
    """
    by_page = defaultdict(list)
    for index, element in enumerate(document.elements):
        box = _box(element)
        if (
            element.kind == "figure"
            and box
            and (box[2] - box[0]) * (box[3] - box[1]) < 2_000_000_000
            and not element.payload.get("caption")
            and not any(
                c.payload.get("visualOwner") == element.payload.get("documentRef")
                for c in document.elements
            )
        ):
            element.kind = "embedded_image"
            element.payload.update(role="unclassified-image", noEmbed=True)
        if element.anchor.get("page") and _box(element):
            by_page[element.anchor["page"]].append(index)
    for indices in by_page.values():
        visuals = [
            i
            for i in indices
            if document.elements[i].kind in {"figure", "table"}
            and not document.elements[i].payload.get("visualOwner")
        ]
        if not visuals:
            continue
        captions = []
        for index in indices:
            element = document.elements[index]
            if element.kind != "caption":
                continue
            if any(_contains(_box(document.elements[v]), _box(element)) for v in visuals):
                element.kind = "figure_text"
                element.payload["noEmbed"] = True
            elif len(element.text) >= 40:
                captions.append(index)
        numbered = [c for c in captions if CAPTION_LABEL.match(document.elements[c].text)]
        if numbered:
            captions = numbered
        groups = defaultdict(list)
        for index in visuals:
            box = _box(document.elements[index])
            candidates = [
                c
                for c in captions
                if _box(document.elements[c])[1] >= box[3] - 25_000
                and _box(document.elements[c])[1] - box[3] < 550_000
                and (
                    numbered
                    or min(_box(document.elements[c])[2], box[2])
                    > max(_box(document.elements[c])[0], box[0])
                )
            ]
            if candidates:
                caption = min(candidates, key=lambda c: _box(document.elements[c])[1] - box[3])
                groups[caption].append(index)
        for caption_index, members in groups.items():
            pictures = [i for i in members if document.elements[i].kind == "figure"]
            if not pictures:
                continue
            boxes = [_box(document.elements[i]) for i in members]
            member_refs = {document.elements[i].payload["documentRef"] for i in members}
            caption_top = _box(document.elements[caption_index])[1]
            # Detection boxes often cover the plot but omit rotated axis labels
            # or panel letters. Include owned annotations above the real caption.
            boxes.extend(
                _box(document.elements[i])
                for i in indices
                if document.elements[i].payload.get("visualOwner") in member_refs
                and _box(document.elements[i])[3] <= caption_top
                and not CAPTION_LABEL.match(document.elements[i].text)
            )
            union = (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
            # Prose between panels invalidates a combined crop. Retain individual regions.
            if any(
                document.elements[i].kind == "paragraph"
                and len(document.elements[i].text) > 100
                and _contains(union, _box(document.elements[i]))
                for i in indices
            ):
                continue
            primary = document.elements[min(pictures)]
            owner = primary.payload["documentRef"]
            caption = document.elements[caption_index]
            primary.payload.update(
                caption=caption.text,
                captionRef=caption.payload["documentRef"],
                panelRefs=[document.elements[i].payload["documentRef"] for i in members],
            )
            left, top = max(0, union[0] - 2_000), max(0, union[1] - 2_000)
            right, bottom = min(1_000_000, union[2] + 2_000), min(caption_top, union[3] + 2_000)
            primary.anchor["bboxPpm"] = [left, top, right - left, bottom - top]
            primary.anchor["cropSource"] = "caption-associated-regions"
            for i in members:
                other = document.elements[i]
                previous_owner = other.payload["documentRef"]
                for child in document.elements:
                    if child.payload.get("visualOwner") == previous_owner:
                        child.payload["visualOwner"] = owner
                if other is not primary:
                    other.payload.update(visualOwner=owner, noEmbed=True)
                    if other.kind == "figure":
                        other.kind = "figure_panel"
            caption.payload["visualOwner"] = owner
            # Unassigned short labels inside the combined region belong to the visual too.
            for i in indices:
                other = document.elements[i]
                if (
                    other.kind in {"paragraph", "caption"}
                    and i != caption_index
                    and _contains(union, _box(other))
                ):
                    other.kind = "figure_text"
                    other.payload.update(visualOwner=owner, noEmbed=True)
