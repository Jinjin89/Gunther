"""The Artifacts agent's workspace: what one conversation holds while it makes reports and slides.

A conversation keeps one *brief* (what the user wants; every line is marked as the user's own
words, as typed in the panel, or as the agent's guess), one *pool* of sources, each under a
number it keeps for the whole conversation, and one *work* per artifact (a deck or a report):
the approach, the material cards, the outline, and the parts (a slide or a section each) with
what the Checker found in them. See docs/ARTIFACT_AGENT_PLAN.md.

What is out of date is **computed**, never remembered as a mark. Each step keeps a digest of
what it was made from (the brief's fields, the approach, a part's outline item and cards) and is
out of date when that is not what it would be made from now. A check is out of date when the
text it checked is no longer the part's text. Nothing here calls a model.

Two numberings meet here. The *pool* numbers are fixed for the conversation, and a work's parts
cite them. A saved version numbers the passages it cites 1, 2, 3 in reading order, as its text
shows; ``Work.version_map[n - 1]`` is the pool number of the version's number n.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from gunther.agent import ANY_CITATION, Evidence, sentence_spans
from gunther.brief import _quoted
from gunther.outputs import Pool, Spec, _marked, heading_of, layout_of, section_hash, split_document
from gunther.schemas import ArtifactOut, ConversationCitationOut
from gunther.skillbook import skill_for
from gunther.slide_scene import rebind_graphics_hash

logger = logging.getLogger(__name__)

# Sources one conversation may hold (``AgentLimits.pool``).
POOL_LIMIT = 160
# Lines the brief keeps of each list (constraints, open questions).
MAX_LINES = 8
# Who a work is for when the brief does not say.
DEFAULT_AUDIENCE = "scientist"

Mark = Literal["you", "guessed", "edited"]
Kind = Literal["report", "slides"]
PartState = Literal["ok", "not_written", "out_of_date", "not_checked"]


# The workspace ---------------------------------------------------------------------------------


class Line(BaseModel):
    """One line of the brief, and whose it is: ``you`` said it in a message, ``edited`` was
    typed in the panel, ``guessed`` is the agent's. A model never changes the first two."""

    text: str = Field(max_length=300)
    mark: Mark = "guessed"


class ConversationBrief(BaseModel):
    """What the conversation is for. It belongs to the conversation, not to one artifact."""

    goal: Line | None = None  # what the work is about, and what it is for
    audience: Line | None = None  # free text: "lab members", "first-year students"
    constraints: list[Line] = Field(default_factory=list, max_length=MAX_LINES)
    open: list[Line] = Field(default_factory=list, max_length=MAX_LINES)


class WorkSpec(BaseModel):
    """What the brief says about one artifact."""

    kind: Kind
    use: Line | None = None  # decks: "talk" | "read"
    length: Line | None = None  # "10 minutes", "about 12 slides", "3 pages"
    structure: Line | None = None  # a structure id of the kind's skill
    theme: str | None = None  # slide visual theme, kept apart from report structure
    design_version: int | None = None


class CardRec(BaseModel):
    """A material card (see skill_runner.Card) with an id that stays the same."""

    id: str  # "C1", "C2"…: stable within the work
    claim: str
    refs: list[int] = Field(default_factory=list)  # pool numbers
    type: str = ""
    note: str = ""
    evidence: str = ""
    role: str = ""
    scope: str = ""
    units: str = ""


class ItemRec(BaseModel):
    """One planned part: what the outline says a slide or section is for."""

    id: str  # "p1", "p2"…: stable, never reused
    heading: str
    goal: str = ""
    message: str = ""
    question: str = ""
    requires: list[str] = Field(default_factory=list)
    layout: str = ""  # decks
    link: str = ""  # reports: how it follows the one before
    design: dict[str, str] | None = None
    layout_locked: bool = False
    design_locked: bool = False
    design_attempted: bool = False
    cards: list[str] = Field(default_factory=list)  # card ids
    refs: list[int] = Field(default_factory=list)  # pool numbers planned for it
    queries: list[str] = Field(default_factory=list)


class PartRec(BaseModel):
    """A slide or section as written, and what was found in it."""

    text: str  # as written, citing pool numbers
    inputs: str  # the digest it was written from (``digest_part_inputs``)
    checked: str | None = None  # section_hash of the text its check is for
    issues: list[dict] | None = None  # Issue.out() dicts
    coverage: dict | None = None


class Work(BaseModel):
    """One artifact in the conversation, and everything that was kept while making it."""

    key: str  # the lineage id; "draft_<uuid>" before the first save
    spec: WorkSpec
    title: str = ""
    approach: dict | None = None  # the fields of Approach.out()
    approach_inputs: str = ""
    approach_fixed: dict[str, str] = Field(default_factory=dict)  # fields the user typed
    approach_top: int = 0  # the highest pool number when the approach was written
    cards: list[CardRec] = Field(default_factory=list)
    outline: list[ItemRec] = Field(default_factory=list)
    outline_inputs: str = ""
    outline_review: dict | None = None
    parts: dict[str, PartRec] = Field(default_factory=dict)  # by item id
    version_id: str | None = None  # the version this work matches
    version_map: list[int] = Field(default_factory=list)  # version number n → version_map[n-1]
    skipped: list[dict] = Field(default_factory=list)  # the skill's notes since the last save
    next_card: int = 1
    next_item: int = 1


class Pending(BaseModel):
    """A question the agent asked and has not had an answer to."""

    question: str
    options: list[str]
    message_id: str


class Workspace(BaseModel):
    """Everything a conversation keeps besides its messages and brief."""

    pool: list[dict] = Field(default_factory=list)  # citations; ``ref`` is the pool number
    works: list[Work] = Field(default_factory=list)
    active: str | None = None  # a work key
    pending: Pending | None = None
    log: list[str] = Field(default_factory=list)  # the change log, oldest first
    asked_in_a_row: int = 0


