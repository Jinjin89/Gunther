"""Recover native glyphs and scripts inside the layout parser's own text regions.

PDFium is already bundled with the isolated PDF helper. It provides an independent
font decoder and character baselines; no mathematical notation is guessed from text.
"""

from __future__ import annotations

import ctypes
import math
import re
import unicodedata
from collections import Counter
from contextlib import suppress
from difflib import SequenceMatcher

from gunther.pdf_glyphs import SYMBOL_ENCODING, verify

__all__ = ["SYMBOL_ENCODING", "enrich_native_pdf", "render_visual_pages"]

# Fonts named "Symbol" (Adobe's Symbol and its clones, such as SymbolMT) often
# report their glyphs at private-use code points U+F020–U+F0FF, the font's own
# byte codes. Adobe's published Symbol encoding gives each one's character; the
# printed shapes are then checked like every other glyph (``pdf_glyphs``).
SYMBOL_FONT = re.compile(r"symbol", re.IGNORECASE)
RTL = re.compile(r"[֐-ࣿיִ-﷿ﹰ-ﻼ]")


# Spacing accents some typesetters draw as their own glyph over a letter
# ("Ba¨rbel"); each has a combining form that joins the letter it covers.
ACCENTS = {
    "¨": "\u0308", "´": "\u0301", "`": "\u0300", "ˆ": "\u0302", "˜": "\u0303",
    "¸": "\u0327", "ˇ": "\u030c", "˘": "\u0306", "˙": "\u0307", "˚": "\u030a",
    "¯": "\u0304", "˝": "\u030b",
}
# A web link no wider than this over a few glyphs is an icon (an ORCID badge),
# not words: its glyphs are recorded so a name can be read without them.
ICON_LINK_POINTS = 15


def _compose_accents(chars: list[dict]) -> list[dict]:
    """Join a separately drawn accent to the neighbouring letter its box covers."""
    result = list(chars)
    for index, char in enumerate(chars):
        mark = ACCENTS.get(char["text"])
        if mark is None or "left" not in char:
            continue
        best, overlap = None, 0.0
        for neighbour in (index - 1, index + 1):
            if not 0 <= neighbour < len(chars):
                continue
            other = chars[neighbour]
            if "left" not in other or not other["text"].isalpha() or other["page"] != char["page"]:
                continue
            shared = min(char["right"], other["right"]) - max(char["left"], other["left"])
            if shared > overlap:
                best, overlap = neighbour, shared
        if best is None:
            continue
        letter = result[best]
        result[best] = {**letter, "text": unicodedata.normalize("NFC", letter["text"] + mark)}
        result[index] = None
    return [char for char in result if char is not None]


def _visual_order(chars: list[dict]) -> list[dict]:
    """Left-to-right order within each printed line.

    Typesetters often draw sized delimiters and operators after the symbols they
    enclose, so content-stream order reads "p()x" for "p(x)". Lines are found
    from glyph boxes; spaces are rebuilt from the printed gaps. Text already in
    order, and right-to-left scripts, are returned unchanged.
    """
    boxed = [c for c in chars if "left" in c and c["text"].strip()]
    if not boxed or any(RTL.search(c["text"]) for c in boxed):
        return chars
    inverted = any(
        b["right"] <= a["left"] + 0.1 and abs(b["cy"] - a["cy"]) < 0.7 * max(a["size"], b["size"])
        for a, b in zip(boxed, boxed[1:], strict=False)
    )
    if not inverted:
        return chars
    lines: list[list[dict]] = []
    for char in boxed:
        half = max(char["size"], 1) * 0.35
        line = next(
            (
                line for line in lines
                if line[0]["page"] == char["page"]
                and min(c["cy"] for c in line) - half
                <= char["cy"]
                <= max(c["cy"] for c in line) + half
            ),
            None,
        )
        if line is None:
            lines.append([char])
        else:
            line.append(char)
    ordered: list[dict] = []
    for line in lines:
        line.sort(key=lambda c: c["left"])
        if ordered:
            ordered.append({"text": "\n", "size": 0, "y": 0})
        for previous, char in zip([None, *line], line, strict=False):
            if previous is not None and char["left"] - previous["right"] > 0.15 * max(
                char["size"], previous["size"]
            ):
                ordered.append({"text": " ", "size": 0, "y": char["y"]})
            ordered.append(char)
    return ordered


