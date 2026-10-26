"""Check a PDF's text layer against the glyph shapes it prints.

Every glyph in a PDF is named by a character in the text layer (the font's
ToUnicode map or its encoding). Some fonts name their glyphs wrongly: a math
font can name its minus sign "2" and its parentheses "ð" and "Þ", so a printed
"−0.5" reads "20.5". The printed shape is the evidence of what the character is.

For each (font, named character) pair, one or two printed samples are cut from
the rendered page and compared with reference shapes of candidate characters,
drawn by PDFium's built-in standard fonts. No per-font tables, no model:

- ``verified``: the shape fits the named character (most glyphs);
- ``decoded``: the shape cannot be the named character and exactly one candidate
  class fits; the text takes that character and keeps a record of the change;
- ``uncertain``: the font has been shown to misname glyphs (or names a
  private-use or control code) and no candidate fits clearly; the text shows
  U+FFFD there and the record keeps what the text layer said.

A font is only distrusted when one of its glyphs is shown to be misnamed, so an
unusual but honest typeface (a script or display face) is never marked.
"""

from __future__ import annotations

import ctypes
import math
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

REPLACEMENT = "�"
REFERENCE_FONTS = (
    "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
    "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Courier", "Symbol", "ZapfDingbats",
)
CANDIDATES = (
    list("0123456789")
    + list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    + list("!\"#%&'()*+,-./:;<=>?[]{}|~^_@\\")
    + list("−×÷±∓·•°′″≤≥≠≈≡∼∝∞∂∇∑∏∫√∈∉∀∃∅∩∪⊂⊃⊆⊇→←↔⇒⇐⇔↑↓〈〉…–—‘’“”§¶†‡")
    + list("αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΥΦΨΩϑϕϖς")
    + list("✓✔✗✘■□●○◆◇★❑❒❏❐")
    + ["fi", "fl", "ff", "ffi", "ffl"]
)
# Shapes the references cannot tell apart; the first is used when the shape decides.
SAME_SHAPE = (
    "−–‒", "-‐", "—―", "·⋅", "µμ", "∼~", "′'’", "″\"”", "✓✔", "✗✘", "□❏❐",
    "oο○", "OΟ0", "lI|1", "xχ", "Σ∑", "Π∏", "Δ∆", "vν", "uυ", "pρ", "κk", "γy", "ιi",
    "φϕ", "θϑ", "πϖ",
)
SHAPE_CLASS = {char: index for index, group in enumerate(SAME_SHAPE) for char in group}
# Bars share one shape and differ in length (ems of ink): hyphen, minus or en dash, em
# dash. Lengths between the bands are not read as either.
BARS = {"-": (0.0, 0.42), "−": (0.45, 0.78), "—": (0.85, 9.0)}
BAR_CHARACTERS = frozenset("-‐−–‒—―")
# Characters whose glyphs vary too much between typefaces to judge from references.
UNJUDGED = frozenset("aglI1|'’‘\"“”`^~_")

GRID = 24
VERIFIED, DECODED, UNCERTAIN = "verified", "decoded", "uncertain"
# Scores, calibrated on correctly named glyphs of 40 typefaces and on misnamed ones.
BLATANT = 0.05  # the named character shares nothing with the print
GROSS = 0.12  # the named character is nothing like the print
FAIR = 0.45  # another character is like it
FITS = 0.3  # in a font shown to misname glyphs, a named character below this is doubted
DOUBT = 0.2  # … when another character fits this much better
CLEAR = 0.55  # a candidate decodes the glyph at or above this score
MARGIN = 0.08  # and beats every other shape class by this much


def checkable(text: str) -> bool:
    """Characters the references cover: Latin, Greek, digits, punctuation, math, marks,
    plus private-use and control codes, which never name a printed character."""
    if not text or text.isspace():
        return False
    if suspicious(text):
        return True
    for char in text:
        code = ord(char)
        if not (
            code < 0x250 or 0x370 <= code < 0x400 or 0x2000 <= code < 0x2400
            or 0x2500 <= code < 0x2800 or 0x27E0 <= code < 0x2800 or 0xFB00 <= code < 0xFB07
        ):
            return False
    return True


