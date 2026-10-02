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
# About 1.7 units a Latin word, so that 14 English words come to about 24 Chinese characters.
WORD_UNITS = 1.7
TABLES = ("compare", "two_column", "bar_chart", "line_chart")
CHARTS = ("bar_chart", "line_chart")


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
    text: str = ""  # the whole section, heading and speaker notes included

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
    longest = int(params.get("max_run", 2))
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
        if page.layout in ("title", *TABLES, "big_number", "quote"):
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


def chart_numbers_cited(pages: Sequence[Page], _: Mapping[str, Any], __: Context) -> list[Finding]:
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
}


def run_checks(
    names: Sequence[str],
    pages: Sequence[Page],
    params: Mapping[str, Mapping[str, Any]],
    context: Context,
) -> list[Finding]:
    found: list[Finding] = []
    for name in names:
        found += CHECKS[name](pages, params.get(name, {}), context)
    return found