def _pitch(chars: list[dict]) -> str | None:
    """``fixed`` when the printed glyphs advance by one width (a code listing's type),
    ``proportional`` when they do not; None for too few glyphs to tell."""
    advances = sorted(
        (c["advanceRight"] - c["advanceLeft"]) / c["size"]
        for c in chars
        if c["text"].strip() and c.get("size") and "advanceRight" in c and "advanceLeft" in c
    )
    if len(advances) < 12:
        return None
    middle = advances[len(advances) // 2]
    alike = sum(1 for a in advances if abs(a - middle) <= 0.03 * middle)
    return "fixed" if alike >= 0.9 * len(advances) else "proportional"


def _native_text(chars: list[dict], rules: dict | None = None) -> dict:
    sizes = Counter(round(c["size"], 1) for c in chars if c["text"].strip() and c["size"])
    if not sizes:
        return {}
    size = sizes.most_common(1)[0][0]
    # Each region part (separated by an explicit break) is ordered on its own.
    parts: list[list[dict]] = [[]]
    for char in chars:
        if char["text"] == "\n" and "left" not in char:
            parts.append([])
        else:
            parts[-1].append(char)
    reordered: list[dict] = []
    for part in parts:
        if reordered:
            reordered.append({"text": "\n", "size": 0, "y": 0})
        reordered.extend(_visual_order(_compose_accents(part)))
    chars = reordered
    baselines = Counter(
        round(c["y"], 1) for c in chars if c["size"] >= size * 0.9 and c["text"].strip()
    )
    # Only baselines supported by several full-sized glyphs establish a text line.
    lines = [y for y, count in baselines.items() if count >= 3]
    output: list[tuple[str, str | None, dict]] = []
    pending_break = line_start = False
    previous: dict | None = None
    for char in chars:
        text = char["text"]
        if text in {"\r", "\n"}:
            pending_break = True
            continue
        if text.strip() and char.get("size", 0) >= size * 0.85 and "y" in char:
            # A full-sized glyph on another baseline, back at the left, starts a printed
            # line, whether or not the text layer put a line break before it (its break
            # can lie outside the region, after a page number set on the line above).
            if (
                previous is not None and "left" in char and "left" in previous
                and char["left"] < previous["left"]
                and abs(char["y"] - previous["y"]) > 0.6 * max(char["size"], previous["size"])
            ):
                pending_break = True
            previous = char
        if char.get("hyphen") or text == "\u00ad":
            # A discretionary hyphen printed at the column edge is not a character
            # in the reflowed word. Its glyph and placement remain in the PDF.
            if output and output[-1][0] == " ":
                output.pop()
            output.append(("", "join", {}))
            continue
        if not text or (ord(text[0]) < 32 and not text.isspace()):
            # A control code left after the glyph check printed nothing.
            continue
        if pending_break:
            while output and output[-1][0] == " ":
                output.pop()
            join = bool(output and output[-1][1] == "join")
            if join:
                output.pop()
            elif output and output[-1][0] != "-":
                output.append((" ", None, {}))
            line_start = bool(output)
            pending_break = False
        script = None
        if lines and 0 < char["size"] < size * 0.85 and text.strip():
            baseline = min(lines, key=lambda y: abs(y - char["y"]))
            delta = char["y"] - baseline
            if size * 0.12 < abs(delta) < size * 0.8:
                script = "super" if delta > 0 else "sub"
        if text.isspace():
            if output and output[-1][0] != " ":
                output.append((" ", None, {}))
        else:
            output.append((text, script, {**char, "lineStart": True} if line_start else char))
            line_start = False
    # A word broken by a hyphen at the region's end goes on in the next region.
    marks = [c for c in output if c[0].strip() or c[1] == "join"]
    joins_next = bool(marks) and marks[-1][1] == "join"
    while output and not output[-1][0].strip():
        output.pop()
    while output and not output[0][0].strip():
        output.pop(0)
    text = "".join(c for c, _, _ in output)
    spans = []
    glyphs = []
    lines: list[int] = []
    icons: list[dict] = []
    marks: list[dict] = []
    typography: list[list] = []
    offset = 0
    for char, script, source in output:
        if source.get("lineStart") or (not typography and char.strip()):
            if source.get("lineStart"):
                lines.append(offset)
            typography.append([offset, []])
        if typography and "top" in source:
            typography[-1][1].append(source)
        if mark := _mark(source):
            mark.update(start=offset, end=offset + len(char))
            marks.append(mark)
        if source.get("icon"):
            if icons and icons[-1]["end"] == offset:
                icons[-1]["end"] += len(char)
            else:
                icons.append({"start": offset, "end": offset + len(char)})
        if script in {"sub", "super"}:
            if spans and spans[-1]["end"] == offset and spans[-1]["script"] == script:
                spans[-1]["end"] += len(char)
                glyphs[-1].append(source)
            else:
                spans.append({"start": offset, "end": offset + len(char), "script": script})
                glyphs.append([source])
        offset += len(char)
    # A raised run over a lowered run is a fraction only when a native horizontal
    # rule separates their overlapping x-ranges. Exponents alone are not fractions.
    for index in range(len(spans) - 1, 0, -1):
        numerator, denominator = spans[index - 1:index + 1]
        if (
            numerator["script"] != "super" or denominator["script"] != "sub"
            or text[numerator["end"]:denominator["start"]].strip()
        ):
            continue
        top, bottom = glyphs[index - 1], glyphs[index]
        if not all("left" in c for c in [*top, *bottom]):
            continue
        page = top[0]["page"]
        left = min(c["left"] for c in [*top, *bottom])
        right = max(c["right"] for c in [*top, *bottom])
        if any(
            min(c["cy"] for c in bottom) < (b + t) / 2 < max(c["cy"] for c in top)
            and x0 <= left + 1 and x1 >= right - 1 and x1 - x0 <= (right - left) + 4
            for x0, b, x1, t in (rules or {}).get(page, [])
        ):
            spans[index - 1:index + 1] = [{
                "start": numerator["start"], "end": denominator["end"],
                "script": "fraction", "numeratorEnd": numerator["end"],
                "denominatorStart": denominator["start"],
            }]
            glyphs[index - 1:index + 1] = [[*top, *bottom]]
    native = {"text": text, "scripts": spans, "fontSize": size, "joinsNext": joins_next}
    if pitch := _pitch(chars):
        native["pitch"] = pitch
    if lines:
        native["lineStarts"] = lines
    if icons:
        native["icons"] = icons
    if marks:
        native["marks"] = marks
    if len(typography) > 1:
        # Each printed line's start, type size and extent (PDF points, y up), so a
        # heading the layout parser merged with a running head can be told apart.
        native["lines"] = [
            [
                start,
                Counter(round(c["size"], 1) for c in found).most_common(1)[0][0],
                round(min(c["left"] for c in found), 1),
                round(max(c["top"] for c in found), 1),
                round(max(c["right"] for c in found), 1),
                round(min(c["bottom"] for c in found), 1),
                Counter(c.get("font") for c in found).most_common(1)[0][0],
            ]
            for start, found in typography if found
        ]
    return native


def _mark(source: dict) -> dict | None:
    """The record a glyph leaves in the text: decoded, unreadable, or a form selection."""
    if glyph := source.get("glyph"):
        return {"mark": glyph["state"], "from": glyph["from"]}
    if form := source.get("form"):
        return {"mark": "checkbox", **form}
    return None


def _icon_links(pdf, page) -> list[tuple[float, float, float, float]]:
    """Boxes of small web links on a page: badges drawn as glyphs, such as ORCID's."""
    import pypdfium2.raw as raw

    boxes = []
    for position in range(raw.FPDFPage_GetAnnotCount(page.raw)):
        annotation = raw.FPDFPage_GetAnnot(page.raw, position)
        try:
            if raw.FPDFAnnot_GetSubtype(annotation) != raw.FPDF_ANNOT_LINK:
                continue
            rect = raw.FS_RECTF()
            raw.FPDFAnnot_GetRect(annotation, rect)
            width, height = rect.right - rect.left, rect.top - rect.bottom
            if not (0 < width <= ICON_LINK_POINTS and 0 < height <= ICON_LINK_POINTS):
                continue
            link = raw.FPDFAnnot_GetLink(annotation)
            action = raw.FPDFLink_GetAction(link) if link else None
            # Only links out of the document; in-document links are citations.
            if action and raw.FPDFAction_GetType(action) == raw.PDFACTION_URI:
                boxes.append((rect.left, rect.bottom, rect.right, rect.top))
        finally:
            raw.FPDFPage_CloseAnnot(annotation)
    return boxes


def _owned(by_page: dict[int, list[dict]], owner: dict) -> dict[tuple[int, int], list[dict]]:
    """Each region's glyphs in content order; ``owner`` maps (page, index) to a region."""
    owned: dict[tuple[int, int], list[dict]] = {}
    for number, chars in by_page.items():
        previous = None
        for char in chars:
            found = owner.get((number, char["index"]))
            if found is None and not char["text"].strip() and previous is not None:
                # A line's closing space and break sit just past the last glyph, outside
                # the layout box; without them the next line's first word is glued on.
                found = previous
            if found:
                owned.setdefault(found[1:], []).append(char)
            previous = found
    return owned


def enrich_native_pdf(document: dict, path: str) -> None:
    """Use a native region only when it agrees with the layout text's coverage.

    Also recorded for the next layers: the physical page count, which glyphs the text
    layer misnames (``pdf_glyphs``), checkbox selections and form answers placed in the
    text where they are printed, and the layout of each formula.
    """
    import pypdfium2 as pdfium

    by_page: dict[int, list[dict]] = {}
    rules: dict[int, list[tuple]] = {}
    with pdfium.PdfDocument(path) as pdf:
        document["gunther_page_count"] = len(pdf)
        with suppress(Exception):
            # Form fields must be loaded before any page is.
            pdf.init_forms()
        squares: dict[int, list[tuple]] = {}
        fields: dict[int, list[dict]] = {}
        for number in range(len(pdf)):
            page = pdf[number]
            textpage = page.get_textpage()
            try:
                paths = list(page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_PATH]))
                rules[number + 1] = [
                    box for obj in paths
                    if (box := obj.get_bounds()) and 0 <= box[3] - box[1] < 1
                    and 3 < box[2] - box[0] < 100
                ]
                squares[number + 1] = _squares(paths)
                fields[number + 1] = _form_fields(pdf, page)
                icons = _icon_links(pdf, page)
                by_page[number + 1] = _glyphs(pdfium, textpage, number + 1, icons)
            finally:
                textpage.close()
                page.close()
        try:
            report = verify(pdf, by_page)
            document["gunther_glyphs"] = report.summary()
        except Exception as error:
            # Unchecked text is reported, never presented as checked.
            document["gunther_glyphs"] = {"error": type(error).__name__}
        document["gunther_forms"] = _place_checkboxes(pdf, document, by_page, squares, fields)
        _table_cells(document, by_page)
        _analyse_formulas(document, by_page, rules)

    # Layout boxes can overlap a neighbouring line. Each glyph belongs to the one
    # region it lies deepest inside, so no paragraph absorbs another's first line.
    regions = []
    for position, item in enumerate(document.get("texts", [])):
        for part, prov in enumerate(item.get("prov", [])):
            number, box = prov.get("page_no"), prov.get("bbox", {})
            if not all(k in box for k in ("l", "t", "r", "b")):
                continue
            top, bottom = box["t"], box["b"]
            if box.get("coord_origin") == "TOPLEFT":
                height = document["pages"][str(number)]["size"]["height"]
                top, bottom = height - top, height - bottom
            top, bottom = max(top, bottom), min(top, bottom)
            regions.append((number, box["l"], top, box["r"], bottom, position, part))
    owner: dict[tuple[int, int], tuple[float, int, int]] = {}
    for number, left, top, right, bottom, position, part in regions:
        for char in by_page.get(number, []):
            x, y = char["x"], char["cy"]
            if left - 0.5 <= x <= right + 0.5 and bottom - 0.5 <= y <= top + 0.5:
                depth = min(x - left, right - x, y - bottom, top - y)
                key = (number, char["index"])
                if key not in owner or depth > owner[key][0]:
                    owner[key] = (depth, position, part)
    for number, chars in by_page.items():
        for char in chars:
            # A selection belongs to the text of the option printed beside it.
            if char.get("owner") is not None and (number, char["owner"]) in owner:
                owner[(number, char["index"])] = owner[(number, char["owner"])]
    owned = _owned(by_page, owner)

    for position, item in enumerate(document.get("texts", [])):
        selected = []
        for part in range(len(item.get("prov", []))):
            segment = owned.get((position, part), [])
            if selected and segment:
                selected.append({"text": "\n", "size": 0, "y": 0})
            selected.extend(segment)
        native = _native_text(selected, rules)
        if not native:
            continue
        item["gunther_font_size"] = native["fontSize"]
        original = item.get("text") or item.get("orig") or ""
        flagged = sum(1 for char in selected if char.get("glyph"))
        if flagged:
            # Until the native text is accepted below, the layout text keeps these glyphs
            # as its own decoder read them; that must not pass as checked.
            item["gunther_unverified_glyphs"] = flagged
        # A different reading order, clipped region or scan cannot replace layout/OCR.
        left, right = _comparable(original), _comparable(native["text"])
        if not left or len(left) > 20_000 or not 0.8 <= len(right) / len(left) <= 1.2:
            continue
        if SequenceMatcher(None, left, right, autojunk=False).ratio() >= 0.9:
            item.pop("gunther_unverified_glyphs", None)
            for char in selected:
                if char.get("record"):
                    char["record"]["placed"] = "text"
            if len(item.get("prov", [])) > 1:
                # The layout parser can merge separate printed regions into one item (a
                # running head inside a paragraph cut by a page break); each region's own
                # text lets the next layer tell them apart.
                native["regions"] = [
                    {
                        "text": found.get("text", ""),
                        "scripts": found.get("scripts", []),
                        "joinsNext": found.get("joinsNext", False),
                        **{
                            key: found[key]
                            for key in ("fontSize", "lineStarts", "icons", "marks", "lines")
                            if key in found
                        },
                    }
                    for found in (
                        _native_text(owned.get((position, part), []), rules)
                        for part in range(len(item["prov"]))
                    )
                ]
            item["gunther_native"] = native


