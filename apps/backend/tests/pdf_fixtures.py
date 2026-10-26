"""A small PDF writer for regression fixtures that are designed here, not collected.

It writes what real PDFs contain when they go wrong: embedded TrueType fonts
whose text layer (ToUnicode) names glyphs wrongly, drawn glyph fonts (Type 3)
whose codes are digits or accented letters, vector figures, link and form
widgets, bookmarks and metadata. Every fixture is built from code in the test
that uses it, so no document is copied from anywhere.

``layout()`` also returns a Docling-like JSON of the regions the fixture placed,
so the pipeline after the layout model can be tested without running it.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

FONT_CANDIDATES = {
    "sans": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
    "serif": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "C:/Windows/Fonts/times.ttf",
    ],
    "serif-bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
        "C:/Windows/Fonts/timesbd.ttf",
    ],
}


def find_font(kind: str = "sans") -> Path | None:
    """A TrueType font with Latin, Greek and math glyphs on this machine, if any."""
    for candidate in FONT_CANDIDATES[kind]:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


# TrueType -------------------------------------------------------------------------


class TrueType:
    """The parts of a TrueType file a PDF needs: glyph ids, widths and metrics."""

    def __init__(self, path: Path):
        self.data = path.read_bytes()
        offset = 0
        if self.data[:4] == b"ttcf":
            offset = struct.unpack(">I", self.data[12:16])[0]
        count = struct.unpack(">H", self.data[offset + 4 : offset + 6])[0]
        self.tables = {}
        for index in range(count):
            record = self.data[offset + 12 + 16 * index : offset + 28 + 16 * index]
            tag, _, start, length = struct.unpack(">4sIII", record)
            self.tables[tag.decode("latin-1")] = (start, length)
        head = self.table("head")
        self.units = struct.unpack(">H", head[18:20])[0]
        self.bbox = struct.unpack(">hhhh", head[36:44])
        hhea = self.table("hhea")
        self.ascent, self.descent = struct.unpack(">hh", hhea[4:8])
        metrics = struct.unpack(">H", hhea[34:36])[0]
        hmtx = self.table("hmtx")
        self.advances = [struct.unpack(">H", hmtx[4 * i : 4 * i + 2])[0] for i in range(metrics)]
        self.cmap = self._cmap()
        self.name = path.stem.replace(" ", "")

    def table(self, tag: str) -> bytes:
        start, length = self.tables[tag]
        return self.data[start : start + length]

    def _cmap(self) -> dict[int, int]:
        cmap = self.table("cmap")
        count = struct.unpack(">H", cmap[2:4])[0]
        records = [struct.unpack(">HHI", cmap[4 + 8 * i : 12 + 8 * i]) for i in range(count)]
        mapping: dict[int, int] = {}
        for wanted in ((3, 10), (0, 4), (3, 1), (0, 3)):
            for platform, encoding, start in records:
                if (platform, encoding) != wanted:
                    continue
                kind = struct.unpack(">H", cmap[start : start + 2])[0]
                if kind == 12:
                    groups = struct.unpack(">I", cmap[start + 12 : start + 16])[0]
                    for group in range(groups):
                        first, last, glyph = struct.unpack(
                            ">III", cmap[start + 16 + 12 * group : start + 28 + 12 * group]
                        )
                        for code in range(first, min(last, first + 5000) + 1):
                            mapping.setdefault(code, glyph + code - first)
                elif kind == 4:
                    segments = struct.unpack(">H", cmap[start + 6 : start + 8])[0] // 2
                    base = start + 14
                    ends = struct.unpack(f">{segments}H", cmap[base : base + 2 * segments])
                    base += 2 * segments + 2
                    starts = struct.unpack(f">{segments}H", cmap[base : base + 2 * segments])
                    base += 2 * segments
                    deltas = struct.unpack(f">{segments}h", cmap[base : base + 2 * segments])
                    base += 2 * segments
                    ranges = struct.unpack(f">{segments}H", cmap[base : base + 2 * segments])
                    for index in range(segments):
                        for code in range(starts[index], ends[index] + 1):
                            if code == 0xFFFF:
                                continue
                            if ranges[index] == 0:
                                glyph = (code + deltas[index]) & 0xFFFF
                            else:
                                at = (
                                    base + 2 * index + ranges[index]
                                    + 2 * (code - starts[index])
                                )
                                glyph = struct.unpack(">H", cmap[at : at + 2])[0]
                                if glyph:
                                    glyph = (glyph + deltas[index]) & 0xFFFF
                            if glyph:
                                mapping.setdefault(code, glyph)
                if mapping:
                    return mapping
        return mapping

    def glyph(self, character: str) -> int:
        glyph = self.cmap.get(ord(character))
        if not glyph:
            raise KeyError(f"{self.name} has no glyph for {character!r}")
        return glyph

    def width(self, glyph: int) -> float:
        advance = self.advances[min(glyph, len(self.advances) - 1)]
        return advance * 1000 / self.units


# PDF objects ------------------------------------------------------------------------


@dataclass
class Ref:
    number: int

    def __str__(self) -> str:
        return f"{self.number} 0 R"


def _string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    try:
        escaped.encode("latin-1")
        return f"({escaped})"
    except UnicodeEncodeError:
        return "<FEFF" + text.encode("utf-16-be").hex().upper() + ">"


def _tounicode(pairs: dict[int, str], width: int) -> bytes:
    digits = 2 * width
    lines = [
        "/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Fixture-UCS def", "/CMapType 2 def", "1 begincodespacerange",
        f"<{'0' * digits}> <{'F' * digits}>", "endcodespacerange",
    ]
    items = sorted(pairs.items())
    for start in range(0, len(items), 100):
        chunk = items[start : start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for code, text in chunk:
            lines.append(f"<{code:0{digits}X}> <{text.encode('utf-16-be').hex().upper()}>")
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode()


@dataclass
class Font:
    """A font resource; ``encode`` returns the bytes a text-showing operator takes."""

    key: str
    kind: str  # base14 | truetype | type3
    truetype: TrueType | None = None
    # Type 3: printed character -> one-byte code; TrueType: shown character -> claimed text.
    codes: dict[str, int] = field(default_factory=dict)
    claims: dict[str, str] = field(default_factory=dict)
    widths: dict[str, float] = field(default_factory=dict)
    used: dict[int, str] = field(default_factory=dict)

    def encode(self, text: str) -> str:
        if self.kind == "base14":
            return _string(text)
        if self.kind == "type3":
            return "<" + "".join(f"{self.codes[c]:02X}" for c in text) + ">"
        glyphs = []
        for character in text:
            glyph = self.truetype.glyph(character)
            self.used[glyph] = self.claims.get(character, character)
            glyphs.append(f"{glyph:04X}")
        return "<" + "".join(glyphs) + ">"

    def advance(self, character: str) -> float:
        """Width of one character in thousandths of the type size."""
        if self.kind == "truetype":
            return self.truetype.width(self.truetype.glyph(character))
        if self.kind == "type3":
            return self.widths[character]
        return BASE14_WIDTH.get(character, 500 if character.isupper() else 480)

    def measure(self, text: str, size: float) -> float:
        return sum(self.advance(c) for c in text) * size / 1000


BASE14_WIDTH = {" ": 250, "i": 278, "l": 278, "j": 278, "t": 333, "f": 333, "r": 389,
                "m": 778, "w": 722, "M": 889, "W": 944, ".": 250, ",": 250, ":": 278}

# Drawn glyphs in a 1000-unit em: operators and delimiters as filled outlines.
DRAWN = {
    "−": (556, "60 232 m 496 232 l 496 276 l 60 276 l h f"),
    "+": (556, "60 232 m 256 232 l 256 40 l 300 40 l 300 232 l 496 232 l 496 276 l 300 276 l "
               "300 468 l 256 468 l 256 276 l 60 276 l h f"),
    "=": (556, "60 150 m 496 150 l 496 194 l 60 194 l h f 60 314 m 496 314 l 496 358 l "
               "60 358 l h f"),
    "(": (333, "260 -180 m 120 -60 60 100 60 270 c 60 440 120 600 260 720 c 290 690 l "
               "170 580 120 440 120 270 c 120 100 170 -40 290 -150 c h f"),
    ")": (333, "73 -180 m 213 -60 273 100 273 270 c 273 440 213 600 73 720 c 43 690 l "
               "163 580 213 440 213 270 c 213 100 163 -40 43 -150 c h f"),
    "[": (333, "90 -180 m 270 -180 l 270 -136 l 140 -136 l 140 676 l 270 676 l 270 720 l "
               "90 720 l h f"),
    "]": (333, "63 -180 m 243 -180 l 243 720 l 63 720 l 63 676 l 193 676 l 193 -136 l "
               "63 -136 l h f"),
    "×": (556, "92 98 m 278 222 l 464 98 l 488 130 l 312 254 l 488 378 l 464 410 l 278 286 l "
               "92 410 l 68 378 l 244 254 l 68 130 l h f"),
}


class PdfBuilder:
    def __init__(self) -> None:
        self.objects: list[bytes | None] = []
        self.pages: list[Page] = []
        self.fonts: list[Font] = []
        self.info: dict[str, str] = {}
        self.xmp: str | None = None
        self.outline: list[tuple[str, int, float, int]] = []
        self.fields: list[Ref] = []

    def reserve(self) -> Ref:
        self.objects.append(None)
        return Ref(len(self.objects))

    def put(self, ref: Ref, body: str | bytes) -> Ref:
        self.objects[ref.number - 1] = body.encode("latin-1") if isinstance(body, str) else body
        return ref

    def add(self, body: str | bytes) -> Ref:
        return self.put(self.reserve(), body)

    def stream(self, data: bytes, extra: str = "", compress: bool = True) -> Ref:
        if compress:
            data = zlib.compress(data)
            extra += " /Filter /FlateDecode"
        return self.add(f"<< /Length {len(data)}{extra} >>\nstream\n".encode() + data
                        + b"\nendstream")

    # Fonts

    def base14(self, name: str = "Times-Roman") -> Font:
        font = Font(f"F{len(self.fonts) + 1}", "base14")
        # The symbol fonts use their own built-in encoding: a code names a symbol.
        encoding = "" if name in {"Symbol", "ZapfDingbats"} else " /Encoding /WinAnsiEncoding"
        font.ref = self.add(  # type: ignore[attr-defined]
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{name}{encoding} >>"
        )
        self.fonts.append(font)
        return font

    def truetype(self, path: Path, claims: dict[str, str] | None = None) -> Font:
        """An embedded TrueType font; ``claims`` gives a wrong text for some characters."""
        font = Font(f"F{len(self.fonts) + 1}", "truetype", TrueType(path), claims=claims or {})
        font.ref = self.reserve()  # type: ignore[attr-defined]
        self.fonts.append(font)
        return font

    def drawn(self, glyphs: dict[str, str], name: str = "DrawnMath") -> Font:
        """A Type 3 font of drawn operators; ``glyphs`` maps each to the text it claims.

        Codes are the claimed characters' own codes when they fit in one byte, as in
        fonts whose glyph names carry no meaning and whose text layer is the code.
        """
        font = Font(f"F{len(self.fonts) + 1}", "type3")
        procs, used, next_code = {}, set(), 0x80
        unicode_map = {}
        for printed, claimed in glyphs.items():
            code = ord(claimed) if len(claimed) == 1 and ord(claimed) < 256 else None
            if code is None or code in used:
                while next_code in used:
                    next_code += 1
                code = next_code
            used.add(code)
            width, path = DRAWN[printed]
            procs[code] = self.stream(f"{width} 0 0 -200 {width} 740 d1\n{path}".encode(), "",
                                      compress=False)
            font.codes[printed] = code
            font.widths[printed] = width
            unicode_map[code] = claimed
        first, last = min(procs), max(procs)
        names = " ".join(f"/g{code}" for code in range(first, last + 1))
        widths = " ".join(
            str(next((w for p, w in font.widths.items() if font.codes[p] == code), 0))
            for code in range(first, last + 1)
        )
        charprocs = " ".join(f"/g{code} {ref}" for code, ref in procs.items())
        tounicode = self.stream(_tounicode(unicode_map, 1), compress=False)
        font.ref = self.add(  # type: ignore[attr-defined]
            f"<< /Type /Font /Subtype /Type3 /Name /{name} /FontBBox [0 -200 1000 800] "
            "/FontMatrix [0.001 0 0 0.001 0 0] "
            f"/CharProcs << {charprocs} >> /Encoding << /Type /Encoding /Differences "
            f"[{first} {names}] >> /FirstChar {first} /LastChar {last} /Widths [{widths}] "
            f"/ToUnicode {tounicode} /Resources << >> >>"
        )
        self.fonts.append(font)
        return font

    def _finish_truetype(self, font: Font) -> None:
        tt = font.truetype
        # Each embedded subset has its own six-letter tag, as typesetters write them.
        tag = "AAAAA" + chr(ord("A") + self.fonts.index(font) % 26)
        file = self.stream(tt.data, f" /Length1 {len(tt.data)}")
        scale = 1000 / tt.units
        descriptor = self.add(
            f"<< /Type /FontDescriptor /FontName /{tag}+{tt.name} /Flags 4 "
            f"/FontBBox [{' '.join(str(round(v * scale)) for v in tt.bbox)}] /ItalicAngle 0 "
            f"/Ascent {round(tt.ascent * scale)} /Descent {round(tt.descent * scale)} "
            f"/CapHeight {round(tt.ascent * scale * 0.9)} /StemV 80 /FontFile2 {file} >>"
        )
        widths = " ".join(f"{g} [{round(tt.width(g))}]" for g in sorted(font.used))
        cid = self.add(
            f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{tag}+{tt.name} "
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
            f"/FontDescriptor {descriptor} /CIDToGIDMap /Identity /DW 1000 /W [{widths}] >>"
        )
        tounicode = self.stream(_tounicode(font.used, 2), compress=False)
        self.put(font.ref, (  # type: ignore[attr-defined]
            f"<< /Type /Font /Subtype /Type0 /BaseFont /{tag}+{tt.name} /Encoding /Identity-H "
            f"/DescendantFonts [{cid}] /ToUnicode {tounicode} >>"
        ))

    # Pages

    def page(self, width: float = 612, height: float = 792) -> Page:
        page = Page(self, len(self.pages) + 1, width, height)
        self.pages.append(page)
        return page

    def build(self, path: Path | None = None) -> bytes:
        for font in self.fonts:
            if font.kind == "truetype":
                self._finish_truetype(font)
        pages_ref = self.reserve()
        page_refs = [page.finish(pages_ref) for page in self.pages]
        self.put(pages_ref, f"<< /Type /Pages /Kids [{' '.join(map(str, page_refs))}] "
                            f"/Count {len(page_refs)} >>")
        catalog = f"<< /Type /Catalog /Pages {pages_ref}"
        if self.outline:
            catalog += f" /Outlines {self._outline(page_refs)} /PageMode /UseOutlines"
        if self.xmp:
            catalog += " /Metadata " + str(self.stream(
                self.xmp.encode(), " /Type /Metadata /Subtype /XML", compress=False
            ))
        if self.fields:
            catalog += f" /AcroForm << /Fields [{' '.join(map(str, self.fields))}] >>"
        catalog_ref = self.add(catalog + " >>")
        info_ref = (
            self.add("<< " + " ".join(f"/{k} {_string(v)}" for k, v in self.info.items()) + " >>")
            if self.info else None
        )
        output = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, body in enumerate(self.objects, 1):
            offsets.append(len(output))
            output += f"{number} 0 obj\n".encode() + (body or b"null") + b"\nendobj\n"
        xref = len(output)
        output += f"xref\n0 {len(self.objects) + 1}\n0000000000 65535 f \n".encode()
        for offset in offsets:
            output += f"{offset:010d} 00000 n \n".encode()
        trailer = f"<< /Size {len(self.objects) + 1} /Root {catalog_ref}"
        if info_ref:
            trailer += f" /Info {info_ref}"
        output += f"trailer\n{trailer} >>\nstartxref\n{xref}\n%%EOF\n".encode()
        data = bytes(output)
        if path is not None:
            path.write_bytes(data)
        return data

    def _outline(self, page_refs: list[Ref]) -> Ref:
        root = self.reserve()
        items = [self.reserve() for _ in self.outline]
        parents: list[tuple[int, int]] = []  # (level, index)
        children: dict[int | None, list[int]] = {}
        parent_of: dict[int, int | None] = {}
        for index, (_, _, _, level) in enumerate(self.outline):
            while parents and parents[-1][0] >= level:
                parents.pop()
            parent = parents[-1][1] if parents else None
            parent_of[index] = parent
            children.setdefault(parent, []).append(index)
            parents.append((level, index))
        for index, (title, page, top, _) in enumerate(self.outline):
            siblings = children[parent_of[index]]
            position = siblings.index(index)
            body = f"<< /Title {_string(title)} /Parent " + (
                str(items[parent_of[index]]) if parent_of[index] is not None else str(root)
            )
            if position:
                body += f" /Prev {items[siblings[position - 1]]}"
            if position < len(siblings) - 1:
                body += f" /Next {items[siblings[position + 1]]}"
            if kids := children.get(index):
                body += f" /First {items[kids[0]]} /Last {items[kids[-1]]} /Count {len(kids)}"
            body += f" /Dest [{page_refs[page - 1]} /XYZ 0 {top} 0] >>"
            self.put(items[index], body)
        top_level = children.get(None, [])
        self.put(root, f"<< /Type /Outlines /First {items[top_level[0]]} "
                       f"/Last {items[top_level[-1]]} /Count {len(top_level)} >>")
        return root


@dataclass
class Placed:
    """A region the fixture printed, for the Docling-like layout."""

    label: str
    page: int
    box: tuple[float, float, float, float]  # left, top, right, bottom; top-left origin
    text: str = ""
    children: list[int] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


class Page:
    def __init__(self, builder: PdfBuilder, number: int, width: float, height: float):
        self.builder, self.number, self.width, self.height = builder, number, width, height
        self.ops: list[str] = []
        self.fonts: set[str] = set()
        self.images: dict[str, Ref] = {}
        self.annots: list[Ref] = []
        self.placed: list[Placed] = []
        self.ref = builder.reserve()

    def text(self, x: float, y: float, text: str, font: Font, size: float) -> float:
        """Show ``text`` with its baseline at ``y`` (from the top); returns its width."""
        self.fonts.add(font.key)
        baseline = self.height - y
        self.ops.append(
            f"BT /{font.key} {size} Tf {x:.2f} {baseline:.2f} Td {font.encode(text)} Tj ET"
        )
        return font.measure(text, size)

    def runs(self, x: float, y: float, runs: list[tuple[str, Font, float, float]]) -> float:
        """Several runs on one line: (text, font, size, rise in points). Returns the end x."""
        for text, font, size, rise in runs:
            x += self.text(x, y - rise, text, font, size)
        return x

    def paragraph(self, x: float, y: float, width: float, text: str, font: Font, size: float,
                  leading: float | None = None) -> float:
        """``text`` wrapped to ``width``; returns the y of the next line."""
        lines, line = [], ""
        for word in text.split():
            trial = f"{line} {word}".strip()
            if line and font.measure(trial, size) > width:
                lines.append(line)
                line = word
            else:
                line = trial
        if line:
            lines.append(line)
        return self.lines(x, y, lines, font, size, leading)

    def lines(self, x: float, y: float, lines: list[str], font: Font, size: float,
              leading: float | None = None) -> float:
        """Lines of one paragraph from ``y`` down; returns the y of the next line."""
        leading = leading or size * 1.25
        for line in lines:
            self.text(x, y, line, font, size)
            y += leading
        return y

    def draw(self, operators: str) -> None:
        self.ops.append(operators)

    def rect(self, left: float, top: float, width: float, height: float, *,
             fill: str | None = None, stroke: str | None = "0 0 0", line: float = 1) -> None:
        y = self.height - top - height
        ops = [f"q {line} w"]
        if fill:
            ops.append(f"{fill} rg")
        if stroke:
            ops.append(f"{stroke} RG")
        paint = "B" if fill and stroke else "f" if fill else "S"
        ops.append(f"{left:.2f} {y:.2f} {width:.2f} {height:.2f} re {paint} Q")
        self.ops.append(" ".join(ops))

    def polyline(self, points: list[tuple[float, float]], *, line: float = 1,
                 color: str = "0 0 0") -> None:
        path = " ".join(
            f"{x:.2f} {self.height - y:.2f} {'m' if i == 0 else 'l'}"
            for i, (x, y) in enumerate(points)
        )
        self.ops.append(f"q {line} w {color} RG {path} S Q")

    def image(self, left: float, top: float, width: float, height: float, pixels) -> None:
        """Draw a PIL image."""
        rgb = pixels.convert("RGB")
        name = f"Im{len(self.images) + 1}"
        self.images[name] = self.builder.stream(
            rgb.tobytes(),
            f" /Type /XObject /Subtype /Image /Width {rgb.width} /Height {rgb.height} "
            "/ColorSpace /DeviceRGB /BitsPerComponent 8",
        )
        y = self.height - top - height
        self.ops.append(f"q {width:.2f} 0 0 {height:.2f} {left:.2f} {y:.2f} cm /{name} Do Q")

    def link(self, left: float, top: float, width: float, height: float, uri: str) -> None:
        y = self.height - top - height
        self.annots.append(self.builder.add(
            f"<< /Type /Annot /Subtype /Link /Rect [{left} {y} {left + width} {y + height}] "
            f"/Border [0 0 0] /A << /S /URI /URI {_string(uri)} >> >>"
        ))

    def checkbox(self, left: float, top: float, size: float, name: str, checked: bool) -> None:
        """A form check box widget with its own on and off appearances."""
        y = self.height - top - size
        box = f"0.5 w 0 0 0 RG 0.5 0.5 {size - 1} {size - 1} re S"
        tick = f"{box} 1.2 w {size * 0.2} {size * 0.5} m {size * 0.42} {size * 0.22} l " \
               f"{size * 0.82} {size * 0.82} l S"
        on = self.builder.stream(tick.encode(), f" /Type /XObject /Subtype /Form /BBox [0 0 "
                                 f"{size} {size}]", compress=False)
        off = self.builder.stream(box.encode(), f" /Type /XObject /Subtype /Form /BBox [0 0 "
                                  f"{size} {size}]", compress=False)
        state = "/Yes" if checked else "/Off"
        ref = self.builder.add(
            f"<< /Type /Annot /Subtype /Widget /FT /Btn /T {_string(name)} /V {state} "
            f"/AS {state} /Rect [{left} {y} {left + size} {y + size}] /F 4 "
            f"/AP << /N << /Yes {on} /Off {off} >> >> /P {self.ref} >>"
        )
        self.annots.append(ref)
        self.builder.fields.append(ref)

    def place(self, label: str, box: tuple[float, float, float, float], text: str = "",
              **extra) -> int:
        """Record a layout region (top-left origin) for ``layout()``; returns its index."""
        self.placed.append(Placed(label, self.number, box, text, extra=extra))
        return len(self.placed) - 1

    def finish(self, parent: Ref) -> Ref:
        fonts = " ".join(
            f"/{font.key} {font.ref}" for font in self.builder.fonts  # type: ignore[attr-defined]
            if font.key in self.fonts
        )
        images = " ".join(f"/{name} {ref}" for name, ref in self.images.items())
        content = self.builder.stream("\n".join(self.ops).encode("latin-1"), compress=False)
        resources = f"<< /Font << {fonts} >> /XObject << {images} >> >>"
        annots = f" /Annots [{' '.join(map(str, self.annots))}]" if self.annots else ""
        self.builder.put(self.ref, (
            f"<< /Type /Page /Parent {parent} /MediaBox [0 0 {self.width} {self.height}] "
            f"/Resources {resources} /Contents {content}{annots} >>"
        ))
        return self.ref


def layout(builder: PdfBuilder) -> dict:
    """A Docling-like JSON of every placed region, in placement (reading) order.

    Text regions carry the text the PDF's text layer gives (what a layout parser
    reads), not what is printed. Pictures and tables keep their captions as
    ``captions`` links and children, as Docling exports them.
    """
    document: dict = {
        "body": {"self_ref": "#/body", "children": []},
        "furniture": {"self_ref": "#/furniture", "children": []},
        "texts": [], "pictures": [], "tables": [], "groups": [],
        "pages": {
            str(page.number): {"size": {"width": page.width, "height": page.height},
                               "page_no": page.number}
            for page in builder.pages
        },
    }
    refs: dict[tuple[int, int], str] = {}
    for page in builder.pages:
        for index, placed in enumerate(page.placed):
            collection = {"picture": "pictures", "table": "tables"}.get(placed.label, "texts")
            ref = f"#/{collection}/{len(document[collection])}"
            refs[(page.number, index)] = ref
            left, top, right, bottom = placed.box
            item = {
                "self_ref": ref, "label": placed.label, "children": [],
                "prov": [{"page_no": page.number, "charspan": [0, len(placed.text)], "bbox": {
                    "l": left, "t": page.height - top, "r": right, "b": page.height - bottom,
                    "coord_origin": "BOTTOMLEFT",
                }}],
            }
            if collection == "texts":
                item.update(text=placed.text, orig=placed.text)
            item.update(placed.extra)
            document[collection].append(item)
    for page in builder.pages:
        for index, placed in enumerate(page.placed):
            ref = refs[(page.number, index)]
            item = _item(document, ref)
            owner = placed.extra.get("owner")
            for child in placed.children:
                child_ref = refs[(page.number, child)]
                item["children"].append({"$ref": child_ref})
                if page.placed[child].label == "caption":
                    item.setdefault("captions", []).append({"$ref": child_ref})
            item.pop("owner", None)
            if owner is not None or any(index in p.children for p in page.placed):
                continue
            tree = "furniture" if placed.label in {"page_header", "page_footer"} else "body"
            document[tree]["children"].append({"$ref": ref})
    return document


def _item(document: dict, ref: str) -> dict:
    _, collection, index = ref.split("/")
    return document[collection][int(index)]
