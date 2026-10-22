# ruff: noqa: E501
"""The judge: a model reads an artifact and says how good it is (text; slide PNGs when the
judge model can see images).

Three tests: an absolute rubric with a verdict per slide or section, the titles-only test, and
a blind, order-swapped pairwise comparison of two methods. The scoring math at the bottom is
plain code and is unit-tested.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from gunther.llm import Image, ModelError, ModelGateway, ModelInfo
from gunther.outputs import split_document

Verdict = Literal["ship", "minor edit", "rework"]
CRITERIA = ("insight", "storyline", "titles", "evidence", "clarity", "correctness")
PAIR_CRITERIA = ("insight", "storyline", "titles", "evidence", "clarity", "overall")
MAX_IMAGES = 24
SOURCE_QUOTE_CHARS = 2400  # whole passages: a studio passage is about 1,400 characters
SOURCE_BUDGET_CHARS = 120_000


# Usage -------------------------------------------------------------------------------------


@dataclass
class Tally:
    calls: int = 0
    tokens: int = 0

    def add(self, other: Tally) -> None:
        self.calls += other.calls
        self.tokens += other.tokens


class Meter:
    """Counts model calls and tokens per thread bucket (the judge runs several at once)."""

    def __init__(self, gateway: ModelGateway) -> None:
        self._local = threading.local()
        self._lock = threading.Lock()
        real = gateway._stream

        def counted(*args: Any, **kwargs: Any) -> Any:
            tokens = 0
            try:
                result = real(*args, **kwargs)
                used = (result[3] if isinstance(result, tuple) and len(result) > 3 else None) or {}
                tokens = used.get("total_tokens") or (
                    used.get("prompt_tokens", 0) + used.get("completion_tokens", 0)
                )
                return result
            finally:
                bucket = getattr(self._local, "bucket", None)
                if bucket is not None:
                    with self._lock:
                        bucket.calls += 1
                        bucket.tokens += tokens

        gateway._stream = counted  # type: ignore[method-assign]

    @contextmanager
    def bucket(self) -> Iterator[Tally]:
        tally = Tally()
        self._local.bucket = tally
        try:
            yield tally
        finally:
            self._local.bucket = None


# Schemas -----------------------------------------------------------------------------------


class SectionVerdict(BaseModel):
    index: int = Field(description="Slide or section number, counted from 1")
    verdict: Verdict
    reason: str = Field(max_length=300)


class Absolute(BaseModel):
    insight: int = Field(ge=1, le=10)
    storyline: int = Field(ge=1, le=10)
    titles: int = Field(ge=1, le=10)
    evidence: int = Field(ge=1, le=10)
    clarity: int = Field(ge=1, le=10)
    correctness: int = Field(ge=1, le=10)
    visual: int | None = Field(default=None, ge=1, le=10)
    sections: list[SectionVerdict]
    summary: str = Field(max_length=600)


class Retold(BaseModel):
    core_message: str


class Matched(BaseModel):
    match: bool
    reason: str = Field(max_length=300)


class Compared(BaseModel):
    insight: Literal["A", "B", "tie"]
    storyline: Literal["A", "B", "tie"]
    titles: Literal["A", "B", "tie"]
    evidence: Literal["A", "B", "tie"]
    clarity: Literal["A", "B", "tie"]
    overall: Literal["A", "B", "tie"]
    reason: str = Field(max_length=500)


# Views of an artifact ----------------------------------------------------------------------


def parts_of(kind: str, content: str) -> list[str]:
    return split_document(content, kind)[1]


def numbered(kind: str, content: str) -> str:
    """The document with each slide or section labelled by its number."""

    label = "Slide" if kind == "slides" else "Section"
    preamble, parts = split_document(content, kind)
    shown = [f"[{label} {number}]\n{text}" for number, text in enumerate(parts, start=1)]
    return "\n\n".join(([f"[Opening]\n{preamble}"] if preamble else []) + shown)


def source_view(citations: Sequence[dict[str, Any]]) -> str:
    """The cited passages, compact: [n] title, locator: the start of the quote."""

    lines, size = [], 0
    for number, cite in enumerate(citations, start=1):
        quote = " ".join(str(cite.get("quote") or cite.get("supportText") or "").split())
        line = (
            f"[{cite.get('ref') or number}] {cite.get('sourceTitle') or cite.get('source_title') or ''}"
            f", {cite.get('locator') or 'no locator'}: “{quote[:SOURCE_QUOTE_CHARS]}”"
        )
        if size + len(line) > SOURCE_BUDGET_CHARS:
            lines.append("(further passages left out)")
            break
        lines.append(line)
        size += len(line)
    return "\n".join(lines) or "(the document cites nothing)"


def titles_of(kind: str, content: str) -> list[str]:
    """Slide titles, or the report title and its headings, in order."""

    titles = []
    if kind == "slides":
        for text in parts_of("slides", content):
            for line in text.splitlines():
                if re.match(r"^#{1,2} \S", line):
                    titles.append(line.lstrip("#").strip())
                    break
        return titles
    for line in content.splitlines():
        if re.match(r"^#{1,2} \S", line):
            titles.append(line.lstrip("#").strip())
    return titles


def core_message_of(kind: str, content: str) -> str:
    """What the artifact itself says its point is: a deck's subtitle (or summary slide), a
    report's executive summary (its first section)."""

    if kind == "slides":
        parts = parts_of("slides", content)
        if parts:
            lines = [
                ln.strip() for ln in parts[0].splitlines()
                if ln.strip() and not ln.strip().startswith(("<!--", "#", "Note:"))
            ]  # fmt: skip
            if lines:
                return lines[0]
        for text in parts:
            if "layout: summary" in text:
                return re.sub(r"<!--.*?-->", "", text, flags=re.S).split("Note:")[0].strip()
        return ""
    preamble, sections = split_document(content, "report")
    if sections:
        return re.sub(r"<!--.*?-->", "", sections[0]).strip()[:1500]
    return preamble[:1500]


