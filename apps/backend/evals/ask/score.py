"""Scoring for the Ask exam: cheap checks from the answer itself, and one judge call.

Nothing here talks to the app. The runner hands over what an answer looked like (an
:class:`Outcome`) and gets back the checks that failed and the judge's reading.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from gunther.agent import SENTENCE_END, UNSOURCED
from gunther.llm import ModelError, ModelGateway, ModelInfo

CASES_FILE = Path(__file__).parent / "questions.toml"
LIBRARY_DIR = Path(__file__).parent / "library"
CATEGORIES = {
    "rework",
    "lookup",
    "follow-up",
    "conflict",
    "trap",
    "not in library",
    "web",
    "section",
    "long",
    "skill",
    "auto-skill",
    "research",
}
SEARCH_RULES = {"must", "must_not", "any"}

JUDGE = """You grade one answer from a research assistant. You see the question, the answer split into numbered sentences, the full text of every source it cites, and lists of points to look for.

1. "support": for every sentence that cites sources, is it "supported" (the cited sources state it), "partly", or "not"?
2. "unmarked": copy every sentence that states a fact about the subject but has neither a source number nor the marker [?]. Statements about the sources themselves (what they do or do not contain), about the search, or about the answer, and headings, questions and suggested next steps, are not facts here.
3. "mentioned": for each "must mention" point, true if the answer conveys it.
4. "claimed": for each "must not claim" point, true if the answer states it.
Judge only against the texts shown. Reply with the JSON object only."""  # noqa: E501


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    question: str
    history: tuple[str, ...] = ()
    web: bool = False
    skill: str = ""
    budget: str = ""
    search: str = "any"
    cite_any: tuple[str, ...] = ()
    must_not_cite: tuple[str, ...] = ()
    cite_web: bool = False
    must_mention: tuple[str, ...] = ()
    must_not_claim: tuple[str, ...] = ()
    max_unverified: int = 1
    max_words: int = 0
    max_searches: int = 0
    must_not_contain: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    expect_skill: str = ""
    expect_state: str = ""


def load_cases(path: Path = CASES_FILE) -> list[Case]:
    """Every case in the file, checked: a mistyped field or rule is an error, not a skip."""

    raw = tomllib.loads(path.read_text(encoding="utf-8"))["case"]
    known = set(Case.__dataclass_fields__)
    cases = []
    for entry in raw:
        extra = set(entry) - known
        if extra:
            raise ValueError(f"Case {entry.get('id')}: unknown fields {sorted(extra)}")
        converted = {k: tuple(v) if isinstance(v, list) else v for k, v in entry.items()}
        case = Case(**converted)
        if case.category not in CATEGORIES:
            raise ValueError(f"Case {case.id}: unknown category {case.category!r}")
        if case.search not in SEARCH_RULES:
            raise ValueError(f"Case {case.id}: unknown search rule {case.search!r}")
        cases.append(case)
    ids = [case.id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("Case ids are not unique")
    return cases


def library_texts(directory: Path = LIBRARY_DIR) -> dict[str, str]:
    """The demo library: each file's text by its title (the first line, without the "# ")."""

    texts = {}
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        texts[text.splitlines()[0].removeprefix("# ").strip()] = text
    return texts


@dataclass
class Outcome:
    """What one case produced: the last answer and what it cost."""

    answer: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    calls: int = 0
    tokens: int = 0
    # Why there is no answer to score (the request failed).
    failed: str = ""


def word_count(text: str) -> int:
    """Words by spaces, and every Chinese character as a word of its own."""

    cjk = re.compile(r"[㐀-䶿一-鿿]")
    return len(cjk.findall(text)) + len(cjk.sub(" ", text).split())


def searched(context: dict[str, Any]) -> bool:
    """Whether the agent looked something up for the question itself (claim checks do not count)."""

    return any(
        step.get("tool") in ("search_library", "search_web")
        and not str(step.get("label", "")).startswith("Checked")
        for step in context.get("steps") or []
    )


def search_count(context: dict[str, Any]) -> int:
    return sum(
        1
        for step in context.get("steps") or []
        if step.get("tool") in ("search_library", "search_web")
        and not str(step.get("label", "")).startswith("Checked")
    )


def cited_titles(outcome: Outcome) -> list[str]:
    return [c["sourceTitle"] for c in outcome.citations if c.get("kind", "library") == "library"]


def unverified(answer: str) -> int:
    return answer.count(UNSOURCED)


