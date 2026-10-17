"""Scoring for the Artifacts exam: what changed, what stayed, what it cost, and one judge call.

Nothing here talks to the app. The runner hands over what a case looked like (an
:class:`Outcome`: the version before, the version after, or the question or answer given)
and gets back the measures, the checks that failed and the judge's reading.

Saving renumbers the source numbers of a whole document in reading order, so a part nobody
touched can still read ``[2]`` where it read ``[3]``. Parts are therefore compared with
every ``[n]`` replaced by the identity of its passage (:func:`identified`); a part counts as
untouched when the two readings agree. Whether the raw text is also the same is reported
beside it, but it is not what passes or fails a case.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from evals.ask.score import LIBRARY_DIR, library_texts, word_count
from gunther.llm import ModelError, ModelGateway, ModelInfo
from gunther.outputs import join_document, split_document

__all__ = ["LIBRARY_DIR", "library_texts"]

CASES_FILE = Path(__file__).parent / "cases.toml"
CATEGORIES = {
    "small edit",
    "find by content",
    "find by focus",
    "find by conversation",
    "structure",
    "upstream",
    "content",
    "whole document",
    "question",
    "asking",
}
# The kind of output each artifact of a case is made of.
ARTIFACTS = {"deck": "slides", "report": "report", "none": ""}
INTENTS = ("ask", "answer", "make", "change")
ASKS = ("must", "may", "must_not")
ROUTES = ("revise", "rebuild", "outline", "new", "none")
# What each placeholder in a case needs its setup to have made.
PLACEHOLDERS = {"typo": "typo", "fixed": "typo", "quote": "quote"}
CITATION = re.compile(r"\[(?:([ipd]):)?(\d+(?:\s*[,，、]\s*\d+)*)\]")
UNSOURCED = re.compile(r"\[\?\]")
# Anything a model puts in brackets as a source mark, including slips like [?, 24].
MARKS = re.compile(r"\[[\d?,，、\sipd:]*\]")
# The line that says how a slide is drawn, when it has one: <!-- layout: compare -->. The exam
# reads the document as it is written and does not use the parser of the code it measures.
LAYOUT_MARK = re.compile(
    r"^[ \t\r\n]*<!--[ \t]*layout:[ \t]*([a-z][a-z0-9_]*)[^>]*-->[ \t]*(?:\r?\n|\Z)"
)
MARK_LINE = re.compile(r"<!--[^>]*-->")
JUDGE = """You grade one reply from a document assistant. A person asked it to make or change a report or a slide deck, or asked it a question about one. You see the conversation, the document as it was before the last request (numbered parts, with what the Checker found in each), and what the assistant did: the changed document, its answer, or its question. Then one question to answer with yes or no.
Judge only against the texts shown. Reply with the JSON object {"answer": true or false, "why": "one short sentence"}."""  # noqa: E501


# The case file ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Focus:
    """What the person points at: a part (counted from 1), selected text in it, or only
    the part that is on screen (``selected`` false)."""

    section: int | None = None
    quote: str = ""
    selected: bool = True


@dataclass(frozen=True)
class Turn:
    message: str
    focus: Focus | None = None


@dataclass(frozen=True)
class Setup:
    """What a case does to its own copy of the fixture before the first message."""

    # Break one word of this part. ``{typo}`` is the broken word, ``{fixed}`` the right one.
    typo: int | None = None
    # ``{quote}`` is the Kth sentence of part N: (N, K).
    quote: tuple[int, int] | None = None


@dataclass(frozen=True)
class Edit:
    """One change to an outline item, for a person who edits the outline and rebuilds."""

    part: int
    heading: str | None = None
    goal: str | None = None
    layout: str | None = None


@dataclass(frozen=True)
class Today:
    """How the old path takes a message (the baseline). Left empty, it follows from the case."""

    route: str = ""
    why: str = ""
    edit: tuple[Edit, ...] = ()
    # For a new document: what the person would have picked in the old screen.
    kind: str = ""
    audience: str = "scientist"
    use: str = ""


@dataclass(frozen=True)
class Expect:
    intent: tuple[str, ...] = ("change",)
    changed: tuple[int, ...] = ()
    may_change: tuple[int, ...] = ()
    may_all: bool = False
    # None: the case does not say. Left out of the file it is 0, unless anything may change.
    count_delta: int | None = None
    layout: dict[int, str] = field(default_factory=dict)
    order: tuple[int, ...] = ()
    new_at: tuple[int, ...] = ()
    shorter: tuple[int, ...] = ()
    cites: tuple[str, ...] = ()
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    # Sentences that must be gone: no sentence or line of the result reads like this.
    gone: tuple[str, ...] = ()
    asks: str = ""
    judge: str = ""

    @property
    def asks_rule(self) -> str:
        return self.asks or ("must" if self.intent == ("ask",) else "must_not")


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    artifact: str
    message: str
    expect: Expect
    history: tuple[Turn, ...] = ()
    focus: Focus | None = None
    setup: Setup = field(default_factory=Setup)
    today: Today = field(default_factory=Today)

    def resolved(self, subs: dict[str, str]) -> Case:
        """The case with ``{typo}``, ``{fixed}`` and ``{quote}`` replaced by what the setup made."""

        def fill(text: str) -> str:
            for name, value in subs.items():
                text = text.replace("{" + name + "}", value)
            return text

        focus = self.focus
        if focus is not None:
            focus = Focus(focus.section, fill(focus.quote), focus.selected)
        expect = self.expect.__dict__ | {
            "must_contain": tuple(fill(x) for x in self.expect.must_contain),
            "must_not_contain": tuple(fill(x) for x in self.expect.must_not_contain),
            "gone": tuple(fill(x) for x in self.expect.gone),
            "judge": fill(self.expect.judge),
        }
        return Case(
            self.id,
            self.category,
            self.artifact,
            fill(self.message),
            Expect(**expect),
            self.history,
            focus,
            self.setup,
            self.today,
        )


@dataclass(frozen=True)
class Slot:
    """One part of a fixture's fixed outline, and what its built text must contain."""

    heading: str
    goal: str = ""
    layout: str = ""
    has: tuple[str, ...] = ()
    paragraphs: int = 0