def suspicious(text: str) -> bool:
    """A private-use, control or unassigned code: the text layer names no character."""
    return any(
        unicodedata.category(char) in {"Co", "Cc", "Cn", "Cs"} or char == REPLACEMENT
        for char in text
    )


@dataclass
class Shape:
    """Ink of one glyph: its bitmap on a fixed grid and its place on the baseline, in ems."""

    grid: np.ndarray
    width: float
    height: float
    top: float
    bottom: float

    @property
    def blank(self) -> bool:
        return self.width <= 0 or self.height <= 0


def shape_of(pixels: np.ndarray, em: float, baseline: float) -> Shape | None:
    """The ink in a grey crop; ``baseline`` is the baseline's row in it, ``em`` in pixels."""
    if pixels.size == 0 or em <= 0:
        return None
    border = np.concatenate([pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]])
    background = float(np.median(border))
    ink = np.abs(pixels.astype(np.float32) - background) > 70
    rows, columns = np.nonzero(ink)
    if rows.size == 0:
        return Shape(np.zeros((GRID, GRID), np.float32), 0, 0, 0, 0)
    top, bottom = rows.min(), rows.max() + 1
    left, right = columns.min(), columns.max() + 1
    cut = ink[top:bottom, left:right].astype(np.float32)
    height, width = cut.shape
    # The ink box is stretched to the grid: proportions are compared as geometry, so
    # a narrow monospaced "m" and a wide one still share their strokes.
    grid = _resize(cut, GRID)
    return Shape(
        grid=_blur(grid),
        width=width / em,
        height=height / em,
        top=(baseline - top) / em,
        bottom=(baseline - bottom) / em,
    )


def _resize(bitmap: np.ndarray, size: int) -> np.ndarray:
    """Area-average a bitmap down (or repeat it up) to ``size`` × ``size``."""
    rows = np.linspace(0, bitmap.shape[0], size + 1)
    columns = np.linspace(0, bitmap.shape[1], size + 1)
    output = np.zeros((size, size), np.float32)
    for row in range(size):
        r0 = int(rows[row])
        r1 = max(r0 + 1, int(math.ceil(rows[row + 1])))
        for column in range(size):
            c0 = int(columns[column])
            c1 = max(c0 + 1, int(math.ceil(columns[column + 1])))
            output[row, column] = bitmap[r0:r1, c0:c1].mean()
    return output


def _blur(grid: np.ndarray) -> np.ndarray:
    padded = np.pad(grid, 1)
    total = sum(
        padded[1 + dy : 1 + dy + grid.shape[0], 1 + dx : 1 + dx + grid.shape[1]]
        for dy in (-1, 0, 1) for dx in (-1, 0, 1)
    )
    return (grid * 2 + total) / 11


def similarity(a: Shape, b: Shape) -> float:
    """1 for the same shape in the same place on the line, toward 0 for different ones."""
    if a.blank or b.blank:
        return 0.0
    x, y = a.grid - a.grid.mean(), b.grid - b.grid.mean()
    norm = float(np.linalg.norm(x) * np.linalg.norm(y))
    shape = max(0.0, float((x * y).sum()) / norm) if norm else 0.0

    def ratio(p: float, q: float) -> float:
        return abs(math.log((p + 0.03) / (q + 0.03)))

    place = (
        1.0 * ratio(a.height, b.height) + 0.6 * ratio(a.width, b.width)
        + 2.0 * abs(a.top - b.top) + 2.0 * abs(a.bottom - b.bottom)
    )
    return shape * math.exp(-place)