def checks(case: Case, outcome: Outcome) -> dict[str, bool]:
    """Each deterministic check by name, True when it holds."""

    found = {"answered": not outcome.failed}
    if outcome.failed:
        return found
    context = outcome.context
    titles = set(cited_titles(outcome))
    did_search = searched(context)
    found["error"] = not context.get("modelError")
    if case.search != "any":
        found["searched"] = did_search == (case.search == "must")
    if case.cite_any:
        found["cited"] = bool(titles & set(case.cite_any))
    if case.must_not_cite:
        banned = titles if "*" in case.must_not_cite else titles & set(case.must_not_cite)
        found["no_trap"] = not banned
    if case.cite_web:
        found["cited_web"] = any(c.get("kind") == "web" for c in outcome.citations)
    found["unverified"] = unverified(outcome.answer) <= case.max_unverified
    if case.max_words:
        found["words"] = word_count(outcome.answer) <= case.max_words
    if case.max_searches:
        found["searches"] = search_count(context) <= case.max_searches
    lowered = outcome.answer.lower()
    for text in case.must_not_contain:
        found[f"without {text!r}"] = text.lower() not in lowered
    if case.expect_skill or case.category == "auto-skill":
        found["skill"] = str((context.get("skill") or {}).get("name", "")) == case.expect_skill
    if case.expect_state:
        found["state"] = str((context.get("research") or {}).get("state", "")) == case.expect_state
    return found


# What the judge says --------------------------------------------------------------


class SupportVerdict(BaseModel):
    n: int
    verdict: Literal["supported", "partly", "not"]


class Judgement(BaseModel):
    support: list[SupportVerdict] = Field(default_factory=list, max_length=80)
    unmarked: list[str] = Field(default_factory=list, max_length=40)
    mentioned: list[bool] = Field(default_factory=list, max_length=20)
    claimed: list[bool] = Field(default_factory=list, max_length=20)


def sentences(text: str) -> list[str]:
    """The answer's sentences; source numbers after the full stop belong to the one before."""

    found, start = [], 0
    for match in SENTENCE_END.finditer(text):
        end = match.end()
        tail = re.match(r"(?:\s*\[[^\]]*\])+", text[end:])
        if tail:
            end += tail.end()
        if end > start:
            found.append(text[start:end].strip())
            start = end
    found.append(text[start:].strip())
    return [s for s in found if s]


def judge_prompt(case: Case, outcome: Outcome, library: dict[str, str]) -> str:
    """Everything the judge sees. A library source is shown whole, a web source by its quote.

    An answer numbers its sources 1, 2, 3 in the order of its source list; ``ref`` is the
    conversation's id for a source and differs after the first turn, so it is not used. A
    demo file cited through several passages is shown once, under all its numbers.
    """

    numbers: dict[tuple[str, str], list[int]] = {}
    texts: dict[tuple[str, str], tuple[str, str]] = {}
    for number, citation in enumerate(outcome.citations, start=1):
        title = citation.get("sourceTitle", "")
        if citation.get("kind", "library") == "library" and title in library:
            key, text = ("library", title), library[title]
        else:
            text = citation.get("quote", "")
            key = (citation.get("url") or title, text)
        numbers.setdefault(key, []).append(number)
        texts[key] = (title, text)
    shown = [
        f"{''.join(f'[{n}]' for n in found)} {texts[key][0]}\n{texts[key][1]}"
        for key, found in numbers.items()
    ]
    numbered = "\n".join(f"{n}. {s}" for n, s in enumerate(sentences(outcome.answer), start=1))
    mention = "\n".join(f"{n}. {p}" for n, p in enumerate(case.must_mention, start=1)) or "(none)"
    claim = "\n".join(f"{n}. {p}" for n, p in enumerate(case.must_not_claim, start=1)) or "(none)"
    return (
        f"Question:\n{case.question}\n\nAnswer, in numbered sentences:\n{numbered}\n\n"
        f"Sources cited:\n{chr(10).join(shown) or '(none)'}\n\n"
        f"Must mention:\n{mention}\n\nMust not claim:\n{claim}"
    )


def judge(
    gateway: ModelGateway, model: ModelInfo, case: Case, outcome: Outcome, library: dict[str, str]
) -> tuple[Judgement | None, str]:
    """One judge call for an answer; (None, why) when the judge could not answer."""

    try:
        judgement, _ = gateway.complete_json(
            model,
            Judgement,
            system=JUDGE,
            prompt=judge_prompt(case, outcome, library),
            effort="low",
        )
    except ModelError as error:
        return None, str(error)
    return judgement, ""


def judged_failures(case: Case, judgement: Judgement | None) -> list[str]:
    """What the judge's reading fails: a "not" verdict, an unmarked fact, a missed or false point"""

    if judgement is None:
        return ["judge"]
    failures = []
    if any(v.verdict == "not" for v in judgement.support):
        failures.append("support")
    # A rework reply restates the user's own text or an earlier answer; it needs no sources.
    if judgement.unmarked and case.category != "rework":
        failures.append("unmarked")
    # A judge that skipped a point has not confirmed it.
    mentioned = [*judgement.mentioned, *[False] * len(case.must_mention)][: len(case.must_mention)]
    if not all(mentioned):
        failures.append("mentions")
    if any(judgement.claimed[: len(case.must_not_claim)]):
        failures.append("claims")
    return failures


def failures(case: Case, found: dict[str, bool], judgement: Judgement | None) -> list[str]:
    """The names of everything that failed for one case; empty means it passed."""

    bad = [name for name, ok in found.items() if not ok]
    if found.get("answered", True):
        bad += judged_failures(case, judgement)
    return bad