def _glyphs(pdfium, textpage, number: int, icons: list) -> list[dict]:
    """Every horizontal glyph on a page with its font, boxes and baseline."""
    raw = pdfium.raw
    chars = []
    loose = raw.FS_RECTF()
    for index in range(textpage.count_chars()):
        text = textpage.get_text_range(index, 1)
        obj = textpage.get_textobj(index)
        size = 0.0
        if obj is not None:
            matrix = obj.get_matrix()
            # Rotated labels belong to visuals, never a horizontal paragraph.
            if abs(matrix.b) > 0.01 or abs(matrix.c) > 0.01:
                continue
            size = obj.get_font_size() * abs(matrix.d)
        name = ctypes.create_string_buffer(128)
        flags = ctypes.c_int()
        raw.FPDFText_GetFontInfo(textpage, index, name, 128, ctypes.byref(flags))
        font = name.value.decode("latin-1")
        handle = raw.FPDFTextObj_GetFont(obj.raw) if obj is not None else None
        address = ctypes.cast(handle, ctypes.c_void_p).value if handle else 0
        if len(text) == 1 and 0xF020 <= ord(text) <= 0xF0FE and SYMBOL_FONT.search(font):
            text = SYMBOL_ENCODING.get(ord(text) - 0xF000, text)
        x, y = ctypes.c_double(), ctypes.c_double()
        raw.FPDFText_GetCharOrigin(textpage, index, x, y)
        left, bottom, right, top = textpage.get_charbox(index)
        if not all(math.isfinite(v) for v in (left, bottom, right, top, size)):
            continue
        char = {
            "index": index, "text": text, "size": size, "y": y.value,
            "x": (left + right) / 2, "cy": (bottom + top) / 2,
            "hyphen": raw.FPDFText_IsHyphen(textpage, index) == 1,
            "left": left, "right": right, "top": top, "bottom": bottom, "page": number,
            # Two embedded fonts can share a name and map their glyphs differently.
            "font": f"{font}/{flags.value}/{address:x}",
            "icon": any(
                x0 <= (left + right) / 2 <= x1 and y0 <= (bottom + top) / 2 <= y1
                for x0, y0, x1, y1 in icons
            ),
        }
        if raw.FPDFText_GetLooseCharBox(textpage, index, loose):
            char.update(advanceLeft=loose.left, advanceRight=loose.right)
        chars.append(char)
    return chars


