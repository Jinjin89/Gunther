"""The studio deck and report format (docs/STUDIO_FORMAT.md): layouts, budgets, and a checker
that says exactly what a slide gets wrong, so the writer can be asked to fix that and only that.

The desktop draws the same grammar (apps/desktop/src/outputs/deck/parseDeck.ts); the fixtures in
apps/desktop/src/outputs/deck/fixtures/ are read by tests/test_studio_format.py on this side.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from gunther.studio.titles import is_topic_title

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
            if re.search(r"\d", CITE.sub("", lines[0])) and not CITE.search(lines[0]):
                problems.append(
                    "The subtitle states numbers: cite the passages they come from, like [2]."
                )
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
        latin_words = re.findall(r"[A-Za-z0-9'’%.-]+", CITE.sub("", slide.title))
        cjk_chars = sum(1 for ch in slide.title if "\u4e00" <= ch <= "\u9fff")
        short = (len(latin_words) < 5 and cjk_chars < 8) if not cjk_chars else cjk_chars < 8
        if slide.layout not in ("title", "section") and (
            short or is_topic_title(slide.title) or LABEL_TITLE.search(slide.title)
        ):
            problems.append(
                "The title names a topic or announces content instead of stating the point. "
                'Write the point itself as a full sentence (not "Early applications" but what '
                "the early applications show)."
            )
        uncited = uncited_lines(slide.body if slide.layout != "title" else slide.body[:1])
        if uncited:
            problems.append(
                f'"{uncited[0][:70]}" states a number without a citation: add the passage '
                "number after it, or drop the number."
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
        if number > 1 and is_label_heading(heading):
            # The first section is the executive summary; its heading may be a plain label.
            problems.append(
                f'Section {number} heading "{heading[:80]}" names a topic instead of stating '
                "the point. Write the section's claim itself as a short sentence."
            )
        if size > REPORT_HEADING_UNITS:
            problems.append(
                f"Section {number} heading is {size} units; the limit is {REPORT_HEADING_UNITS}. "
                f'Shorten it to one claim: "{heading[:80]}"'
            )
    body_text = re.sub(r"^:::exhibit.*?^:::\s*$", "", text, flags=re.M | re.S)
    for match in META.finditer(body_text):
        problems.append(
            f'"{match.group(0)}": write about the world, not about the material — state the '
            "finding itself and cite the passage."
        )
        break
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
    r"the documents?|the provided|this dataset|(?:in |nothing in )?the files?\b|"
    r"according to the (?:material|sources?))\b|"
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


def is_label_heading(heading: str) -> bool:
    """A heading that names a topic ("Three asks", "Next steps") rather than stating a point."""

    plain = CITE.sub("", heading).replace("**", "").strip()
    latin_words = re.findall(r"[A-Za-z0-9'’%.-]+", plain)
    cjk_chars = sum(1 for ch in plain if "\u4e00" <= ch <= "\u9fff")
    short = cjk_chars < 8 if cjk_chars else len(latin_words) < 5
    return short or is_topic_title(plain) or bool(LABEL_TITLE.search(plain))


LABEL_TITLE = re.compile(
    r"^(?:the )?(?:two|three|four|five|\d+) (?:key )?(?:findings|numbers|things|points|reasons|"
    r"facts|takeaways|lessons|questions|risks|asks|steps|actions|recommendations|options)\b|"
    r"\bin one (?:sentence|slide|line|breath)\b|"
    r"\b(?:talk|deck|presentation|report|story) in "
    r"(?:two|three|four|five|\d+) (?:parts|steps|acts)\b|"
    r"^(?:agenda|outline|roadmap|contents|what (?:we|i) will cover)\s*$|"
    r"^(?:key (?:findings|takeaways|points)|next steps|"
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


def _countable(number: str) -> bool:
    if number.endswith("%"):
        return True
    plain = number.replace(",", "").replace("，", "")
    if re.fullmatch(r"(?:19|20)\d\d", plain):
        return False  # a year
    try:
        value = float(plain)
    except ValueError:
        return False
    return not (value == int(value) and value <= 12)


def uncited_lines(lines: list[str]) -> list[str]:
    """Body lines (a table row is a line) that state a number but cite nothing. A table's
    header row and a stat value whose label line cites are not counted."""

    found = []
    for index, line in enumerate(lines):
        text = line.strip()
        if not text or text.startswith(("#", "!", "|---", "| ---")) or CITE.search(text):
            continue
        following = next((ln.strip() for ln in lines[index + 1 :] if ln.strip()), "")
        if text.startswith("|") and re.fullmatch(r"\|?[\s:|-]+\|?", following):
            continue
        if re.fullmatch(r"\*\*[^*]+\*\*", text) and CITE.search(following):
            continue
        plain = re.sub(r"\b[A-Za-z]+[-‑]?\d+[A-Za-z]*\b", "", text)  # names like GPT-4
        if any(_countable(n) for n in re.findall(r"\d[\d,，.]*%?", plain)):
            found.append(text)
    return found


def trim_to_notes(text: str, *, first: bool, read: bool = False) -> str:
    """Last resort for a slide still over a length budget: the trailing clause of its longest
    over-long line moves into the speaker notes, with its citations, so nothing is lost. Kept
    only when it leaves the slide with fewer problems."""

    slide = parse_slide(text)
    before = [p for p in check_slide(slide, first=first, read=read) if "units long" in p]
    if not before:
        return text
    lines = text.split("\n")
    body = [
        n
        for n, ln in enumerate(lines)
        if ln.strip()
        and not ln.lstrip().startswith(("<!--", "#", "|", "!"))
        and not re.match(r"^\s*(Note|Notes|备注)\s*[:：]", ln)
    ]
    note_at = next(
        (n for n, ln in enumerate(lines) if re.match(r"^\s*(Note|Notes|备注)\s*[:：]", ln)), None
    )
    for n in sorted(body, key=lambda i: -units(lines[i])):
        line = lines[n]
        cuts = [m.start() for m in re.finditer(r"(?:; |；| — | – |, but |，但|。)", line)]
        cuts = [c for c in cuts if c > len(line) * 0.4]
        if not cuts:
            continue
        cut = cuts[-1]
        head, tail = line[:cut].rstrip(" ;；—–,，"), line[cut:].lstrip(" ;；—–,，。").strip()
        if not tail:
            continue
        refs = "".join(dict.fromkeys(re.findall(r"\[(?:\d+|\?)\]", line)))
        if refs and not CITE.search(head):
            head = head + " " + refs
        candidate = list(lines)
        candidate[n] = head
        tail = tail[0].upper() + tail[1:] if tail[:1].islower() else tail
        tail = tail if CITE.search(tail) or not refs else tail + " " + refs
        if note_at is None:
            candidate.append(f"Note: {tail}")
        else:
            candidate[note_at] = candidate[note_at].rstrip() + f" {tail}"
        fixed = "\n".join(candidate)
        after = [
            p for p in check_slide(parse_slide(fixed), first=first, read=read) if "units long" in p
        ]
        if len(after) < len(before):
            return fixed
    return text


def _telling(keys) -> bool:
    """Numbers specific enough to say where they came from: two or more, or one that is a
    decimal or has three or more digits."""

    keys = {re.sub(r"%$", "", k) for k in keys}
    return len(keys) >= 2 or any("." in k or len(re.sub(r"\D", "", k)) >= 3 for k in keys)


def table_separators(text: str) -> str:
    """A Markdown table whose header row has no separator line under it gets one, so the first
    row reads (and draws) as the header, not as data."""

    lines = text.split("\n")
    out: list[str] = []
    for index, line in enumerate(lines):
        out.append(line)
        is_row = line.strip().startswith("|")
        starts = is_row and (index == 0 or not lines[index - 1].strip().startswith("|"))
        following = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if starts and following.startswith("|") and not re.fullmatch(r"\|?[\s:|-]+\|?", following):
            columns = len(line.strip().strip("|").split("|"))
            out.append("|" + " --- |" * columns)
    return "\n".join(out)


def cite_stated_numbers(text: str, passages: dict[int, str]) -> str:
    """A line still stating numbers with no citation cites the passage that states all of
    them, or else two passages the same text already cites that state them between them.
    Only when the numbers tell where they came from (see ``_telling``)."""

    def stated(ref: int) -> set[str]:
        return {n.lstrip("-") for n in numbers_in(passages[ref])}

    near = [int(n) for n in dict.fromkeys(re.findall(r"\[(\d+)\]", text)) if int(n) in passages]
    out = []
    for line in text.split("\n"):
        if line.strip() and line in uncited_lines([line]):
            numbers = [n.rstrip("%") for n in re.findall(r"\d[\d,，.]*%?", line) if _countable(n)]
            keys = {normal_number(n) for n in numbers}
            if keys and _telling(keys):
                found = [ref for ref in passages if keys <= stated(ref)]
                if len(found) == 1:
                    line = line.rstrip() + f" [{found[0]}]"
                elif not found:
                    pairs = [
                        (one, two)
                        for i, one in enumerate(near)
                        for two in near[i + 1 :]
                        if keys <= stated(one) | stated(two)
                        and keys & stated(one)
                        and keys & stated(two)
                    ]
                    if pairs:
                        line = line.rstrip() + f" [{pairs[0][0]}][{pairs[0][1]}]"
        out.append(line)
    return "\n".join(out)


def cite_repeated_numbers(text: str) -> str:
    """A line that repeats a number already cited elsewhere in the same document carries that
    citation too. Only when every countable number on the line is found cited elsewhere, and
    the match tells (see ``_telling``): one common number ("90 days") borrows nothing."""

    cited: dict[str, list[str]] = {}
    for line in text.split("\n"):
        refs = re.findall(r"\[(\d+)\]", line)
        if not refs:
            continue
        for number in re.findall(r"\d[\d,，.]*%?", CITE.sub("", line)):
            if _countable(number):
                key = normal_number(number.rstrip("%")) + ("%" if number.endswith("%") else "")
                cited.setdefault(key, [])
                cited[key] += [r for r in refs if r not in cited[key]]
    out = []
    for line in text.split("\n"):
        if line.strip() and line in uncited_lines([line]):
            numbers = [n for n in re.findall(r"\d[\d,，.]*%?", line) if _countable(n)]
            keys = [
                normal_number(n.rstrip("%")) + ("%" if n.endswith("%") else "") for n in numbers
            ]
            if keys and _telling(keys) and all(k in cited for k in keys):
                refs = list(dict.fromkeys(r for k in keys for r in cited[k]))[:3]
                line = line.rstrip() + " " + "".join(f"[{r}]" for r in refs)
        out.append(line)
    return "\n".join(out)