@dataclass(frozen=True)
class Fixture:
    name: str
    kind: str
    audience: str
    brief: str
    outline: tuple[Slot, ...]
    use: str = ""
    style: str = ""
    title: str = ""

    @property
    def hash(self) -> str:
        """Names the fixture's definition: a changed outline or brief builds it again."""

        text = json.dumps(self.__dict__, default=lambda item: item.__dict__, sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()[:12]

    def request(self) -> dict[str, Any]:
        """The build request that makes it (without an id or a model)."""

        body: dict[str, Any] = {
            "kind": self.kind,
            "audience": self.audience,
            "brief": self.brief,
            "scope": {"mode": "library"},
            "outline": [
                {"heading": s.heading, "goal": s.goal, **({"layout": s.layout} if s.layout else {})}
                for s in self.outline
            ],
        }
        if self.kind == "report":
            body["style"] = self.style or "overview"
        elif self.use:
            body["use"] = self.use
        if self.title:
            body["title"] = self.title
        return body

    def problems(self, parts: Sequence[str]) -> list[str]:
        """What the built parts lack that the cases rely on; empty when they will do."""

        if len(parts) != len(self.outline):
            return [f"it has {len(parts)} parts; the outline has {len(self.outline)}"]
        found = []
        for number, (slot, text) in enumerate(zip(self.outline, parts, strict=True), start=1):
            lowered = text.casefold()
            found += [
                f"part {number} lacks “{need}”"
                for need in slot.has
                if need.casefold() not in lowered
            ]
            chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", MARK_LINE.sub("", text))]
            paragraphs = [chunk for chunk in chunks if chunk and not chunk.startswith("#")]
            if slot.paragraphs and len(paragraphs) < slot.paragraphs:
                found.append(f"part {number} has fewer than {slot.paragraphs} paragraphs")
        return found


def _fields(raw: dict[str, Any], known: set[str], where: str) -> None:
    extra = set(raw) - known
    if extra:
        raise ValueError(f"{where}: unknown fields {sorted(extra)}")


def _focus(raw: dict[str, Any] | None, where: str) -> Focus | None:
    if not raw:
        return None
    _fields(raw, {"section", "quote", "selected"}, f"{where} focus")
    return Focus(raw.get("section"), str(raw.get("quote", "")), bool(raw.get("selected", True)))


def _numbers(items: Any) -> tuple[int, ...]:
    return tuple(int(item) for item in items)


def _expect(raw: dict[str, Any], where: str) -> Expect:
    _fields(raw, set(Expect.__dataclass_fields__) - {"may_all"}, f"{where} expect")
    intent = raw.get("intent", "change")
    may = raw.get("may_change", [])
    everything = may == "all"
    delta = raw.get("count_delta")
    if delta is None and not everything:
        delta = 0
    expect = Expect(
        intent=(intent,) if isinstance(intent, str) else tuple(intent),
        changed=_numbers(raw.get("changed", [])),
        may_change=() if everything else _numbers(may),
        may_all=everything,
        count_delta=None if delta == "any" else delta,
        layout={int(number): str(name) for number, name in raw.get("layout", {}).items()},
        order=_numbers(raw.get("order", [])),
        new_at=_numbers(raw.get("new_at", [])),
        shorter=_numbers(raw.get("shorter", [])),
        cites=tuple(raw.get("cites", [])),
        must_contain=tuple(raw.get("must_contain", [])),
        must_not_contain=tuple(raw.get("must_not_contain", [])),
        gone=tuple(raw.get("gone", [])),
        asks=str(raw.get("asks", "")),
        judge=str(raw.get("judge", "")),
    )
    if not set(expect.intent) <= set(INTENTS):
        raise ValueError(f"{where}: unknown intent {expect.intent}")
    if expect.asks and expect.asks not in ASKS:
        raise ValueError(f"{where}: unknown asks rule {expect.asks!r}")
    return expect


def _today(raw: dict[str, Any] | None, where: str) -> Today:
    if not raw:
        return Today()
    _fields(raw, set(Today.__dataclass_fields__), f"{where} today")
    edits = []
    for item in raw.get("edit", []):
        _fields(item, set(Edit.__dataclass_fields__), f"{where} today edit")
        edits.append(
            Edit(int(item["part"]), item.get("heading"), item.get("goal"), item.get("layout"))
        )
    today = Today(
        route=str(raw.get("route", "")),
        why=str(raw.get("why", "")),
        edit=tuple(edits),
        kind=str(raw.get("kind", "")),
        audience=str(raw.get("audience", "scientist")),
        use=str(raw.get("use", "")),
    )
    if today.route and today.route not in ROUTES:
        raise ValueError(f"{where}: unknown route {today.route!r}")
    return today


def load_cases(path: Path = CASES_FILE) -> list[Case]:
    """Every case in the file, checked: a mistyped field or rule is an error, not a skip."""

    raw = tomllib.loads(path.read_text(encoding="utf-8")).get("case", [])
    known = {
        "id",
        "category",
        "artifact",
        "message",
        "history",
        "focus",
        "setup",
        "today",
        "expect",
    }
    cases = []
    for entry in raw:
        where = f"Case {entry.get('id')}"
        _fields(entry, known, where)
        history = []
        for turn in entry.get("history", []):
            _fields(turn, {"message", "focus"}, f"{where} history")
            history.append(Turn(str(turn["message"]), _focus(turn.get("focus"), where)))
        setup = entry.get("setup", {})
        _fields(setup, {"typo", "quote"}, f"{where} setup")
        quote = setup.get("quote")
        case = Case(
            id=str(entry["id"]),
            category=str(entry["category"]),
            artifact=str(entry["artifact"]),
            message=str(entry["message"]),
            expect=_expect(entry.get("expect", {}), where),
            history=tuple(history),
            focus=_focus(entry.get("focus"), where),
            setup=Setup(setup.get("typo"), (int(quote[0]), int(quote[1])) if quote else None),
            today=_today(entry.get("today"), where),
        )
        if case.category not in CATEGORIES:
            raise ValueError(f"{where}: unknown category {case.category!r}")
        if case.artifact not in ARTIFACTS:
            raise ValueError(f"{where}: unknown artifact {case.artifact!r}")
        cases.append(case)
    ids = [case.id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("Case ids are not unique")
    return cases


def load_fixtures(path: Path = CASES_FILE) -> dict[str, Fixture]:
    """The fixtures of the case file, by name (``deck``, ``report``)."""

    raw = tomllib.loads(path.read_text(encoding="utf-8")).get("fixture", {})
    fixtures = {}
    for name, entry in raw.items():
        where = f"Fixture {name}"
        _fields(entry, {"kind", "audience", "brief", "outline", "use", "style", "title"}, where)
        slots = []
        for item in entry["outline"]:
            _fields(item, set(Slot.__dataclass_fields__), f"{where} outline")
            slots.append(
                Slot(
                    item["heading"],
                    item.get("goal", ""),
                    item.get("layout", ""),
                    tuple(item.get("has", [])),
                    int(item.get("paragraphs", 0)),
                )
            )
        fixtures[name] = Fixture(
            name,
            entry["kind"],
            entry["audience"],
            entry["brief"],
            tuple(slots),
            entry.get("use", ""),
            entry.get("style", ""),
            entry.get("title", ""),
        )
    return fixtures


def problems(cases: Sequence[Case], fixtures: dict[str, Fixture]) -> list[str]:
    """What is wrong with the case file as a whole: a part that is not in its fixture, a
    placeholder nothing sets up, an artifact with no fixture."""

    found = []
    for case in cases:
        where = f"Case {case.id}"
        fixture = fixtures.get(case.artifact)
        if case.artifact != "none" and fixture is None:
            found.append(f"{where}: there is no {case.artifact} fixture")
            continue
        count = len(fixture.outline) if fixture else 0
        expect = case.expect
        mentioned = {
            "changed": expect.changed,
            "may_change": expect.may_change,
            "shorter": expect.shorter,
            "order": expect.order,
            "layout": tuple(expect.layout),
            "typo": (case.setup.typo,) if case.setup.typo else (),
            "quote": (case.setup.quote[0],) if case.setup.quote else (),
            "edit": tuple(edit.part for edit in case.today.edit),
            "focus": tuple(
                turn.focus.section
                for turn in [*case.history, Turn(case.message, case.focus)]
                if turn.focus and turn.focus.section
            ),
        }
        for what, numbers in mentioned.items():
            found += [
                f"{where}: {what} names part {n}, but the {case.artifact} has {count} parts"
                for n in numbers
                if not 1 <= n <= count
            ]
        after = count + (expect.count_delta or 0)
        found += [
            f"{where}: new_at names position {n}, but the document will have {after} parts"
            for n in expect.new_at
            if not 1 <= n <= after
        ]
        if expect.order and sorted(expect.order) != list(range(1, count + 1)):
            found.append(f"{where}: order must list every part once")
        words = [case.message, expect.judge, *expect.must_contain, *expect.must_not_contain]
        words += expect.gone
        words += [case.focus.quote] if case.focus else []
        for name in re.findall(r"\{(\w+)\}", " ".join(words)):
            if name not in PLACEHOLDERS or getattr(case.setup, PLACEHOLDERS[name]) is None:
                found.append(f"{where}: {{{name}}} is not set up")
        if case.artifact == "none" and (case.history or case.focus):
            found.append(f"{where}: nothing exists yet, so there is no history or focus")
    return found


# The document before and after ------------------------------------------------------------


@dataclass
class Version:
    """A saved version of a report or a deck, as the exam reads it."""

    content: str
    kind: str = "slides"
    title: str = ""
    id: str = ""
    number: int = 0
    audience: str = ""
    style: str | None = None
    use: str | None = None
    brief: str = ""
    # The passages it cites: {"ref", "sourceId", "sourceTitle", "quote", "url", "supportText"}.
    citations: list[dict[str, Any]] = field(default_factory=list)
    # Its outline: [{"heading", "goal", "layout"}].
    outline: list[dict[str, Any]] = field(default_factory=list)
    # What the Checker found in each part, as the app shows it: {"index", "checked", "issues"}.
    sections: list[dict[str, Any]] = field(default_factory=list)

    @property
    def parts(self) -> list[str]:
        return split_document(self.content, self.kind)[1]

    @classmethod
    def from_artifact(cls, data: dict[str, Any]) -> Version:
        """A version from what the API returns for it."""

        provenance = data.get("provenance") or {}
        return cls(
            content=data["content"],
            kind=data.get("kind", "slides"),
            title=data.get("title", ""),
            id=data.get("id", ""),
            number=data.get("versionNumber", 0),
            audience=data.get("audience", ""),
            style=data.get("style"),
            use=provenance.get("use"),
            brief=data.get("brief", ""),
            citations=data.get("citations", []),
            outline=data.get("outline", []),
            sections=data.get("sections", []),
        )


@dataclass
class Outcome:
    """What one case produced: the document before and after, or an answer or a question."""

    # What the assistant did: ask | answer | make | change ("" when it did not get that far).
    intent: str = ""
    # The version the last message was sent on, and the one it saved (None: nothing saved).
    before: Version | None = None
    after: Version | None = None
    asked: bool = False
    question: str = ""
    options: list[str] = field(default_factory=list)
    reply: str = ""
    seconds: float = 0.0
    calls: int = 0
    tokens: int = 0
    # Why there is nothing to score (the request failed), or why the old path cannot do it.
    failed: str = ""
    impossible: str = ""
    # How the request was made, in a few words (the baseline's route).
    route: str = ""
    notes: list[str] = field(default_factory=list)


def plain(text: str) -> str:
    """Text without its source numbers and markers, on one line."""

    text = " ".join(UNSOURCED.sub("", CITATION.sub("", MARK_LINE.sub("", text))).split())
    return re.sub(r"\s+([,.;:!?，。；：！？])", r"\1", text)


def marked_layout(part: str) -> str | None:
    """The layout a slide says it has, if it has a marker line."""

    found = LAYOUT_MARK.match(part)
    return found.group(1) if found else None


def folded(text: str) -> str:
    """Plain text in a form to look strings up in: one case, straight quotes and dashes."""

    table = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
    return plain(text).translate(table).casefold()


def passage_identity(citation: dict[str, Any]) -> str:
    """Who a passage is, whatever number a version gives it: its source and its quote."""

    source = (
        citation.get("sourceId")
        or citation.get("source_id")
        or citation.get("url")
        or citation.get("sourceTitle")
        or citation.get("source_title")
        or ""
    )
    quote = " ".join(str(citation.get("quote", "")).split())
    return f"{source}:{hashlib.sha256(quote.encode()).hexdigest()[:12]}"


def identified(text: str, citations: Sequence[dict[str, Any]]) -> str:
    """The text with every source number ``[n]`` replaced by the identity of its passage."""

    by_ref = {c["ref"]: passage_identity(c) for c in citations if c.get("ref") is not None}

    def swap(match: re.Match[str]) -> str:
        label = f"{match.group(1)}:" if match.group(1) else ""
        found = [int(n) for n in re.findall(r"\d+", match.group(2))]
        return f"[{label}{','.join(by_ref.get(n, f'?{n}') for n in found)}]"

    return CITATION.sub(swap, text)


@dataclass
class Alignment:
    """Which part of the new document is which part of the old (counted from 0)."""

    pairs: dict[int, int]
    deleted: list[int]
    added: list[int]


def _pair_in_order(old: Sequence[str], new: Sequence[str]) -> list[tuple[int | None, int | None]]:
    """The best way to line two short runs of parts up, in order (a part may be left out).

    Parts that are not alike at all are still paired when the runs are as long as each
    other: a part rewritten into another language is still that part.
    """

    gap, base = -0.5, 0.25
    rows, cols = len(old), len(new)
    like = [[difflib.SequenceMatcher(None, a[:1500], b[:1500]).ratio() for b in new] for a in old]
    best = [[0.0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        best[i][0] = i * gap
    for j in range(1, cols + 1):
        best[0][j] = j * gap
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            best[i][j] = max(
                best[i - 1][j - 1] + base + like[i - 1][j - 1],
                best[i - 1][j] + gap,
                best[i][j - 1] + gap,
            )
    out: list[tuple[int | None, int | None]] = []
    i, j = rows, cols
    while i or j:
        if i and j and best[i][j] == best[i - 1][j - 1] + base + like[i - 1][j - 1]:
            out.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and best[i][j] == best[i - 1][j] + gap:
            out.append((i - 1, None))
            i -= 1
        else:
            out.append((None, j - 1))
            j -= 1
    return out[::-1]


def align(old: Sequence[str], new: Sequence[str]) -> Alignment:
    """Match the parts of two documents (as :func:`identified` reads them).

    Parts that read the same are matched first, in order; one that reads the same somewhere
    else has moved. Between them, what is left is matched in order by likeness, so a part
    that was rewritten stays the same part, and what has no partner was removed or added.
    """

    pairs: dict[int, int] = {}
    steps = difflib.SequenceMatcher(None, list(old), list(new), autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in steps:
        if tag == "equal":
            pairs.update(zip(range(i1, i2), range(j1, j2), strict=True))
    taken = set(pairs.values())
    free_old = [i for i in range(len(old)) if i not in pairs]
    free_new = [j for j in range(len(new)) if j not in taken]
    for i in list(free_old):
        same = next((j for j in free_new if new[j] == old[i]), None)
        if same is not None:
            pairs[i] = same
            free_old.remove(i)
            free_new.remove(same)
    for tag, i1, i2, j1, j2 in steps:
        if tag == "equal":
            continue
        left = [i for i in range(i1, i2) if i in free_old]
        right = [j for j in range(j1, j2) if j in free_new]
        for i, j in _pair_in_order([old[i] for i in left], [new[j] for j in right]):
            if i is not None and j is not None:
                pairs[left[i]] = right[j]
    taken = set(pairs.values())
    return Alignment(
        pairs,
        [i for i in range(len(old)) if i not in pairs],
        [j for j in range(len(new)) if j not in taken],
    )


# Numbers ----------------------------------------------------------------------------------

NUMBER = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(\s?%)?")
# A whole number up to here, with no percent sign, is a count or a slide number, not a fact.
SMALL_NUMBER = 12
WORD_VALUES = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
}


def _ordinals(cardinals: dict[str, int]) -> dict[str, int]:
    """ "first", "fourth", "twentieth": the ordinal for each number word."""

    irregular = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fifth": 5,
        "eighth": 8,
        "ninth": 9,
        "twelfth": 12,
    }
    found = dict(irregular)
    for word, value in cardinals.items():
        if word.endswith("y"):
            found[word[:-1] + "ieth"] = value
        elif value not in irregular.values():
            found[word + "th"] = value
    return found


# So that "the thirty-first of August" counts as having said 31.
WORD_VALUES |= _ordinals(WORD_VALUES)


def _value(core: str) -> str:
    try:
        return format(Decimal(core.replace(",", "")).normalize(), "f")
    except InvalidOperation:
        return core


def figures(text: str) -> list[tuple[str, bool]]:
    """The numbers a text states, as (value, whether a reader would take it as a fact)."""

    cleaned = re.sub(r"(?m)^\s*\d+[.)]\s+", "", MARKS.sub("", text))
    found = []
    for match in NUMBER.finditer(cleaned):
        value = _value(match.group(1))
        small = "." not in value and int(Decimal(value)) <= SMALL_NUMBER
        found.append((value, bool(match.group(2)) or not small))
    return found


def known_figures(texts: Sequence[str]) -> set[str]:
    """Every number the texts state, in digits or in words ("fifteen")."""

    known = {value for text in texts for value, _ in figures(text)}
    for text in texts:
        for word in re.findall(r"[a-z]+(?:-[a-z]+)?", text.lower()):
            values = [WORD_VALUES.get(part) for part in word.split("-")]
            if all(values):
                known.add(str(sum(v for v in values if v)))
    return known


# Sentences and fixtures -------------------------------------------------------------------


def sentences_of(part: str, notes: bool = False) -> list[str]:
    """What a person could select in a part: its sentences and list lines, without the
    heading, tables and images, and without source numbers. A slide's speaker notes are left
    out unless ``notes``."""

    found = []
    for line in part.splitlines():
        text = line.strip()
        if notes:
            text = text.removeprefix("Note:").strip()
        if not text or text.startswith(("Note:", "|", "!", ">", "#", "<!--", "---")):
            continue
        text = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", text).replace("**", "")
        for piece in re.split(r"(?<=[.!?。！？])\s+", plain(text)):
            if len(piece.split()) >= 4 or len(piece) >= 12:
                found.append(piece)
    return found


def _break(word: str) -> str | None:
    """The word with two inside letters swapped; None when that changes nothing."""

    broken = word[:2] + word[3] + word[2] + word[4:]
    return broken if broken != word else None


def prepare(case: Case, content: str, kind: str) -> tuple[str, dict[str, str]]:
    """The fixture's text as this case starts from it, and what its placeholders stand for.

    A typo is the first long word of the part with two inside letters swapped.
    """

    preamble, parts = split_document(content, kind)
    subs: dict[str, str] = {}
    if case.setup.typo:
        number = case.setup.typo
        lines = parts[number - 1].splitlines()
        for at, line in enumerate(lines):
            if line.startswith(("Note:", "|", "!", "#", "<!--")):
                continue
            words = re.findall(r"(?<![\w\[])[A-Za-z]{7,}(?!\w)", line)
            word = next((w for w in words if _break(w)), None)
            if word:
                broken = _break(word) or word
                pattern = rf"(?<![\w\[]){re.escape(word)}(?!\w)"
                lines[at] = re.sub(pattern, broken, line, count=1)
                subs.update(typo=broken, fixed=word)
                break
        if "typo" not in subs:
            raise ValueError(f"Part {number} has no word long enough to break")
        parts[number - 1] = "\n".join(lines)
        content = join_document(preamble, parts, kind)
    if case.setup.quote:
        number, nth = case.setup.quote
        found = sentences_of(parts[number - 1])
        if len(found) < nth:
            raise ValueError(f"Part {number} has {len(found)} sentences; the case wants #{nth}")
        subs["quote"] = found[nth - 1]
    return content, subs


# Measures and checks ----------------------------------------------------------------------


def _bare(sentence: str) -> str:
    """A sentence to compare with another: folded, without the punctuation that ends it."""

    return folded(sentence).rstrip(" .!?。！？;:,，")


def spoken_words(outcome: Outcome) -> list[str]:
    """What the assistant said to the person, apart from the document."""

    return [outcome.reply, outcome.question, *outcome.options]


def _result_text(outcome: Outcome) -> str:
    """Everything the assistant gave back: the document, and what it said or asked."""

    said = spoken_words(outcome)
    return plain(" ".join([outcome.after.content if outcome.after else "", *said]))


def _cited_titles(part: str, citations: Sequence[dict[str, Any]]) -> set[str]:
    by_ref = {c.get("ref"): c.get("sourceTitle") or c.get("source_title") or "" for c in citations}
    return {
        by_ref.get(int(n), "")
        for match in CITATION.finditer(part)
        for n in re.findall(r"\d+", match.group(2))
    } - {""}


def _invented(
    case: Case, outcome: Outcome, library: dict[str, str], stated: Sequence[str]
) -> list[str]:
    """Numbers in ``stated`` that are in none of: the document before, the notes and passages
    it or the new version cites, and what the person wrote."""

    versions = [v for v in (outcome.before, outcome.after) if v is not None]
    written = [turn.message for turn in case.history] + [case.message]
    if case.focus and case.focus.quote:
        written.append(case.focus.quote)
    texts = [*written, outcome.before.content if outcome.before else ""]
    for version in versions:
        for citation in version.citations:
            texts.append(library.get(citation.get("sourceTitle", ""), ""))
            texts += [str(citation.get(key) or "") for key in ("quote", "supportText")]
    known = known_figures(texts)
    return sorted({v for text in stated for v, fact in figures(text) if fact and v not in known})


def measure(case: Case, outcome: Outcome, library: dict[str, str]) -> dict[str, Any]:
    """Numbers and lists about what the case did, without verdicts. Part numbers count the
    version the message was sent on (from 1); positions count the version it saved."""

    before, after, expect = outcome.before, outcome.after, case.expect
    spoken = [outcome.reply, outcome.question, *outcome.options]
    result: dict[str, Any] = {"saved": after is not None, "asked": outcome.asked}
    if after is None:
        result["invented"] = _invented(case, outcome, library, [" ".join(spoken)])
        return result
    old_parts = before.parts if before else []
    new_parts = after.parts
    old = [identified(t, before.citations) for t in old_parts] if before else []
    new = [identified(t, after.citations) for t in new_parts]
    placing = align(old, new)
    gone = set(placing.deleted)
    changed = [i + 1 for i in range(len(old)) if i in gone or old[i] != new[placing.pairs[i]]]
    allowed = set(expect.changed) | set(expect.may_change)
    required = set(expect.changed)
    untouched = [] if expect.may_all else [n for n in range(1, len(old) + 1) if n not in allowed]
    unexpected = [] if expect.may_all else [n for n in changed if n not in allowed]
    missed = [n for n in sorted(required) if n not in changed]
    result.update(
        parts=[len(old), len(new)],
        changed=changed,
        deleted=[i + 1 for i in placing.deleted],
        added=[j + 1 for j in placing.added],
        pairs={str(i + 1): j + 1 for i, j in placing.pairs.items()},
        unexpected=unexpected,
        missed=missed,
        changed_ok=len(changed) - len(unexpected),
        required_done=len(required) - len(missed),
        untouched=len(untouched),
        damaged=[n for n in untouched if n in changed],
        raw_damaged=[
            n
            for n in untouched
            if (n - 1) in gone or old_parts[n - 1] != new_parts[placing.pairs[n - 1]]
        ],
        order=[i + 1 for i, _ in sorted(placing.pairs.items(), key=lambda pair: pair[1])],
    )
    layouts = [item.get("layout") or "" for item in after.outline]
    drawn = len(layouts) == len(new_parts)

    def layout_at(j: int) -> str | None:
        """What a slide says it is: its marker, else the outline of the version."""

        return marked_layout(new_parts[j]) or (layouts[j] if drawn else None) or None

    result["layouts"] = {
        str(n): layout_at(placing.pairs[n - 1]) if (n - 1) in placing.pairs else None
        for n in expect.layout
    }
    result["words"] = {
        str(n): [
            word_count(plain(old_parts[n - 1])),
            word_count(plain(new_parts[placing.pairs[n - 1]]))
            if (n - 1) in placing.pairs
            else None,
        ]
        for n in expect.shorter
    }
    # What the new version has to answer for: the parts that changed and the parts it added.
    fresh = sorted(
        {placing.pairs[n - 1] for n in changed if (n - 1) in placing.pairs} | set(placing.added)
    )
    support = {"ok": 0, "flagged": 0, "unchecked": 0}
    cited: set[str] = set()
    for j in fresh:
        cited |= _cited_titles(new_parts[j], after.citations)
        section = next((s for s in after.sections if s.get("index") == j), None)
        verdicts = [i.get("verdict") for i in (section or {}).get("issues", [])]
        if section is None or not section.get("checked"):
            support["unchecked"] += 1
        elif {"unsupported", "contradicted"} & set(verdicts):
            support["flagged"] += 1
        else:
            support["ok"] += 1
    result.update(cited=sorted(cited), support=support)
    stated = [new_parts[j] for j in fresh] + [" ".join(spoken)]
    result["invented"] = _invented(case, outcome, library, stated)
    return result


def checks(case: Case, outcome: Outcome, measures: dict[str, Any]) -> dict[str, bool]:
    """Each deterministic check by name, True when it holds."""

    found = {"answered": not (outcome.failed or outcome.impossible)}
    if not found["answered"]:
        return found
    expect = case.expect
    rule = expect.asks_rule
    # A question the case allowed is a fair answer; nothing else is asked of it.
    excused = rule == "may" and outcome.asked
    if rule != "may":
        found["asked"] = outcome.asked == (rule == "must")
    found["intent"] = outcome.intent in expect.intent or excused
    found["saved"] = measures["saved"] == (outcome.intent in ("change", "make"))
    if excused:
        return found
    heard = folded(_result_text(outcome))
    for text in expect.must_contain:
        found[f"contains {text!r}"] = folded(text) in heard
    for text in expect.must_not_contain:
        found[f"without {text!r}"] = folded(text) not in heard
    if expect.gone:
        said = [outcome.after.content if outcome.after else "", *spoken_words(outcome)]
        sentences = {_bare(s) for text in said for s in sentences_of(text, notes=True)}
        for text in expect.gone:
            found[f"gone {text!r}"] = _bare(text) not in sentences
    found["numbers"] = not measures["invented"]
    if not measures["saved"]:
        return found
    found["precision"] = not measures["unexpected"]
    found["recall"] = not measures["missed"]
    found["untouched"] = not measures["damaged"]
    old, new = measures["parts"]
    if expect.count_delta is not None and outcome.before is not None:
        found["count"] = new - old == expect.count_delta
    if expect.layout:
        found["layout"] = all(
            measures["layouts"][str(n)] == name for n, name in expect.layout.items()
        )
    if expect.order:
        found["order"] = measures["order"] == list(expect.order)
    if expect.new_at:
        found["new_at"] = measures["added"] == list(expect.new_at)
    if expect.shorter:
        found["shorter"] = all(
            after is not None and after < before for before, after in measures["words"].values()
        )
    if expect.cites:
        found["cites"] = bool(set(expect.cites) & set(measures["cited"]))
    found["checked"] = measures["support"]["unchecked"] == 0
    return found


# What the judge says ----------------------------------------------------------------------


class Verdict(BaseModel):
    answer: bool = False
    why: str = ""


def _document(parts: Sequence[str], marks: dict[int, str]) -> str:
    return "\n\n".join(f"[Part {n}]{marks.get(n, '')}\n{text}" for n, text in enumerate(parts, 1))


def _sources(citations: Sequence[dict[str, Any]], size: int) -> list[str]:
    return [
        f"[{c['ref']}] {c.get('sourceTitle', '')}: {str(c.get('quote', ''))[:size]}"
        for c in citations[:40]
        if c.get("ref") is not None
    ]


def judge_prompt(case: Case, outcome: Outcome, measures: dict[str, Any]) -> str:
    """Everything the judge sees: the conversation, the document before (with the Checker's
    findings), what the assistant did, and the question."""

    before, after = outcome.before, outcome.after
    lines = ["Conversation:"]
    lines += [f"Person: {turn.message}" for turn in case.history]
    lines.append(f"Person (the request): {case.message}")
    if case.focus:
        where = f"part {case.focus.section}" if case.focus.section else "a part"
        how = "selected" if case.focus.selected else "only on screen"
        quote = f"; selected text: “{case.focus.quote}”" if case.focus.quote else ""
        lines.append(f"(The person was pointing at {where}: {how}{quote}.)")
    lines += ["", "The document before the request:"]
    if before is None:
        lines.append("(nothing yet)")
    else:
        found = [
            f"Part {s['index'] + 1}: {i['verdict']}: {str(i['claim'])[:160]}"
            for s in before.sections
            for i in s.get("issues", [])
        ]
        lines += [_document(before.parts, {}), "", "What the Checker found in it:"]
        lines += found or ["(nothing)"]
        lines += ["", "Sources it cites:", *(_sources(before.citations, 200) or ["(none)"])]
    lines += ["", "What the assistant did:"]
    if after is not None:
        pairs = {int(a): b for a, b in measures["pairs"].items()}
        same = {b for a, b in pairs.items() if a not in measures["changed"]}
        shown = ["(unchanged)" if n in same else text for n, text in enumerate(after.parts, 1)]
        marks = {n: " (new)" for n in measures["added"]}
        removed = (
            f"; it removed part(s) {_list(measures['deleted'])}" if measures["deleted"] else ""
        )
        lines += [
            f"It saved a new version with {len(shown)} parts{removed}.",
            _document(shown, marks),
        ]
        lines += ["", "Sources the new version cites:", *_sources(after.citations, 300)]
    else:
        if outcome.asked:
            lines.append(f"It asked: {outcome.question} Options: {'; '.join(outcome.options)}")
        if outcome.reply:
            lines.append(f"It answered: {outcome.reply}")
        if not (outcome.asked or outcome.reply):
            lines.append("It saved nothing and said nothing.")
    lines += ["", f"Question: {case.expect.judge}"]
    return "\n".join(lines)


def judge(
    gateway: ModelGateway,
    model: ModelInfo,
    case: Case,
    outcome: Outcome,
    measures: dict[str, Any],
) -> tuple[Verdict | None, str]:
    """One judge call for a case; (None, why) when the judge could not answer."""

    try:
        verdict, _ = gateway.complete_json(
            model,
            Verdict,
            system=JUDGE,
            prompt=judge_prompt(case, outcome, measures),
            effort="low",
        )
    except ModelError as error:
        return None, str(error)
    return verdict, ""


def judged(case: Case, outcome: Outcome, verdict: Verdict | None) -> list[str]:
    """What the judge's reading fails: no answer from the judge, or a "no"."""

    excused = case.expect.asks_rule == "may" and outcome.asked
    if not case.expect.judge or excused:
        return []
    if verdict is None:
        return ["judge"]
    return [] if verdict.answer else ["judged no"]


def failures(
    case: Case, outcome: Outcome, found: dict[str, bool], verdict: Verdict | None
) -> list[str]:
    """The names of everything that failed for one case; empty means it passed."""

    if outcome.impossible:
        return ["not possible today"]
    bad = [name for name, ok in found.items() if not ok]
    if found.get("answered", True):
        bad += judged(case, outcome, verdict)
    return bad


def _list(numbers: Sequence[Any]) -> str:
    return ", ".join(str(n) for n in numbers)


def explain(
    case: Case,
    outcome: Outcome,
    measures: dict[str, Any],
    found: dict[str, bool],
    verdict: Verdict | None,
    error: str,
) -> list[str]:
    """One short sentence for each thing that failed, for the scorecard."""

    if outcome.impossible:
        return [f"Not possible today: {outcome.impossible}"]
    if outcome.failed:
        return [outcome.failed]
    expect, why = case.expect, []
    if found.get("intent") is False:
        why.append(f"it {outcome.intent or 'did nothing'}; expected {' or '.join(expect.intent)}")
    if found.get("asked") is False:
        why.append("it asked a question" if outcome.asked else "it did not ask")
    if found.get("saved") is False:
        why.append("it saved a version" if measures["saved"] else "it saved no version")
    if measures.get("unexpected"):
        why.append(f"changed part(s) {_list(measures['unexpected'])}, which should stay")
    if measures.get("missed"):
        why.append(f"left part(s) {_list(measures['missed'])} as they were")
    if measures.get("damaged"):
        why.append(
            f"{len(measures['damaged'])} of {measures['untouched']} parts that should stay"
            f" were not the same (part(s) {_list(measures['damaged'])})"
        )
    if found.get("count") is False:
        old, new = measures["parts"]
        why.append(f"{new} parts after {old}; expected {old + (expect.count_delta or 0)}")
    if found.get("layout") is False:
        wrong = [
            f"{n} is {measures['layouts'][str(n)] or 'unknown'}, not {name}"
            for n, name in expect.layout.items()
            if measures["layouts"][str(n)] != name
        ]
        why.append("layout of part " + "; ".join(wrong))
    if found.get("order") is False:
        why.append(f"order is {_list(measures['order'])}")
    if found.get("new_at") is False:
        why.append(f"new part(s) at {_list(measures['added'])}, not {_list(expect.new_at)}")
    if found.get("shorter") is False:
        longer = [
            n
            for n, (before, after) in measures["words"].items()
            if after is None or after >= before
        ]
        why.append(f"part(s) {_list(longer)} not shorter")
    if found.get("cites") is False:
        why.append("it does not cite " + " or ".join(expect.cites))
    if found.get("checked") is False:
        why.append(f"{measures['support']['unchecked']} changed part(s) were not checked")
    if found.get("numbers") is False:
        why.append("numbers from nowhere: " + ", ".join(measures["invented"]))
    for name, ok in found.items():
        if not ok and name.startswith("contains"):
            why.append(name.replace("contains", "lacks", 1))
        if not ok and name.startswith("without"):
            why.append(name.replace("without", "still has", 1))
        if not ok and name.startswith("gone"):
            why.append(name.replace("gone", "still says", 1))
    reading = judged(case, outcome, verdict)
    if "judge" in reading:
        why.append(f"the judge could not answer ({error})")
    if "judged no" in reading and verdict is not None:
        why.append(f"judge: no. {verdict.why}".strip())
    return why or ["failed"]