# A drawn checkbox: a small, square, stroked outline (PDF points).
CHECKBOX_SIDE = (4.0, 20.0)


def _squares(paths) -> list[tuple[float, float, float, float]]:
    """Boxes of small square outlines on a page, as (left, bottom, right, top)."""
    import pypdfium2.raw as raw

    found = []
    for obj in paths:
        left, bottom, right, top = obj.get_bounds()
        width, height = right - left, top - bottom
        if not (CHECKBOX_SIDE[0] <= width <= CHECKBOX_SIDE[1]
                and CHECKBOX_SIDE[0] <= height <= CHECKBOX_SIDE[1]):
            continue
        if abs(width - height) > 0.2 * max(width, height):
            continue
        fill, stroke = ctypes.c_int(), ctypes.c_int()
        raw.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill), ctypes.byref(stroke))
        # A filled square is a bullet or a swatch; an outline is a box to tick. Its path
        # is a closed rectangle: four sides (a tick or a cross inside is not).
        if not stroke.value or fill.value or raw.FPDFPath_CountSegments(obj.raw) < 4:
            continue
        found.append((left, bottom, right, top))
    # A box drawn inside another (a frame and its inner edge) is one box.
    return [
        box for box in found
        if not any(other != box and _overlap(box, other) > 0.9 and _area(other) > _area(box)
                   for other in found)
    ]