@dataclass
class References:
    """Reference shapes of the candidate characters, drawn by PDFium's standard fonts."""

    shapes: dict[str, list[Shape]] = field(default_factory=dict)
    _matrix: tuple | None = field(default=None, repr=False)

    def established(self, text: str) -> bool:
        """Drawn by several standard text fonts, so its shape is known across designs.
        Greek and mathematical characters come from the one Symbol font and vary too much
        between typefaces to prove a font wrong."""
        return len(self.shapes.get(text, ())) >= 3

    def add(self, text: str, shape: Shape) -> None:
        self.shapes.setdefault(text, []).append(shape)
        self._matrix = None

    def scores(self, sample: Shape) -> dict[str, float]:
        """``similarity`` of the sample to every reference, best per character."""
        if sample.blank or not self.shapes:
            return {}
        if self._matrix is None:
            texts, grids, places = [], [], []
            for text, found in self.shapes.items():
                for shape in found:
                    vector = (shape.grid - shape.grid.mean()).ravel()
                    norm = float(np.linalg.norm(vector))
                    texts.append(text)
                    grids.append(vector / norm if norm else vector)
                    places.append((shape.height, shape.width, shape.top, shape.bottom))
            self._matrix = (texts, np.array(grids), np.array(places))
        texts, grids, places = self._matrix
        vector = (sample.grid - sample.grid.mean()).ravel()
        norm = float(np.linalg.norm(vector))
        shape = np.maximum(0.0, grids @ (vector / norm)) if norm else np.zeros(len(texts))
        place = (
            1.0 * np.abs(np.log((sample.height + 0.03) / (places[:, 0] + 0.03)))
            + 0.6 * np.abs(np.log((sample.width + 0.03) / (places[:, 1] + 0.03)))
            + 2.0 * np.abs(sample.top - places[:, 2]) + 2.0 * np.abs(sample.bottom - places[:, 3])
        )
        values = shape * np.exp(-place)
        best: dict[str, float] = {}
        for text, value in zip(texts, values.tolist(), strict=True):
            if value > best.get(text, -1.0):
                best[text] = value
        return best

    def best(self, sample: Shape, text: str) -> float | None:
        found = self.shapes.get(text)
        if not found:
            return None
        return max(similarity(sample, reference) for reference in found)


_REFERENCES: References | None = None

# Adobe's published encodings of the two symbol fonts every PDF reader carries, so a
# reference can be drawn by code whatever Unicode mapping the font program has.
SYMBOL_ENCODING = dict(zip(
    range(0x20, 0x7F),
    " !∀#∃%&∋()∗+,−./0123456789:;<=>?≅ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ[∴]⊥_‾"
    "αβχδεφγηιϕκλμνοπθρστυϖωξψζ{|}∼",
    strict=True,
)) | dict(zip(
    range(0xA1, 0xFF),
    "ϒ′≤⁄∞ƒ♣♦♥♠↔←↑→↓°±″≥×∝∂•÷≠≡≈…⏐⎯↵ℵℑℜ℘⊗⊕∅∩∪⊃⊇⊄⊂⊆∈∉∠∇®©™∏√⋅¬∧∨⇔⇐⇑⇒⇓◊〈®©™∑⎛⎜⎝⎡⎢⎣⎧⎨⎩⎪"
    "�〉∫⌠⎮⌡⎞⎟⎠⎤⎥⎦⎫⎬⎭",
    strict=True,
))
DINGBATS = {"✓": 0x33, "✔": 0x34, "✗": 0x37, "✘": 0x38, "★": 0x48, "●": 0x6C, "■": 0x6E,
            "❏": 0x6F, "❐": 0x70, "❑": 0x71, "❒": 0x72, "◆": 0x75}
BY_CODE = {
    "Symbol": {char: code for code, char in SYMBOL_ENCODING.items() if char != REPLACEMENT},
    "ZapfDingbats": DINGBATS,
}


_TRIED: set[str] = set()


def references(named: set[str] | frozenset[str] = frozenset()) -> References:
    """Every candidate, and each named character asked for, in every standard font that
    has it. Drawn once per process; a character no standard font has stays absent."""
    global _REFERENCES
    if _REFERENCES is None:
        _REFERENCES = _draw_references(CANDIDATES)
        _TRIED.update(CANDIDATES)
    missing = sorted(
        text for text in named if text not in _TRIED and not suspicious(text) and text.strip()
    )
    if missing:
        _TRIED.update(missing)
        extra = _draw_references(missing)
        for text, shapes in extra.shapes.items():
            for shape in shapes:
                _REFERENCES.add(text, shape)
    return _REFERENCES


