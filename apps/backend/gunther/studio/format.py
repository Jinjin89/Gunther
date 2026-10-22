"""The studio deck and report format (docs/STUDIO_FORMAT.md): layouts, budgets, and a checker
that says exactly what a slide gets wrong, so the writer can be asked to fix that and only that.

The desktop draws the same grammar (apps/desktop/src/outputs/deck/parseDeck.ts); the fixtures in
apps/desktop/src/outputs/deck/fixtures/ are read by tests/test_studio_format.py on this side.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

LAYOUTS = (
    "title",
    "section",
    "summary",
    "stat",
    "stats",
    "chart",
    "table",
    "compare",
    "process",
    "timeline",
    "quote",
    "figure",
    "statement",
    "points",
    "actions",
)
THEMES = ("paper", "ink", "editorial")
CHART_KINDS = ("bar", "column", "line")
DESIGN = "v2"

TITLE_UNITS = (12, 90)
# Body budgets for a talk deck; a deck for reading may use READ_FACTOR times as much.
READ_FACTOR = 1.5

MARK = re.compile(r"\A\s*<!--\s*layout:\s*(?P<layout>[a-z_]+)(?P<attrs>[^>]*?)\s*-->[ \t]*\n?")
ATTR = re.compile(r";\s*(?P<key>[a-z_]+)\s*:\s*(?P<value>[^;]+?)\s*(?=;|$)")
CITE = re.compile(r"\s*\[(?:\?|\d+(?:\s*[,，]\s*\d+)*)\]")
BOLD_LEAD = re.compile(r"^\*\*(?P<lead>[^*]+?)\*\*\s*[:：—–-]?\s*(?P<rest>.*)$")
NUMBERED = re.compile(r"^\d+[.)]\s+(?P<text>.+)$")
BULLET = re.compile(r"^[-*•]\s+(?P<text>.+)$")
IMAGE = re.compile(r"^!\[[^\]]*\]\(gunther-asset:[A-Za-z0-9_-]+\)\s*$")
NUMBER = re.compile(r"[-+−]?\d[\d,，.]*")


def units(text: str) -> int:
    """How long a piece of text looks on a slide: a CJK character counts as two, citation
    marks and Markdown emphasis count nothing."""

    plain = CITE.sub("", text).replace("**", "").replace("__", "")
    plain = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", plain)
    total = 0
    for char in plain.strip():
        width = unicodedata.east_asian_width(char)
        total += 2 if width in ("W", "F") else 1
    return total


@dataclass
class Slide:
    layout: str
    attrs: dict[str, str]
    title: str  # the action title (or the title slide's / section's title)
    body: list[str]  # the lines between the title and the notes, blank lines dropped
    notes: str
    problems: list[str] = field(default_factory=list)


def split_slides(content: str) -> list[str]:
    parts, current, fenced = [], [], False
    for line in content.replace("\r\n", "\n").split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
        if not fenced and line.strip() == "---":
            parts.append("\n".join(current).strip())
            current = []
        else:
            current.append(line)
    parts.append("\n".join(current).strip())
    return [part for part in parts if part]


def parse_slide(text: str) -> Slide:
    found = MARK.match(text)
    layout, attrs, rest = "", {}, text
    if found:
        layout = found.group("layout")
        attrs = {
            m.group("key"): m.group("value").strip() for m in ATTR.finditer(found.group("attrs"))
        }
        rest = text[found.end() :]
    notes = ""
    lines: list[str] = []
    in_notes = False
    for line in rest.split("\n"):
        if not in_notes and re.match(r"^\s*(Note|Notes|备注|讲者备注)\s*[:：]", line):
            in_notes = True
            notes = re.sub(r"^\s*(Note|Notes|备注|讲者备注)\s*[:：]\s*", "", line)
            continue
        if in_notes:
            notes += "\n" + line
        else:
            lines.append(line.rstrip())
    title = ""
    body: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        if not title and re.match(r"^#{1,2}\s+", line):
            title = re.sub(r"^#{1,2}\s+", "", line).strip()
            continue
        body.append(line.strip())
    return Slide(layout, attrs, title, body, notes.strip())


def _items(body: list[str]) -> tuple[list[str], list[str]]:
    """List items (bullets or numbered) and the other lines."""

    items, other = [], []
    for line in body:
        match = BULLET.match(line) or NUMBERED.match(line)
        if match:
            items.append(match.group("text").strip())
        else:
            other.append(line)
    return items, other


def _table(body: list[str]) -> tuple[list[list[str]], list[str]]:
    rows, other = [], []
    for line in body:
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
                continue
            rows.append(cells)
        else:
            other.append(line)
    return rows, other


def _number_cell(cell: str) -> bool:
    plain = CITE.sub("", cell).replace("**", "").strip()
    return bool(re.fullmatch(r"[-+−]?\d[\d,，]*(?:\.\d+)?\s*(?:%|[A-Za-z€$£¥%°一-鿿]{0,6})", plain))


def check_slide(slide: Slide, *, first: bool, read: bool = False) -> list[str]:
    """What is wrong with one slide, as instructions the writer can act on. Empty: fine."""

    problems: list[str] = []
    factor = READ_FACTOR if read else 1.0

    def limit(what: str, text: str, most: int, least: int = 1) -> None:
        size = units(text)
        most = int(most * factor) if what not in {"title"} else most
        if size > most:
            problems.append(
                f"{what} is {size} units long; the limit is {most}. Shorten it by {size - most}."
            )
        elif size < least:
            problems.append(f"{what} is missing or too short.")

    layout = slide.layout
    if layout not in LAYOUTS:
        return [f'Unknown layout "{layout}". Use one of: {", ".join(LAYOUTS)}.']
    if first and layout != "title":
        problems.append("The first slide must use the title layout.")
    if layout == "title":
        lines = [line for line in slide.body]
        if not slide.title:
            problems.append('The title slide needs a "# title" line.')
        else:
            limit("title", slide.title, 80, 8)
        if lines:
            limit("subtitle", lines[0], 160)
        if len(lines) > 2:
            problems.append("The title slide has at most a subtitle line and a meta line.")
        return problems
    if layout == "section":
        if not slide.title:
            problems.append('A section slide needs a "# title" line.')
        else:
            limit("section title", slide.title, 80, 4)
        if len(slide.body) > 1:
            problems.append("A section slide has at most one line under its title.")
        return problems
    if not slide.title:
        problems.append('Every content slide starts with "## " and its action title.')
    else:
        limit("title", slide.title, TITLE_UNITS[1], TITLE_UNITS[0])
    body = slide.body
    items, other = _items(body)

    def leads(
        min_items: int, max_items: int, lead_max: int, detail_max: int, *, need_lead: bool
    ) -> None:
        if not min_items <= len(items) <= max_items:
            problems.append(f"Use {min_items}–{max_items} list items; there are {len(items)}.")
        for number, item in enumerate(items, 1):
            match = BOLD_LEAD.match(item)
            if not match:
                if need_lead:
                    problems.append(
                        f'Item {number} must start with a bold lead: "**lead**: detail".'
                    )
                limit(f"item {number}", item, detail_max)
                continue
            limit(f"item {number} lead", match.group("lead"), lead_max)
            if match.group("rest"):
                limit(f"item {number} detail", match.group("rest"), detail_max)

    if layout == "summary":
        leads(2, 4, 60, 130, need_lead=True)
        if other:
            problems.append("A summary slide holds only its list items.")
    elif layout == "stat":
        if not body or not re.fullmatch(r"\*\*[^*]+\*\*(?:\s*\[[^\]]+\])*", body[0]):
            problems.append('The first line under the title is the bold value: "**40 h → 16 h**".')
        else:
            limit("value", body[0], 16)
        if len(body) < 2:
            problems.append("Add the label line under the value: what the number measures.")
        else:
            limit("label", body[1], 90)
        if len(body) > 3:
            problems.append("A stat slide has the value, a label and at most one context line.")
        elif len(body) == 3:
            limit("context", body[2], 160)
    elif layout == "stats":
        if not 2 <= len(items) <= 4:
            problems.append(f"Use 2–4 items; there are {len(items)}.")
        for number, item in enumerate(items, 1):
            match = BOLD_LEAD.match(item)
            if not match:
                problems.append(f'Item {number} must be "**value** label".')
                continue
            limit(f"item {number} value", match.group("lead"), 10)
            limit(f"item {number} label", match.group("rest"), 60)
        if len(other) > 1:
            problems.append("At most one closing line after the items.")
        elif other:
            limit("closing line", other[0], 140)
    elif layout in ("chart", "table"):
        rows, rest = _table(body)
        if len(rows) < 3:
            problems.append("Write a Markdown table: a header row and at least two rows.")
        else:
            head, data = rows[0], rows[1:]
            width = len(head)
            if any(len(row) != width for row in data):
                problems.append("Every table row must have as many cells as the header.")
            if layout == "chart":
                kind = slide.attrs.get("chart", "bar")
                if kind not in CHART_KINDS:
                    problems.append(f"chart must be one of {', '.join(CHART_KINDS)}.")
                if not 2 <= width <= 4:
                    problems.append("A chart table has a label column and 1–3 numeric columns.")
                if not 2 <= len(data) <= 12:
                    problems.append(f"A chart has 2–12 rows; there are {len(data)}.")
                for number, row in enumerate(data, 1):
                    for cell in row[1:]:
                        if not _number_cell(cell):
                            problems.append(
                                f'Row {number}: "{cell}" is not a number; a chart cell is a number '
                                "with its citation, the unit in the header. Use the table layout "
                                "if the values are not numbers."
                            )
                            break
                series = max(0, width - 1)
                most = 12 if kind == "bar" else 18 if kind == "column" else 36
                if series and len(data) * series > most:
                    problems.append(
                        f"{len(data)} rows × {series} series is too dense for a {kind} chart "
                        f"(at most {most} bars): drop rows or series, or use another chart."
                    )
                bold = sum(1 for row in data if row and row[0].startswith("**"))
                if bold > 1:
                    problems.append("Highlight at most one row (bold label).")
            else:
                if not 2 <= width <= 5:
                    problems.append(f"A table has 2–5 columns; there are {width}.")
                if not 2 <= len(data) <= 7:
                    problems.append(f"A table has 2–7 rows; there are {len(data)}.")
                for number, row in enumerate(data, 1):
                    if sum(units(cell) for cell in row) > int(100 * factor):
                        problems.append(
                            f"Row {number} holds {sum(units(c) for c in row)} units across its "
                            f"cells; a row may hold at most {int(100 * factor)}. Shorten cells."
                        )
                    for cell in row:
                        if units(cell) > int(50 * factor):
                            problems.append(
                                f"Row {number}: a cell is {units(cell)} units; "
                                f"the limit is {int(50 * factor)}."
                            )
                            break
        if len(rest) > 1:
            problems.append("At most one line after the table: the takeaway.")
        elif rest:
            limit("takeaway", rest[0], 140)
    elif layout == "compare":
        sides = [i for i, line in enumerate(body) if line.startswith("### ")]
        if len(sides) != 2:
            problems.append('Write exactly two sides, each starting with "### side name".')
        else:
            for which, start in enumerate(sides):
                end = sides[which + 1] if which == 0 else len(body)
                limit(f"side {which + 1} name", body[start][4:], 30)
                chunk = body[start + 1 : end]
                side_items, side_other = _items(chunk)
                if not 2 <= len(side_items) <= 4:
                    problems.append(
                        f"Side {which + 1} needs 2–4 list items; with less to compare, use "
                        "another layout (stat, statement or table)."
                    )
                for number, item in enumerate(side_items, 1):
                    limit(f"side {which + 1} item {number}", item, 90)
                if which == 1 and len(side_other) > 1:
                    problems.append("At most one verdict line after the two sides.")
                elif which == 1 and side_other:
                    limit("verdict", side_other[0], 140)
                elif which == 0 and side_other:
                    problems.append("Side 1 holds only list items.")
            if sides and sides[0] != 0:
                problems.append("Start the body with the first side.")
    elif layout == "process":
        if any(not NUMBERED.match(line) for line in body):
            problems.append('Write the steps as a numbered list: "1. **step**: detail".')
        leads(2, 6, 24, 90, need_lead=True)
    elif layout == "timeline":
        leads(3, 6, 16, 90, need_lead=True)
        if other:
            problems.append("A timeline holds only its dated items.")
    elif layout == "quote":
        quote = [line for line in body if line.startswith(">")]
        if not quote:
            problems.append('Write the quote as "> words".')
        else:
            limit("quote", " ".join(line.lstrip("> ") for line in quote), 240)
        who = [line for line in body if not line.startswith(">")]
        if len(who) != 1:
            problems.append('Add one attribution line: "— who, where [n]".')
        else:
            limit("attribution", re.sub(r"^[—–-]+\s*", "", who[0]), 90)
    elif layout == "figure":
        if not body or not IMAGE.match(body[0]):
            problems.append(
                "The first line is the figure, copied exactly: ![caption](gunther-asset:…)."
            )
        if len(body) != 2:
            problems.append("Under the figure, one takeaway line.")
        else:
            limit("takeaway", body[1], 160)
    elif layout == "statement":
        if not other:
            problems.append("Write the statement line under the title.")
        else:
            limit("statement", other[0], 180)
            if _overlap(slide.title, other[0]) > 0.6:
                problems.append(
                    "The title repeats the statement. Make the title a short lead-in (the "
                    "question or the context) and let the statement carry the point."
                )
            if len(other) > 1:
                problems.append("One statement line, then at most three list items.")
        if len(items) > 3:
            problems.append("At most three list items under the statement.")
        for number, item in enumerate(items, 1):
            limit(f"item {number}", item, 80)
    elif layout == "points":
        leads(2, 4, 40, 120, need_lead=False)
        if other:
            problems.append("A points slide holds only its list items.")
    elif layout == "actions":
        leads(2, 4, 24, 100, need_lead=True)
        if other:
            problems.append("An actions slide holds only its list items.")
    return problems


def check_deck(
    content: str, *, read: bool = False, notes_required: bool = True
) -> list[tuple[int, list[str]]]:
    """Each slide (counted from 1) with what is wrong with it; slides with nothing wrong are
    left out."""

    found = []
    slides = split_slides(content)
    if not slides:
        return [(1, ["The deck is empty."])]
    parsed = [parse_slide(text) for text in slides]
    points_seen = 0
    for index in range(len(slides)):
        slide = parsed[index]
        problems = check_slide(slide, first=index == 0, read=read)
        meta = META.search(" ".join([slide.title, *slide.body]))
        if meta:
            problems.append(
                f'"{meta.group(0)}": write about the world, not about the material — state the '
                "finding or the decision itself."
            )
        if slide.layout not in ("title", "section") and LABEL_TITLE.search(slide.title):
            problems.append(
                "The title announces content instead of stating it. Put the point itself in the "
                'title (not "Three findings change the decision" but what they are).'
            )
        if CITE.search(slide.title):
            problems.append(
                "Take the citation marks out of the title; the body proves it and cites."
            )
        if index > 0 and _same(slide.title, parsed[0].title):
            problems.append(
                "This slide repeats the deck title. Give it its own job: usually the answer "
                "with its 2–4 findings (summary layout)."
            )
        if slide.layout == "points":
            points_seen += 1
            if points_seen > 2:
                problems.append(
                    "The deck already has two points slides. Rewrite this one with the layout "
                    "its content really has: stats or a chart for numbers, table or compare "
                    "for comparisons, process or timeline for sequences, statement for one idea."
                )
        if index >= 2 and slide.layout == parsed[index - 1].layout == parsed[index - 2].layout:
            problems.append(
                f"Three {slide.layout} slides in a row: show this one with a different layout "
                "that fits its content."
            )
        if notes_required and slide.layout not in ("title", "section") and not slide.notes:
            problems.append('Add speaker notes after a line starting with "Note:".')
        if index > 0 and slide.layout == "title":
            problems.append("Only the first slide uses the title layout.")
        if problems:
            found.append((index + 1, problems))
    return found


def chart_numbers(text: str) -> list[tuple[str, list[int]]]:
    """Each numeric cell of a chart slide with the source numbers it cites (same cell, else the
    row's label cell)."""

    slide = parse_slide(text)
    if slide.layout != "chart":
        return []
    rows, _ = _table(slide.body)
    out = []
    for row in rows[1:]:
        label_refs = [int(n) for n in re.findall(r"\[(\d+)\]", row[0])] if row else []
        for cell in row[1:]:
            refs = [int(n) for n in re.findall(r"\[(\d+)\]", cell)] or label_refs
            value = CITE.sub("", cell).replace("**", "").strip()
            number = NUMBER.search(value)
            if number:
                out.append((number.group(), refs))
    return out


def normal_number(text: str) -> str:
    text = text.replace(",", "").replace("，", "").replace("−", "-")
    try:
        value = float(text)
    except ValueError:
        return text
    return f"{value:g}"


def numbers_in(text: str) -> set[str]:
    return {normal_number(match.group()) for match in NUMBER.finditer(text)}


def with_marker(text: str, layout: str, **attrs: str) -> str:
    """The slide with a fresh marker (any old marker is replaced)."""

    found = MARK.match(text)
    body = text[found.end() :] if found else text
    suffix = "".join(f"; {key}: {value}" for key, value in attrs.items() if value)
    return f"<!-- layout: {layout}{suffix} -->\n{body.lstrip()}"


REPORT_HEADING_UNITS = 90
SUMMARY_MARK = "<!-- block: summary -->"
EXHIBIT = re.compile(r"^:::exhibit(?P<attrs>[^\n]*)\n(?P<body>.*?)\n:::\s*$", re.M | re.S)


def check_report(text: str) -> list[str]:
    """What is wrong with a report as a whole, as instructions the writer can act on."""

    problems: list[str] = []
    headings = re.findall(r"^## (.+)$", text, re.M)
    if not headings:
        return ['The report has no "## " sections.']
    for number, heading in enumerate(headings, 1):
        size = units(heading)
        if size > REPORT_HEADING_UNITS:
            problems.append(
                f"Section {number} heading is {size} units; the limit is {REPORT_HEADING_UNITS}. "
                f'Shorten it to one claim: "{heading[:80]}"'
            )
    if re.search(r"^- \*\*(Finding|发现|Key finding)\*\*", text, re.M | re.I):
        problems.append(
            'Summary bullets start with a label ("**Finding**"); make each bold lead the '
            "finding itself."
        )
    exhibits = EXHIBIT.findall(text)
    numeric_sections = sum(
        1
        for chunk in re.split(r"^## ", text, flags=re.M)[2:]
        if len(re.findall(r"\d[\d.,]*\s?(?:%|°C|€|\$|m²|kWh|million|billion|万|亿)", chunk)) >= 3
    )
    wanted = min(4, (numeric_sections + 1) // 2)
    if len(exhibits) < wanted:
        problems.append(
            f"The report has {len(exhibits)} exhibits but {numeric_sections} sections rest on "
            f"compared numbers: add at least {wanted - len(exhibits)} exhibit(s) "
            "(:::exhibit chart table with caption and Source line) where numbers are compared."
        )
    for number, (_attrs, body) in enumerate(exhibits, 1):
        lines = [line for line in body.split("\n") if line.strip()]
        if not lines or lines[0].startswith("|"):
            problems.append(f"Exhibit {number} needs a caption line before its table.")
        if not any(line.startswith("|") for line in lines):
            problems.append(f"Exhibit {number} needs a Markdown table.")
        if not lines or not re.match(r"^(Source|Sources|来源)\s*[:：]", lines[-1]):
            problems.append(f'Exhibit {number} ends with a "Source: …" line.')
    return problems


def ensure_summary_mark(text: str) -> str:
    """The first section is the executive summary: it carries the summary marker."""

    if SUMMARY_MARK in text:
        return text
    return re.sub(
        r"^(## .+)$", lambda m: m.group(1) + "\n" + SUMMARY_MARK, text, count=1, flags=re.M
    )


LABEL = re.compile(
    r"^(Subtitle|Meta line|Meta|Verdict|Takeaway|Label|Context|Caption)\s*:\s*", re.I
)


def strip_labels(slide: str) -> str:
    """Drop format labels a writer copied onto a slide's lines (never inside the notes)."""

    out, notes = [], False
    for line in slide.split("\n"):
        if re.match(r"^\s*(Note|Notes|备注)\s*[:：]", line):
            notes = True
        out.append(line if notes else LABEL.sub("", line))
    return "\n".join(out)


def _same(one: str, two: str) -> bool:
    def norm(text: str) -> str:
        return re.sub(r"\W+", "", CITE.sub("", text)).casefold()

    return bool(one) and norm(one) == norm(two)


META = re.compile(
    r"\b(?:the sources?|the materials?|the evidence (?:shows|suggests)|supported points|"
    r"the documents?|the provided|this dataset|according to the (?:material|sources?))\b|"
    r"现有材料|材料显示|资料显示|根据材料",
    re.I,
)


def cite_captions(text: str) -> str:
    """An exhibit caption that states numbers cites what its table cites: the caption reads
    them off the table."""

    def fix(match: re.Match[str]) -> str:
        block = match.group(0)
        lines = block.split("\n")
        if len(lines) < 3:
            return block
        caption = lines[1]
        body = re.sub(r"^(?:Exhibit|图表|图)\s*\d+", "", caption)
        if CITE.search(caption) or not re.search(r"\d", body):
            return block
        refs = list(dict.fromkeys(re.findall(r"\[(\d+)\]", "\n".join(lines[2:]))))
        if not refs:
            return block
        lines[1] = caption.rstrip() + " " + "".join(f"[{n}]" for n in refs)
        return "\n".join(lines)

    return EXHIBIT.sub(fix, text)


def cite_exhibit_rows(text: str) -> str:
    """An exhibit row with numbers and no citation cites what the exhibit's Source line cites:
    the table is read off those sources, and each value must be checkable against them."""

    def fix(match: re.Match[str]) -> str:
        lines = match.group(0).split("\n")
        source = next(
            (ln for ln in reversed(lines) if re.match(r"^(Source|Sources|来源)\s*[:：]", ln)), ""
        )
        refs = "".join(f"[{n}]" for n in dict.fromkeys(re.findall(r"\[(\d+)\]", source)))
        if not refs:
            return match.group(0)
        out = []
        for line in lines:
            cells = line.strip().strip("|").split("|") if line.startswith("|") else []
            data = (
                cells
                and not all(re.fullmatch(r"\s*:?-{2,}:?\s*", c) for c in cells)
                and re.search(r"\d", "".join(cells[1:]))
                and not CITE.search(line)
                and line is not lines[0]
            )
            if data and out and any(o.startswith("|") for o in out):
                line = line.rstrip().rstrip("|").rstrip() + f" {refs} |"
            out.append(line)
        return "\n".join(out)

    return EXHIBIT.sub(fix, text)


LABEL_TITLE = re.compile(
    r"^(?:the )?(?:two|three|four|five|\d+) (?:key )?(?:findings|numbers|things|points|reasons|"
    r"facts|takeaways|lessons|questions|risks)\b|^(?:key (?:findings|takeaways|points)|next steps|"
    r"summary|overview|conclusions?)\s*$|^[三四五两]个(?:发现|数字|要点|原因)",
    re.I,
)


def _overlap(one: str, two: str) -> float:
    """How much of the shorter text's words (or CJK characters) the longer one shares."""

    def bag(text: str) -> set[str]:
        plain = CITE.sub("", text).casefold()
        words = set(re.findall(r"[a-z0-9]{3,}", plain))
        return words | {ch for ch in plain if "\u4e00" <= ch <= "\u9fff"}

    a, b = bag(one), bag(two)
    return len(a & b) / max(1, min(len(a), len(b)))