# Calls -------------------------------------------------------------------------------------

ABSOLUTE_SYSTEM = """You are a demanding senior consultant reviewing a {kind} that an AI wrote \
from the source passages shown. You judge it as a client or a board would.

Calibration is strict. "ship" means a demanding consultant would present or send this slide or \
section unchanged. "minor edit" means one minute of fixing a word, a number format or a trim. \
"rework" means the title, point, structure or evidence needs rewriting. Most AI output is not \
"ship". Do not be generous: a vague, generic, hedged or filler slide is "rework". A slide that \
states a claim the passages do not support is "rework" whatever its style.

Score 1-10 (5 = adequate but unremarkable, 8 = a top professional would be proud, 10 = rare):
- insight: does it tell the audience something non-obvious and useful, with a clear answer?
- storyline: does the order build an argument toward a conclusion and a decision?
- titles: is every title a full sentence that carries the point (an action title)?
- evidence: are claims backed by specific numbers and sources, used well?
- clarity: is each slide or section easy to take in, at the right density, in plain words?
- correctness: do the numbers and claims match the source passages? (Cited numbers must match \
the passages; flag any that do not.)
{visual}
Give a verdict and a one-line reason for EVERY {unit}, numbered from 1 as labelled. Then a short \
summary of the biggest weaknesses."""

VISUAL_LINE = (
    "- visual: the attached images are the rendered slides in order; judge layout, density, "
    "hierarchy and whether any text is clipped or crowded.\n"
)


def _ask(gateway: ModelGateway, model: ModelInfo, schema: type, system: str, prompt: str,
         images: Sequence[Image] = ()) -> tuple[Any | None, str]:  # fmt: skip
    try:
        value, _ = gateway.complete_json(
            model, schema, system=system, prompt=prompt, images=images, effort="low"
        )
    except ModelError as error:
        return None, str(error)
    return value, ""


def load_images(paths: Sequence[Path]) -> list[Image]:
    return [Image.from_path(path, "image/png") for path in list(paths)[:MAX_IMAGES]]