def _draw_references(candidates) -> References:
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    size, step = 40.0, 64.0
    columns = 30
    rows = math.ceil(len(candidates) * len(REFERENCE_FONTS) / columns) + 1
    width, height = columns * step + 40, rows * step + 40
    document = raw.FPDF_CreateNewDocument()
    page = raw.FPDFPage_New(document, 0, width, height)
    # (text, x, y, drawn by its code in a symbol font)
    placed: list[tuple[str, float, float, bool]] = []
    buffers = []
    for name in REFERENCE_FONTS:
        font = raw.FPDFText_LoadStandardFont(document, name.encode())
        codes = BY_CODE.get(name)
        for text in candidates:
            if codes is not None and text not in codes:
                continue
            slot = len(placed)
            x = 20 + (slot % columns) * step
            y = height - 20 - (slot // columns + 1) * step + 16
            obj = raw.FPDFPageObj_CreateTextObj(document, font, size)
            if codes is not None:
                code = (ctypes.c_uint * 1)(codes[text])
                raw.FPDFText_SetCharcodes(obj, code, 1)
            else:
                buffer = ctypes.create_string_buffer((text + "\0").encode("utf-16-le"))
                buffers.append(buffer)
                raw.FPDFText_SetText(obj, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort)))
            raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
            raw.FPDFPage_InsertObject(page, obj)
            placed.append((text, x, y, codes is not None))
    raw.FPDFPage_GenerateContent(page)
    pdf = pdfium.PdfDocument(document)
    try:
        rendered = pdf[0]
        textpage = rendered.get_textpage()
        image = rendered.render(scale=1.0, grayscale=True).to_numpy()
        if image.ndim == 3:
            image = image[..., 0]
        # Which characters each slot really drew: a font that cannot encode one draws another.
        drawn: dict[int, str] = defaultdict(str)
        for index in range(textpage.count_chars()):
            char = textpage.get_text_range(index, 1)
            if not char.strip():
                continue
            x, y = ctypes.c_double(), ctypes.c_double()
            raw.FPDFText_GetCharOrigin(textpage, index, x, y)
            column = round((x.value - 20) / step)
            row = round((height - 4 - y.value) / step) - 1
            drawn[row * columns + column] += char
        result = References()
        for slot, (text, x, y, by_code) in enumerate(placed):
            got = drawn.get(slot, "")
            if not by_code and (
                unicodedata.normalize("NFKC", got) != unicodedata.normalize("NFKC", text)
            ):
                continue
            crop = image[int(height - y - size * 1.1) : int(height - y + size * 0.45),
                         int(x - 4) : int(x + step - 8)]
            shape = shape_of(crop, size, size * 1.1)
            if shape is not None and not shape.blank:
                result.add(text, shape)
        textpage.close()
        rendered.close()
    finally:
        pdf.close()
    return result


@dataclass
class Verdict:
    state: str
    text: str
    named: str
    score: float | None = None
    best: str | None = None
    best_score: float = 0.0
    rival: float = 0.0
    established: bool = False


def _class(text: str | None) -> object:
    return SHAPE_CLASS.get(text, text)


def judge(sample: Shape, named: str, refs: References | None = None) -> Verdict:
    """Scores of one printed sample: the named character, the best candidate, its rival."""
    refs = refs or references()
    found = refs.scores(sample)
    named_score = None if suspicious(named) else found.get(named)
    if named_score is None and not suspicious(named):
        # A compatibility form (a ligature, a full-width letter) is judged by its parts.
        plain = unicodedata.normalize("NFKC", named)
        if plain != named:
            named_score = found.get(plain)
    scores = sorted(
        ((score, candidate) for candidate in CANDIDATES
         if (score := found.get(candidate)) is not None),
        reverse=True,
    )
    best_score, best = scores[0] if scores else (0.0, None)
    if best in BAR_CHARACTERS:
        # Which bar is decided by its length; the rival is the best other shape.
        best = _bar(sample)
        rival = next((score for score, candidate in scores
                      if candidate not in BAR_CHARACTERS), 0.0)
    else:
        rival = next((score for score, candidate in scores
                      if _class(candidate) != _class(best)), 0.0)
    established = refs.established(named) or (
        named_score is not None and refs.established(unicodedata.normalize("NFKC", named))
    )
    return Verdict(VERIFIED, named, named, named_score, best, best_score, rival, established)


