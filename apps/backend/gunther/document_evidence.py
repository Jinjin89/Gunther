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
import unicodedata
from collections import Counter
from dataclasses import replace

from gunther.document_model import LABELS, Block, Evidence, table_text

# Docling labels without a counterpart in ``LABELS``.
_ALIASES = {"paragraph": "text", "chart": "picture", "document_index": "table"}
_COLLECTIONS = ("texts", "tables", "pictures", "groups")
# A box the layout parser classed by its state, before the option's text.
CHECKBOX_LABELS = {"checkbox_selected": "☒", "checkbox_unselected": "☐"}
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
            parts = None
            if collection == "texts":
                parts = _split(block, item) or _split_lines(block, item, pages)
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
    # Every item the parser produced is a block, even one its tree does not link: it is
    # placed or dropped with a reason like the rest, never lost without a record.
    unlinked = [
        ref for ref in objects
        if ref not in visited and not ref.startswith("#/groups/")
        and not _owned_by_visited(ref, objects, visited)
    ]
    for ref in unlinked:
        if ref in visited:
            continue  # reached as the child of an earlier one
        first = len(blocks)
        walk(ref, 0, None)
        for block in blocks[first:]:
            block.extra["unlinked"] = True
    manifest = {
        "pageCount": document.get("gunther_page_count") or len(document.get("pages") or {}),
        "layoutPageCount": len(document.get("pages") or {}),
        "conversionStatus": document.get("gunther_status", "unknown"),
        "parser": document.get("gunther_parser"),
    }
    for key, name in (("gunther_glyphs", "glyphCheck"), ("gunther_forms", "formFields")):
        if document.get(key):
            manifest[name] = document[key]
    if document.get("gunther_bookmarks"):
        manifest["bookmarks"] = document["gunther_bookmarks"]
    if document.get("gunther_native_warning"):
        manifest["nativeTextWarning"] = document["gunther_native_warning"]
    evidence = Evidence(blocks, pages=pages, metadata=metadata or {}, manifest=manifest)
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
    if raw_label in CHECKBOX_LABELS and not text.startswith(tuple(CHECKBOX_LABELS.values())):
        # The layout parser saw a box and its state; the state is part of the answer.
        mark = CHECKBOX_LABELS[raw_label]
        text = f"{mark} {text}".strip()
        inline.append({"start": 0, "end": 1, "mark": "checkbox", "source": "layout",
                       "checked": mark == CHECKBOX_LABELS["checkbox_selected"]})
    formula = item.get("gunther_formula") if label == "formula" else None
    if formula is not None:
        extra.update(sourceText=text, formulaLayout=formula.get("layout"),
                     glyphCheck=formula.get("glyphs", "unavailable"))
        if formula.get("number"):
            extra["equationNumber"] = formula["number"]
        text = str(formula.get("text") or "")
        inline = [*formula.get("scripts", []), *formula.get("marks", [])]
        if text:
            extra["textSource"] = "pdf-native-glyphs"
    elif native := item.get("gunther_native"):
        extra.update(textSource="pdf-native-glyphs", sourceText=text)
        if label == "code" and native.get("pitch") == "proportional":
            # A listing is set in fixed-pitch type; proportional type is running text.
            label = "text"
            extra["layoutLabel"] = "code"
        text = str(native.get("text") or "")
        inline = [*native.get("scripts", []), *native.get("marks", [])]
        extra.update(_layout_marks(native))
    else:
        text, unreadable = _unreadable(text)
        inline.extend(unreadable)
    if item.get("gunther_unverified_glyphs") and formula is None:
        # The layout text is kept, but the glyph check found misnamed glyphs it holds.
        extra["glyphCheck"] = "unverified"
    if item.get("gunther_ocr"):
        extra.update(extraction="rapidocr-metadata-check", **item["gunther_ocr"])
    if item.get("hyperlink"):
        extra["hyperlink"] = str(item["hyperlink"])
    if item.get("code_language"):
        extra["codeLanguage"] = str(item["code_language"])
    table = None
    if label == "table":
        table = item.get("data") or {}
        text = table_text(table)
        if any(cell.get("gunther_unverified") for cell in table.get("table_cells", [])):
            extra["glyphCheck"] = "unverified"
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
        scripts = [[*entry.get("scripts", []), *entry.get("marks", [])] for entry in found]
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


# Keys of an inline span that are offsets into its block's text.
SPAN_OFFSETS = ("start", "end", "numeratorEnd", "denominatorStart")


