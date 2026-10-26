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
from difflib import SequenceMatcher

# Math fonts of the Advent 3B2 typesetting system declare wrong Unicode values
# for their delimiters and operators (a parenthesis is reported as "ð"). The
# glyphs were checked against their rendered shapes. Keyed by the font's base
# name, so every document set in these fonts reads correctly.
FONT_GLYPHS = {
    "AdvP4C4E74": {
        "ð": "(", "Þ": ")", "½": "[", "Š": "]", "¼": "=", "þ": "+", "À": "−",
        "j": "|", "k": "‖", "f": "{", "g": "}", "2": "∈", "p": "√", "0": "′",
    },
    "AdvP4C4E46": {"P": "∑", "X": "∑", "Z": "∫", "p": "√", "q": "√"},
}
# Fonts named "Symbol" (Adobe's Symbol and its clones, such as SymbolMT) often
# report their glyphs at private-use code points U+F020–U+F0FF, the font's own
# byte codes. Adobe's published Symbol encoding gives each one's character.
SYMBOL_ENCODING = dict(zip(
    range(0x20, 0x7F),
    " !∀#∃%&∋()∗+,−./0123456789:;<=>?≅ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ[∴]⊥_‾"
    "αβχδεφγηιϕκλμνοπθρστυϖωξψζ{|}∼",
    strict=True,
)) | dict(zip(
    range(0xA1, 0xFF),
    "ϒ′≤⁄∞ƒ♣♦♥♠↔←↑→↓°±″≥×∝∂•÷≠≡≈…⏐⎯↵ℵℑℜ℘⊗⊕∅∩∪⊃⊇⊄⊂⊆∈∉∠∇®©™∏√⋅¬∧∨⇔⇐⇑⇒⇓◊〈®©™∑⎛⎜⎝⎡⎢⎣⎧⎨⎩⎪"
    "\ufffd〉∫⌠⎮⌡⎞⎟⎠⎤⎥⎦⎫⎬⎭",
    strict=True,
))
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
    for char in chars:
        text = char["text"]
        if text in {"\r", "\n"}:
            pending_break = True
            continue
        if char.get("hyphen") or text == "\u00ad":
            # A discretionary hyphen printed at the column edge is not a character
            # in the reflowed word. Its glyph and placement remain in the PDF.
            if output and output[-1][0] == " ":
                output.pop()
            output.append(("", "join", {}))
            continue
        if not text or (ord(text[0]) < 32 and not text.isspace()):
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
    offset = 0
    for char, script, source in output:
        if source.get("lineStart"):
            lines.append(offset)
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
    if lines:
        native["lineStarts"] = lines
    if icons:
        native["icons"] = icons
    return native


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
    """Use a native region only when it agrees with the layout text's coverage."""
    import pypdfium2 as pdfium

    by_page: dict[int, list[dict]] = {}
    rules: dict[int, list[tuple]] = {}
    with pdfium.PdfDocument(path) as pdf:
        for number in range(len(pdf)):
            page = pdf[number]
            textpage = page.get_textpage()
            try:
                rules[number + 1] = [
                    box for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_PATH])
                    if (box := obj.get_bounds()) and 0 <= box[3] - box[1] < 1
                    and 3 < box[2] - box[0] < 100
                ]
                icons = _icon_links(pdf, page)
                chars = []
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
                    pdfium.raw.FPDFText_GetFontInfo(textpage, index, name, 128, ctypes.byref(flags))
                    font = name.value.decode("latin-1").rpartition("+")[2]
                    text = FONT_GLYPHS.get(font, {}).get(text, text)
                    if (
                        len(text) == 1 and 0xF020 <= ord(text) <= 0xF0FE
                        and SYMBOL_FONT.search(font)
                    ):
                        text = SYMBOL_ENCODING.get(ord(text) - 0xF000, text)
                    x, y = ctypes.c_double(), ctypes.c_double()
                    pdfium.raw.FPDFText_GetCharOrigin(textpage, index, x, y)
                    left, bottom, right, top = textpage.get_charbox(index)
                    if not all(math.isfinite(v) for v in (left, bottom, right, top, size)):
                        continue
                    chars.append({
                        "index": index, "text": text, "size": size, "y": y.value,
                        "x": (left + right) / 2, "cy": (bottom + top) / 2,
                        "hyphen": pdfium.raw.FPDFText_IsHyphen(textpage, index) == 1,
                        "left": left, "right": right, "page": number + 1,
                        "icon": any(
                            x0 <= (left + right) / 2 <= x1 and y0 <= (bottom + top) / 2 <= y1
                            for x0, y0, x1, y1 in icons
                        ),
                    })
                by_page[number + 1] = chars
            finally:
                textpage.close()
                page.close()

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
        # A different reading order, clipped region or scan cannot replace layout/OCR.
        def comparable(text):
            return re.sub(r"[\W_]+", "", text.casefold())
        left, right = comparable(original), comparable(native["text"])
        if not left or len(left) > 20_000 or not 0.8 <= len(right) / len(left) <= 1.2:
            continue
        if SequenceMatcher(None, left, right, autojunk=False).ratio() >= 0.9:
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
                            for key in ("fontSize", "lineStarts", "icons") if key in found
                        },
                    }
                    for found in (
                        _native_text(owned.get((position, part), []), rules)
                        for part in range(len(item["prov"]))
                    )
                ]
            item["gunther_native"] = native


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