def misnamed(verdict: Verdict) -> bool:
    """The named character is nothing like the print, and another character is like it."""
    if verdict.best is None or verdict.best_score < FAIR:
        return False
    if suspicious(verdict.named):
        return True
    return (
        verdict.established and verdict.score is not None and verdict.score <= GROSS
        and _class(verdict.best) != _class(verdict.named) and verdict.named not in UNJUDGED
    )


def identified(verdict: Verdict) -> bool:
    """One candidate class fits clearly better than every other, and reading it as that
    class cannot turn one letter or digit into another (a Greek letter printed in a math
    font can look like a Latin one; those stay uncertain)."""
    return (
        verdict.best is not None and verdict.best not in UNJUDGED
        and not (len(verdict.best) == 1 and verdict.best.isascii() and verdict.best.isalnum())
        and verdict.best_score >= CLEAR and verdict.best_score - verdict.rival >= MARGIN
    )


def doubted(verdict: Verdict) -> bool:
    """In a font shown to misname glyphs: another class fits clearly better than the name."""
    return (
        verdict.established and verdict.score is not None and verdict.score < FITS
        and verdict.best is not None and verdict.named not in UNJUDGED
        and _class(verdict.best) != _class(verdict.named)
        and verdict.best_score - verdict.score >= DOUBT
    )


def _bar(sample: Shape) -> str | None:
    """The bar a sample's length makes it, or None between the bands. A minus and an en
    dash have the same length and read as a minus."""
    return next((bar for bar, (low, high) in BARS.items() if low <= sample.width <= high), None)


def _representative(best: str) -> str:
    """One character for a shape class."""
    group = next((g for g in SAME_SHAPE if best in g), None)
    return group[0] if group else best


@dataclass
class Report:
    """Per (font, named character): the verdict applied to every such glyph."""

    verdicts: dict[tuple[str, str], Verdict] = field(default_factory=dict)
    distrusted: set[str] = field(default_factory=set)
    checked: int = 0
    pages: list[int] = field(default_factory=list)

    def summary(self) -> dict:
        decoded = sorted(
            {(v.named, v.text) for v in self.verdicts.values() if v.state == DECODED}
        )
        return {
            "checkedPairs": self.checked,
            "pagesRendered": self.pages,
            "fontsMisnaming": len(self.distrusted),
            "decoded": [{"from": a, "to": b} for a, b in decoded],
            "uncertain": sorted(
                {v.named for v in self.verdicts.values() if v.state == UNCERTAIN}
            ),
        }


