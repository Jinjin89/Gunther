"""Checks a skill can ask for: what code can tell about an outline or a draft.

Each check reads the pages (a report's sections or a deck's slides) and its settings from
the skill, and says what it found. Findings are not verdicts on facts (the Checker in
outputs does that); they are what the editor and the revise step look at. A check never
changes the text.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

CITE = re.compile(r"\[(?:[ipd]:)?(?:\d+(?:\s*[,，、]\s*\d+)*|\?)\]")
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")
WORD = re.compile(r"[A-Za-z0-9][\w'’.%-]*")
ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
ORDERED = re.compile(r"^\s*\d+[.)]\s+\S")
BOLD_START = re.compile(r"^\*\*.+?\*\*")
BOLD_ONLY = re.compile(r"^\*\*(.+?)\*\*$")
IMAGE = re.compile(r"^!\[[^\]]*\]\(gunther-asset:([A-Za-z0-9_-]+)\)$")
# The line of dashes under a table's header, and a number with at most a sign and a currency
# sign before it and a unit after it.
RULE = re.compile(r"^\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?$")
VALUE = re.compile(r"[~≈<>≤≥±+\-−–]?[$€£¥￥]?\d+(?:\.\d+)?[^\d]{0,12}")
# About 1.7 units a Latin word, so that 14 English words come to about 24 Chinese characters.
WORD_UNITS = 1.7
TABLES = ("compare", "two_column", "bar_chart", "line_chart")
CHARTS = ("bar_chart", "line_chart")


def figures_of(item: Any) -> list[dict[str, Any]]:
    """The figures kept with a passage (its citation's ``figures``), each with an ``assetId``."""

    payload = getattr(item, "payload", None)
    citation = payload[0] if isinstance(payload, tuple) and payload else None
    return [figure for figure in getattr(citation, "figures", []) if figure.get("assetId")]


def units(text: str) -> float:
    """How long a piece of text reads: a CJK character is 1, a Latin word 1.7."""

    plain = CITE.sub("", text).replace("**", "").replace("__", "")
    return len(CJK.findall(plain)) + WORD_UNITS * len(WORD.findall(CJK.sub(" ", plain)))


@dataclass(frozen=True)
class Page:
    """A section of a report or a slide of a deck, as a check sees it."""

    index: int
    heading: str
    layout: str = ""
    # The whole section, heading and speaker notes included. A deck's layout marker is not
    # part of it: it is the line that says how the slide is drawn, and is given as ``layout``.
    text: str = ""
    link: str = ""

    @property
    def body(self) -> list[str]:
        """The lines under the heading, up to the speaker notes."""

        lines = self.text.splitlines()[1:]
        cut = next((at for at, line in enumerate(lines) if line.startswith("Note:")), len(lines))
        return [line for line in lines[:cut] if line.strip()]


@dataclass(frozen=True)
class Context:
    kind: str  # "report" | "slides"
    use: str | None = None  # a deck's: "talk" | "read"
    limits: Mapping[str, int] | None = None
    evidence: Mapping[int, Any] | None = None


@dataclass(frozen=True)
class Finding:
    check: str
    index: int | None  # the page, counted from 0; None for the whole output
    message: str

    def out(self) -> dict[str, Any]:
        return {"check": self.check, "index": self.index, "message": self.message}


Check = Callable[[Sequence[Page], Mapping[str, Any], Context], list[Finding]]


def _count(name: str, pages: Sequence[Page], low: int | None, high: int | None) -> list[Finding]:
    what = "slides" if name == "page_count" else "sections"
    if low is not None and len(pages) < low:
        return [Finding(name, None, f"There are {len(pages)} {what}; at least {low} are wanted.")]
    if high is not None and len(pages) > high:
        return [Finding(name, None, f"There are {len(pages)} {what}; at most {high} fit.")]
    return []


def section_count(pages: Sequence[Page], _: Mapping[str, Any], context: Context) -> list[Finding]:
    limits = context.limits or {}
    return _count("section_count", pages, limits.get("sections_min"), limits.get("sections_max"))


def page_count(pages: Sequence[Page], _: Mapping[str, Any], context: Context) -> list[Finding]:
    limits = context.limits or {}
    return _count("page_count", pages, limits.get("pages_min"), limits.get("pages_max"))


def first_and_last(pages: Sequence[Page], _: Mapping[str, Any], __: Context) -> list[Finding]:
    found = []
    if pages and pages[0].layout != "title":
        found.append(Finding("first_and_last", 0, "The first slide should be the title slide."))
    if len(pages) > 1 and pages[-1].layout != "takeaways":
        found.append(
            Finding("first_and_last", len(pages) - 1, "The last slide should be the takeaways.")
        )
    return found


def repeated_heading(
    pages: Sequence[Page], _: Mapping[str, Any], context: Context
) -> list[Finding]:
    """Flag exact repeated slide headings for a human review; similarity is not logic."""
    if context.kind != "slides":
        return []
    seen: dict[str, int] = {}
    found = []
    for page in pages:
        heading = " ".join(page.heading.casefold().split())
        if not heading:
            continue
        if heading in seen:
            found.append(
                Finding(
                    "repeated_heading",
                    page.index,
                    f"This repeats the heading on slide {seen[heading] + 1}; "
                    "check whether the slides make distinct points.",
                )
            )
        else:
            seen[heading] = page.index
    return found


def slide_links(pages: Sequence[Page], _: Mapping[str, Any], context: Context) -> list[Finding]:
    """Report absent planned transitions without claiming that present links are sound."""
    if context.kind != "slides":
        return []
    return [
        Finding(
            "slide_link",
            page.index,
            "No planned relationship to the previous slide is recorded; review the transition.",
        )
        for page in pages[1:]
        if not page.link.strip()
    ]


def layout_run(pages: Sequence[Page], params: Mapping[str, Any], _: Context) -> list[Finding]:
    """The same layout too many times in a row. With ``max_text`` the text layout, the last
    resort, is also kept to that many slides in a deck and never used twice in a row."""

    longest = int(params.get("max_run", 2))
    most_text = params.get("max_text")
    found, run = [], 0
    for at, page in enumerate(pages):
        same = at and page.layout == pages[at - 1].layout
        run = run + 1 if same else 1
        if run == longest + 1 and page.layout not in ("", "title", "takeaways"):
            found.append(
                Finding(
                    "layout_run",
                    at,
                    f"“{page.layout}” is used {run} times in a row; vary the form, or check "
                    "the slides are not just retelling the material.",
                )
            )
        if most_text is not None and page.layout == "text" and run == 2:
            found.append(
                Finding(
                    "layout_run",
                    at,
                    "Two text slides in a row; text is the last resort, so give one of them "
                    "the layout that matches what it says.",
                )
            )
    if most_text is not None:
        texts = [page for page in pages if page.layout == "text"]
        if len(texts) > int(most_text):
            found.append(
                Finding(
                    "layout_run",
                    None,
                    f"{len(texts)} slides use the text layout; at most {most_text} should. "
                    "Use the layout that matches a number, a comparison, a sequence, a change "
                    "over time or a figure.",
                )
            )
    return found


def heading_length(
    pages: Sequence[Page], params: Mapping[str, Any], context: Context
) -> list[Finding]:
    low, high = float(params.get("min_units", 0)), float(params.get("max_units", 1_000))
    found = []
    for page in pages:
        if page.layout == "title" or (context.kind == "slides" and page.index == 0):
            continue
        size = units(page.heading)
        if size < low:
            found.append(
                Finding(
                    "heading_length", page.index, "The heading is a topic word; make it a claim."
                )
            )
        elif size > high:
            found.append(
                Finding("heading_length", page.index, "The heading is too long for one claim.")
            )
    return found


def filler_phrases(pages: Sequence[Page], params: Mapping[str, Any], _: Context) -> list[Finding]:
    phrases = [str(item) for item in params.get("phrases", [])]
    found = []
    for page in pages:
        lowered = page.text.casefold()
        used = [phrase for phrase in phrases if phrase.casefold() in lowered]
        if used:
            found.append(
                Finding(
                    "filler_phrases",
                    page.index,
                    "Empty phrases: " + ", ".join(f"“{phrase}”" for phrase in used),
                )
            )
    return found


def density(pages: Sequence[Page], params: Mapping[str, Any], context: Context) -> list[Finding]:
    """Too many items on a slide, or items too long, for what the deck is for."""

    talk = context.use != "read"
    found = []
    for page in pages:
        if page.layout in ("title", *TABLES, "big_number", "quote", "figure"):
            continue
        items = [match.group(1) for line in page.body if (match := ITEM.match(line))]
        loose = [line for line in page.body if not ITEM.match(line)]
        most = params.get(f"{page.layout}_items")
        if most is None:
            most = params.get("talk_items" if talk else "read_items")
        longest = params.get("talk_item_units" if talk else "read_item_units")
        if page.layout in ("cards", "timeline") and longest is not None:
            longest = 2 * longest  # a name, then a sentence
        count = len(items) + (len(loose) if page.layout in ("", "text") else 0)
        if most is not None and count > int(most):
            found.append(
                Finding("density", page.index, f"{count} items; at most {most} fit this slide.")
            )
        if longest is not None:
            long_ones = [item for item in [*items, *loose] if units(item) > float(longest)]
            if long_ones and talk:
                found.append(
                    Finding(
                        "density",
                        page.index,
                        f"{len(long_ones)} line(s) too long for a talk slide; shorten them or "
                        "move the words to the speaker notes.",
                    )
                )
            elif long_ones:
                found.append(Finding("density", page.index, f"{len(long_ones)} line(s) too long."))
    return found


def _table(page: Page) -> tuple[list[str], list[list[str]]] | None:
    rows = [line.strip() for line in page.body if line.strip().startswith("|")]
    if len(rows) < 2:
        return None
    cells = [[cell.strip() for cell in row.strip("|").split("|")] for row in rows]
    return cells[0], [row for row in cells[2:] if any(row)]


def diagram_edge_rows(text: str) -> list[list[str]]:
    table = _table(Page(0, "", "diagram", text))
    return table[1] if table else []


DIAGRAM_CITES = re.compile(r"\[(?:[ipd]:)?(\d+(?:\s*[,，、]\s*\d+)*)\]")


def _diagram_width(text: str) -> float:
    plain = re.sub(
        r"\\(.)", r"\1", CITE.sub("", text).replace("**", "").replace("__", "").replace("`", "")
    )
    return sum(
        1.0 if CJK.fullmatch(char) else 0.28 if char.isspace() else 0.58 if char.isalnum() else 0.4
        for char in plain
    )


def diagram_problem(text: str) -> str | None:
    """Validate a small evidence-labeled DAG described as a three-column Markdown table."""
    page = Page(0, "", "diagram", text)
    table = _table(page)
    if table is None:
        return "A diagram needs one Markdown table with a header and at least one edge."
    header, rows = table

    def normalize(value: str) -> str:
        plain = re.sub(
            r"\\(.)",
            r"\1",
            CITE.sub("", value).replace("**", "").replace("__", "").replace("`", ""),
        )
        return re.sub(r"\s+", " ", plain.strip()).casefold()

    normalized_headers = [normalize(cell) for cell in header]
    if (
        len(header) != 3
        or any(not label for label in normalized_headers)
        or len(set(normalized_headers)) != 3
    ):
        return (
            "A diagram table needs three distinct, named columns in start, relationship, end order."
        )
    if not 1 <= len(rows) <= 8 or any(len(row) != 3 for row in rows):
        return f"A diagram supports 1 to 8 complete edge rows; this table has {len(rows)}."
    nodes: list[str] = []
    adjacency: dict[str, set[str]] = {}
    indegree: dict[str, int] = {}
    edges: set[tuple[str, str]] = set()
    edge_rows: list[tuple[str, str, str]] = []
    for row in rows:
        source, relation, target = row
        if CITE.search(source) or CITE.search(target):
            return (
                "Diagram node labels cannot carry citations; cite each relationship cell instead."
            )
        source_key, target_key = normalize(source), normalize(target)
        relation_text = normalize(relation)
        if not source_key or not target_key or not relation_text:
            return "Every diagram edge needs a readable start, relationship, and end label."
        if (
            units(source) > 24
            or units(target) > 24
            or units(relation) > 18
            or _diagram_width(source) > 15
            or _diagram_width(target) > 15
            or _diagram_width(relation) > 13.5
        ):
            return (
                "A diagram label is too long to fit safely; use the original table or split "
                "the graph."
            )
        refs = [
            int(number)
            for group in DIAGRAM_CITES.findall(relation)
            for number in re.findall(r"\d+", group)
        ]
        if not refs:
            return "Every diagram relationship needs at least one numbered source citation."
        if source_key == target_key:
            return "A diagram cannot contain a self edge."
        edge = (source_key, target_key)
        if edge in edges:
            return "A diagram cannot repeat the same directed edge."
        edges.add(edge)
        edge_rows.append((source_key, relation, target_key))
        for node in (source_key, target_key):
            if node not in indegree:
                nodes.append(node)
                indegree[node] = 0
                adjacency[node] = set()
        adjacency[source_key].add(target_key)
        indegree[target_key] += 1
    if not 2 <= len(nodes) <= 6:
        return f"A diagram supports 2 to 6 distinct nodes; this table has {len(nodes)}."
    ready = [node for node in nodes if indegree[node] == 0]
    rank = {node: 0 for node in ready}
    visited = 0
    while ready:
        node = ready.pop(0)
        visited += 1
        for target in adjacency[node]:
            rank[target] = max(rank.get(target, 0), rank[node] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(nodes):
        return "This relationship table contains a cycle; keep it as a table or rewrite its edges."
    layers: dict[int, int] = {}
    for value in rank.values():
        layers[value] = layers.get(value, 0) + 1
    if len(layers) > 3 or max(layers.values(), default=0) > 3:
        return "This graph is too deep or wide to lay out clearly; split it or keep the table."
    by_rank = {level: [node for node in nodes if rank[node] == level] for level in layers}
    centers = {
        node: 210 + (index - (len(by_rank[rank[node]]) - 1) / 2) * 140
        for node in nodes
        for index in [by_rank[rank[node]].index(node)]
    }
    lanes: dict[int, list[float]] = {}
    for source, _, target in edge_rows:
        gap = rank[target] - rank[source]
        if gap != 1:
            return (
                "A relationship skips a graph layer; keep the table or describe one edge at a time."
            )
        center = (centers[source] + centers[target]) / 2
        lane = rank[source]
        if any(abs(center - prior) < 64 for prior in lanes.get(lane, [])):
            return (
                "Relationship labels would overlap in this graph; keep the table or split "
                "the diagram."
            )
        lanes.setdefault(lane, []).append(center)
    return None


def table_size(pages: Sequence[Page], params: Mapping[str, Any], _: Context) -> list[Finding]:
    columns, rows = int(params.get("max_columns", 5)), int(params.get("max_rows", 6))
    found = []
    for page in pages:
        if page.layout not in TABLES:
            continue
        table = _table(page)
        if table is None:
            found.append(
                Finding("table_size", page.index, f"A “{page.layout}” slide needs a table.")
            )
            continue
        header, body = table
        if len(header) > columns or len(body) > rows:
            found.append(
                Finding(
                    "table_size",
                    page.index,
                    f"The table is {len(body)} × {len(header)}; at most {rows} rows and "
                    f"{columns} columns fit.",
                )
            )
    return found


def chart_numbers_cited(
    pages: Sequence[Page], _: Mapping[str, Any], context: Context
) -> list[Finding]:
    found = []
    for page in pages:
        if page.layout not in CHARTS or (table := _table(page)) is None:
            continue
        bare = [
            row[0]
            for row in table[1]
            if re.search(r"\d", CITE.sub("", " ".join(row[1:])))
            and not re.search(r"\[\d", " ".join(row))
        ]
        if bare:
            found.append(
                Finding(
                    "chart_numbers_cited",
                    page.index,
                    "Numbers with no source: " + ", ".join(f"“{label}”" for label in bare),
                )
            )
        if context.evidence is not None:
            for row in table[1]:
                joined = " ".join(row)
                refs = [
                    int(n)
                    for group in re.findall(r"\[([\d,，、 ]+)\]", joined)
                    for n in re.findall(r"\d+", group)
                ]
                passages = "\n".join(
                    context.evidence[n].context or context.evidence[n].text
                    for n in refs
                    if n in context.evidence
                )
                # Permit CJK text immediately before a value while avoiding a partial match
                # inside an ASCII identifier such as ``rate12``.
                pattern = r"(?<![A-Za-z0-9_.+\-−–—])[+\-−–—]?\d[\d,]*(?:\.\d+)?%?"

                def normalized(value: str) -> str:
                    return (
                        value.replace("−", "-")
                        .replace("–", "-")
                        .replace("—", "-")
                        .replace(",", "")
                    )

                source_values = {normalized(v) for v in re.findall(pattern, passages)}
                header_unit = "%" if any("%" in cell for cell in table[0][1:]) else ""
                values = [
                    normalized(v)
                    for v in re.findall(pattern, CITE.sub("", " ".join(row[1:])))
                ]
                missing = [
                    v
                    for v in values
                    if v + (header_unit if not v.endswith("%") else "") not in source_values
                ]
                if missing:
                    found.append(
                        Finding(
                            "chart_numbers_cited",
                            page.index,
                            f"Chart values for “{row[0]}” are absent from cited passages: "
                            f"{', '.join(missing)}. Use sourced values with matching units; "
                            "a derived value needs an explicit formula and operands.",
                        )
                    )
    return found


def _rows(page: Page) -> list[list[str]] | None:
    """The rows of the table in a page (not its header), or None when it has no table: a
    header row, a line of dashes under it, and rows."""

    lines = [line.strip() for line in page.body if line.strip().startswith("|")]
    if len(lines) < 2 or not RULE.match(lines[1]):
        return None
    cells = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]
    return [row for row in cells[2:] if any(row)]


def _is_value(cell: str) -> bool:
    """Whether a table cell is a number once its citations, unit and thousands separators are
    taken off."""

    plain = CITE.sub("", cell).replace("**", "").replace("__", "").strip()
    plain = re.sub(r"(?<=\d)[,，\u2009\u202f ](?=\d{3}(?!\d))", "", plain)
    return VALUE.fullmatch(plain) is not None


def _misfit(page: Page, context: Context) -> str | None:
    """Why a page does not take the form its layout asks for, or None when it does."""

    layout, body = page.layout, page.body
    if layout == "composed":
        # The bounded scene validator checks this layout after the body has been fact-checked.
        return None
    if layout == "title":
        # The title stays the title: a missing message line is no reason to draw it as text.
        first = next((line for line in page.text.splitlines() if line.strip()), "")
        if not first.startswith("# "):
            return (
                "A “title” slide is a “# ” title line with one line under it that says the "
                "core message."
            )
    elif layout == "big_number":
        number = BOLD_ONLY.match(body[0].strip()) if body else None
        if number is None or units(number.group(1)) > 24 or len(body) < 2:
            return (
                "A “big_number” slide is one line that is only the number in bold (like "
                "**62%**; at most about 24 characters or 14 words), then a line saying what "
                "it is compared with and where it comes from."
            )
    elif layout in ("compare", "two_column"):
        if not _rows(page):
            return (
                f"A “{layout}” slide is a table: a header row, a line of dashes under it, "
                "then the rows."
            )
    elif layout == "diagram":
        if problem := diagram_problem(page.text):
            return problem
    elif layout in CHARTS:
        rows = _rows(page)
        if rows is None:
            return (
                f"A “{layout}” slide is a table: a header row, a line of dashes under it, "
                "then 2 to 6 rows with a number in the second column."
            )
        if not 2 <= len(rows) <= 6:
            return f"A “{layout}” slide is a table of 2 to 6 rows; this one has {len(rows)}."
        bad = [row[0] for row in rows if len(row) < 2 or not _is_value(row[1])]
        if bad:
            return (
                f"In a “{layout}” slide the second column is a number (the unit goes in the "
                f"header); it is not for {', '.join(f'“{label}”' for label in bad)}."
            )
    elif layout == "flow":
        steps = [line for line in body if ORDERED.match(line)]
        if not 2 <= len(steps) <= 6:
            return f"A “flow” slide is a numbered list of 2 to 6 steps; this one has {len(steps)}."
    elif layout in ("timeline", "cards"):
        items = [match.group(1) for line in body if (match := ITEM.match(line))]
        most = 7 if layout == "timeline" else 6
        if not 2 <= len(items) <= most:
            return f"A “{layout}” slide is a list of 2 to {most} lines; this one has {len(items)}."
        if any(not BOLD_START.match(item) for item in items):
            what = "the time" if layout == "timeline" else "a name"
            return f"In a “{layout}” slide every line starts with {what} in bold, like **…**."
    elif layout == "quote":
        quoted = next((at for at, line in enumerate(body) if line.strip().startswith("> ")), None)
        by = next((at for at, line in enumerate(body) if line.strip().startswith("—")), None)
        if quoted is None or by is None or by < quoted:
            return (
                "A “quote” slide is a line starting with “> ” that holds the words, then a "
                "line starting with “—” that says who said it."
            )
    elif layout == "figure":
        image = IMAGE.match(body[0].strip()) if body else None
        if image is None or len(body) < 2 or any("![" in line for line in body[1:]):
            return (
                "A “figure” slide is one line that is only the image, ![caption]"
                "(gunther-asset:…), then one line saying what to look at."
            )
        if context.evidence is not None:
            kept = {
                str(figure["assetId"])
                for item in context.evidence.values()
                for figure in figures_of(item)
            }
            if image.group(1) not in kept:
                return (
                    "The image of a “figure” slide must be one of the figures kept with the "
                    "material, written exactly as given."
                )
    return None


def layout_shape(pages: Sequence[Page], _: Mapping[str, Any], context: Context) -> list[Finding]:
    """A slide that does not take the form its layout asks for (references/layouts.md), so it
    cannot be drawn as that layout. The text and takeaways layouts take any form."""

    found = []
    for page in pages:
        if problem := _misfit(page, context):
            found.append(Finding("layout_shape", page.index, problem))
            continue
        if page.layout == "diagram" and context.evidence is not None:
            table = _table(page)
            missing = sorted(
                {
                    int(number)
                    for row in (table[1] if table else [])
                    for group in DIAGRAM_CITES.findall(" ".join(row[1:2]))
                    for number in re.findall(r"\d+", group)
                    if int(number) not in context.evidence
                }
            )
            if missing:
                found.append(
                    Finding(
                        "layout_shape",
                        page.index,
                        "Diagram relationships refer to unavailable source number(s): "
                        f"{', '.join(map(str, missing))}.",
                    )
                )
    return found


CHECKS: dict[str, Check] = {
    "section_count": section_count,
    "page_count": page_count,
    "first_and_last": first_and_last,
    "repeated_heading": repeated_heading,
    "slide_links": slide_links,
    "layout_run": layout_run,
    "heading_length": heading_length,
    "filler_phrases": filler_phrases,
    "density": density,
    "table_size": table_size,
    "chart_numbers_cited": chart_numbers_cited,
    "layout_shape": layout_shape,
}


def run_checks(
    names: Sequence[str],
    pages: Sequence[Page],
    params: Mapping[str, Mapping[str, Any]],
    context: Context,
) -> list[Finding]:
    found: list[Finding] = []
    for page in pages:
        if page.text and not any(line.strip() for line in page.body):
            found.append(Finding("empty_page", page.index, "This page has no content."))
    for name in names:
        found += CHECKS[name](pages, params.get(name, {}), context)
    return found
