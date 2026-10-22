# ruff: noqa: E501
"""Hard-defect linter: pure code, no model (docs/ARTIFACT_QUALITY_GOAL.md section 1).

``lint(kind, content, use, measure)`` returns a list of ``Defect``. Rules:

- ``overflow``, ``small_text``, ``empty_half``: read from the renderer's ``measure.json``
  (skipped, and said so by the caller, when there is none).
- ``over_budget``: a slide field over the budgets of ``docs/STUDIO_FORMAT.md``. Units: a CJK
  character counts 2, anything else 1; citation marks and ``**`` do not count. ``read`` decks
  get 1.5x on body fields (titles keep theirs). Layouts the format does not know (the older
  skills decks) are checked for their title only.
- ``topic_title``: an action title (or a report heading) under 12 units, or without a
  verb-like word. Latin: fewer than 4 words, or (under 6 words) none of an auxiliary/common verb, a word
  ending in -ed/-ing/-ize/-ise/-ify, or a digit. CJK: under 8 characters, or none of a short list of
  verb characters and no digit. Title and section slides are exempt.
- ``meta_phrase``: disclaimer or meta words on a slide's visible text (not speaker notes).
- ``uncited_number``: a number on a line (a table row counts as a line) with no ``[n]`` or
  ``[?]`` on it. Not counted: years, whole numbers up to 12 without a percent sign, headings,
  marker lines, and a bold-only line (a stat value), which takes the citation of the next line.
- ``missing_notes``: a talk deck's content slide (not title or section) without ``Note:``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from gunther.outputs import split_document

MARKER = re.compile(r"^\s*<!--\s*(.*?)\s*-->\s*$")
CITE = re.compile(r"\[(?:\d+(?:\s*[,–-]\s*\d+)*|\?)\]")
NOTE_LINE = re.compile(r"^\s*Note:\s?", re.IGNORECASE)
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")
ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
BOLD_LEAD = re.compile(r"^\*\*(.+?)\*\*[:：]?\s*(.*)$")

META_PHRASES = (
    "does not represent", "do not represent", "not represent", "synthetic",
    "cannot establish", "can not establish", "not actual", "for illustration only",
    "illustrative only", "fictional", "made-up", "made up data", "as an ai",
    "不代表", "仅供", "无法确定", "无法证实", "虚构", "模拟数据", "训练数据",
)  # fmt: skip

AUXILIARIES = frozenset(
    [
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "can",
        "could",
        "will",
        "would",
        "may",
        "might",
        "must",
        "should",
        "shall",
        "isn't",
        "aren't",
        "wasn't",
        "don't",
        "doesn't",
        "can't",
        "won't",
    ]
)
COMMON_VERBS = frozenset(
    [
        "cut",
        "cuts",
        "raise",
        "raises",
        "rise",
        "rises",
        "fall",
        "falls",
        "drop",
        "drops",
        "grow",
        "grows",
        "lead",
        "leads",
        "make",
        "makes",
        "show",
        "shows",
        "need",
        "needs",
        "mean",
        "means",
        "beat",
        "beats",
        "lose",
        "loses",
        "win",
        "wins",
        "keep",
        "keeps",
        "hold",
        "holds",
        "turn",
        "turns",
        "set",
        "sets",
        "let",
        "lets",
        "help",
        "helps",
        "cost",
        "costs",
        "save",
        "saves",
        "drive",
        "drives",
        "bring",
        "brings",
        "give",
        "gives",
        "take",
        "takes",
        "find",
        "finds",
        "put",
        "puts",
        "run",
        "runs",
        "reach",
        "reaches",
        "stay",
        "stays",
        "gain",
        "gains",
        "split",
        "splits",
        "cap",
        "caps",
        "depend",
        "depends",
        "fail",
        "fails",
        "work",
        "works",
        "break",
        "breaks",
        "open",
        "opens",
        "close",
        "closes",
        "build",
        "builds",
        "move",
        "moves",
        "shift",
        "shifts",
        "outpace",
        "outpaces",
        "outperform",
        "outperforms",
        "slow",
        "slows",
        "speed",
        "speeds",
        "double",
        "doubles",
        "triple",
        "triples",
        "halve",
        "halves",
        "shrink",
        "shrinks",
        "expand",
        "expands",
        "matter",
        "matters",
        "explain",
        "explains",
        "require",
        "requires",
        "replace",
        "replaces",
        "limit",
        "limits",
        "support",
        "supports",
        "decide",
        "decides",
        "choose",
        "chooses",
        "ask",
        "asks",
        "answer",
        "answers",
        "promise",
        "promises",
        "signal",
        "signals",
        "offer",
        "offers",
    ]
)
VERB_SUFFIXES = ("ed", "ing", "ize", "ise", "ify", "ises", "izes", "ifies")
CJK_VERBS = "是有为将达增降提超占实推促保稳扩降涨跌升减加使让需要能会可已仍未难靠赖决定选择取得带来形成成为面临"

SLIDE_BUDGETS: dict[str, dict[str, int]] = {}  # filled below, one dict per layout


def units(text: str) -> int:
    """Length in budget units: a CJK character is 2, anything else 1; marks do not count."""

    text = CITE.sub("", text).replace("**", "")
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text.strip())


def has_cjk(text: str) -> bool:
    return any(unicodedata.east_asian_width(ch) in ("W", "F") for ch in text)


@dataclass(frozen=True)
class Defect:
    where: str  # "slide 3", "section 2", "document"
    rule: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"where": self.where, "rule": self.rule, "detail": self.detail}


# Slides --------------------------------------------------------------------------------------


@dataclass
class Slide:
    number: int
    layout: str
    options: dict[str, str]
    title: str  # "## title", or "# title" for title and section slides
    subtitle: str
    body: list[str]  # visible lines after the title, without blank lines
    notes: str
    has_notes: bool
    visible: str  # all visible text, for phrase checks


def parse_slide(number: int, text: str) -> Slide:
    layout, options = "", {}
    lines = []
    for line in text.splitlines():
        found = MARKER.match(line)
        if found and "layout:" in found.group(1):
            for part in found.group(1).split(";"):
                key, _, value = part.partition(":")
                if value:
                    options[key.strip()] = value.strip()
            layout = options.get("layout", "")
        else:
            lines.append(line)
    visible: list[str] = []
    notes: list[str] = []
    in_notes = False
    for line in lines:
        if not in_notes and NOTE_LINE.match(line):
            in_notes = True
            notes.append(NOTE_LINE.sub("", line))
        elif in_notes:
            notes.append(line)
        else:
            visible.append(line)
    content = [line for line in visible if line.strip() and not line.strip().startswith("---")]
    title = ""
    for index, line in enumerate(content):
        if line.startswith("## ") or line.startswith("# "):
            title = line.lstrip("#").strip()
            content = content[:index] + content[index + 1 :]
            break
    if not layout:
        layout = "title" if number == 1 else "text"
    subtitle = ""
    if layout in {"title", "section"} and content and not content[0].lstrip().startswith("|"):
        subtitle = content[0].strip()
    return Slide(
        number, layout, options, title, subtitle, content, "\n".join(notes).strip(),
        in_notes, "\n".join(visible),
    )  # fmt: skip


def _items(lines: list[str]) -> list[str]:
    return [m.group(1) for line in lines if (m := ITEM.match(line))]


def _split_lead(item: str) -> tuple[str, str]:
    found = BOLD_LEAD.match(item.strip())
    return (found.group(1), found.group(2)) if found else ("", item)


def _closing(lines: list[str]) -> str:
    """A closing line: the last line that is not a table row, item, heading or quote."""
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith(("|", ">", "#", "—", "-", "*", "!")) or ITEM.match(line):
            return "" if not stripped.startswith("**") else ""
        return stripped
    return ""


def _table(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                rows.append(cells)
    return rows


def budget_defects(slide: Slide, use: str) -> list[Defect]:
    where = f"slide {slide.number}"
    out: list[Defect] = []
    scale = 1.5 if use == "read" else 1.0

    def check(name: str, text: str, limit: int, body: bool = True) -> None:
        if not text:
            return
        allowed = int(limit * scale) if body else limit
        size = units(text)
        if size > allowed:
            out.append(
                Defect(
                    where, "over_budget", f"{name} is {size} units, limit {allowed}: {text[:60]}"
                )
            )  # noqa: E501

    def count(name: str, number: int, limit: int) -> None:
        if number > limit:
            out.append(Defect(where, "over_budget", f"{name}: {number} items, limit {limit}"))

    layout, lines = slide.layout, slide.body
    if layout == "title":
        check("title", slide.title, 80, body=False)
        check("subtitle", slide.subtitle, 160, body=False)
        meta = lines[1].strip() if len(lines) > 1 else ""
        check("meta", meta, 80, body=False)
        return out
    if layout == "section":
        return out
    check("title", slide.title, 100, body=False)
    items = _items(lines)
    if layout == "summary":
        count("summary", len(items), 4)
        for item in items:
            lead, detail = _split_lead(item)
            check("lead", lead, 60)
            check("detail", detail, 130)
    elif layout == "stat":
        value = next(
            (m.group(1) for ln in lines if (m := re.fullmatch(r"\*\*(.+?)\*\*", ln.strip()))), ""
        )  # noqa: E501
        rest = [ln for ln in lines if not re.fullmatch(r"\*\*(.+?)\*\*", ln.strip())]
        check("value", value, 16)
        check("label", rest[0] if rest else "", 90)
        check("context", rest[1] if len(rest) > 1 else "", 160)
    elif layout == "stats":
        count("stats", len(items), 4)
        for item in items:
            value, label = _split_lead(item)
            check("value", value, 10)
            check("label", label, 60)
        non_items = [ln for ln in lines if not ITEM.match(ln)]
        check("closing", non_items[-1] if non_items else "", 140)
    elif layout in {"chart", "table"}:
        rows = _table(lines)
        if layout == "table":
            count("table rows", max(len(rows) - 1, 0), 7)
            count("table columns", max((len(r) for r in rows), default=0), 5)
            for row in rows:
                for cell in row:
                    check("cell", cell, 50)
        else:
            count("chart rows", max(len(rows) - 1, 0), 12)
        non_table = [ln for ln in lines if not ln.strip().startswith("|")]
        check("closing", non_table[-1] if non_table else "", 140)
    elif layout == "compare":
        sides = [ln[4:].strip() for ln in lines if ln.startswith("### ")]
        for side in sides:
            check("side name", side, 30)
        for item in items:
            check("item", item, 90)
        count("items", len(items), 8)
        tail = (
            lines[-1]
            if lines and not ITEM.match(lines[-1]) and not lines[-1].startswith("#")
            else ""
        )  # noqa: E501
        check("verdict", tail, 140)
    elif layout in {"process", "timeline", "actions", "points"}:
        limit = {"process": (24, 90, 6), "timeline": (16, 90, 6),
                 "actions": (24, 100, 4), "points": (40, 120, 4)}[layout]  # fmt: skip
        count(layout, len(items), limit[2])
        for item in items:
            lead, detail = _split_lead(item)
            if layout == "timeline" and not lead:
                lead, detail = "", item
            check("lead", lead, limit[0])
            check("detail", detail, limit[1])
    elif layout == "quote":
        quote = " ".join(ln.lstrip("> ").strip() for ln in lines if ln.startswith(">"))
        check("quote", quote, 240)
        attribution = next((ln for ln in lines if ln.startswith("—")), "")
        check("attribution", attribution, 90)
    elif layout == "figure":
        text = [ln for ln in lines if not ln.startswith("!")]
        check("takeaway", text[-1] if text else "", 160)
    elif layout == "statement":
        text = [ln for ln in lines if not ITEM.match(ln)]
        check("statement", text[0] if text else "", 180)
        for item in items:
            check("item", item, 80)
        count("items", len(items), 3)
    return out


def is_topic_title(title: str) -> bool:
    """True when a title is a topic label rather than a sentence that carries a point."""

    clean = CITE.sub("", title).replace("**", "").strip()
    if not clean:
        return True
    if units(clean) < 12:
        return True
    if has_cjk(clean):
        cjk = [ch for ch in clean if unicodedata.east_asian_width(ch) in ("W", "F")]
        return len(cjk) < 8 or not (any(ch in CJK_VERBS for ch in cjk) or re.search(r"\d", clean))
    words = re.findall(r"[\w'’%.-]+", clean.lower())
    if len(words) < 4:
        return True
    if len(words) >= 6 or re.search(r"\d", clean):
        return False
    for word in words:
        word = word.replace("’", "'")
        if word in AUXILIARIES or word in COMMON_VERBS:
            return False
        if len(word) > 4 and word.endswith(VERB_SUFFIXES):
            return False
    return True


# Numbers and citations -------------------------------------------------------------------


def _countable(number: str) -> bool:
    if number.endswith("%"):
        return True
    plain = number.replace(",", "")
    if re.fullmatch(r"(?:19|20)\d\d", plain):
        return False
    try:
        value = float(plain)
    except ValueError:
        return False
    return not (value == int(value) and value <= 12 and "," not in number)


def uncited_numbers(lines: list[str]) -> list[str]:
    """Lines that state a number without a citation mark on them."""

    found: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or MARKER.match(line) or stripped.startswith(("#", "---", "!")):
            continue
        if re.fullmatch(r"\|?[\s:|-]+\|?", stripped):
            continue
        if CITE.search(stripped):
            continue
        if re.fullmatch(r"\*\*[^*]+\*\*", stripped):  # a stat value takes the next line's mark
            following = next((ln for ln in lines[index + 1 :] if ln.strip()), "")
            if CITE.search(following):
                continue
        text = re.sub(r"\(?(?:https?://\S+)\)?", "", stripped)
        text = re.sub(r"\b[A-Za-z]+[-‑]?\d+[A-Za-z]*\b", "", text)  # names like GPT-4, B2
        if any(_countable(n) for n in NUMBER.findall(text)):
            found.append(stripped[:90])
    return found


def meta_hits(text: str) -> list[str]:
    lowered = text.lower()
    return [phrase for phrase in META_PHRASES if phrase in lowered]


# Measure -----------------------------------------------------------------------------------


def _truthy(value: Any) -> bool:
    return bool(value) and value not in ("false", "0", 0)


def measure_defects(measure: dict[str, Any] | None) -> list[Defect]:
    """Overflow, small text and empty halves from the renderer's measure.json.

    Read tolerantly: a list ``slides`` (or ``sections``) of records with an index/number and any
    of ``overflow``/``clipped`` (bool or count), ``minFontPx``/``minFont``/``minFontSize``,
    ``emptyHalf``/``emptyHalves``/``empty``; a record may instead list ``defects``.
    """

    if not measure:
        return []
    records = measure.get("slides") or measure.get("sections") or measure.get("pages") or []
    out: list[Defect] = []
    for position, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        number = record.get("index", record.get("number", position))
        if isinstance(number, int) and record.get("index") is not None and number == 0:
            number = 1
        where = f"slide {number}"
        for key in ("overflow", "clipped", "overflowing"):
            if _truthy(record.get(key)):
                out.append(Defect(where, "overflow", f"{key}={record[key]}"))
                break
        for key in ("minFontPx", "minFont", "minFontSize", "minFontSizePx"):
            value = record.get(key)
            if isinstance(value, int | float) and 0 < value < 18:
                out.append(Defect(where, "small_text", f"{value}px text"))
                break
        for key in ("emptyHalf", "emptyHalves", "empty", "emptyHalfSlide"):
            if _truthy(record.get(key)):
                out.append(Defect(where, "empty_half", f"{key}={record[key]}"))
                break
        for item in record.get("defects", []) or []:
            out.append(
                Defect(
                    where,
                    str(item.get("rule", "layout")) if isinstance(item, dict) else "layout",
                    str(item)[:120],
                )
            )  # noqa: E501
    return out


# Entry point -------------------------------------------------------------------------------


def lint(
    kind: str, content: str, use: str = "", measure: dict[str, Any] | None = None
) -> list[Defect]:
    """Every hard defect of one artifact. ``kind`` is "slides" or "report"."""

    defects = measure_defects(measure)
    if kind == "slides":
        _, parts = split_document(content, "slides")
        for number, text in enumerate(parts, start=1):
            slide = parse_slide(number, text)
            where = f"slide {number}"
            defects += budget_defects(slide, use)
            if slide.layout not in {"title", "section"} and is_topic_title(slide.title):
                defects.append(Defect(where, "topic_title", f"“{slide.title[:80]}”"))
            for phrase in meta_hits(slide.visible):
                defects.append(Defect(where, "meta_phrase", f"“{phrase}” on the slide"))
            # A title slide's last line is its meta line (audience, date): no claim.
            checked = slide.body[:1] if slide.layout == "title" else slide.body
            for line in uncited_numbers(checked):
                defects.append(Defect(where, "uncited_number", line))
            if use == "talk" and slide.layout not in {"title", "section"} and not slide.notes:
                defects.append(Defect(where, "missing_notes", "talk slide without speaker notes"))
        return defects
    preamble, sections = split_document(content, "report")
    for number, text in enumerate(sections, start=1):
        where = f"section {number}"
        heading = text.splitlines()[0].removeprefix("## ").strip() if text else ""
        summary = "block: summary" in text
        if is_topic_title(heading) and not summary:
            defects.append(Defect(where, "topic_title", f"“{heading[:80]}”"))
        for phrase in meta_hits(text):
            defects.append(Defect(where, "meta_phrase", f"“{phrase}”"))
        body = [ln for ln in text.splitlines()[1:] if not ln.startswith(("Source:", ":::"))]
        for line in uncited_numbers(body):
            defects.append(Defect(where, "uncited_number", line))
    for phrase in meta_hits(preamble):
        defects.append(Defect("document", "meta_phrase", f"“{phrase}”"))
    return defects


def counts(defects: list[Defect]) -> dict[str, int]:
    out: dict[str, int] = {}
    for defect in defects:
        out[defect.rule] = out.get(defect.rule, 0) + 1
    return out