def _area(box: tuple) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def _form_fields(pdf, page) -> list[dict]:
    """Interactive form widgets on a page: checkboxes, radio buttons and filled-in text."""
    import pypdfium2.raw as raw

    formenv = getattr(pdf, "formenv", None)
    if formenv is None:
        return []
    found = []
    for position in range(raw.FPDFPage_GetAnnotCount(page.raw)):
        annotation = raw.FPDFPage_GetAnnot(page.raw, position)
        try:
            if raw.FPDFAnnot_GetSubtype(annotation) != raw.FPDF_ANNOT_WIDGET:
                continue
            kind = raw.FPDFAnnot_GetFormFieldType(formenv.raw, annotation)
            rect = raw.FS_RECTF()
            raw.FPDFAnnot_GetRect(annotation, rect)
            box = (min(rect.left, rect.right), min(rect.bottom, rect.top),
                   max(rect.left, rect.right), max(rect.bottom, rect.top))
            name = _annotation_text(raw.FPDFAnnot_GetFormFieldName, formenv.raw, annotation)
            if kind in {raw.FPDF_FORMFIELD_CHECKBOX, raw.FPDF_FORMFIELD_RADIOBUTTON}:
                found.append({
                    "box": box, "field": name,
                    "kind": "radio" if kind == raw.FPDF_FORMFIELD_RADIOBUTTON else "checkbox",
                    "checked": bool(raw.FPDFAnnot_IsChecked(formenv.raw, annotation)),
                    "value": _annotation_text(
                        raw.FPDFAnnot_GetFormFieldExportValue, formenv.raw, annotation
                    ),
                })
            elif kind in {raw.FPDF_FORMFIELD_TEXTFIELD, raw.FPDF_FORMFIELD_COMBOBOX}:
                value = _annotation_text(raw.FPDFAnnot_GetFormFieldValue, formenv.raw, annotation)
                if value.strip():
                    found.append({"box": box, "field": name, "kind": "text", "value": value})
        finally:
            raw.FPDFPage_CloseAnnot(annotation)
    return found


def _annotation_text(function, formenv, annotation) -> str:
    length = function(formenv, annotation, None, 0)
    if length <= 2:
        return ""
    buffer = ctypes.create_string_buffer(length)
    function(formenv, annotation, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort)), length)
    return buffer.raw[: length - 2].decode("utf-16-le", errors="replace")


CHECKED, UNCHECKED = "☒", "☐"


