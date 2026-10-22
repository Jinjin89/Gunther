"""The studio inside an Artifacts conversation.

``StudioWorkAgents`` stands where ``SkillAgents`` stands for ``WorkSteps`` (artifact_steps), for a
work the studio made: checking, fitting and rewriting one part follow the studio's format and
checker, and ``write_part`` / ``review_parts`` replace the skill's per-part writer and polish.
``load_built`` puts a whole studio build into a work, as ``import_work`` does for a version.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from gunther.agent import Evidence
from gunther.artifact_workspace import (
    ItemRec,
    PartRec,
    Work,
    _stamp,
    _take_check,
    _translate,
)
from gunther.llm import ModelError
from gunther.outputs import BuiltOutput, Draft, Issue, Pool, Spec, section_hash, split_document
from gunther.studio import format as fmt
from gunther.studio import prompts as P
from gunther.studio.pipeline import StudioAgents, _unfence
from gunther.studio.reference import DECK_FORMAT, READ_NOTE, REPORT_FORMAT

logger = logging.getLogger(__name__)


def is_studio(work: Work) -> bool:
    return bool(work.approach and work.approach.get("method") == "studio")


@dataclass(frozen=True)
class StudioSkill:
    """Just enough of a skill for WorkSteps: the kind, the uses, no steps of its own."""

    kind: str
    name: str = "studio"
    version: int = 1
    uses: tuple[str, ...] = ("talk", "read")
    layouts: tuple[str, ...] = fmt.LAYOUTS

    def step(self, _id: str) -> None:
        return None


PART_WRITE = """\
You are the best writer at a top consulting firm, working on one {unit} of an existing \
{what}. The whole document is shown so the {unit} fits it: same voice, no repetition, the \
right link to its neighbours.

Brief: {brief}
Audience: {audience}.{use_line}

{task}

{voice}

{truth}

{format}

Reply with only the {unit}, complete, in the format above."""


class ReviewAsk(BaseModel):
    part: int
    instruction: str


class ReviewPlan(BaseModel):
    understood: str = ""
    changes: list[ReviewAsk] = Field(default_factory=list)


REVIEW = """\
You are a demanding senior partner reading a finished {what} before it goes to the client. \
Brief: {brief}. Audience: {audience}.

Name at most {most} parts that most need improving for this audience — weak or vague titles, \
a part that does not prove its title, repetition, a missing link in the argument, too much \
text, a layout that does not fit the evidence — and for each say exactly what to change. Do \
not ask for facts the document does not have. If it is ready, ask for nothing.