def judge_absolute(
    gateway: ModelGateway,
    model: ModelInfo,
    *,
    kind: str,
    brief: str,
    audience: str,
    use: str,
    content: str,
    citations: Sequence[dict[str, Any]],
    pngs: Sequence[Path] = (),
) -> tuple[Absolute | None, str]:
    images = load_images(pngs) if (model.vision and pngs) else []
    unit = "slide" if kind == "slides" else "section"
    system = ABSOLUTE_SYSTEM.format(
        kind="slide deck" if kind == "slides" else "written report",
        unit=unit,
        visual=VISUAL_LINE if images else "(No images are attached: leave visual null.)\n",
    )
    prompt = (
        f"The request:\n{brief}\n(audience: {audience}"
        + (f"; use: {use}" if use else "")
        + f")\n\nThe {kind} to judge:\n\n{numbered(kind, content)}\n\n"
        f"Source passages it cites (what the sources actually say):\n{source_view(citations)}"
    )
    result, error = _ask(gateway, model, Absolute, system, prompt, images)
    return result, error


class SlideLook(BaseModel):
    index: int = Field(description="Slide number, counted from 1")
    verdict: Verdict
    reason: str = Field(max_length=300)


class VisualJudgment(BaseModel):
    score: int = Field(ge=1, le=10)
    slides: list[SlideLook]
    summary: str = Field(max_length=500)


VISUAL_SYSTEM = """You are a demanding presentation designer judging how slides LOOK. The \
attached images are the rendered slides of one deck, in order (image 1 = slide 1). Judge only \
what you can see, as a person in the back of the room would: readable type size and contrast; \
crowded or empty areas; balance and alignment; whether each chart or table shows its point; \
whether the layout suits the content; titles that wrap badly; anything amateur.

Score the deck 1-10 (5 = adequate, 8 = a top designer would sign off, 10 = rare). Be strict: \
most generated decks have crowded or flat slides. For EVERY slide give a verdict: "ship" = \
present as it is; "minor edit" = a quick trim or nudge; "rework" = the layout, density or chart \
needs redoing. Do not judge the argument or the facts."""


def judge_visual(
    gateway: ModelGateway, model: ModelInfo, *, title: str, pngs: Sequence[Path]
) -> tuple[VisualJudgment | None, str]:
    """A vision model's score of how a deck looks, from its rendered slide images."""

    if not model.vision:
        return None, f"{model.label} cannot see images"
    if not pngs:
        return None, "no rendered slides"
    images = load_images(pngs)
    listing = ", ".join(f"image {n} = slide {n}" for n in range(1, len(images) + 1))
    return _ask(
        gateway, model, VisualJudgment, VISUAL_SYSTEM,
        f"Deck: {title}\n{listing}.", images,
    )  # fmt: skip


TITLES_ONLY_SYSTEM = (
    "You read only the titles of a {kind}, in order. From them alone, write the core message "
    "the {kind} delivers: the answer or conclusion, in two or three sentences, with the key "
    "facts the titles carry. Do not invent anything the titles do not say."
)
MATCH_SYSTEM = (
    "A reader saw only the titles of a {kind} and retold its core message. Compare that "
    "retelling with the {kind}'s own core message. match is true only when the retelling "
    "captures the same central claim or conclusion (wording may differ, details may be missing). "
    "It is false when the central claim is missed, vague, generic or different."
)