def load_workspace(raw: str | None) -> Workspace:
    """A stored workspace. One that cannot be read starts again empty: what it held is
    derived from the versions, which are still there, and importing brings the works back."""

    try:
        return Workspace.model_validate_json(raw or "{}")
    except ValidationError:
        logger.warning("A stored artifact workspace could not be read", exc_info=True)
        return Workspace()


def dump_workspace(workspace: Workspace) -> str:
    return workspace.model_dump_json()


def load_brief(raw: str | None) -> ConversationBrief:
    """A stored brief; anything unreadable is an empty one."""

    try:
        return ConversationBrief.model_validate_json(raw or "{}")
    except ValidationError:
        logger.warning("A stored artifact brief could not be read", exc_info=True)
        return ConversationBrief()


def dump_brief(brief: ConversationBrief) -> str:
    return brief.model_dump_json()


def active_work(workspace: Workspace) -> Work | None:
    """The artifact the conversation is working on now."""

    return next((work for work in workspace.works if work.key == workspace.active), None)


# The pool and its numbers ---------------------------------------------------------------------


def evidence_from_citation(citation: ConversationCitationOut) -> Evidence:
    """A saved citation as a passage a model may cite again, under the number it has."""

    return Evidence(
        kind=citation.kind,
        title=citation.source_title,
        text=citation.quote,
        locator=citation.locator,
        status=citation.status,
        confidence=citation.confidence,
        url=citation.url,
        ref=citation.ref,
        payload=(citation, None),
        context=citation.support_text or "",
        extraction_warnings=tuple(citation.extraction_warnings),
    )


def pool_from(workspace: Workspace) -> Pool:
    """The conversation's pool, each source under the number it has had since it joined."""

    pool = Pool(POOL_LIMIT)
    pool.restore(
        [
            evidence_from_citation(ConversationCitationOut.model_validate(item))
            for item in workspace.pool
        ]
    )
    return pool


def store_pool(workspace: Workspace, pool: Pool) -> None:
    """Keep the pool in the workspace: each source as a citation whose ``ref`` is its number."""

    workspace.pool = [
        item.payload[0].model_copy(update={"ref": item.ref}).model_dump(mode="json")
        for item in pool.items
    ]


def _adopt(pool: Pool, citations: Sequence[ConversationCitationOut]) -> list[int]:
    """The pool numbers of a version's citations, in the version's own order (its numbers 1,
    2, 3…). A passage the pool already holds keeps its number; any other joins under the next."""

    numbers: list[int] = []
    for citation in citations:
        held = pool.adopt(evidence_from_citation(citation.model_copy(update={"ref": None})))
        assert held.ref is not None  # the pool numbers everything it holds
        numbers.append(held.ref)
    return numbers


_GROUP = re.compile(r"([ \t]?)\[(?:(i|p|d):(\d+)|(\d+(?:\s*[,，、]\s*\d+)*))\]")


def _translate(text: str, version_map: Sequence[int]) -> tuple[str, list[int]]:
    """A version's text with its source numbers (``[1]``, ``[p:2]``…) turned into the pool's,
    and the numbers it had to leave out. A number the version has no source for is left out:
    kept as it is, it could be read as some other source of the pool."""

    dropped: list[int] = []

    def number(version_number: int) -> int | None:
        if 1 <= version_number <= len(version_map):
            return version_map[version_number - 1]
        dropped.append(version_number)
        return None

    def replace(match: re.Match[str]) -> str:
        lead = match.group(1)
        if match.group(2):
            mapped = number(int(match.group(3)))
            return f"{lead}[{match.group(2)}:{mapped}]" if mapped is not None else ""
        kept = [
            f"[{mapped}]"
            for part in re.split(r"[,，、]", match.group(4))
            if (mapped := number(int(part))) is not None
        ]
        return lead + "".join(kept) if kept else ""

    translated = _GROUP.sub(replace, text)
    return rebind_graphics_hash(
        text,
        translated,
        {number: mapped for number, mapped in enumerate(version_map, start=1)},
    ), dropped


def pool_numbered(text: str, version_map: Sequence[int]) -> str:
    """A version's text as a work holds it, citing pool numbers. ``[2, 5]`` becomes ``[2][5]``,
    as the Checker writes it; a number the version has no source for is left out."""

    return _translate(text, version_map)[0]


def _cited(text: str) -> list[int]:
    """The numbers a text cites, in order of first use."""

    return list(dict.fromkeys(int(match.group(2)) for match in ANY_CITATION.finditer(text)))


# Finding a passage ------------------------------------------------------------------------------
#
# A message quotes a passage as the person saw it, with the numbers of the saved version; the
# work's text cites the pool's. So the numbers are never part of what is looked for.

# A citation group or the model's own marker: [3], [3, 4], [i:3], [?].
_MARK = r"\[(?:(?:i|p|d):\d+|\d+(?:\s*[,，、]\s*\d+)*|\?)\]"
_MARKS = re.compile(_MARK)
_ENDS_WITH_MARK = re.compile(rf"{_MARK}\s*\Z")
_MARKS_RIGHT_AFTER = re.compile(rf"(?:[ \t]*{_MARK})+")
_PUNCTUATION = ",.;:!?，。；：！？"
# How much a window of sentences must look like the passage to be taken for it.
RESEMBLANCE = 0.9
# The most windows one search compares closely; a longer text is not worth a longer wait.
CLOSE_COMPARISONS = 400