def _split_lines(whole: Block, item: dict, pages: dict) -> list[Block] | None:
    """Printed lines the layout parser read as one item, set apart, are several blocks.

    The layout parser can read a running head and the section heading under it as one
    heading, or stack separate short paragraphs (affiliations, then the dates) into one
    text. Lines with ordinary spacing stay together (a long heading wraps, a paragraph
    runs on); a wide gap starts a new block, and in a heading so does a change of type
    size or face. Each part is judged on its own (a running head repeats at the page
    edge, a heading does not; a line of dates is a declaration).
    """
    native = item.get("gunther_native") or {}
    lines = native.get("lines") or []
    size = pages.get(whole.page) if whole.page is not None else None
    heading = whole.label in {"section_header", "title"}
    # A listing in fixed-pitch type keeps its blank lines; it stays one block.
    if not (heading or whole.label == "text") or len(lines) < 2 or not size:
        return None
    if len(item.get("prov") or []) != 1 or native.get("text") != whole.text:
        return None
    groups: list[list[list]] = [[lines[0]]]
    for previous, line in zip(lines, lines[1:], strict=False):
        sizes = (previous[1], line[1])
        gap = previous[5] - line[3]
        same = gap <= 0.9 * max(sizes) and (
            not heading or (max(sizes) <= 1.08 * min(sizes) and previous[6] == line[6])
        )
        if same:
            groups[-1].append(line)
        else:
            groups.append([line])
    if len(groups) < 2:
        return None
    width, height = size["width"], size["height"]
    starts = [group[0][0] for group in groups] + [len(whole.text)]
    number = int(whole.id[1:])
    parts = []
    for index, group in enumerate(groups):
        text = whole.text[starts[index]:starts[index + 1]].strip()
        if not text:
            return None
        left, right = min(g[2] for g in group), max(g[4] for g in group)
        top, bottom = max(g[3] for g in group), min(g[5] for g in group)
        box = [round(left / width * 1e6), round((height - top) / height * 1e6),
               round((right - left) / width * 1e6), round((top - bottom) / height * 1e6)]
        offset = whole.text.index(text, starts[index])
        extra = {k: v for k, v in whole.extra.items()
                 if k not in {"sourceText", "lineStarts", "icons"}}
        extra["splitFrom"] = whole.extra.get("documentRef")
        icons = [
            {"start": icon["start"] - offset, "end": icon["end"] - offset}
            for icon in whole.extra.get("icons") or ()
            if offset <= icon["start"] and icon["end"] <= offset + len(text)
        ]
        if icons:
            extra["icons"] = icons
        parts.append(replace(
            whole,
            id=f"b{number + index}",
            text=text,
            box=(box[0], box[1], box[0] + box[2], box[1] + box[3]),
            regions=[{"page": whole.page, "bboxPpm": box}],
            inline=[
                {**span, **{key: span[key] - offset for key in SPAN_OFFSETS if key in span}}
                for span in whole.inline
                if offset <= span["start"] and span["end"] <= offset + len(text)
            ],
            font_size=float(max(g[1] for g in group)),
            clues=[],
            extra=extra,
        ))
    # The line in the heading's own type keeps the label; set apart above it at the page
    # top, other type is what a running head looks like, and is labelled so.
    if not heading:
        return parts
    main = max(range(len(parts)), key=lambda i: (parts[i].font_size or 0, len(parts[i].text)))
    for index, part in enumerate(parts):
        if index < main and part.box and part.box[3] < 120_000 and (
            (part.font_size or 0) < 0.95 * (parts[main].font_size or 0)
        ):
            part.label = "page_header"
    return parts


def _unreadable(text: str) -> tuple[str, list[dict]]:
    """Private-use and control codes name no character: shown as U+FFFD and marked."""
    marks, output = [], []
    for char in text:
        if char.isspace() or unicodedata.category(char) not in {"Co", "Cc", "Cs", "Cn"}:
            output.append(char)
            continue
        if marks and marks[-1]["end"] == len(output):
            marks[-1]["end"] += 1
            marks[-1]["from"] += char
        else:
            marks.append({"start": len(output), "end": len(output) + 1, "mark": "uncertain",
                          "from": char})
        output.append("\ufffd")
    return "".join(output), marks


def _owned_by_visited(ref: str, objects: dict, visited: set[str]) -> bool:
    """Whether an item is a child of a visited item (reached through its parent)."""
    parent = (objects.get(ref) or {}).get("parent") or {}
    return parent.get("$ref") in visited if isinstance(parent, dict) else False


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
