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

CITE = re.compile(r"\[(?:\d+(?:\s*[,，、]\s*\d+)*|\?)\]")
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
                pattern = r"(?<!\w)[+-]?\d[\d,]*(?:\.\d+)?%?"
                source_values = {v.replace(",", "") for v in re.findall(pattern, passages)}
                header_unit = "%" if any("%" in cell for cell in table[0][1:]) else ""
                values = [
                    v.replace(",", "") for v in re.findall(pattern, CITE.sub("", " ".join(row[1:])))
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
            return (
                f"A “flow” slide is a numbered list of 2 to 6 steps; this one has {len(steps)}."
            )
    elif layout in ("timeline", "cards"):
        items = [match.group(1) for line in body if (match := ITEM.match(line))]
        most = 7 if layout == "timeline" else 6
        if not 2 <= len(items) <= most:
            return (
                f"A “{layout}” slide is a list of 2 to {most} lines; this one has {len(items)}."
            )
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
    return found


CHECKS: dict[str, Check] = {
    "section_count": section_count,
    "page_count": page_count,
    "first_and_last": first_and_last,
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