def _comparable(text: str) -> tuple[str, list[int]]:
    """A text as ``locate`` compares it: whitespace collapsed to one space, capitals folded,
    citation groups and [?] left out, and no space left before a stop. With it, where each of
    its characters came from in ``text``."""

    chars: list[str] = []
    came_from: list[int] = []
    marks = _MARKS.finditer(text)
    mark = next(marks, None)
    at = 0
    while at < len(text):
        if mark is not None and at == mark.start():
            at, mark = mark.end(), next(marks, None)
            continue
        char = text[at]
        if char.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                came_from.append(at)
        else:
            if char in _PUNCTUATION and chars and chars[-1] == " ":
                chars.pop()
                came_from.pop()
            for folded in char.casefold():
                chars.append(folded)
                came_from.append(at)
        at += 1
    if chars and chars[-1] == " ":
        chars.pop()
        came_from.pop()
    return "".join(chars), came_from


def _loosely(passage: str, text: str) -> tuple[int, int] | None:
    """The passage's place in the text when only spacing, capitals and source numbers differ.
    When the passage ends with a source number, the numbers right after the match are part of
    it: they are what the passage's number stood for."""

    wanted, _ = _comparable(passage)
    if not wanted:
        return None
    seen, came_from = _comparable(text)
    at = seen.find(wanted)
    if at < 0:
        return None
    start, end = came_from[at], came_from[at + len(wanted) - 1] + 1
    if _ENDS_WITH_MARK.search(passage):
        after = _MARKS_RIGHT_AFTER.match(text, end)
        if after is not None:
            end = after.end()
    return start, end


def _resembling(passage: str, text: str) -> tuple[int, int] | None:
    """The run of whole sentences (or list lines) of the text that looks most like the passage,
    when it looks enough like it. A model that quotes from memory slips a word or a mark."""

    wanted, _ = _comparable(passage)
    if not wanted:
        return None
    sentences: list[tuple[int, int, str]] = []
    for begin, end in sentence_spans(text):
        chunk = text[begin:end]
        begin += len(chunk) - len(chunk.lstrip())
        end -= len(chunk) - len(chunk.rstrip())
        shown, _ = _comparable(text[begin:end])
        if shown:
            sentences.append((begin, end, shown))
    best, found, compared = 0.0, None, 0
    matcher = difflib.SequenceMatcher(None, "", wanted, autojunk=False)
    for first in range(len(sentences)):
        window = ""
        for last in range(first, len(sentences)):
            window = f"{window} {sentences[last][2]}" if window else sentences[last][2]
            if len(window) > len(wanted) * 1.25 + 4:
                break
            if len(window) < len(wanted) * 0.8 - 4:
                continue
            matcher.set_seq1(window)
            floor = max(RESEMBLANCE, best)
            if matcher.real_quick_ratio() < floor or matcher.quick_ratio() < floor:
                continue
            compared += 1
            if compared > CLOSE_COMPARISONS:
                return found
            ratio = matcher.ratio()
            if ratio >= RESEMBLANCE and ratio > best:
                best, found = ratio, (sentences[first][0], sentences[last][1])
    return found


def locate(passage: str, text: str) -> tuple[int, int] | None:
    """Where a passage is in a text, as (start, end) in ``text``; None when it is not there.

    1. An exact match (the first one).
    2. Otherwise a match with whitespace collapsed, capitals folded and citation groups (and
       [?]) ignored, mapped back to the text's own positions.
    3. Otherwise the best window of whole sentences (or list lines) whose likeness to the
       passage is at least 0.9 (``difflib``).
    """

    if not passage.strip():
        return None
    at = text.find(passage)
    if at >= 0:
        return at, at + len(passage)
    return _loosely(passage, text) or _resembling(passage, text)


# What depends on what -----------------------------------------------------------------------------
#
# brief → approach → outline → parts → checks. A digest is a set of named inputs, each as the
# short hash of its canonical JSON; the whole is compared, and the names say what changed.