def _box_state(pdf, number: int, box: tuple, cache: dict) -> bool | None:
    """Whether ink marks the inside of a printed box; None when the box is not drawn."""
    if number not in cache:
        page = pdf[number - 1]
        try:
            image = page.render(scale=4.0, grayscale=True).to_numpy()
        finally:
            page.close()
        cache[number] = image[..., 0] if image.ndim == 3 else image
    image = cache[number]
    height = image.shape[0] / 4.0
    left, bottom, right, top = box
    x0, x1 = int(left * 4), int(math.ceil(right * 4))
    y0, y1 = int((height - top) * 4), int(math.ceil((height - bottom) * 4))
    crop = image[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if crop.shape[0] < 6 or crop.shape[1] < 6:
        return None
    ink = crop < 160
    inset_y, inset_x = max(2, crop.shape[0] // 5), max(2, crop.shape[1] // 5)
    inside = ink[inset_y:-inset_y, inset_x:-inset_x]
    return bool(inside.size and inside.mean() > 0.06)


def _place_checkboxes(pdf, document: dict, by_page, squares, fields) -> list[dict]:
    """Put each selection where it is printed: ☒ or ☐ before its option's text.

    Sources, most authoritative first: interactive form widgets (their own state), then
    drawn square outlines whose inside is or is not marked in the page pixels. A glyph
    printed inside a box (an "X", a tick, the widget's own appearance) is the mark, so it
    leaves the text. A box in a table goes into the cell at its row and column, so a
    row's answer stays under its column; elsewhere it becomes a glyph of its line, before
    the option it belongs to. Filled-in text fields add their value where the field is.
    """
    records: list[dict] = []
    cache: dict = {}
    cells: dict[tuple[int, int, int], list[tuple[float, str, dict]]] = {}
    for number, chars in by_page.items():
        found = [{**widget, "source": "form-field"} for widget in fields.get(number, [])]
        for box in squares.get(number, []):
            if any(_overlap(box, other["box"]) > 0.4 for other in found):
                continue
            inner = [c for c in chars if _inside(c, box)]
            checked = _box_state(pdf, number, box, cache)
            if checked is None or not (
                inner or _beside_text(box, chars) or _grid_position(document, number, box)
            ):
                continue
            found.append({"box": box, "kind": "checkbox", "checked": checked, "source": "drawn"})
        for entry in sorted(found, key=lambda e: (-e["box"][3], e["box"][0])):
            box = entry["box"]
            record = {key: entry[key] for key in ("kind", "source", "field", "value", "checked")
                      if entry.get(key) not in (None, "")}
            record.update(page=number, box=[round(v, 2) for v in box], placed="none")
            if entry["kind"] != "text":
                for char in chars:
                    if _inside(char, box):
                        char["text"] = ""
                text = CHECKED if entry["checked"] else UNCHECKED
            else:
                text = entry["value"]
            records.append(record)
            if position := _grid_position(document, number, box):
                cells.setdefault(position, []).append((box[0], text, record))
            else:
                _insert_glyph(chars, box, text, record)
    tables = document.get("tables") or []
    for (table, row, column), marks in cells.items():
        data = tables[table].setdefault("data", {})
        found = [
            cell for cell in data.setdefault("table_cells", [])
            if (cell.get("start_row_offset_idx"), cell.get("start_col_offset_idx")) == (row, column)
        ]
        cell = found[0] if found else None
        if cell is None:
            # The layout parser kept no cell where the box is printed; the box is one.
            cell = {
                "text": "", "start_row_offset_idx": row, "end_row_offset_idx": row + 1,
                "start_col_offset_idx": column, "end_col_offset_idx": column + 1,
                "gunther_cell": "drawn box",
            }
            data["table_cells"].append(cell)
        texts = [text for _, text, _ in sorted(marks, key=lambda mark: mark[0])]
        cell["text"] = " ".join([*texts, str(cell.get("text") or "")]).strip()
        cell.setdefault("gunther_marks", []).extend(record for _, _, record in marks)
        for _, _, record in marks:
            record.update(placed="table-cell", row=row, column=column)
    return records


def _grid_position(document: dict, number: int, box: tuple) -> tuple[int, int, int] | None:
    """(table, row, column) of a box printed inside a table's grid on this page.

    Rows and columns are where the layout parser's cells stand: a box belongs to the row
    whose cells' middle is nearest its own, if nearer than half the way to the next row,
    and likewise to a column.
    """
    middle = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    height = document.get("pages", {}).get(str(number), {}).get("size", {}).get("height")
    for index, table in enumerate(document.get("tables") or []):
        provs = [p for p in table.get("prov") or [] if p.get("page_no") == number]
        if not provs or not height:
            continue
        frame = provs[0].get("bbox") or {}
        if not all(key in frame for key in ("l", "t", "r", "b")):
            continue
        low, high = sorted(_points_up(frame, height))
        if not (frame["l"] - 1 <= middle[0] <= frame["r"] + 1 and low - 1 <= middle[1] <= high + 1):
            continue
        rows: dict[int, list[float]] = {}
        columns: dict[int, list[float]] = {}
        for cell in (table.get("data") or {}).get("table_cells") or []:
            bbox = cell.get("bbox") or {}
            if not all(key in bbox for key in ("l", "t", "r", "b")):
                continue
            bottom, top = sorted(_points_up(bbox, height))
            for row in range(
                cell.get("start_row_offset_idx", 0), cell.get("end_row_offset_idx", 0)
            ):
                rows.setdefault(row, []).append((bottom + top) / 2)
            for column in range(
                cell.get("start_col_offset_idx", 0), cell.get("end_col_offset_idx", 0)
            ):
                columns.setdefault(column, []).append((bbox["l"] + bbox["r"]) / 2)
        row = _nearest_line({k: sorted(v)[len(v) // 2] for k, v in rows.items()}, middle[1])
        column = _nearest_line(
            {k: sorted(v)[len(v) // 2] for k, v in columns.items()}, middle[0]
        )
        if row is not None and column is not None:
            return index, row, column
    return None


def _points_up(bbox: dict, height: float) -> tuple[float, float]:
    """A layout box's top and bottom in PDF points, y up."""
    top, bottom = bbox["t"], bbox["b"]
    if str(bbox.get("coord_origin", "TOPLEFT")).upper().startswith("TOP"):
        top, bottom = height - top, height - bottom
    return top, bottom


def _nearest_line(centres: dict[int, float], value: float) -> int | None:
    """The index whose centre is nearest ``value``, if nearer than half its neighbours' gap."""
    if not centres:
        return None
    ordered = sorted(centres.items(), key=lambda item: item[1])
    best = min(range(len(ordered)), key=lambda i: abs(ordered[i][1] - value))
    gaps = [
        abs(ordered[best][1] - ordered[other][1])
        for other in (best - 1, best + 1) if 0 <= other < len(ordered)
    ]
    reach = min(gaps) / 2 if gaps else float("inf")
    return ordered[best][0] if abs(ordered[best][1] - value) <= reach else None


def _insert_glyph(chars: list[dict], box: tuple, text: str, record: dict) -> None:
    """Add a selection (or an answer) as a glyph of its line, before the text after it."""
    line = [c for c in chars if _same_line(c, box)]
    after = [c for c in line if c["left"] >= box[0] - 0.5]
    mark = {key: record[key] for key in ("kind", "source", "checked", "field") if key in record}
    glyph = {
        "index": -1 - len(chars), "text": text, "left": box[0], "right": box[2],
        "top": box[3], "bottom": box[1], "x": (box[0] + box[2]) / 2,
        "hyphen": False, "icon": False, "form": mark, "record": record,
    }
    if line:
        neighbour = min(after, key=lambda c: c["left"]) if after else max(
            line, key=lambda c: c["right"]
        )
        glyph.update(size=neighbour["size"], y=neighbour["y"], cy=neighbour["cy"],
                     page=neighbour["page"], owner=neighbour["index"])
        position = chars.index(neighbour) + (0 if after else 1)
    else:
        height = box[3] - box[1]
        glyph.update(size=height, y=box[1] + 0.15 * height, cy=(box[1] + box[3]) / 2,
                     page=chars[0]["page"] if chars else 0)
        position = len(chars)
    chars.insert(position, glyph)
    if after:
        chars.insert(position + 1, {**glyph, "text": " ", "form": None, "record": None,
                                    "index": glyph["index"] - 1})


def _comparable(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _table_cells(document: dict, by_page: dict[int, list[dict]]) -> None:
    """Table cells read from their own glyphs when those differ from the layout text.

    The layout parser reads cells with its own text decoder, so a misnamed glyph or a
    selection drawn in a cell would be lost there. A cell whose glyphs carry a mark is
    rebuilt from them when both readings agree; otherwise it is flagged unverified.
    """
    for table in document.get("tables") or []:
        pages = [prov.get("page_no") for prov in table.get("prov") or []]
        if len(set(pages)) != 1:
            continue
        number = pages[0]
        height = document.get("pages", {}).get(str(number), {}).get("size", {}).get("height")
        chars = by_page.get(number, [])
        for cell in (table.get("data") or {}).get("table_cells") or []:
            bbox = cell.get("bbox") or {}
            if not height or not all(key in bbox for key in ("l", "t", "r", "b")):
                continue
            top, bottom = bbox["t"], bbox["b"]
            if str(bbox.get("coord_origin", "TOPLEFT")).upper().startswith("TOP"):
                top, bottom = height - top, height - bottom
            low, high = min(top, bottom), max(top, bottom)
            inside = [
                c for c in chars
                if bbox["l"] - 0.5 <= c["x"] <= bbox["r"] + 0.5
                and low - 0.5 <= c["cy"] <= high + 0.5
            ]
            if not any(c.get("glyph") or c.get("form") for c in inside):
                continue
            native = _native_text(inside)
            original = str(cell.get("text") or "")
            marks = native.get("marks", [])
            # The marks themselves are not in the layout text; compare the rest.
            written = "".join(
                char for index, char in enumerate(native.get("text", ""))
                if not any(
                    m["start"] <= index < m["end"] and m["mark"] == "checkbox" for m in marks
                )
            )
            left, right = _comparable(original), _comparable(written)
            agree = (not left and not right) or (
                left and SequenceMatcher(None, left, right, autojunk=False).ratio() >= 0.85
            )
            if agree:
                cell["text"] = native.get("text", "")
                cell["gunther_marks"] = marks
                for char in inside:
                    if char.get("record"):
                        char["record"]["placed"] = "table-cell"
            else:
                cell["gunther_unverified"] = True


def _overlap(a: tuple, b: tuple) -> float:
    """Shared area over the smaller box."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return width * height / smaller if smaller > 0 else 0.0


def _inside(char: dict, box: tuple) -> bool:
    return (char["text"].strip() != "" and box[0] <= char["x"] <= box[2]
            and box[1] <= char["cy"] <= box[3])


def _same_line(char: dict, box: tuple) -> bool:
    middle = (box[1] + box[3]) / 2
    return char["text"].strip() != "" and abs(char["cy"] - middle) < max(
        0.6 * max(char["size"], 1.0), 0.4 * (box[3] - box[1])
    )


def _beside_text(box: tuple, chars: list[dict]) -> bool:
    """Text on the box's line within a few ems: the option the box is ticked for."""
    reach = 3.0 * max(box[3] - box[1], 6.0)
    return any(
        _same_line(c, box)
        and (0 <= c["left"] - box[2] <= reach or 0 <= box[0] - c["right"] <= reach)
        for c in chars
    )


# A printed equation number: "(3)", "(2.1a)", "(A4)", set apart at the line's end.
EQUATION_NUMBER = re.compile(r"\(\s*[\w.,\-–′']{1,8}\s*\)")


def _analyse_formulas(document: dict, by_page: dict[int, list[dict]], rules) -> None:
    """Whether a formula's glyphs read as one line of text, and its printed number.

    A formula printed in two dimensions (a fraction bar, stacked rows, a large operator
    with limits, a radical's bar) has no faithful one-line transcription; the next layer
    shows its picture as the formula and keeps the glyphs only as a text layer.
    """
    for item in document.get("texts") or []:
        if item.get("label") != "formula":
            continue
        chars = []
        for prov in item.get("prov") or []:
            number, box = prov.get("page_no"), prov.get("bbox") or {}
            if not all(key in box for key in ("l", "t", "r", "b")):
                continue
            top, bottom = box["t"], box["b"]
            if str(box.get("coord_origin", "BOTTOMLEFT")).upper().startswith("TOP"):
                height = document["pages"][str(number)]["size"]["height"]
                top, bottom = height - top, height - bottom
            low, high = min(top, bottom), max(top, bottom)
            chars.extend(
                c for c in by_page.get(number, [])
                if box["l"] - 1 <= c["x"] <= box["r"] + 1 and low - 1 <= c["cy"] <= high + 1
            )
        # Spaces belong to the transcription; only printed glyphs give the geometry.
        spaces = [c for c in chars if not c["text"].strip()]
        chars = [c for c in chars if c["text"].strip()]
        if not chars:
            item["gunther_formula"] = {"layout": "image-only", "text": ""}
            continue
        sizes = sorted(c["size"] for c in chars if c["size"] > 0)
        size = sizes[len(sizes) // 2] if sizes else 10.0
        full = [c for c in chars if c["size"] >= 0.85 * size]
        baselines: list[float] = []
        for char in sorted(full, key=lambda c: c["y"]):
            if not baselines or char["y"] - baselines[-1] > 0.45 * size:
                baselines.append(char["y"])
        left, right = min(c["left"] for c in chars), max(c["right"] for c in chars)
        low, high = min(c["bottom"] for c in chars), max(c["top"] for c in chars)
        page = chars[0]["page"]
        ruled = any(
            x1 - x0 > 0.6 * size and x0 < right and x1 > left and low < (b + t) / 2 < high
            for x0, b, x1, t in rules.get(page, [])
        )
        large = any(
            c["size"] > 1.3 * size or (c["top"] - c["bottom"]) > 1.6 * size for c in chars
        )
        # Equation number: the last glyphs, an em or more apart, in parentheses.
        ordered = sorted(chars, key=lambda c: c["left"])
        number = None
        for cut in range(len(ordered) - 1, 0, -1):
            if ordered[cut]["left"] - ordered[cut - 1]["right"] >= 1.2 * size:
                tail = "".join(c["text"] for c in ordered[cut:])
                if EQUATION_NUMBER.fullmatch(tail.strip()):
                    number = tail.strip()
                    numbered = {id(c) for c in ordered[cut:]}
                    chars = [c for c in chars if id(c) not in numbered]
                break
        last = max(c["index"] for c in chars)
        spoken = chars + [c for c in spaces if c["index"] < last]
        native = _native_text(sorted(spoken, key=lambda c: (c["page"], c["index"])), rules)
        issues = [m for m in native.get("marks", []) if m["mark"] in {"uncertain", "decoded"}]
        layout = "two-dimensional" if (len(baselines) > 1 or ruled or large) else "linear"
        item["gunther_formula"] = {
            "layout": layout,
            "text": native.get("text", ""),
            "scripts": native.get("scripts", []),
            "marks": native.get("marks", []),
            "glyphs": "uncertain" if any(m["mark"] == "uncertain" for m in issues)
            else "decoded" if issues else "verified",
            **({"number": number} if number else {}),
        }



def render_visual_pages(document: dict, path: str, directory, *, pixels: int = 16_000_000) -> dict:
    """Render pages that hold figures or formulas sharply enough for reading.

    The layout parser's page images are sized for detection. Crops from them
    are soft on high-density screens, so visual pages are rendered again from
    the PDF itself, bounded by a pixel budget per page.
    """
    import pypdfium2 as pdfium

    wanted = {
        prov.get("page_no")
        for key in ("pictures", "texts")
        for item in document.get(key, [])
        if key == "pictures" or item.get("label") == "formula"
        for prov in item.get("prov", [])
    }
    files = {}
    with pdfium.PdfDocument(path) as pdf:
        for number in sorted(n for n in wanted if isinstance(n, int) and 0 < n <= len(pdf)):
            page = pdf[number - 1]
            try:
                width, height = page.get_size()
                scale = min(3.0, math.sqrt(pixels / max(1.0, width * height)))
                image = page.render(scale=scale).to_pil()
                name = f"page-{number}-visual.png"
                image.save(directory / name, "PNG")
                files[number] = name
            finally:
                page.close()
    return files