def verify(pdf, by_page: dict[int, list[dict]], *, max_pages: int = 12,
           target_em: float = 40.0) -> Report:
    """Judge each (font, character) pair from its printed samples; mark every glyph.

    ``by_page`` holds the glyph dicts ``pdf_native`` collects (``text``, ``font``,
    ``size``, the tight box ``left``/``right``/``top``/``bottom``, the advance
    ``advanceLeft``/``advanceRight`` and the baseline ``y``). Each glyph of a pair that
    is decoded or uncertain gets ``glyph = {"state", "from"}`` and its ``text`` changed.
    """
    report = Report()
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for chars in by_page.values():
        for char in chars:
            text = char.get("text", "")
            if char.get("font") is not None and checkable(text) and char.get("size", 0) > 0:
                groups[(char["font"], text)].append(char)
    if not groups:
        return report
    # Up to two samples per pair, from as few pages as possible, preferring large type.
    wanted: dict[int, list[tuple[tuple[str, str], dict]]] = defaultdict(list)
    per_page: dict[int, int] = defaultdict(int)
    for samples in groups.values():
        for char in samples:
            per_page[char["page"]] += 1
    for key, samples in groups.items():
        ranked = sorted(samples, key=lambda c: (-per_page[c["page"]], -c["size"]))
        for char in ranked[:2]:
            wanted[char["page"]].append((key, char))
    shapes: dict[tuple[str, str], list[Shape]] = defaultdict(list)
    blank: set[tuple[str, str]] = set()
    for number in sorted(wanted, key=lambda page: -len(wanted[page]))[:max_pages]:
        report.pages.append(number)
        page = pdf[number - 1]
        try:
            sizes = sorted(char["size"] for _, char in wanted[number])
            median = sizes[len(sizes) // 2]
            width, height = page.get_size()
            scale = min(target_em / max(median, 1.0), math.sqrt(30e6 / max(1.0, width * height)))
            image = page.render(scale=scale, grayscale=True).to_numpy()
            if image.ndim == 3:
                image = image[..., 0]
            for key, char in wanted[number]:
                shape = _sample(image, char, scale, height)
                if shape is not None and shape.blank:
                    blank.add(key)
                elif shape is not None:
                    shapes[key].append(shape)
        finally:
            page.close()
    refs = references({named for _, named in shapes})
    judged: dict[tuple[str, str], list[Verdict]] = {
        key: [judge(sample, key[1], refs) for sample in samples]
        for key, samples in shapes.items()
    }
    report.checked = len(judged)
    # A font is shown to misname its glyphs by one blatant case or two clear ones.
    evidence: dict[str, list[Verdict]] = defaultdict(list)
    for (font, _), verdicts in judged.items():
        if all(misnamed(v) for v in verdicts):
            evidence[font].append(verdicts[0])
    for font, cases in evidence.items():
        blatant = any(
            (v.score is not None and v.score <= BLATANT or suspicious(v.named))
            and identified(v) for v in cases
        )
        if blatant or len(cases) >= 2:
            report.distrusted.add(font)
    for key, verdicts in judged.items():
        font, named = key
        lying = font in report.distrusted
        texts = {_representative(v.best) for v in verdicts if v.best is not None}
        decodable = all(identified(v) for v in verdicts) and len(texts) == 1
        wrong = all(misnamed(v) for v in verdicts) or (lying and all(doubted(v) for v in verdicts))
        first = verdicts[0]
        if (lying or suspicious(named)) and wrong and decodable:
            first.state, first.text = DECODED, texts.pop()
        elif suspicious(named) or (lying and any(misnamed(v) or doubted(v) for v in verdicts)):
            # Samples that disagree (one name for two shapes) are uncertain too.
            first.state, first.text = UNCERTAIN, REPLACEMENT
        report.verdicts[key] = first
    for key, samples in groups.items():
        verdict = report.verdicts.get(key)
        if verdict is None and key in blank and suspicious(key[1]):
            # A code that prints nothing (a placeholder) carries no text either.
            for char in samples:
                char["text"] = ""
            continue
        if verdict is None and (key[0] in report.distrusted or suspicious(key[1])):
            # Not sampled within the page budget: a lying font's glyph cannot be vouched for.
            verdict = report.verdicts[key] = Verdict(UNCERTAIN, REPLACEMENT, key[1])
        if verdict is None or verdict.state == VERIFIED:
            continue
        for char in samples:
            char["glyph"] = {"state": verdict.state, "from": key[1]}
            char["text"] = verdict.text
    return report


def _sample(image: np.ndarray, char: dict, scale: float, page_height: float) -> Shape | None:
    """Cut one glyph from the page image: its advance across, its tight box up and down."""
    left = max(char["left"], char.get("advanceLeft", char["left"]))
    right = min(char["right"], char.get("advanceRight", char["right"]))
    if right - left < 0.05 * char["size"]:
        left, right = char["left"], char["right"]
    top, bottom = char["top"], char["bottom"]
    pad = 1.5 / scale
    x0, x1 = int((left - pad) * scale), int(math.ceil((right + pad) * scale))
    y0 = int((page_height - top - pad) * scale)
    y1 = int(math.ceil((page_height - bottom + pad) * scale))
    if x1 <= x0 or y1 <= y0:
        return None
    crop = image[max(0, y0) : max(0, y1), max(0, x0) : max(0, x1)]
    baseline = (page_height - char["y"]) * scale - max(0, y0)
    return shape_of(crop, char["size"] * scale, baseline)
