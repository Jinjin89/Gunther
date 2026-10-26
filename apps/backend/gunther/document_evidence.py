"""Layer 1: numbered, read-only blocks from the layout parser's JSON.

Docling's body and furniture trees are walked in its reading order. Every text,
picture, table or formula item becomes a ``Block`` with its exact text (the PDF's
own glyphs when ``pdf_native`` accepted them), its page regions and its
typography. A text item the parser stitched together from separate printed
regions (a paragraph and the running head inside its page break) becomes one
block per region, so each can be judged on its own. Nothing here decides
structure; ``structure_draft`` does.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace

from gunther.document_model import LABELS, Block, Evidence

# Docling labels without a counterpart in ``LABELS``.
_ALIASES = {"paragraph": "text", "chart": "picture", "document_index": "table"}
_COLLECTIONS = ("texts", "tables", "pictures", "groups")
# Headings, formulas and code stay whole: their regions are lines of one thing.
_SPLIT = frozenset({
    "text", "list_item", "caption", "footnote", "reference", "other",
    "page_header", "page_footer", "page_number",
})


def build_evidence(document: dict, metadata: dict | None = None) -> Evidence:
    """Blocks with ids ``b0, b1, …`` in reading order, one per item or printed region."""
    objects = {
        f"#/{collection}/{index}": item
        for collection in _COLLECTIONS
        for index, item in enumerate(document.get(collection) or [])
    }
    pages = {}
    for key, page in (document.get("pages") or {}).items():
        size = page.get("size") or {}
        if size.get("width") and size.get("height"):
            pages[int(key)] = {"width": float(size["width"]), "height": float(size["height"])}
    blocks: list[Block] = []
    visited: set[str] = set()
    characters = 0

    def walk(ref: str, depth: int, owner: str | None) -> None:
        nonlocal characters
        if ref in visited or depth > 64 or len(blocks) > 50_000:
            raise ValueError("Invalid or oversized document hierarchy")
        visited.add(ref)
        item = objects.get(ref)
        if item is None:
            raise ValueError("Document contains an unresolved content reference")
        collection = ref.split("/")[1]
        block = None
        if collection != "groups":
            block = _block(f"b{len(blocks)}", ref, collection, item, pages)
            parts = _split(block, item) if collection == "texts" else None
            for part in parts or [block]:
                # A caption is linked to its picture, not printed inside it.
                if owner and part.label != "caption":
                    part.inside = owner
                characters += len(part.text)
                if characters > 950_000:
                    raise ValueError("Document exceeds the structured text budget")
                blocks.append(part)
        if block is not None and block.label == "picture":
            owner = block.id
        for child in item.get("children") or []:
            if child.get("$ref"):
                walk(child["$ref"], depth + 1, owner)

    for child in (document.get("body") or {}).get("children") or []:
        if child.get("$ref"):
            walk(child["$ref"], 0, None)
    for child in (document.get("furniture") or {}).get("children") or []:
        if child.get("$ref") and child["$ref"] not in visited:
            walk(child["$ref"], 0, None)
    evidence = Evidence(
        blocks,
        pages=pages,
        metadata=metadata or {},
        manifest={
            "pageCount": len(document.get("pages") or {}),
            "conversionStatus": document.get("gunther_status", "unknown"),
            "parser": document.get("gunther_parser"),
        },
    )
    evidence.body_font_size = body_font_size(evidence)
    return evidence


def body_font_size(evidence: Evidence) -> float | None:
    """The size carrying most running prose, from native glyph evidence only."""
    sizes: Counter = Counter()
    for block in evidence.blocks:
        if block.label == "text" and len(block.text) > 200 and block.font_size and block.placeable:
            sizes[block.font_size] += len(block.text)
    return sizes.most_common(1)[0][0] if sizes else None


def _block(identifier: str, ref: str, collection: str, item: dict, pages: dict) -> Block:
    raw_label = str(item.get("label") or "")
    label = _ALIASES.get(raw_label, raw_label)
    if label not in LABELS:
        label = "text" if collection == "texts" else "other"
    if collection == "pictures":
        label = "picture"
    elif collection == "tables":
        label = "table"
    extra: dict = {"documentRef": ref}
    if item.get("content_layer"):
        extra["contentLayer"] = item["content_layer"]
    text = str(item.get("text") or item.get("orig") or "")
    inline: list[dict] = []
    if native := item.get("gunther_native"):
        extra.update(textSource="pdf-native-glyphs", sourceText=text)
        text = str(native.get("text") or "")
        inline = list(native.get("scripts") or [])
        extra.update(_layout_marks(native))
    if item.get("gunther_ocr"):
        extra.update(extraction="rapidocr-metadata-check", **item["gunther_ocr"])
    if item.get("hyperlink"):
        extra["hyperlink"] = str(item["hyperlink"])
    if item.get("code_language"):
        extra["codeLanguage"] = str(item["code_language"])
    table = None
    if label == "table":
        table = item.get("data") or {}
        text = "\n".join(str(cell.get("text", "")) for cell in table.get("table_cells", []))
    if label in {"picture", "table"}:
        for key, name in (("captions", "captionRefs"), ("footnotes", "footnoteRefs")):
            refs = [entry["$ref"] for entry in item.get(key) or [] if entry.get("$ref")]
            if refs:
                extra[name] = refs
    marker = str(item["marker"]) if item.get("marker") else None
    if marker is not None:
        extra["enumerated"] = bool(item.get("enumerated"))
    regions = [_region(prov, pages) for prov in item.get("prov") or []]
    first = next((r["bboxPpm"] for r in regions if "bboxPpm" in r), None)
    level = item.get("level") if label in {"section_header", "title"} else None
    return Block(
        id=identifier,
        label=label,
        text=text,
        page=regions[0]["page"] if regions else None,
        box=(first[0], first[1], first[0] + first[2], first[1] + first[3]) if first else None,
        regions=regions,
        font_size=float(item["gunther_font_size"]) if item.get("gunther_font_size") else None,
        level=int(level) if isinstance(level, int | float) else None,
        inline=inline,
        marker=marker,
        image_key=ref if label in {"picture", "table", "formula"} else None,
        table=table,
        extra=extra,
    )


def _split(whole: Block, item: dict) -> list[Block] | None:
    """One block per printed region of a stitched item, or None to keep it whole.

    Each region's text is the PDF's own glyphs in that region when native text was
    accepted, otherwise the parser's text cut at its character spans. When neither
    is available and consistent, the item stays one block.
    """
    provs = item.get("prov") or []
    if len(provs) < 2 or len(whole.regions) != len(provs) or whole.label not in _SPLIT:
        return None
    cut = _cut(str(item.get("text") or ""), provs)
    native = item.get("gunther_native")
    if native:
        found = native.get("regions")
        if not isinstance(found, list) or len(found) != len(provs):
            return None
        texts = [str(entry.get("text") or "") for entry in found]
        marks = [_layout_marks(entry) for entry in found]
        scripts = [list(entry.get("scripts") or []) for entry in found]
        joins = [bool(entry.get("joinsNext")) for entry in found]
        # A running head inside a paragraph has its own type size; the item's is a mix.
        sizes = [entry.get("fontSize") or whole.font_size for entry in found]
    elif cut is not None:
        texts, scripts, sizes = cut, [[] for _ in cut], [whole.font_size for _ in cut]
        marks = [{} for _ in cut]
        # The parser keeps the hyphen; glued without a space it reads as printed.
        joins = [bool(re.search(r"[^\W\d_]-$", text)) for text in cut]
    else:
        return None
    if any(not text.strip() for text in texts):
        return None
    number = int(whole.id[1:])
    parts = []
    for index, region in enumerate(whole.regions):
        extra = {
            key: value for key, value in whole.extra.items()
            if key not in {"sourceText", "lineStarts", "icons"}
        }
        extra.update(marks[index])
        if native and cut is not None:
            extra["sourceText"] = cut[index]
        if index:
            extra["sameItem"] = parts[-1].id
            extra.pop("enumerated", None)
        if joins[index] and index < len(provs) - 1:
            extra["joinsNext"] = True
        box = region.get("bboxPpm")
        parts.append(replace(
            whole,
            id=f"b{number + index}",
            text=texts[index],
            page=region.get("page"),
            box=(box[0], box[1], box[0] + box[2], box[1] + box[3]) if box else None,
            regions=[region],
            inline=scripts[index],
            font_size=float(sizes[index]) if sizes[index] else None,
            marker=whole.marker if index == 0 else None,
            clues=[],
            extra=extra,
        ))
    return parts


def _layout_marks(native: dict) -> dict:
    """Where printed lines start, and which glyphs are only a link icon (an ORCID badge)."""
    return {key: native[key] for key in ("lineStarts", "icons") if native.get(key)}


def _cut(text: str, provs: list[dict]) -> list[str] | None:
    """The text at each region's character span, when the spans tile the text."""
    pieces, end = [], 0
    for prov in provs:
        span = prov.get("charspan")
        if not (
            isinstance(span, list | tuple) and len(span) == 2
            and all(isinstance(value, int) for value in span)
        ):
            return None
        start, stop = span
        if not end <= start < stop <= len(text) or text[end:start].strip():
            return None
        pieces.append(text[start:stop])
        end = stop
    return pieces if not text[end:].strip() else None


def _region(prov: dict, pages: dict) -> dict:
    """A provenance region in parts per million of its page: [left, top, width, height]."""
    page = prov.get("page_no")
    region: dict = {"page": page}
    size = pages.get(page) if isinstance(page, int) else None
    box = prov.get("bbox") or {}
    if size and all(key in box for key in ("l", "t", "r", "b")):
        width, height = size["width"], size["height"]
        left, top, right, bottom = (float(box[key]) for key in ("l", "t", "r", "b"))
        if str(box.get("coord_origin", "TOPLEFT")).upper() in {"BOTTOMLEFT", "BOTTOM_LEFT"}:
            top, bottom = height - top, height - bottom
        top, bottom = min(top, bottom), max(top, bottom)
        left, right = min(left, right), max(left, right)
        region["bboxPpm"] = [
            round(left / width * 1_000_000),
            round(top / height * 1_000_000),
            round((right - left) / width * 1_000_000),
            round((bottom - top) / height * 1_000_000),
        ]
    return region