def _short(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _digest(inputs: Mapping[str, object]) -> str:
    return json.dumps(
        {name: _short(value) for name, value in inputs.items()},
        sort_keys=True,
        separators=(",", ":"),
    )


def _words(line: Line | None) -> str:
    return " ".join(line.text.split()) if line else ""


def _same_words(line: Line | None) -> str:
    """A brief line for a digest: spacing and capitals are no change."""

    return _fold(line.text) if line else ""


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def _audience(brief: ConversationBrief) -> str:
    """Who the steps write for: the brief's audience, else a scientist. Saying "scientist" when
    the brief had none is no change."""

    return _words(brief.audience) or DEFAULT_AUDIENCE


APPROACH_FROM = {
    "goal": "the goal",
    "audience": "the audience",
    "kind": "the kind",
    "use": "the use",
    "length": "the length",
    "structure": "the structure",
}
OUTLINE_FROM = {"approach": "the approach", "cards": "the material cards"}
PART_FROM = {
    "item": "its outline item",
    "cards": "its cards",
    "audience": "the audience",
    "kind": "the kind",
    "use": "the use",
}


def digest_approach_inputs(brief: ConversationBrief, work: Work) -> str:
    """What the approach is written from: the brief's goal and audience (a scientist when it
    has none), and the artifact's kind, use, length and structure; spacing and capitals are
    no change. Constraints are not part of it (§2.5)."""

    return _digest(
        {
            "goal": _same_words(brief.goal),
            "audience": _fold(_audience(brief)),
            "kind": work.spec.kind,
            "use": _same_words(work.spec.use),
            "length": _same_words(work.spec.length),
            "structure": _same_words(work.spec.structure),
        }
    )


def digest_outline_inputs(work: Work) -> str:
    """What the outline is planned from: the approach, and the material cards it points to."""

    return _digest({"approach": work.approach, "cards": [card.model_dump() for card in work.cards]})


def digest_part_inputs(brief: ConversationBrief, work: Work, item: ItemRec) -> str:
    """What a part is written from: its outline item (heading, goal, layout, link, design), the
    contents of its cards, and the audience (a scientist when the brief has none), kind and use."""

    cards = {card.id: card for card in work.cards}
    return _digest(
        {
            "item": {
                "heading": item.heading,
                "goal": item.goal,
                "layout": item.layout,
                "link": item.link,
                "design": item.design,
            }
            | {
                key: value
                for key, value in {
                    "message": item.message,
                    "question": item.question,
                    "requires": item.requires,
                }.items()
                if value
            },
            "cards": [
                cards[card_id].model_dump() if card_id in cards else {"missing": card_id}
                for card_id in item.cards
            ],
            "audience": _fold(_audience(brief)),
            "kind": work.spec.kind,
            "use": _same_words(work.spec.use),
        }
    )


def _what_changed(was: str, now: str, names: Mapping[str, str]) -> str:
    """The inputs that differ between two digests, in words ("the goal and the audience
    changed"). A digest that cannot be read says only that its inputs changed."""

    try:
        before, after = json.loads(was), json.loads(now)
        differ = [label for key, label in names.items() if before.get(key) != after.get(key)]
    except (ValueError, AttributeError):
        differ = []
    if not differ:
        return "what it was made from changed"
    listed = differ[0] if len(differ) == 1 else f"{', '.join(differ[:-1])} and {differ[-1]}"
    return f"{listed} changed"


class StepState(BaseModel):
    """Whether the approach or the outline is up to date."""

    state: Literal["ok", "not_written", "out_of_date"]
    why: str = ""


class PartStatus(BaseModel):
    """Where one part stands. ``position`` counts from 1."""

    position: int
    item_id: str
    heading: str
    layout: str
    state: PartState
    why: str = ""


def approach_state(work: Work, brief: ConversationBrief) -> StepState:
    """The approach is out of date when the brief fields it was written from are not today's."""

    if work.approach is None:
        return StepState(state="not_written")
    now = digest_approach_inputs(brief, work)
    if work.approach_inputs != now:
        return StepState(
            state="out_of_date", why=_what_changed(work.approach_inputs, now, APPROACH_FROM)
        )
    return StepState(state="ok")


def outline_state(work: Work) -> StepState:
    """The outline is out of date when the approach it was planned from is not today's."""

    if not work.outline:
        return StepState(state="not_written")
    now = digest_outline_inputs(work)
    if work.outline_inputs != now:
        return StepState(
            state="out_of_date", why=_what_changed(work.outline_inputs, now, OUTLINE_FROM)
        )
    return StepState(state="ok")


def status(work: Work, brief: ConversationBrief) -> list[PartStatus]:
    """Each part, in outline order: not written, out of date (its item, cards, the audience,
    the kind or the use are not what it was written from), not checked (its check is of other
    text, or there is none), or ok."""

    found: list[PartStatus] = []
    for position, item in enumerate(work.outline, start=1):
        part = work.parts.get(item.id)
        state: PartState = "ok"
        why = ""
        if part is None:
            state = "not_written"
        else:
            now = digest_part_inputs(brief, work, item)
            if part.inputs != now:
                state, why = "out_of_date", _what_changed(part.inputs, now, PART_FROM)
            elif part.issues is None or part.checked != section_hash(part.text):
                state = "not_checked"
        found.append(
            PartStatus(
                position=position,
                item_id=item.id,
                heading=item.heading,
                layout=item.layout,
                state=state,
                why=why,
            )
        )
    return found


def ready(work: Work, brief: ConversationBrief) -> list[str]:
    """What stops this artifact from being saved, in plain words. Saving needs nothing out of
    date: the approach, the outline and every part.

    A part that is up to date but not checked does not stop it: that is text the user typed (a
    version saved from the editor), which is saved as it is and shown as "Not re-checked", as
    an edit always was. Everything the agent writes is checked as it is written. An artifact
    brought in from a version made without an approach (an older builder) has none, and needs
    none to take a small change; a new artifact needs one before its outline."""

    if not work.outline:
        return ["Nothing is planned yet."]
    problems: list[str] = []
    approach = approach_state(work, brief)
    if approach.state == "out_of_date":
        problems.append(f"The approach is out of date ({approach.why}).")
    elif approach.state == "not_written" and not work.parts:
        problems.append("The approach is not written yet.")
    outline = outline_state(work)
    if outline.state == "out_of_date":
        problems.append(f"The outline is out of date ({outline.why}).")
    unit = "Slide" if work.spec.kind == "slides" else "Section"
    for part in status(work, brief):
        if part.state == "not_written":
            problems.append(f"{unit} {part.position} is not written yet.")
        elif part.state == "out_of_date":
            problems.append(f"{unit} {part.position} is out of date ({part.why}).")
    return problems


def _stamp(work: Work, brief: ConversationBrief) -> None:
    """Record the approach (when there is one), the outline and every part as made from what
    they would be made from now."""

    if work.approach is not None:
        work.approach_inputs = digest_approach_inputs(brief, work)
    work.outline_inputs = digest_outline_inputs(work)
    for item in work.outline:
        if item.id in work.parts:
            work.parts[item.id].inputs = digest_part_inputs(brief, work, item)


# The brief ------------------------------------------------------------------------------------


class BriefUpdate(BaseModel):
    """What the latest message says about the work: one point, and the user's words for it."""

    field: Literal[
        "goal",
        "audience",
        "constraint",
        "drop_constraint",
        "open",
        "close_open",
        "use",
        "length",
        "structure",
    ]
    text: str = Field(default="", max_length=300)
    # The user's own words that say it; empty for the model's own guess.
    quote: str = Field(default="", max_length=300)


@dataclass
class BriefResult:
    """The brief and the artifact's spec after the updates, and why some were not made."""

    brief: ConversationBrief
    spec: WorkSpec | None
    notes: list[str] = field(default_factory=list)
    changed: bool = False


def _is_theirs(quote: str, said: str, clicked: str) -> bool:
    """Whether a quote is the user's: found in their latest message (spaces and case folded,
    as the brief of an Ask conversation does), or the option they clicked."""

    if _quoted(quote, said):
        return True
    return bool(_fold(quote)) and _fold(quote) == _fold(clicked)


def _find(lines: Sequence[Line], text: str) -> int | None:
    """The line a model means by these words: the one with the same words, else the only one
    that contains them or is contained in them."""

    wanted = _fold(text)
    for at, line in enumerate(lines):
        if _fold(line.text) == wanted:
            return at
    near = [
        at
        for at, line in enumerate(lines)
        if wanted in _fold(line.text) or _fold(line.text) in wanted
    ]
    return near[0] if len(near) == 1 else None


def _brief_value(kind: Kind, name: str, text: str) -> str | None:
    """A use or a structure as the kind's skill knows it, or None. Other fields are free text."""

    skill = skill_for(kind)
    wanted = _fold(text)
    if name == "use":
        return wanted if wanted in skill.uses else None
    if name == "structure":
        # A report style ("decision brief") names the structure it fixes.
        return wanted if wanted in skill.structures else skill.styles.get(wanted.replace(" ", "_"))
    return " ".join(text.split())


def _set_line(current: Line | None, text: str, mark: Mark, label: str) -> tuple[Line | None, str]:
    """A single line (goal, audience, use…) after an update, and why it was not made. A line
    the user said or edited is replaced only by an update that is the user's again; the same
    words are no change, except that a guess the user now says becomes theirs."""

    if current is not None and _fold(current.text) == _fold(text):
        if mark == "you" and current.mark == "guessed":
            return current.model_copy(update={"mark": "you"}), ""
        return current, ""
    if current is not None and current.mark != "guessed" and mark != "you":
        whose = "you wrote it" if current.mark == "edited" else "you said it"
        return current, f"Kept the {label} as it is: {whose}, and the update is a guess."
    return Line(text=text, mark=mark), ""


def _add_line(lines: list[Line], text: str, mark: Mark, label: str) -> str:
    """Add a constraint or an open question, and say why it was not added. At the limit the
    oldest guess makes room; the user's own lines never do."""

    same = next((at for at, line in enumerate(lines) if _fold(line.text) == _fold(text)), None)
    if same is not None:
        if mark == "you" and lines[same].mark == "guessed":
            lines[same] = lines[same].model_copy(update={"mark": "you"})
        return ""
    if len(lines) >= MAX_LINES:
        oldest = next((at for at, line in enumerate(lines) if line.mark == "guessed"), None)
        if oldest is None:
            return f"Could not add the {label}: there are already {MAX_LINES}, all yours."
        del lines[oldest]
    lines.append(Line(text=text, mark=mark))
    return ""


def apply_brief_updates(
    brief: ConversationBrief,
    work: Work | None,
    updates: Sequence[BriefUpdate],
    said: str,
    clicked: str = "",
) -> BriefResult:
    """Fold a decision's brief updates into the brief and the active artifact's spec.

    - An update whose quote is in the user's latest message (``said``), or is the option they
      clicked, is marked ``you``; any other is a guess.
    - A ``you`` or ``edited`` line is replaced only by an update marked ``you``.
    - Dropping a constraint or closing an open question needs the user's words; the agent may
      close one of its own guessed questions without them.
    - ``use``, ``length`` and ``structure`` belong to the active artifact (``work``).

    Nothing is changed in place. An update that is not made says why in ``notes``.
    """

    brief = brief.model_copy(deep=True)
    spec = work.spec.model_copy(deep=True) if work is not None else None
    before = (brief.model_dump(), spec.model_dump() if spec else None)
    # Once the approach is written, what it was written from changes only by the user's words:
    # a new guess would make it (and the outline and parts after it) out of date for nothing.
    settled = work is not None and work.approach is not None
    notes: list[str] = []
    for update in updates:
        name = update.field
        text = " ".join(update.text.split())
        if not text:
            notes.append(f"Left out a {name.replace('_', ' ')} update with no text.")
            continue
        mark: Mark = "you" if _is_theirs(update.quote, said, clicked) else "guessed"
        note = ""
        if settled and mark == "guessed" and name in APPROACH_FROM:
            notes.append(
                f"Kept the {name} as it is: the approach was written from it, and only your own "
                "words change it now."
            )
            continue
        if name in ("goal", "audience"):
            line, note = _set_line(getattr(brief, name), text, mark, name)
            setattr(brief, name, line)
        elif name == "constraint":
            note = _add_line(brief.constraints, text, mark, "constraint")
        elif name == "open":
            note = _add_line(brief.open, text, mark, "open question")
        elif name == "drop_constraint":
            at = _find(brief.constraints, text)
            if mark != "you":
                note = f"Kept the constraint “{text[:60]}”: dropping one needs your own words."
            elif at is None:
                note = f"There is no constraint “{text[:60]}” to drop."
            else:
                del brief.constraints[at]
        elif name == "close_open":
            at = _find(brief.open, text)
            if at is None:
                note = f"There is no open question “{text[:60]}” to close."
            elif mark == "you" or brief.open[at].mark == "guessed":
                del brief.open[at]
            else:
                note = f"Kept the open question “{text[:60]}”: closing it needs your own words."
        elif spec is None:
            note = f"Could not set the {name}: there is no artifact yet."
        elif name == "use" and spec.kind != "slides":
            note = "A report has no use; it was left out."
        elif (value := _brief_value(spec.kind, name, text)) is None:
            skill = skill_for(spec.kind)
            known = skill.uses if name == "use" else skill.structures
            note = f"“{text[:60]}” is not a {name} of the {spec.kind} skill" + (
                f"; choose one of: {', '.join(known)}." if known else "."
            )
        else:
            line, note = _set_line(getattr(spec, name), value, mark, name)
            setattr(spec, name, line)
        if note:
            notes.append(note)
    after = (brief.model_dump(), spec.model_dump() if spec else None)
    return BriefResult(brief, spec, notes, before != after)


def edit_brief(
    brief: ConversationBrief,
    work: Work | None,
    *,
    goal: str | None = None,
    audience: str | None = None,
    constraints: Sequence[str] | None = None,
    open: Sequence[str] | None = None,
    use: str | None = None,
    length: str | None = None,
    structure: str | None = None,
) -> tuple[ConversationBrief, WorkSpec | None]:
    """The brief and spec after the user typed in the panel. A line they set is ``edited``;
    one they left as it was keeps its mark. ``None`` leaves a field alone, empty text clears
    it, and a list is the whole new list. Raises ValueError for a use or structure the skill
    does not have, or when there is no artifact to set one on."""

    brief = brief.model_copy(deep=True)
    spec = work.spec.model_copy(deep=True) if work is not None else None

    def typed(current: Line | None, text: str) -> Line | None:
        text = " ".join(text.split())
        if not text:
            return None
        if current is not None and _fold(current.text) == _fold(text):
            return current
        return Line(text=text, mark="edited")

    def listed(current: list[Line], texts: Sequence[str]) -> list[Line]:
        kept: list[Line] = []
        for text in texts:
            was = next((line for line in current if _fold(line.text) == _fold(text)), None)
            line = typed(was, text)
            if line is not None and all(_fold(line.text) != _fold(other.text) for other in kept):
                kept.append(line)
        if len(kept) > MAX_LINES:
            raise ValueError(f"A list holds at most {MAX_LINES} lines.")
        return kept

    if goal is not None:
        brief.goal = typed(brief.goal, goal)
    if audience is not None:
        brief.audience = typed(brief.audience, audience)
    if constraints is not None:
        brief.constraints = listed(brief.constraints, constraints)
    if open is not None:
        brief.open = listed(brief.open, open)
    for name, text in (("use", use), ("length", length), ("structure", structure)):
        if text is None:
            continue
        if spec is None:
            raise ValueError(f"There is no artifact yet to set the {name} on.")
        if not text.strip():
            setattr(spec, name, None)
        elif name == "use" and spec.kind != "slides":
            raise ValueError("A report has no use.")
        elif (value := _brief_value(spec.kind, name, text)) is None:
            raise ValueError(f"“{text[:60]}” is not a {name} of the {spec.kind} skill.")
        else:
            setattr(spec, name, typed(getattr(spec, name), value))
    return brief, spec


def keep_edits(
    base: ConversationBrief, mine: ConversationBrief, theirs: ConversationBrief
) -> ConversationBrief:
    """The brief a turn ends with, keeping what the user changed in the panel while it ran.

    ``base`` is the brief the turn began with, ``mine`` the one it ends with and ``theirs`` the
    one stored now. A line the user set, changed or removed meanwhile is theirs; the rest is
    the turn's."""

    kept = mine.model_copy(deep=True)
    for name in ("goal", "audience"):
        if getattr(theirs, name) != getattr(base, name):
            setattr(kept, name, getattr(theirs, name))
    for name in ("constraints", "open"):
        lines = list(getattr(kept, name))
        was, now = getattr(base, name), getattr(theirs, name)
        for line in was:
            if line not in now:
                lines = [other for other in lines if _fold(other.text) != _fold(line.text)]
        for line in now:
            if line in was:
                continue
            lines = [other for other in lines if _fold(other.text) != _fold(line.text)]
            lines.append(line)
        setattr(kept, name, lines[:MAX_LINES])
    return kept


def keep_spec_edits(base: WorkSpec, mine: WorkSpec, theirs: WorkSpec) -> WorkSpec:
    """An artifact's spec at the end of a turn, keeping the fields the user changed meanwhile."""

    kept = mine.model_copy(deep=True)
    for name in ("use", "length", "structure"):
        if getattr(theirs, name) != getattr(base, name):
            setattr(kept, name, getattr(theirs, name))
    return kept


def render_brief(brief: ConversationBrief, work: Work | None = None) -> str:
    """The brief as plain lines for the skill's steps: what it is about, for whom, how it is
    used and how long, and the constraints. Open questions are for the agent to settle, not
    for the writer, so they are left out."""

    lines: list[str] = []
    if brief.goal:
        lines.append(f"Goal: {_words(brief.goal)}")
    if brief.audience:
        lines.append(f"Audience: {_words(brief.audience)}")
    if work is not None:
        for name in ("use", "length", "structure"):
            if getattr(work.spec, name):
                lines.append(f"{name.capitalize()}: {_words(getattr(work.spec, name))}")
    if brief.constraints:
        lines.append("Constraints:")
        lines += [f"- {_words(line)}" for line in brief.constraints]
    return "\n".join(lines)


def _style_of(work: Work) -> str | None:
    """The report style whose structure the user chose, which it fixes. A structure the
    agent guessed fixes nothing."""

    line = work.spec.structure
    if work.spec.kind != "report" or line is None or line.mark == "guessed":
        return None
    styles = skill_for("report").styles
    return next(
        (style for style, structure in styles.items() if structure == _fold(line.text)), None
    )


def spec_of(brief: ConversationBrief, work: Work) -> Spec:
    """The request the skill's steps are given: the brief's audience (a scientist when there
    is none), the brief as lines, and for a report the style that fixes the chosen structure."""

    return Spec(
        kind=work.spec.kind,
        audience=_audience(brief),
        style=_style_of(work),
        brief=render_brief(brief, work),
        theme=work.spec.theme,
        design_version=work.spec.design_version,
    )


# Bringing versions in ----------------------------------------------------------------------------


def _layout_of(kind: str, position: int, section: str, planned: str | None = None) -> str:
    """How a slide is drawn, as far as it can be told: by its layout marker; else by the
    layout it was planned with; else, for a deck's first slide headed with "# ", the title
    slide. Empty when none of these says."""

    if kind != "slides":
        return ""
    marked, body = layout_of(section)
    if marked:
        return marked
    if planned:
        return planned
    return "title" if position == 0 and body.lstrip().startswith("# ") else ""


def _incomplete(coverage: Mapping[str, Any] | None) -> bool:
    return coverage is not None and coverage.get("status") != "complete"


def _coverage_in_pool(
    coverage: Mapping[str, Any], text: str, version_map: Sequence[int]
) -> dict[str, Any]:
    """A check's coverage record with the version's numbers turned into the pool's."""

    claims = []
    for claim in coverage.get("claims", []):
        mapped = dict(claim)
        if "text" in claim:
            mapped["text"] = pool_numbered(str(claim["text"]), version_map)
        if "refs" in claim:
            mapped["refs"] = [
                version_map[ref - 1] for ref in claim["refs"] if 1 <= ref <= len(version_map)
            ]
        claims.append(mapped)
    result = {**coverage, "answerHash": section_hash(text)}
    if "claims" in coverage:
        result["claims"] = claims
    return result


def _take_check(
    part: PartRec, section: str, record: Mapping[str, Any] | None, version_map: Sequence[int]
) -> bool:
    """Give a part what the Checker found in the version's section that has its text.

    ``record`` is the version's, keyed by the hash of the section in the version's numbers;
    the part's own check stays unless it has none or a poorer one. True when the part changed.
    """

    if not record or record.get("hash") != section_hash(section):
        return False
    held = part.issues is not None and part.checked == section_hash(part.text)
    if held and not (_incomplete(part.coverage) and not _incomplete(record.get("coverage"))):
        return False
    coverage = record.get("coverage")
    part.checked = section_hash(part.text)
    part.issues = [dict(issue) for issue in record.get("issues", [])]
    part.coverage = _coverage_in_pool(coverage, part.text, version_map) if coverage else None
    return True


def _note_dropped(work: Work, step: str, position: int, dropped: Sequence[int]) -> None:
    """Say on the work that a section cited numbers its version has no source for."""

    if dropped:
        unit = "Slide" if work.spec.kind == "slides" else "Section"
        numbers = ", ".join(f"[{number}]" for number in dict.fromkeys(dropped))
        work.skipped.append(
            {
                "step": step,
                "label": f"{unit} {position}",
                "reason": f"It cites {numbers}, but its version has no such sources; "
                "they were left out.",
            }
        )


def _spec_of_version(version: ArtifactOut) -> WorkSpec:
    """The spec a version was made to: its kind, a deck's use, and the structure it follows."""

    skill = skill_for(version.kind)
    spec = WorkSpec(kind=version.kind)
    if version.kind == "slides":
        first = split_document(version.content, version.kind)[1]
        if first:
            spec.theme = _marked(first[0]).theme
            spec.design_version = _marked(first[0]).design_version
    use = getattr(version.provenance, "use", None)
    if use:
        spec.use = Line(text=use, mark="guessed")
    approach = getattr(version.provenance, "approach", None)
    if version.style in skill.styles:
        # A report style the person picked fixes its structure; "auto" does not.
        spec.structure = Line(text=skill.styles[version.style], mark="edited")
    elif approach is not None and approach.structure:
        spec.structure = Line(text=approach.structure, mark="guessed")
    return spec


def import_work(
    version: ArtifactOut,
    checks: Mapping[str, Mapping[str, Any]],
    workspace: Workspace,
    brief: ConversationBrief,
) -> Work:
    """Make a work from a version made before the agent kept its notes (or with no work),
    and add it to the workspace.

    - The spec comes from the version's kind, use and style; the approach from its provenance.
    - There are no cards. The outline is the version's outline zipped with its sections (made
      from the sections' headings when the counts differ); each item lists the sources its
      section cites, and a slide's layout is its marker when it has one. The parts are the
      sections, with the latest checks matched by hash.
    - The version's citations join the pool: numbers 1..N in an empty pool, and the same N
      numbers in ``version_map``; a source the pool already holds keeps its number.
    - Everything is stamped as up to date. When the brief has no audience yet, the version's
      own becomes a guessed one, so that nothing is out of date for having been unsaid.

    ``checks`` are the version's latest records by section hash. Raises ValueError when the
    workspace already has a work for the version's lineage.
    """

    if any(work.key == version.lineage_id for work in workspace.works):
        raise ValueError(f"The workspace already has a work for {version.lineage_id}.")
    kind = version.kind
    sections = split_document(version.content, kind)[1]
    pool = pool_from(workspace)
    version_map = _adopt(pool, version.citations)
    work = Work(
        key=version.lineage_id,
        spec=_spec_of_version(version),
        title=version.title,
        version_id=version.id,
        version_map=version_map,
        approach_top=pool.top,
    )
    approach = getattr(version.provenance, "approach", None)
    if approach is not None:
        work.approach = approach.model_dump(mode="json")
    planned = list(version.outline) if len(version.outline) == len(sections) else []
    for position, section in enumerate(sections):
        plan = planned[position] if planned else None
        text, dropped = _translate(section, version_map)
        marked = _marked(section) if kind == "slides" else None
        design = plan.design.model_dump() if plan and plan.design else None
        if marked and marked.composition != "standard":
            design = {**(design or {}), "composition": marked.composition}
        item = ItemRec(
            id=plan.id if plan and plan.id else f"p{work.next_item}",
            heading=heading_of(section) or (plan.heading if plan else ""),
            goal=plan.goal if plan else "",
            message=plan.message if plan else "",
            question=plan.question if plan else "",
            requires=list(plan.requires) if plan else [],
            layout=_layout_of(kind, position, section, plan.layout if plan else None),
            link=plan.link if plan else "",
            design=design,
            layout_locked=plan.layout_locked if plan else False,
            design_locked=plan.design_locked if plan else False,
            design_attempted=plan.design_attempted if plan else False,
            refs=_cited(text),
        )
        work.next_item += 1
        part = PartRec(text=text, inputs="")
        _take_check(part, section, checks.get(section_hash(section)), version_map)
        work.outline.append(item)
        work.parts[item.id] = part
        _note_dropped(work, "import", position + 1, dropped)
    if brief.audience is None and version.audience:
        brief.audience = Line(text=version.audience, mark="guessed")
    _stamp(work, brief)
    store_pool(workspace, pool)
    workspace.works.append(work)
    if workspace.active is None:
        workspace.active = work.key
    workspace.log.append(
        f"“{version.title}” (version {version.version_number}) was brought in: "
        "it was made before the agent kept its notes."
    )
    return work


def _match(work: Work, sections: Sequence[str], texts: Sequence[str]) -> list[ItemRec | None]:
    """The item each section of a changed document belongs to: the one whose part has exactly
    this text; else the one with the same heading; else, when there are as many sections as
    items, the one in the same place. A section that matches none is new, and an item that no
    section matches is gone. Each rule runs over all the sections before the next begins, so a
    section that moved is not mistaken for another."""

    found: list[ItemRec | None] = [None] * len(sections)
    free = list(work.outline)

    def take(wanted: Callable[[int, ItemRec], bool]) -> None:
        for position in range(len(sections)):
            if found[position] is not None:
                continue
            item = next((item for item in free if wanted(position, item)), None)
            if item is not None:
                found[position] = item
                free.remove(item)

    take(
        lambda position, item: item.id in work.parts and work.parts[item.id].text == texts[position]
    )
    take(lambda position, item: _fold(item.heading) == _fold(heading_of(sections[position])))
    if len(sections) == len(work.outline):
        take(lambda position, item: work.outline[position] is item)
    return found


def _refresh_checks(work: Work, head: ArtifactOut, checks: Mapping[str, Mapping[str, Any]]) -> bool:
    """The work already matches the head, so only the head's checks can be new (the user
    pressed Check again): a part whose text is a section of the head takes its findings."""

    by_text = {
        pool_numbered(section, work.version_map): section
        for section in split_document(head.content, work.spec.kind)[1]
    }
    changed = False
    for part in work.parts.values():
        section = by_text.get(part.text)
        if section is not None and _take_check(
            part, section, checks.get(section_hash(section)), work.version_map
        ):
            changed = True
    return changed


def sync(
    work: Work,
    head: ArtifactOut,
    checks: Mapping[str, Mapping[str, Any]],
    workspace: Workspace,
    brief: ConversationBrief,
) -> bool:
    """Bring a work in line with the newest version of its lineage when that was made outside
    the agent (a hand edit; the old build and revise). True when anything changed.

    When the work already matches the head, only the head's checks can be new (Check again).
    Otherwise:

    1. The head's citations are found in the pool by what they quote, and added when the pool
       lacks them. That gives the head's ``version_map``; the sections are turned into the
       pool's numbers with it.
    2. Each section is matched to an item (``_match``). A part whose text is the same stays as
       it is, digest included. Any other section becomes the part's text, with the head's
       check when its hash matches and none otherwise, and counts as up to date: the user
       wrote it. A heading the user changed becomes the item's, and so does a layout marker.
    3. A section no item matches becomes a new item with the next id. An item no section
       matches is dropped, and its id is never used again.

    The approach and the outline are not touched: the head's own are not read.
    """

    if work.version_id == head.id:
        return _refresh_checks(work, head, checks)
    kind = work.spec.kind
    pool = pool_from(workspace)
    version_map = _adopt(pool, head.citations)
    store_pool(workspace, pool)
    sections = split_document(head.content, kind)[1]
    translated = [_translate(section, version_map) for section in sections]
    texts = [text for text, _ in translated]
    matched = _match(work, sections, texts)
    items: list[ItemRec] = []
    parts: dict[str, PartRec] = {}
    written: set[str] = set()
    for position, (section, (text, dropped), old) in enumerate(
        zip(sections, translated, matched, strict=True)
    ):
        if old is None:
            item = ItemRec(
                id=f"p{work.next_item}",
                heading=heading_of(section),
                layout=_layout_of(kind, position, section),
                refs=_cited(text),
            )
            work.next_item += 1
            part = None
        else:
            item, part = old, work.parts.get(old.id)
        _note_dropped(work, "sync", position + 1, dropped)
        if part is None or part.text != text:
            if old is not None:
                # What the user typed says what the slide is called and how it is drawn now.
                item = item.model_copy(
                    update={
                        "heading": heading_of(section) or item.heading,
                        "layout": _layout_of(kind, position, section, item.layout),
                    }
                )
            part = PartRec(text=text, inputs="")
            written.add(item.id)
        _take_check(part, section, checks.get(section_hash(section)), version_map)
        items.append(item)
        parts[item.id] = part
    work.outline, work.parts = items, parts
    work.title, work.version_map, work.version_id = head.title, version_map, head.id
    for item in work.outline:
        if item.id in written:
            parts[item.id].inputs = digest_part_inputs(brief, work, item)
    return True
