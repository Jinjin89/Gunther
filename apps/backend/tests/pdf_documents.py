"""Two whole documents designed for the PDF fidelity tests, built from code.

``paper_pdf`` is a short journal-style article with the failure classes a reader
must survive: a math font whose text layer names its minus sign "2" and its
parentheses "ð" and "Þ", a running head printed just above a section heading,
display and inline equations with numbers, a figure whose axes, tick labels and
legend lie outside the embedded image, a caption with a subscript and an
exponent, numbered and raised citations, a bibliography whose heading is printed
again on its next page, dates and a DOI printed on the first page, an incomplete
author list in the file metadata, bookmarks, and a last page with no text.

``form_pdf`` is a one-page checklist: answers ticked in a table's columns, form
check boxes with their own states, and options ticked with dingbat glyphs.

All names, numbers and wording are invented here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pdf_fixtures import PdfBuilder, find_font

RUNNING_HEAD = "Synthetic Sensors Letters 3 (2024) 41-52"
TITLE = "Measuring drift in synthetic sensors"


@dataclass
class Expected:
    """What the fixture prints, for the assertions."""

    pages: int = 0
    values: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)


def _fonts(builder: PdfBuilder):
    serif, bold, sans = find_font("serif"), find_font("serif-bold"), find_font("sans")
    if not (serif and bold and sans):
        return None
    return (
        builder.truetype(serif),
        builder.truetype(bold),
        # The same glyphs as ``sans``, with a text layer that names some of them wrongly.
        builder.truetype(sans, claims={"−": "2", "(": "ð", ")": "Þ", "=": "¼", "+": "þ"}),
        builder.truetype(sans),
    )


def paper_pdf(path: Path) -> Expected | None:
    """Write the article to ``path``; None when this machine lacks the fonts."""
    from PIL import Image, ImageDraw

    builder = PdfBuilder()
    fonts = _fonts(builder)
    if fonts is None:
        return None
    serif, bold, math, sans = fonts
    expected = Expected(pages=5, values=["−0.42", "(n = 12)", "−2x + 1"])
    left, width = 72.0, 468.0

    def running(page, number: int) -> None:
        page.text(left, 40, RUNNING_HEAD, serif, 8)
        page.text(left + width - 12, 40, str(number), serif, 8)

    # Page 1: title, byline, dates, abstract, introduction.
    page = builder.page()
    running(page, 41)
    page.text(left, 96, TITLE, bold, 17)
    page.runs(left, 122, [
        ("Ana Kovač", serif, 11, 0), ("1", serif, 7, 4), (", Ben Ortiz", serif, 11, 0),
        ("2", serif, 7, 4), (" and Chen Li", serif, 11, 0), ("1", serif, 7, 4),
    ])
    page.text(left, 138, "1 Institute of Example Studies, Lakeside", serif, 8)
    page.text(left, 148, "2 Department of Imaginary Physics, Hilltown", serif, 8)
    page.text(left, 172, "Received: 3 March 2024; Accepted: 9 May 2024; Published: 2 June 2024",
              serif, 8)
    page.text(left, 183, "https://doi.org/10.5555/sensors.2024.17", serif, 8)
    page.text(left, 212, "Abstract", bold, 11)
    y = page.paragraph(left, 228, width, (
        "We follow twelve synthetic sensors for one hundred days and report how far their "
        "readings drift from a fixed reference. Drift is small but steady, and it does not "
        "depend on the hour of the day or the order in which the units were installed."
    ), serif, 10)
    # A sentence with values set in the misnamed math font.
    page.runs(left, y, [
        ("Across the units the mean change was ", serif, 10, 0), ("−", math, 10, 0),
        ("0.42 ", serif, 10, 0), ("(", math, 10, 0), ("n ", serif, 10, 0), ("=", math, 10, 0),
        (" 12", serif, 10, 0), (")", math, 10, 0), (" per day.", serif, 10, 0),
    ])
    y += 30
    expected.headings.append("1 Introduction")
    page.text(left, y, "1 Introduction", bold, 11)
    y = page.paragraph(left, y + 16, width, (
        "Sensors that are left in place for months slowly lose their calibration. Field "
        "studies have measured this loss for single devices [1] and for small networks [2, 3], "
        "but the methods differ so much that their numbers cannot be compared directly. Here we "
        "use one protocol for every unit and report the drift in the same unit throughout."
    ), serif, 10)
    page.runs(left, y, [
        ("Earlier work measured drift in the field", serif, 10, 0), ("4", serif, 6.5, 3.5),
        (" and found it grew with temperature.", serif, 10, 0),
    ])

    # Page 2: a heading right under the running head, equations, a figure.
    page = builder.page()
    running(page, 42)
    expected.headings.append("2 Methods")
    page.text(left, 58, "2 Methods", bold, 11)
    y = page.paragraph(left, 76, width, (
        "Each unit was read every hour against the reference instrument. The relative drift "
        "of a unit is the ratio of its accumulated error to the time it has been running, "
        "and it is estimated for every unit separately before the units are compared."
    ), serif, 10)
    # A display equation in two dimensions: a fraction over a rule, with its number.
    page.text(272, y + 14, "e", serif, 11)
    page.draw(f"q 0.6 w 0 0 0 RG 268 {792 - y - 18:.2f} m 284 {792 - y - 18:.2f} l S Q")
    page.text(273, y + 30, "t", serif, 11)
    page.text(290, y + 22, "=", sans, 11)
    page.text(302, y + 22, "r", serif, 11)
    page.text(left + width - 20, y + 22, "(1)", serif, 10)
    # A one-line equation whose minus and equals are named wrongly by the text layer.
    y += 56
    page.runs(250, y, [
        ("y ", serif, 11, 0), ("=", math, 11, 0), (" ", serif, 11, 0), ("−", math, 11, 0),
        ("2x ", serif, 11, 0), ("+", math, 11, 0), (" 1", serif, 11, 0),
    ])
    page.text(left + width - 20, y, "(2)", serif, 10)
    # The figure: a raster of bars, and vector axes, ticks and a legend outside it.
    image = Image.new("RGB", (300, 160), "white")
    draw = ImageDraw.Draw(image)
    for index, height in enumerate((60, 95, 130, 110, 150)):
        draw.rectangle((20 + index * 55, 160 - height, 55 + index * 55, 159), fill=(70, 70, 70))
    top = y + 30
    page.image(130, top, 300, 160, image)
    page.polyline([(124, top - 4), (124, top + 166), (440, top + 166)], line=1)
    for index, label in enumerate(("0", "25", "50", "75", "100")):
        tick = 150 + index * 55
        page.polyline([(tick, top + 166), (tick, top + 171)], line=0.8)
        page.text(tick - 4, top + 182, label, sans, 7)
    page.text(250, top + 194, "Hours", sans, 8)
    page.rect(452, top + 20, 8, 8, fill="0.27 0.27 0.27", stroke=None)
    page.text(464, top + 27, "Unit S2", sans, 7)
    caption_y = top + 218
    page.runs(left, caption_y, [
        ("Figure 1. Drift of sensor S", serif, 9, 0), ("2", serif, 6, -2),
        (" over 10", serif, 9, 0), ("3", serif, 6, 3), (" hours of operation [1].", serif, 9, 0),
    ])
    page.paragraph(left, caption_y + 26, width, (
        "The drift of the second sensor is typical: it rises during the first weeks and then "
        "stays nearly flat, which is why the remaining units are summarised by one number."
    ), serif, 10)

    # Page 3: results and the bibliography.
    page = builder.page()
    running(page, 43)
    expected.headings.append("3 Results")
    page.text(left, 72, "3 Results", bold, 11)
    y = page.paragraph(left, 90, width, (
        "All twelve units drifted in the same direction. The spread between units was smaller "
        "than the spread reported for networks of the same size [3], and no unit drifted fast "
        "enough to need recalibration within the hundred days of the study [5]."
    ), serif, 10)
    page.text(left, y + 24, "References", bold, 11)
    y += 42
    for entry in (
        "[1] Moreau, L. Drift in a single field sensor. Synthetic Instruments 12 (2019) 3-9.",
        "[2] Okafor, N., Lind, P. Calibration loss in small networks. Example Methods 4 (2020).",
        "[3] Varga, T. Comparing drift across networks. Imaginary Physics 8 (2021) 101-110.",
    ):
        y = page.paragraph(left, y, width, entry, serif, 9) + 4

    # Page 4: the bibliography goes on under its heading printed again.
    page = builder.page()
    running(page, 44)
    page.text(left, 72, "References", bold, 11)
    y = 90
    for entry in (
        "[4] Haddad, R. Temperature and drift. Field Notes 2 (2018) 44-47.",
        "[5] Sato, K. When to recalibrate. Practical Sensing 6 (2022) 12-19.",
    ):
        y = page.paragraph(left, y, width, entry, serif, 9) + 4

    # Page 5: nothing but its page number.
    page = builder.page()
    page.text(left + width - 12, 760, "45", serif, 8)

    builder.outline = [
        (TITLE, 1, 700, 1), ("Abstract", 1, 590, 2), ("1 Introduction", 1, 500, 2),
        ("2 Methods", 2, 740, 2), ("3 Results", 3, 720, 2), ("References", 3, 560, 2),
    ]
    builder.info = {
        "Title": TITLE, "Author": "Kovač, A.; Ortiz, B.",
        "CreationDate": "D:20250115093000+02'00'", "ModDate": "D:20250201101500Z",
        "Creator": "SynthWriter 2.1", "Producer": "Example PDF Library 9.0",
    }
    builder.xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:creator><rdf:Seq><rdf:li>Ana Kovač</rdf:li><rdf:li>Ben Ortiz</rdf:li>"
        "</rdf:Seq></dc:creator></rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    builder.build(path)
    return expected


def form_pdf(path: Path) -> Expected | None:
    """Write the checklist to ``path``; None when this machine lacks the fonts."""
    builder = PdfBuilder()
    fonts = _fonts(builder)
    if fonts is None:
        return None
    serif, bold, _, sans = fonts
    dingbats = builder.base14("ZapfDingbats")
    page = builder.page()
    left = 72.0
    page.text(left, 80, "Site inspection checklist", bold, 16)
    page.paragraph(left, 104, 468, (
        "Tick one answer for every item. An item that does not apply to this site is ticked "
        "under N/A."
    ), serif, 10)
    # The table: an item column and three answer columns, ruled.
    columns = [left, 300, 380, 460, 540]
    rows = [130, 152, 174, 196, 218]
    for x in columns:
        page.polyline([(x, rows[0]), (x, rows[-1])], line=0.6)
    for y in rows:
        page.polyline([(columns[0], y), (columns[-1], y)], line=0.6)
    for x, label in zip(columns[1:], ("Yes", "No", "N/A"), strict=False):
        page.text(x + 28, rows[0] + 15, label, bold, 10)
    page.text(left + 6, rows[0] + 15, "Item", bold, 10)
    answers = [("Exits marked", 0), ("Extinguisher present", 1), ("Signage visible", 2)]
    for row, (item, ticked) in enumerate(answers, start=1):
        page.text(left + 6, rows[row] + 15, item, serif, 10)
        for column in range(3):
            x, y = columns[column + 1] + 34, rows[row] + 6
            page.rect(x, y, 10, 10, line=0.8)
            if column == ticked:
                page.polyline([(x + 2, y + 2), (x + 8, y + 8)], line=1.2)
                page.polyline([(x + 2, y + 8), (x + 8, y + 2)], line=1.2)
    # Interactive check boxes that keep their own state.
    page.checkbox(left, 246, 10, "confirm", True)
    page.text(left + 16, 255, "I confirm the inspection was done on site", serif, 10)
    page.checkbox(left, 268, 10, "followup", False)
    page.text(left + 16, 277, "A follow-up visit is needed", serif, 10)
    # Options ticked with dingbat glyphs: a box (❏) and a heavy tick (✔).
    page.text(left, 304, "o", dingbats, 10)
    page.text(left + 14, 304, "Photos attached", serif, 10)
    page.text(left, 320, "4", dingbats, 10)
    page.text(left + 14, 320, "Report signed", serif, 10)
    page.text(left, 350, "Inspector: R. Diaz", sans, 10)
    builder.info = {"Title": "Site inspection checklist", "Producer": "Example PDF Library 9.0"}
    builder.build(path)
    return Expected(pages=1)