Reply with a JSON object: {{"understood": "the core message in one sentence", "changes": \
[{{"part": number, "instruction": "..."}}]}}"""


class StudioWorkAgents(StudioAgents):
    def __init__(self, gateway, kind: str, limits=None, *, tools=(), reader=None) -> None:
        super().__init__(gateway, limits)
        self.studio = True
        self.skill = StudioSkill(kind)
        self.tools = {tool.name: tool for tool in tools}
        self.reader = reader
        self.cards: list[Any] = []
        self.approach = None
        self.outline_review = None

    def final_notes(self, spec, planned, drafts, pool) -> list[dict[str, Any]]:
        """Form problems left in the finished whole, as notes for the version."""

        if spec.kind == "slides":
            deck = "\n\n---\n\n".join(d.text for d in drafts)
            found = fmt.check_deck(deck, read=self.use == "read", notes_required=False)
            return [
                {"step": "format", "label": f"Slide {n}", "reason": problems[0]}
                for n, problems in found
            ]
        return []

    def record(self) -> dict[str, Any]:
        return {"name": "studio", "version": 1, "skipped": self.skipped, "trail": self.trail}

    def review_rendered(self, built, spec, events=None):
        return built

    @staticmethod
    def _what(spec: Spec) -> str:
        return "a slide deck" if spec.kind == "slides" else "a report"

    # What WorkSteps calls for edits and checks --------------------------------------------

    def check_built(self, model, text, tool, pool: Pool, emit, section) -> Draft:
        by_ref = pool.by_ref()
        issues, coverage = self._claims(model, text, by_ref)
        issues += self._chart_issues(text, by_ref)
        if any(issue.note.startswith("Chart value") for issue in issues):
            text = fmt.with_marker(text, "table")
        return Draft(text, issues, coverage)

    def check_unbuilt(self, model, text, by_ref, coverage=None) -> list[Issue]:
        issues, found = self._claims(model, text, by_ref)
        issues += self._chart_issues(text, by_ref)
        if coverage is not None:
            coverage.update(found)
        return issues

    def fit(self, model, effort, spec, planned, index, pool, refs, notes, before, emit, text):
        """A slide that breaks the format is written again once; else it keeps its words."""

        if spec.kind != "slides":
            return text
        use = self.use or "talk"
        slide = fmt.parse_slide(text)
        problems = fmt.check_slide(slide, first=index == 0, read=use == "read")
        if not problems:
            return text
        system = P.FIX_SLIDES.format(
            format=DECK_FORMAT + ("\n\n" + READ_NOTE if use == "read" else ""),
            problems=f"Slide {index + 1} problems:\n"
            + "\n".join(f"- {p}" for p in problems)
            + f"\n\nSlide {index + 1} as written:\n{text}",
        )
        try:
            fixed = fmt.split_slides(
                _unfence(self._text(model, system, "Rewrite it now.", effort, 4000))
            )
        except ModelError as error:
            logger.info("Fitting a slide failed: %s", error)
            return text
        return fmt.strip_labels(fixed[0]) if fixed else text

    # Writing one part, reviewing the whole ------------------------------------------------

    def write_part(
        self,
        model,
        effort,
        spec: Spec,
        parts: list[str],
        headings: list[str],
        index: int,
        pool: Pool,
        *,
        goal: str = "",
        layout: str = "",
        instruction: str | None = None,
    ) -> str:
        deck = spec.kind == "slides"
        unit = "slide" if deck else "section"
        use = self.use or "talk"
        current = parts[index] if index < len(parts) else ""
        whole = "\n\n---\n\n".join(
            f"[{unit} {n}]\n{text}"
            if text
            else f"[{unit} {n}] (not written yet: {headings[n - 1]})"
            for n, text in enumerate(parts, 1)
        )
        if instruction and current:
            task = (
                f"Rewrite {unit} {index + 1} as instructed, changing only what the instruction "
                f"needs: {instruction}\n\nThe {unit} now:\n{current}"
            )
        else:
            task = (
                f"Write {unit} {index + 1}. Its title: {headings[index]}."
                + (f" Its job: {goal}." if goal else "")
                + (f" Layout: {layout}." if deck and layout else "")
            )
        cited = [int(n) for n in re.findall(r"\[(\d+)\]", whole)]
        material = self._part_material(pool, cited, f"{headings[index]} {goal} {instruction or ''}")
        system = PART_WRITE.format(
            unit=unit,
            what=self._what(spec),
            brief=spec.brief or "(none)",
            audience=spec.audience,
            use_line=f" Use: {use}." if deck else "",
            task=task,
            voice=P.VOICE.format(language=self._language(spec, pool)),
            truth=P.TRUTH,
            format=(DECK_FORMAT + ("\n\n" + READ_NOTE if use == "read" else ""))
            if deck
            else REPORT_FORMAT,
        )
        prompt = f"The whole {self._what(spec)[2:]}:\n\n{whole}\n\nThe material:\n\n{material}"
        text = _unfence(self._text(model, system, prompt, effort, 6000))
        if deck:
            pieces = fmt.split_slides(text)
            text = fmt.strip_labels(pieces[0]) if pieces else text
            if index == 0 and current:
                attrs = fmt.parse_slide(current).attrs
                text = fmt.with_marker(text, "title", **{k: v for k, v in attrs.items()})
        return text

    def _part_material(self, pool: Pool, cited: list[int], about: str) -> str:
        """The passages a part may need: those the document cites, then the ones sharing the
        most words with what the part is about, within the writer's budget."""

        by_ref = pool.by_ref()
        words = {w for w in re.findall(r"\w{4,}", about.casefold())}

        def score(item: Evidence) -> int:
            return len(words & set(re.findall(r"\w{4,}", item.text.casefold())))

        order = list(dict.fromkeys(r for r in cited if r in by_ref))
        order += [
            item.ref
            for item in sorted(pool.items, key=score, reverse=True)
            if item.ref not in order
        ]
        chosen, size = [], 0
        for ref in order:
            text = self._passage(by_ref[ref], 3000)
            if size + len(text) > 60_000:
                break
            chosen.append(ref)
            size += len(text)
        return "\n\n".join(self._passage(by_ref[ref], 3000) for ref in sorted(chosen))

    def review_parts(
        self, model, effort, spec: Spec, parts: list[str], most: int = 4
    ) -> list[ReviewAsk]:
        unit = "slide" if spec.kind == "slides" else "section"
        whole = "\n\n---\n\n".join(f"[{unit} {n}]\n{text}" for n, text in enumerate(parts, 1))
        system = REVIEW.format(
            what=self._what(spec), brief=spec.brief or "(none)", audience=spec.audience, most=most
        )
        try:
            plan, _ = self.gateway.complete_json(
                model, ReviewPlan, system=system, prompt=whole, effort="medium"
            )
        except ModelError as error:
            logger.info("The review failed: %s", error)
            return []
        return [c for c in plan.changes if 1 <= c.part <= len(parts)][:most]


def load_built(work: Work, built: BuiltOutput, made: Pool, pool: Pool, brief) -> None:
    """A studio build into ``work``: approach, outline and parts, in the conversation pool's
    numbers. Only the passages the build cites join the conversation pool (``made`` is the
    larger pool the studio read; ``built.pool_refs`` are its numbers)."""

    by_ref = made.by_ref()
    version_map = [pool.adopt(by_ref[ref]).ref for ref in built.pool_refs]
    sections = split_document(built.content, work.spec.kind)[1]
    checks = {item["hash"]: item for item in built.checks}
    work.title = built.title
    work.approach = dict(built.approach or {})
    work.outline = []
    work.parts = {}
    work.version_map = list(version_map)
    for section in sections:
        text, _ = _translate(section, version_map)
        if work.spec.kind == "slides":
            slide = fmt.parse_slide(section)
            heading, layout = slide.title, slide.layout
        else:
            heading = re.sub(r"^##\s*", "", section.splitlines()[0]).strip()
            layout = ""
        item = ItemRec(
            id=f"p{work.next_item}",
            heading=heading[:200],
            layout=layout,
            refs=sorted({int(n) for n in re.findall(r"\[(\d+)\]", text)}),
        )
        work.next_item += 1
        part = PartRec(text=text, inputs="")
        _take_check(part, section, checks.get(section_hash(section)), version_map)
        work.outline.append(item)
        work.parts[item.id] = part
    work.approach_top = pool.top
    work.skipped = [*work.skipped, *[dict(s) for s in (built.skill or {}).get("skipped", [])]]
    _stamp(work, brief)
