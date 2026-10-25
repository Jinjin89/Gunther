"""Recover native glyphs and scripts inside the layout parser's own text regions.

PDFium is already bundled with the isolated PDF helper. It provides an independent
font decoder and character baselines; no mathematical notation is guessed from text.
"""

from __future__ import annotations

import ctypes
import math
import re
from collections import Counter
from difflib import SequenceMatcher


def _native_text(chars: list[dict], rules: dict | None = None) -> dict:
    sizes = Counter(round(c["size"], 1) for c in chars if c["text"].strip() and c["size"])
    if not sizes:
        return {}
    size = sizes.most_common(1)[0][0]
    baselines = Counter(
        round(c["y"], 1) for c in chars if c["size"] >= size * 0.9 and c["text"].strip()
    )
    # Only baselines supported by several full-sized glyphs establish a text line.
    lines = [y for y, count in baselines.items() if count >= 3]
    output: list[tuple[str, str | None, dict]] = []
    pending_break = False
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
            output.append((text, script, char))
    while output and not output[-1][0].strip():
        output.pop()
    while output and not output[0][0].strip():
        output.pop(0)
    text = "".join(c for c, _, _ in output)
    spans = []
    glyphs = []
    offset = 0
    for char, script, source in output:
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
    return {"text": text, "scripts": spans, "fontSize": size}


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
    owned: dict[tuple[int, int], list[dict]] = {}
    for number, chars in by_page.items():
        for char in chars:
            if found := owner.get((number, char["index"])):
                owned.setdefault(found[1:], []).append(char)

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