def judge_titles_only(
    gateway: ModelGateway, model: ModelInfo, *, kind: str, content: str
) -> dict[str, Any]:
    """The titles-only test: a fresh call retells the message, a second compares it."""

    name = "deck" if kind == "slides" else "report"
    titles = titles_of(kind, content)
    own = core_message_of(kind, content)
    if not titles or not own:
        return {"pass": False, "error": "no titles or no core message to compare", "titles": titles}
    listing = "\n".join(f"{number}. {title}" for number, title in enumerate(titles, start=1))
    retold, error = _ask(gateway, model, Retold, TITLES_ONLY_SYSTEM.format(kind=name),
                         f"Titles of the {name}:\n{listing}")  # fmt: skip
    if retold is None:
        return {"pass": False, "error": error, "titles": titles}
    compared, error = _ask(
        gateway, model, Matched, MATCH_SYSTEM.format(kind=name),
        f"The {name}'s own core message:\n{own}\n\nThe retelling from the titles alone:\n"
        f"{retold.core_message}",
    )  # fmt: skip
    if compared is None:
        return {"pass": False, "error": error, "titles": titles, "retold": retold.core_message}
    return {"pass": compared.match, "reason": compared.reason, "titles": titles,
            "retold": retold.core_message, "own": own[:600]}  # fmt: skip


PAIR_SYSTEM = """You compare two {kind}s, A and B, made from the same sources for the same \
request. You do not know how either was made. Judge as a demanding client would, on the content \
and wording shown, not on length. For each criterion say which is better, "A", "B" or "tie" (use \
"tie" only when they are truly equal):
- insight: the non-obvious, useful answer the audience gets
- storyline: the order builds an argument to a conclusion
- titles: full-sentence action titles that carry the point
- evidence: specific, sourced, correctly used numbers and facts
- clarity: easy to take in at the right density
- overall: which would you rather present or send?"""


def judge_pair(
    gateway: ModelGateway, model: ModelInfo, *, kind: str, brief: str,
    first: dict[str, Any], second: dict[str, Any],
) -> tuple[Compared | None, str]:  # fmt: skip
    """One blind comparison: ``first`` is shown as A, ``second`` as B."""

    system = PAIR_SYSTEM.format(kind="slide deck" if kind == "slides" else "report")
    prompt = (
        f"The request:\n{brief}\n\n=== A ===\n{numbered(kind, first['content'])}\n\n"
        f"=== B ===\n{numbered(kind, second['content'])}\n\n"
        "A's sources, by A's citation numbers (for checking A's facts):\n"
        f"{source_view(first['citations'])}\n\n"
        "B's sources, by B's citation numbers (for checking B's facts):\n"
        f"{source_view(second['citations'])}"
    )
    return _ask(gateway, model, Compared, system, prompt)


# Scoring math ------------------------------------------------------------------------------


def ship_rate(verdicts: Sequence[str]) -> float | None:
    """(ship + minor edit) over all verdicts; None when there are none."""

    if not verdicts:
        return None
    return sum(1 for v in verdicts if v in ("ship", "minor edit")) / len(verdicts)


def section_verdicts(absolute: Absolute | None, count: int) -> list[SectionVerdict]:
    """One verdict per slide or section, in order. A missing one counts as "rework"
    (the judge did not vouch for it); extra or repeated indices are dropped."""

    if absolute is None:
        return []
    by_index: dict[int, SectionVerdict] = {}
    for item in absolute.sections:
        if 1 <= item.index <= count and item.index not in by_index:
            by_index[item.index] = item
    return [
        by_index.get(n)
        or SectionVerdict(index=n, verdict="rework", reason="not rated by the judge")
        for n in range(1, count + 1)
    ]


def combine_pair(first_order: str, swapped: str) -> str:
    """Two blind verdicts of the same pair into one winner. ``first_order`` has method 1 as A
    and method 2 as B; ``swapped`` has them the other way round. Returns "first", "second" or
    "tie": only a result that survives the swap counts."""

    def decode(verdict: str, first_is_a: bool) -> str:
        if verdict == "tie":
            return "tie"
        return "first" if (verdict == "A") == first_is_a else "second"

    one, two = decode(first_order, True), decode(swapped, False)
    return one if one == two else "tie"


def tally_pairs(results: Sequence[str]) -> dict[str, int]:
    return {key: sum(1 for r in results if r == key) for key in ("first", "second", "tie")}


def mean(values: Sequence[float | int | None]) -> float | None:
    real = [v for v in values if v is not None]
    return sum(real) / len(real) if real else None
