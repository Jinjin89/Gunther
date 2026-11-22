"""The studio: reports and decks made the way a strong analyst and writer would make them.

read → memo (what is worth saying) → storyline (titles first, with a critic) → draft (the whole
thing at once) → format fix → verify (claims against passages, surgical fixes) → assemble.

About eight model calls for a deck instead of dozens, each with the whole picture in view.
See docs/ARTIFACT_QUALITY_GOAL.md §4 and docs/STUDIO_FORMAT.md.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from dataclasses import asdict, dataclass, field, replace
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field

from gunther import usage
from gunther.agent import (
    CITATION_GROUP,
    MARKDOWN_TABLE,
    Evidence,
    check_citations,
    collapse_citations,
)
from gunther.checks import Verdicts, finding, read_verdicts, verdicts_of
from gunther.llm import Image, ModelError, ModelInfo
from gunther.llm import unfence as _unfence
from gunther.model_profiles import Effort
from gunther.output_checks import check_part
from gunther.output_document import assemble_output, project_outline
from gunther.outputs import (
    STYLES,
    BuiltOutput,
    Draft,
    Events,
    Issue,
    Notes,
    OutputAgents,
    Planned,
    Pool,
    Spec,
    _marked,
    check_heading_refs,
    figure_lines,
    heading_of,
    split_document,
)
from gunther.skill_checks import CHARTS, figures_of
from gunther.slide_render import digest
from gunther.studio import design as D
from gunther.studio import format as fmt
from gunther.studio import prompts as P
from gunther.studio import science as S
from gunther.studio.look import look, vision_model
from gunther.studio.reference import DECK_FORMAT, READ_NOTE, REPORT_FORMAT, STUDY_DECK_FORMAT
from gunther.studio.stages import Checkpoint, checkpoint_stage, stage_event

logger = logging.getLogger(__name__)

# How much material is read whole. Beyond it the material is read in parts first (notes).
DIRECT_TOKENS = 60_000
PART_TOKENS = 24_000
CRITIC_ROUNDS = 1  # one review and one revision: a second round rarely changed the story
WORKERS = 12
FIGURE_WORKERS = 4
# Every slide of a deck is also designed as HTML (studio/design.py); False: the template draws.
DESIGN_SLIDES = True
STUDIO_CHECK = (
    "Check this slide or report section. Its title states the conclusion the cited evidence "
    "supports: judge the title against the sources cited in it. Only factual statements can be "
    "'not' or 'partly': numbers, names, dates, events, and what a source says or found — "
    "use 'not' when the cited source does not state it or states otherwise. Recommendations, "
    "decisions, priorities, interpretations and transitions are the author's judgement: mark "
    "them 'inference' or 'nonfactual', never 'not', even when the sources do not say them. "
    "For a chart row, support requires the source to state each value for that row label "
    "and series, including its unit. Use the citations in each value cell, or the row "
    "label's citations if the value cell has none. A value for a different label or series "
    "does not support the row."
)
# What the claim fix works on: a cited fact its passage does not state, and a number with no
# source (the fixer cites the passage that states it, or drops the number).
FIXABLE = ("unsupported", "contradicted", "unsourced")
READ_HINT = r"\b(pre-?read|handout|to read|send|email)\b|阅读|发给|会后"


def tokens(text: str) -> int:
    """A rough count: a CJK character is about one token, other text about four characters."""

    cjk = sum(1 for char in text if "　" <= char <= "鿿" or "가" <= char <= "힯")
    return cjk + (len(text) - cjk) // 4


# What the steps return --------------------------------------------------------------------


class Ref(BaseModel):
    text: str = ""
    refs: list[int] = Field(default_factory=list)


class Quote(BaseModel):
    text: str = ""
    who: str = ""
    refs: list[int] = Field(default_factory=list)


class PartNotes(BaseModel):
    facts: list[Ref] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)


class Insight(BaseModel):
    claim: str
    so_what: str = ""
    refs: list[int] = Field(default_factory=list)


class Fact(BaseModel):
    fact: str
    value: str = ""
    compared_to: str = ""
    refs: list[int] = Field(default_factory=list)


class Tension(BaseModel):
    issue: str
    refs: list[int] = Field(default_factory=list)


class Memo(BaseModel):
    language: str = ""
    answer: str
    insights: list[Insight] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    tensions: list[Tension] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    title: str = ""


class StorySlide(BaseModel):
    title: str
    id: str | None = None
    layout: str = "points"
    job: str = ""
    refs: list[int] = Field(default_factory=list)
    purpose: str = ""
    role: str = ""
    figures: list[S.ClaimFigure] = Field(default_factory=list)
    takeaway: str = ""


class StoryLength(BaseModel):
    requested_count: int | None = Field(default=None, gt=0, strict=True)
    requested_minutes: int | None = Field(default=None, gt=0, strict=True)


class DeckStory(StoryLength):
    title: str
    subtitle: str = ""
    slides: list[StorySlide] = Field(min_length=1)


class StorySection(BaseModel):
    heading: str
    id: str | None = None
    job: str = ""
    exhibits: list[str] = Field(default_factory=list)
    refs: list[int] = Field(default_factory=list)


class ReportStory(StoryLength):
    title: str
    summary_id: str | None = None
    summary_heading: str = ""
    summary_job: str = ""
    summary_points: list[str] = Field(default_factory=list)
    sections: list[StorySection]


class Problem(BaseModel):
    where: str = ""
    problem: str = ""
    fix: str = ""


class Review(BaseModel):
    understood: str = ""
    verdict: str = "revise"
    problems: list[Problem] = Field(default_factory=list)


@dataclass
class Run:
    """What one build knows as it goes."""

    spec: Spec
    use: str  # "talk" | "read" (decks); "" for reports
    language: str
    memo: Memo | None = None
    story: DeckStory | ReportStory | None = None
    reviews: list[Review | S.ScientificReview] | None = None
    purpose: S.Purpose | None = None
    study: S.StudyMap | None = None
    coverage_plan: S.CoveragePlan | None = None
    figure_readings: list[S.FigureReading] | None = None
    limitations: list[str] | None = None
    # The scientific review's outcome: "ready", "open_problems" or "unavailable".
    review_status: str | None = None
    # The notes made when the material was too long to give whole and was read part by part.
    read_digest: str = ""
    plan: tuple[Planned, ...] = ()
    # Latest request errors by one-based slide position in the accepted deck.
    format_repair_errors: dict[int, str] = field(default_factory=dict)


def _no_events(_event: dict[str, Any]) -> None:
    return None


def _outline_text(plan: Sequence[Planned]) -> str:
    return "\n".join(f"{n}. {p.heading} — goal: {p.goal}" for n, p in enumerate(plan, 1))


def _matches_plan(run: Run, parts: Sequence[str]) -> bool:
    return not run.plan or [heading_of(part) for part in parts] == [p.heading for p in run.plan]


def _validate_count(story: DeckStory | ReportStory, actual: int, stage: str) -> None:
    count = story.requested_count
    if count is not None and actual != count:
        unit = "slides" if isinstance(story, DeckStory) else "sections"
        raise ModelError(f"The {stage} has {actual} {unit}. The brief asks for {count}.")


def _validate_storyline(run: Run, story: DeckStory | ReportStory) -> None:
    parts = len(story.slides) if isinstance(story, DeckStory) else 1 + len(story.sections)
    _validate_count(story, len(run.plan) if run.plan else parts, "outline")


def _keeps_heading(run: Run, original: str, repaired: Sequence[str]) -> bool:
    return not run.plan or (len(repaired) == 1 and heading_of(repaired[0]) == heading_of(original))


def _in_context(function):
    """Run ``function`` in a copy of this thread's context (traces, budgets) on a worker."""

    context = contextvars.copy_context()
    return lambda *args: context.copy().run(function, *args)


def material_request(material: str, task: str) -> tuple[str, str]:
    """One opening for every step reading the same build's material."""

    return P.STUDIO_BASE, "The material:\n\n" + material + P.TASK_BOUNDARY + task


def warm_map(function, items, workers: int, progress=None):
    """Finish the first item before submitting the rest, preserving context and order.
    ``progress(done, total)`` hears each finished item, one at a time."""

    items = list(items)
    if not items:
        return []
    lock, finished = Lock(), [0]

    def counted(item):
        out = function(item)
        if progress is not None:
            with lock:
                finished[0] += 1
                progress(finished[0], len(items))
        return out

    first = counted(items[0])
    if len(items) == 1:
        return [first]
    with ThreadPoolExecutor(workers) as pool:
        return [first, *pool.map(_in_context(counted), items[1:])]


class _MaterialCheckerGateway:
    """Adapt only the studio's sentence checks, keeping Ask's checking logic intact."""

    def __init__(self, agents, material: str) -> None:
        self.agents, self.material = agents, material

    def complete_json(self, model, schema, *, system, prompt, **kwargs):
        system, prompt = material_request(self.material, system + "\n\n" + prompt)
        self.agents._count_model_call()
        return self.agents.gateway.complete_json(
            model, schema, system=system, prompt=prompt, schema_at="end", **kwargs
        )


class StudioAgents(OutputAgents):
    """Makes a report or a deck with the Studio method."""

    def __init__(
        self,
        gateway,
        limits=None,
        *,
        use: str | None = None,
        renderer=None,
        design: bool | None = None,
        asset_reader=None,
        reusable_figures: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(gateway, limits)
        self.design = DESIGN_SLIDES if design is None else design
        # Draws slides with the real renderer for the "look" step; None: that step is skipped.
        self.renderer = renderer
        self.asset_reader = asset_reader or getattr(renderer, "asset_reader", None)
        self.scientific_context: dict[str, Any] | None = None
        self.use = use if use in ("talk", "read") else None
        self.trail: list[dict[str, Any]] = []
        self._started: dict[str, float] = {}
        self._active_stages: list[str] = []
        self._stage_calls: dict[str, int] = {}
        self._counter_lock = Lock()
        self.reusable_figures = list(reusable_figures or [])

    # Plumbing -----------------------------------------------------------------------------

    def _stage(self, emit: Events, step: str, label: str, state: str, detail: str = "") -> None:
        event = stage_event(step, label, state, detail or None)
        if state == "running":
            self._started[step] = time.monotonic()
            self._active_stages.append(step)
            self._stage_calls.setdefault(step, 0)
        if state in ("done", "failed"):
            took = round(time.monotonic() - self._started.get(step, time.monotonic()), 3)
            event["seconds"] = took
            calls = self._stage_calls.get(step, 0)
            event["modelCalls"] = calls
            self.trail.append(
                {
                    "step": step,
                    "state": state,
                    "detail": detail,
                    "seconds": took,
                    "modelCalls": calls,
                }
            )
            if self._active_stages and self._active_stages[-1] == step:
                self._active_stages.pop()
            logger.info("Studio %s %s in %ss: %s", step, state, took, detail[:120])
        emit(event)

    def _progress(self, emit: Events, step: str, label: str, detail: str) -> None:
        """What a running stage is doing now, shown beside its label; its clock keeps going."""

        emit(stage_event(step, label, "running", detail))

    def _count_model_call(self, stage: str | None = None) -> None:
        stage = stage or (self._active_stages[-1] if self._active_stages else None)
        if stage is None:
            return
        with self._counter_lock:
            self._stage_calls[stage] = self._stage_calls.get(stage, 0) + 1

    def _json(self, model, schema, system, prompt, effort):
        if self._active_stages:
            self._count_model_call(self._active_stages[-1])
        # A reply in the wrong shape (twice, inside the gateway) is asked for once more from
        # scratch before the step fails: one bad reply should not cost the whole build.
        try:
            result, _ = self.gateway.complete_json(
                model, schema, system=system, prompt=prompt, effort=effort, schema_at="end"
            )
        except ModelError as error:
            if "could not read" not in str(error):
                raise
            logger.info("Asking again from scratch after an unreadable reply")
            self._count_model_call()
            result, _ = self.gateway.complete_json(
                model, schema, system=system, prompt=prompt, effort=effort, schema_at="end"
            )
        return result

    def _text(self, model, system, prompt, effort, max_tokens=16_000, *, on_text=None) -> str:
        if self._active_stages:
            self._count_model_call(self._active_stages[-1])
        # A thinking model spends its budget thinking first: never ask for less than this.
        room = max(max_tokens, 16_000)
        try:
            completion = self.gateway.complete(
                model, system=system, messages=prompt, effort=effort, max_tokens=room,
                on_text=on_text,
            )
        except ModelError as error:
            if "ran out of room" not in str(error) or effort in (None, "off", "low"):
                raise
            logger.info("Out of room while thinking; asking again with less thinking")
            self._count_model_call()
            completion = self.gateway.complete(
                model, system=system, messages=prompt, effort="low", max_tokens=room,
                on_text=on_text,
            )
        return completion.text

    @staticmethod
    def _passage(item: Evidence, limit: int | None = None) -> str:
        text = item.context or item.text
        if limit:
            text = text[:limit]
        where = f" · {item.locator}" if item.locator else ""
        citation = item.payload[0] if isinstance(item.payload, tuple) and item.payload else None
        metadata = getattr(citation, "source_metadata", None) or {}
        metadata_line = (
            "\nSource metadata: " + json.dumps(metadata, ensure_ascii=False)
            if metadata
            else ""
        )
        return (
            f"[{item.ref}] ({item.title}{where})\n{text.strip()}"
            + ("\n" + "\n".join(figure_lines(item)) if figures_of(item) else "")
            + (
                "\nFigure provenance: " + json.dumps(figures_of(item), ensure_ascii=False)
                if figures_of(item)
                else ""
            )
            + S.warning_line(item)
            + metadata_line
        )

    def _material(self, items: list[Evidence]) -> str:
        return "\n\n".join(self._passage(item) for item in items)

    def _language(self, spec: Spec, pool: Pool) -> str:
        sample = spec.brief or " ".join(item.text[:200] for item in pool.items[:10])
        cjk = sum(1 for char in sample if "一" <= char <= "鿿")
        return "Chinese (简体中文)" if cjk > len(sample) * 0.15 else "English"

    # 1. Reading ---------------------------------------------------------------------------

    def read(
        self,
        model,
        run: Run,
        pool: Pool,
        notes: Notes,
        emit: Events,
        kept: dict[str, Any] | None = None,
    ) -> str:
        """The material as the memo step reads it: whole when it fits, else notes per part.
        ``kept``: the notes an earlier run of the same request made, reused when the person's
        own notes are the same."""

        items = sorted(pool.items, key=lambda item: item.ref)
        whole = self._material(items)
        extra = self._notes_text(notes)
        if tokens(whole) <= DIRECT_TOKENS:
            return extra + whole
        if kept and kept.get("notes") == extra and kept.get("digest"):
            self._stage(emit, "read", "Reading the material", "done", "kept from before")
            run.read_digest = kept["digest"]
            return extra + run.read_digest + "\n\n" + whole
        self._stage(emit, "read", "Reading the material", "running")
        parts: list[list[Evidence]] = [[]]
        size = 0
        for item in items:
            cost = tokens(self._passage(item))
            if parts[-1] and size + cost > PART_TOKENS:
                parts.append([])
                size = 0
            parts[-1].append(item)
            size += cost
        what = "a slide deck" if run.spec.kind == "slides" else "a report"
        system = P.NOTES.format(what=what, brief=run.spec.brief or "(none)", kind=what)

        def note(part: list[Evidence]) -> PartNotes | None:
            try:
                return self._json(
                    model, PartNotes, *material_request(self._material(part), system), "low"
                )
            except ModelError as error:
                logger.info("Reading a part failed: %s", error)
                return None

        with ThreadPoolExecutor(WORKERS) as workers:
            found = list(workers.map(_in_context(note), parts))
        lines = []
        for part, result in zip(parts, found, strict=True):
            if result is None:
                # A part that could not be read is given whole, cut to its share.
                lines.append(self._material(part)[:PART_TOKENS])
                continue
            lines += [
                f"- {fact.text} {''.join(f'[{r}]' for r in fact.refs)}" for fact in result.facts
            ]
            lines += [
                f'- Quote: "{quote.text}" — {quote.who} {"".join(f"[{r}]" for r in quote.refs)}'
                for quote in result.quotes
            ]
        self._stage(emit, "read", "Reading the material", "done", f"{len(parts)} parts")
        sources = sorted({item.title for item in items})
        run.read_digest = (
            "Sources: " + "; ".join(sources) + "\n\nNotes from the material:\n" + "\n".join(lines)
        )
        # Keep raw passages too: writers and checks previously fetched them after
        # reading notes. No step may lose evidence to make its opening match.
        return extra + run.read_digest + "\n\n" + whole

    @staticmethod
    def _notes_text(notes: Notes) -> str:
        lines = []
        if notes.knowledge:
            lines.append("The person's saved notes (context, never cited):")
            lines += [f"- {title}: {text[:600]}" for title, text in notes.knowledge]
        if notes.discussions:
            lines.append("The person's earlier discussions (context, never cited):")
            lines += [f"- {title}: {text[:400]}" for title, text in notes.discussions]
        if notes.extraction_gaps:
            lines.append("Selected-source access/extraction gaps (do not guess around them):")
            lines += [f"- {text[:600]}" for text in notes.extraction_gaps]
        return ("\n".join(lines) + "\n\n") if lines else ""

    # Scientific handoffs ------------------------------------------------------------------

    @staticmethod
    def _study_text(run: Run) -> str:
        readings = "\n".join(r.model_dump_json() for r in run.figure_readings or [])
        references = []
        for reading in run.figure_readings or []:
            references.append(f"![{reading.shows}](gunther-asset:{reading.asset_id})")
            for panel in reading.panels:
                if panel.box:
                    references.append(
                        f"![{reading.asset_id} panel {panel.label}: {panel.shows}] "
                        f"({S.crop_reference(reading.asset_id, panel.box)})"
                    )
        return (
            (run.study.model_dump_json() if run.study else "")
            + "\nActual FigureReadings (absent means not visually read):\n"
            + readings
            + (
                "\nExact figure references the writer may copy:\n" + "\n".join(references)
                if references
                else ""
            )
            + (
                "\nCoverage plan:\n" + run.coverage_plan.model_dump_json()
                if run.coverage_plan
                else ""
            )
            + "\nInternal limitations: "
            + "; ".join(run.limitations or [])
        )

    def _science_limit(self, run: Run, reason: str) -> None:
        if run.limitations is None:
            run.limitations = []
        run.limitations.append(reason)
        self.skipped.append(
            {"step": "science", "label": "Scientific evidence limitation", "reason": reason}
        )

    def study_map(
        self, model, effort, run: Run, pool: Pool, material: str, emit: Events
    ) -> S.StudyMap:
        """The study's argument, mapped before any slide is planned. Links to passages or
        figures the material does not hold are sent back once; still wrong, the step fails."""

        self._stage(emit, "study", "Understanding the study", "running")
        system, prompt = material_request(
            material,
            P.STUDY_MAP.format(brief=run.spec.brief or "(none)", audience=run.spec.audience),
        )
        study = self._json(model, S.StudyMap, system, prompt, effort)
        if problems := S.map_problems(study, pool.items):
            again = (
                prompt
                + "\n\nYour study map:\n"
                + study.model_dump_json()
                + "\n\nFix these links and return the whole map again:\n"
                + "\n".join(f"- {p}" for p in problems)
            )
            study = self._json(model, S.StudyMap, system, again, effort)
            if problems := S.map_problems(study, pool.items):
                self._stage(emit, "study", "Understanding the study", "failed", problems[0])
                raise ModelError("The study map cites material it does not have: " + problems[0])
        self._stage(emit, "study", "Understanding the study", "done", f"{len(study.claims)} claims")
        return study

    def coverage_plan(
        self, model, effort, run: Run, pool: Pool, material: str, emit: Events
    ) -> S.CoveragePlan:
        """A source-derived accounting step, independent of the eventual writer storyline."""

        self._stage(emit, "coverage", "Planning source coverage", "running")
        system, prompt = material_request(
            material,
            P.COVERAGE_PLAN.format(
                brief=run.spec.brief or "(none)",
                audience=run.spec.audience,
                figures=", ".join(S.figure_inventory(pool.items)) or "(none)",
                memo=self._study_text(run),
            ),
        )
        plan = self._json(model, S.CoveragePlan, system, prompt, effort)
        if problems := S.coverage_problems(plan, pool.items):
            plan = self._json(
                model,
                S.CoveragePlan,
                system,
                prompt
                + "\n\nYour plan:\n"
                + plan.model_dump_json()
                + "\n\nFix every accounting problem and return the whole plan:\n"
                + "\n".join(f"- {problem}" for problem in problems),
                effort,
            )
            if problems := S.coverage_problems(plan, pool.items):
                self._stage(emit, "coverage", "Planning source coverage", "failed", problems[0])
                raise ModelError(
                    "The coverage plan does not account for the source figures: " + problems[0]
                )
        self._stage(
            emit,
            "coverage",
            "Planning source coverage",
            "done",
            f"{len(plan.figures)} source figures accounted for",
        )
        return plan

    def read_figures(
        self, model, effort, run: Run, pool: Pool, emit: Events = _no_events
    ) -> list[S.FigureReading]:
        assert run.study is not None
        linked = S.figure_inventory(pool.items)
        if not linked:
            return []
        selected = vision_model(self.gateway, model, effort)
        if not selected:
            self._science_limit(
                run, "No model that can see images is set up; figures were not read."
            )
            return []
        if self.asset_reader is None:
            self._science_limit(run, "Source image pixels are unavailable; figures were not read.")
            return []
        eye, eye_effort = selected
        self._stage(emit, "figures", "Looking at the figures", "running")

        def read(asset_id: str) -> S.FigureReading | None:
            passages = [
                e for e in pool.items if any(f["assetId"] == asset_id for f in figures_of(e))
            ]
            source = next(f for f in figures_of(passages[0]) if f["assetId"] == asset_id)
            prompt = (
                f"asset_id: {asset_id}\nLinked claims: "
                + "\n".join(
                    c.model_dump_json()
                    for c in run.study.claims
                    if any(f.asset_id == asset_id for f in c.figures)
                )
                + "\nCaption, provenance and linked passages:\n"
                + self._material(passages)
            )
            fingerprint = digest([prompt, S.pool_print(passages), eye.ref, eye_effort])
            reusable = next(
                (r for r in self.reusable_figures if r.get("source_fingerprint") == fingerprint),
                None,
            )
            if reusable is not None:
                try:
                    reading = S.FigureReading.model_validate(reusable)
                    if not reading.legible or reading.uncertain:
                        self._science_limit(
                            run,
                            f"Figure {asset_id}: "
                            + ("; ".join(reading.uncertain) or "not reliably legible"),
                        )
                    return reading
                except ValueError:
                    pass  # an old saved shape is reread, never guessed
            # The study map step already bounded these IDs to the chosen passages and revision.
            try:
                path, media, _name = self.asset_reader(asset_id)
                self._count_model_call("figures")
                reading, _ = self.gateway.complete_json(
                    eye,
                    S.FigureReading,
                    system=P.FIGURE_READ,
                    prompt=prompt,
                    images=[Image.from_path(path, media)],
                    effort=eye_effort or "low",
                    schema_at="end",
                )
                if reading.asset_id != asset_id:
                    raise ValueError("The figure reading returned a different asset identity.")
                reading.source_revision_id = source.get("revisionId") or ""
                reading.source_fingerprint = fingerprint
                reading.vision_model = eye.ref
                if not reading.legible or reading.uncertain:
                    self._science_limit(
                        run,
                        f"Figure {asset_id}: "
                        + ("; ".join(reading.uncertain) or "not reliably legible"),
                    )
                return reading
            except (ModelError, OSError, LookupError, ValueError) as error:
                # A figure file that is missing or fails its check is a figure not read.
                self._science_limit(run, f"Figure {asset_id} was not read: {error}")
            return None

        readings = [reading for reading in warm_map(read, linked, FIGURE_WORKERS) if reading]
        self._stage(emit, "figures", "Looking at the figures", "done", f"{len(readings)} read")
        return readings

    def scientific_review(self, model, run: Run, content: str, material: str):
        task = P.SCIENTIFIC_REVIEW.format(
            brief=run.spec.brief or "(none)",
            audience=run.spec.audience,
            memo=self._study_text(run),
            coverage=(run.coverage_plan.model_dump_json() if run.coverage_plan else "(none)"),
            story=content,
        )
        return self._json(model, S.ScientificReview, *material_request(material, task), "low")

    @staticmethod
    def _plain_built(deck: str, run: Run, pool: Pool, model, effort) -> BuiltOutput:
        """A capture-only view of the words as they will visibly be drawn."""
        slides = fmt.split_slides(deck)
        citations = []
        for item in pool.items:
            citation = item.payload[0] if isinstance(item.payload, tuple) and item.payload else None
            if citation is not None:
                citations.append(citation.model_copy(update={"ref": item.ref}))
        return BuiltOutput(
            title=run.story.title if isinstance(run.story, DeckStory) else "Scientific deck",
            content=deck,
            outline=[
                {"heading": fmt.parse_slide(text).title or f"Slide {n}"}
                for n, text in enumerate(slides, 1)
            ],
            citations=citations,
            checks=[],
            model=model,
            effort=effort,
        )

    @staticmethod
    def _visible_slides(deck: str) -> str:
        rows = []
        for number, text in enumerate(fmt.split_slides(deck), 1):
            slide = fmt.parse_slide(text)
            lines = []
            if slide.title:
                lines.append(f"Title: {slide.title}")
            for line in slide.body:
                found = re.match(r"^!\[([^]]*)\]\((gunther-asset:[^)]+)\)$", line)
                if not found:
                    lines.append(line)
                    continue
                caption, reference = found.groups()
                parts = S.image_parts(reference)
                lines.append(
                    f"[Image: {caption or 'source figure'}"
                    + ("; enlarged panel region]" if parts and parts[1] else "]")
                )
            rows.append(f"[Slide {number}]\n" + "\n".join(lines))
        return "\n\n".join(rows)
    def _learner_review(
        self, model, effort, run: Run, deck: str, pool: Pool, emit: Events
    ) -> tuple[S.LearnerReview | None, list[Image]]:
        """A separate call with visible content only; source context never enters it."""
        images: list[Image] = []
        sees = vision_model(self.gateway, model, effort)
        learner_model = model
        renderer = getattr(self, "renderer", None)
        if sees and renderer is not None:
            eye, _ = sees
            learner_model = eye
            try:
                snapshot = renderer.capture(
                    self._plain_built(deck, run, pool, eye, effort), run.use
                )
                images = [
                    Image.from_path(renderer.file(snapshot["id"], slide["image"]), "image/png")
                    for slide in snapshot["slides"]
                ]
            except Exception as error:  # noqa: BLE001 - text remains a valid learner view
                logger.info("Rendering for the independent learner failed: %s", error)
                self._science_limit(
                    run, f"The independent learner saw text only; rendering failed: {error}"
                )
        try:
            self._count_model_call("science")
            learner, _ = self.gateway.complete_json(
                learner_model,
                S.LearnerReview,
                system=P.LEARNER_REVIEW.format(
                    brief=run.spec.brief or "(none)", audience=run.spec.audience
                ),
                prompt="Visible slides (notes are deliberately absent):\n\n"
                + self._visible_slides(deck),
                images=images,
                effort="low",
                schema_at="end",
            )
            supplied = len(fmt.split_slides(deck))
            answers = (
                learner.question_and_contribution,
                learner.method_or_experiment,
                learner.figures_and_controls,
                learner.evidence_for_conclusions,
                learner.assumptions_limits_and_questions,
            )
            if any(not any(1 <= n <= supplied for n in answer.evidence) for answer in answers):
                raise ValueError("Learner evidence names a slide that was not supplied.")
            run.reviews.append(learner)
            emit({"type": "learner_review", **learner.model_dump(), "images": bool(images)})
            return learner, images
        except (ModelError, ValueError, LookupError, OSError) as error:
            logger.info("The independent learner review failed: %s", error)
            self.skipped.append(
                {
                    "step": "science",
                    "label": "Independent learner unavailable",
                    "reason": str(error),
                }
            )
            return None, images

    def _compare_learner(
        self, model, run: Run, deck: str, learner: S.LearnerReview, material: str
    ) -> S.CoverageReview | None:
        task = P.COVERAGE_COMPARISON + (
            "\nStudy map, image readings and coverage plan:\n"
            + self._study_text(run)
            + "\nIndependent learner reconstruction:\n"
            + learner.model_dump_json()
            + "\nDraft:\n"
            + deck
        )
        try:
            return self._json(model, S.CoverageReview, *material_request(material, task), "low")
        except ModelError as error:
            logger.info("Comparing the learner with the source failed: %s", error)
            self.skipped.append(
                {"step": "science", "label": "Learner comparison unavailable", "reason": str(error)}
            )
            return None

    def _revise_science(
        self,
        model,
        effort,
        run: Run,
        deck: str,
        pool: Pool,
        material: str,
        problems: list[S.ScientificProblem | str],
    ) -> str:
        """Rewrite affected slides, allowing one slide to become several; structural omissions
        may still ask for the whole deck."""

        slides = fmt.split_slides(deck)
        wanted: set[int] = set()
        structural = False
        for problem in problems:
            references: set[int] = set()
            if isinstance(problem, str):
                # A code problem names its figure or its values: the slides showing them are
                # rewritten. Only a figure no slide shows yet needs the whole deck (a new slide
                # must be placed for it).
                assets = re.findall(r"gunther-asset:([\w-]+)", problem)
                stated = re.search(r"unreadable glyphs:\s*(.+?)\.\s+Omit", problem)
                values = fmt.numbers_in(stated.group(1)) if stated else set()
                showing = {
                    number
                    for number, slide in enumerate(slides, 1)
                    if any(f"gunther-asset:{asset}" in slide for asset in assets)
                    or values
                    & fmt.numbers_in(re.sub(r"\[\d+\]|\(gunther-asset:[^)]*\)", "", slide))
                }
                if showing:
                    wanted |= showing
                else:
                    structural = True
                continue
            for low, high in re.findall(
                r"slides?\s+(\d+)\s*(?:[-–]\s*(\d+))?", problem.where, re.I
            ):
                end_number = int(high) if high else int(low)
                references.update(
                    number for number in range(int(low), end_number + 1)
                    if 1 <= number <= len(slides)
                )
            wanted |= references
            if not references or problem.where.casefold() in {"overall", "document", "deck"}:
                structural = True

        def rejected(reason: str) -> str:
            self.skipped.append(
                {
                    "step": "science",
                    "label": "Targeted scientific revision rejected",
                    "reason": reason,
                }
            )
            return deck

        try:
            if structural or not wanted:
                selected, numbers, labels = slides, list(range(1, len(slides) + 1)), "all"
            else:
                numbers = sorted(wanted)
                selected = [slides[number - 1] for number in numbers]
                labels = ", ".join(map(str, numbers))
            task = P.STUDY_REVISE.format(
                problems="\n".join(
                    f"- {p.where}: {p.problem} — {p.fix}"
                    if isinstance(p, S.ScientificProblem)
                    else f"- {p}"
                    for p in problems
                ),
                slides="\n\n".join(
                    f"<!-- replaces slide {number} -->\nSlide {number}:\n{text}"
                    for number, text in zip(numbers, selected, strict=True)
                ),
            )
            task += (
                f"\nThese are deck slide numbers {labels}. When one input slide becomes several "
                "output slides, start every output group with its `<!-- replaces slide N -->` "
                "marker."
            )
            task += "\nStudy map, image readings and coverage plan:\n" + self._study_text(run)
            revised = _unfence(
                self._text(model, *material_request(material, task), "low", 24_000)
            )
        except ModelError as error:
            logger.info("The targeted scientific revision failed: %s", error)
            return rejected(str(error))

        pieces = fmt.split_slides(revised)
        if structural:
            if len(pieces) < 2:
                return rejected(f"revision returned {len(pieces)} slides")
            prior_errors = run.format_repair_errors
            candidate = self.fix_format(model, effort, run, revised, _no_events)
            if not source_images_kept(deck, candidate):
                run.format_repair_errors = prior_errors
                return rejected("the revision dropped a source image")
            return candidate

        groups: dict[int, list[str]] = {}
        marker = re.compile(r"\A<!--\s*replaces slide (\d+)\s*-->\n?")
        if any(marker.match(piece) for piece in pieces):
            current = None
            for piece in pieces:
                found = marker.match(piece)
                if found:
                    current = int(found.group(1))
                    piece = piece[found.end():]
                if current not in wanted:
                    return rejected("a replacement group names an unrequested slide")
                groups.setdefault(current, []).append(fmt.strip_labels(piece))
            if set(groups) != wanted:
                return rejected("the revision did not replace every requested slide")
        elif len(pieces) == len(selected):
            groups = {
                number: [fmt.strip_labels(piece)]
                for number, piece in zip(numbers, pieces, strict=True)
            }
        elif len(numbers) == 1 and pieces:
            groups = {numbers[0]: [fmt.strip_labels(piece) for piece in pieces]}
        else:
            return rejected(
                f"revision returned {len(pieces)} slides for {len(selected)} inputs "
                "without complete replacement markers"
            )
        if any(
            not source_images_kept(slides[number - 1], "\n\n---\n\n".join(replacements))
            for number, replacements in groups.items()
        ):
            return rejected("the revision dropped a source image")

        out: list[str] = []
        for number, slide in enumerate(slides, 1):
            out.extend(groups.get(number, [slide]))
        return self.fix_format(model, effort, run, "\n\n---\n\n".join(out), _no_events)

    def scientific_gate(
        self, model, effort, run: Run, deck: str, pool: Pool, material: str, emit: Events
    ) -> str:
        """Source-aware coverage, an independent slide-only learner, comparison and code gates.

        Content is settled here, before design. One full review finds the problems; a targeted
        revision fixes them; a re-check asks only whether those problems are gone. A second
        full review of a revised deck always finds something new, so it never settles: what
        the re-check still finds is delivered as open problems. Image problems are repaired
        by code (``S.heal_images``); nothing here stops the build. An unavailable review is
        never ready."""

        assert run.study is not None
        step, label = "science", "Reviewing the scientific explanation"
        cover = run.plan[0].heading if run.plan else None
        deck = S.apply_source_cover(deck, pool.items, title=cover)
        self._stage(emit, step, label, "running", "checking the slides against the material")
        if run.reviews is None:
            run.reviews = []
        try:
            review: S.ScientificReview | None = self.scientific_review(model, run, deck, material)
        except ModelError as error:
            logger.info("The scientific review failed: %s", error)
            review = None
            self.skipped.append(
                {"step": step, "label": "Scientific review unavailable", "reason": str(error)}
            )
        if review is not None:
            run.reviews.append(review)
            emit({"type": "review", "round": 1, **review.model_dump()})
        self._progress(emit, step, label, "a fresh reader is reading only the slides")
        learner, _images = self._learner_review(model, effort, run, deck, pool, emit)
        if learner is not None:
            self._progress(emit, step, label, "comparing what the reader understood")
        comparison = (
            self._compare_learner(model, run, deck, learner, material)
            if learner is not None
            else None
        )
        if comparison is not None:
            run.reviews.append(comparison)
            emit({"type": "coverage_comparison", "round": 1, **comparison.model_dump()})
        found: list[S.ScientificProblem] = list(review.problems if review is not None else [])
        if comparison is not None:
            found += comparison.coverage_problems + comparison.learner_problems
        code = self._science_code(run, deck, pool)
        unavailable = review is None or learner is None or comparison is None
        verdicts_ready = bool(
            review and review.verdict == "ready" and comparison and comparison.verdict == "ready"
        )
        still_open = found
        fixed = 0
        if found or code:
            count = len(found) + len(code)
            self._progress(
                emit, step, label, f"fixing {count} problem{'' if count == 1 else 's'}"
            )
            prior_errors = run.format_repair_errors
            revised = self._revise_science(
                model, effort, run, deck, pool, material, [*found, *code]
            )
            revised = S.apply_source_cover(revised, pool.items, title=cover)
            if not _matches_plan(run, fmt.split_slides(revised)):
                revised = deck
                run.format_repair_errors = prior_errors
            if found and revised != deck:
                self._progress(emit, step, label, "checking that the fixes worked")
                still_open = self._recheck_science(model, run, revised, material, found)
                fixed = len(found) - len(still_open)
            deck = revised
            code = self._science_code(run, deck, pool)
        deck, healed = S.heal_images(deck, run.figure_readings or [], pool.items)
        if healed:
            for note in healed:
                self._science_limit(run, note)
            code = self._science_code(run, deck, pool)
        for problem in code:
            self._science_limit(run, problem)
        if unavailable:
            run.review_status = "unavailable"
            self._science_limit(
                run, "A scientific review could not run; the deck is not review-ready."
            )
            detail = "source review unavailable"
            if review is not None and learner is None:
                detail = "independent learner unavailable"
            elif review is not None and comparison is None:
                detail = "learner comparison unavailable"
            self._stage(emit, step, label, "done", detail)
        elif still_open or code or (not found and not verdicts_ready):
            run.review_status = "open_problems"
            for problem in still_open:
                self._science_limit(run, f"Review: {problem.where}: {problem.problem}")
            if not found and not verdicts_ready:
                self._science_limit(
                    run, "The review did not mark the deck ready, but named no problem to fix."
                )
            if learner and learner.unexplained:
                self._science_limit(
                    run,
                    "Independent learner could not explain: " + "; ".join(learner.unexplained),
                )
            left = len(still_open) + len(code)
            done = f"{fixed} fixed, " if fixed else ""
            self._stage(emit, step, label, "done", f"{done}{left} open problems recorded")
        else:
            run.review_status = "ready"
            self._stage(emit, step, label, "done", f"ready, {fixed} fixed" if fixed else "ready")
        return deck

    def _science_code(self, run: Run, deck: str, pool: Pool) -> list[str]:
        """Every code check of a scientific deck; cover metadata is noted, never revised."""

        assert run.study is not None
        for problem in S.cover_problems(deck, pool.items):
            self._science_limit(run, problem)
        return S.code_problems(
            run.study, run.figure_readings or [], deck, pool.items, plan=run.coverage_plan
        )

    def _recheck_science(
        self, model, run: Run, deck: str, material: str, problems: list[S.ScientificProblem]
    ) -> list[S.ScientificProblem]:
        """Which of the review's problems the revision did not resolve. Unknown counts as open."""

        task = P.SCIENCE_RECHECK.format(
            problems="\n".join(
                f"{n}. {p.where}: {p.problem} — fix: {p.fix}" for n, p in enumerate(problems, 1)
            ),
            deck=deck,
        )
        try:
            check = self._json(model, S.ProblemCheck, *material_request(material, task), "low")
        except ModelError as error:
            logger.info("The scientific re-check failed: %s", error)
            self.skipped.append(
                {
                    "step": "science",
                    "label": "Scientific re-check unavailable",
                    "reason": str(error),
                }
            )
            return problems
        resolved = {row.n for row in check.problems if row.resolved}
        return [p for n, p in enumerate(problems, 1) if n not in resolved]

    # 2. Memo ------------------------------------------------------------------------------

    def memo(self, model, effort, run: Run, material: str, emit: Events) -> Memo:
        self._stage(emit, "memo", "Finding what is worth saying", "running")
        spec = run.spec
        what = "a slide deck" if spec.kind == "slides" else "a report"
        system = P.MEMO.format(
            what=what,
            brief=spec.brief or "(none: choose the most valuable question the material answers)",
            audience=spec.audience,
            use_line=f"Use: {run.use}." if run.use else "",
            kind="deck" if spec.kind == "slides" else "report",
        )
        memo = self._json(model, Memo, *material_request(material, system), effort)
        self._stage(emit, "memo", "Finding what is worth saying", "done", memo.answer[:200])
        emit({"type": "memo", "memo": memo.model_dump()})
        return memo

    def _memo_text(self, memo: Memo) -> str:
        lines = [f"Answer: {memo.answer}", "Insights:"]
        lines += [
            f"- {item.claim} {''.join(f'[{r}]' for r in item.refs)} — so what: {item.so_what}"
            for item in memo.insights
        ]
        lines.append("Key facts:")
        lines += [
            f"- {item.fact}: {item.value}"
            + (f" (vs {item.compared_to})" if item.compared_to else "")
            + " "
            + "".join(f"[{r}]" for r in item.refs)
            for item in memo.facts
        ]
        if memo.tensions:
            lines.append("Tensions and unknowns:")
            lines += [f"- {t.issue} {''.join(f'[{r}]' for r in t.refs)}" for t in memo.tensions]
        if memo.questions:
            lines.append("What the audience will ask:")
            lines += [f"- {q}" for q in memo.questions]
        return "\n".join(lines)

    # 3. Storyline -------------------------------------------------------------------------

    def _length(self, run: Run) -> str:
        if run.spec.kind == "slides":
            if run.study:
                return (
                    "as many slides as needed to teach the central method and source figures"
                    "; split complex figures into explanatory sequences, never shrink evidence"
                )
            return "as many slides as the argument needs, usually 8–12; never pad"
        return "as long as the argument needs, usually 1,500–3,000 words in 4–7 sections"

    def storyline(
        self, model, effort, run: Run, material: str, emit: Events, *,
        previous_outline: Sequence[Planned] = (), instruction: str = "",
        preview_outline: bool = True,
    ) -> DeckStory | ReportStory:
        spec = run.spec
        self._stage(emit, "story", "Planning the storyline", "running")
        memo = (
            self._study_text(run) if run.study else (self._memo_text(run.memo) if run.memo else "")
        )
        if spec.kind == "slides":
            schema = DeckStory
            system = (P.STUDY_STORY if run.study else P.DECK_STORY).format(
                use=run.use,
                audience=spec.audience,
                brief=spec.brief or "(none)",
                pages=self._length(run),
                use_word=run.use,
                use_rule=P.USE_RULES[run.use],
                layout_guide=P.LAYOUT_GUIDE,
                memo=memo,
            )
        else:
            schema = ReportStory
            system = P.REPORT_STORY.format(
                audience=spec.audience,
                brief=spec.brief or "(none)",
                pages=self._length(run),
                memo=memo,
            )
        deck = spec.kind == "slides"
        system += "\n\n" + P.STORY_LENGTH.format(
            parts="slides" if deck else "report sections",
            first="title slide" if deck else "summary",
        )
        if run.plan:
            system += (
                "\nThe approved outline sets the count and order, superseding earlier counts.\n"
                "The approved outline, in order:\n" + _outline_text(run.plan)
            )
        if previous_outline:
            system += (
                "\nThe existing outline, in order. Preserve headings, goals and order where "
                "they still fit the brief and the requested change. Return the existing id for "
                "each continuing part, even if its title or position changes. Use null for a new "
                "part and omit removed parts. Return the report summary's id as summary_id. "
                "Keep only claims supported by the selected material.\n"
                + json.dumps(
                    [{"id": p.id, "heading": p.heading, "goal": p.goal} for p in previous_outline],
                    ensure_ascii=False,
                )
            )
        if instruction:
            system += "\nRequested change to the outline: " + instruction
        figures = self._figures(material)
        system, prompt = material_request(material, system + figures)
        story = self._json(model, schema, system, prompt, effort)
        length = story.model_dump(include={"requested_count", "requested_minutes"})
        run.reviews = []
        for round_ in range(CRITIC_ROUNDS):
            review = self.critique(model, run, story, material)
            run.reviews.append(review)
            emit(
                {
                    "type": "review",
                    "round": round_ + 1,
                    "verdict": review.verdict,
                    "understood": review.understood,
                    "problems": [p.model_dump() for p in review.problems],
                }
            )
            if review.verdict.strip().lower() in ("ready", "unavailable") or not review.problems:
                break
            again = (
                "Revise this scientific storyline; preserve its scientific handoffs.\n{review}"
                if run.study
                else P.STORY_AGAIN
            ).format(
                review="\n".join(f"- [{p.where}] {p.problem} → {p.fix}" for p in review.problems)
            )
            try:
                story = self._json(
                    model,
                    schema,
                    system,
                    prompt + "\n\nYour storyline:\n" + story.model_dump_json() + "\n\n" + again,
                    "low",
                )
            except ModelError as error:
                logger.info("Revising the storyline failed: %s", error)
                self.skipped.append({
                    "step": "story", "label": "Storyline revision unavailable",
                    "reason": str(error),
                })
                break
        if isinstance(story, DeckStory):
            story = self._tidy_deck_story(story)
        story = story.model_copy(update=length)
        _validate_storyline(run, story)
        self._stage(emit, "story", "Planning the storyline", "done", story.title)
        emit({"type": "storyline", "storyline": story.model_dump()})
        if not preview_outline:
            return story
        # The planned titles, as the live outline the app already shows while a build runs.
        planned = (
            [{"heading": s.title, "goal": s.job} for s in story.slides]
            if isinstance(story, DeckStory)
            else [{"heading": story.summary_heading or "Summary", "goal": story.summary_job}]
            + [{"heading": s.heading, "goal": s.job} for s in story.sections]
        )
        if run.plan:
            planned = project_outline(run.plan, include_ids=False, include_layout=False)
        emit({"type": "outline", "title": story.title, "sections": planned})
        return story

    @staticmethod
    def _tidy_deck_story(story: DeckStory) -> DeckStory:
        slides = [
            replace_layout(slide, slide.layout if slide.layout in fmt.LAYOUTS else "points")
            for slide in story.slides
        ]
        if slides[0].layout != "title":
            slides.insert(0, StorySlide(title=story.title, layout="title", job=story.subtitle))
        for index, slide in enumerate(slides[1:], 1):
            if slide.layout == "title":
                slides[index] = replace_layout(slide, "statement")
        return story.model_copy(update={"slides": slides})

    def critique(self, model, run: Run, story, material: str) -> Review:
        spec = run.spec
        if run.study:
            try:
                return self.scientific_review(model, run, story.model_dump_json(), material)
            except ModelError as error:
                logger.info("The storyline review failed: %s", error)
                self.skipped.append(
                    {"step": "story", "label": "Storyline review unavailable", "reason": str(error)}
                )
                return Review(verdict="unavailable")
        if isinstance(story, DeckStory):
            lines = [
                f"{n}. [{s.layout}] {s.title}  — job: {s.job}"
                for n, s in enumerate(story.slides, 1)
            ]
        else:
            lines = [f"Title: {story.title}", f"Summary: {story.summary_heading}"]
            lines += [f"  - {p}" for p in story.summary_points]
            lines += [
                f"{n}. {s.heading}  — job: {s.job}"
                + (f" — exhibits: {'; '.join(s.exhibits)}" if s.exhibits else "")
                for n, s in enumerate(story.sections, 1)
            ]
        system = P.CRITIC.format(
            brief=spec.brief or "(none)",
            audience=spec.audience,
            story="\n".join(lines),
            answer=run.memo.answer if run.memo else "(not given)",
        )
        try:
            return self._json(
                model, Review, *material_request(material, system + "\n\nReview it now."), "low"
            )
        except ModelError as error:
            logger.info("The storyline review failed: %s", error)
            self.skipped.append(
                {"step": "story", "label": "Storyline review unavailable", "reason": str(error)}
            )
            return Review(verdict="unavailable")

    @staticmethod
    def _figures(material: str) -> str:
        found = re.findall(r"!\[[^\]]*\]\(gunther-asset:[A-Za-z0-9_-]+\)", material)
        if not found:
            return "\n\nNo figures are available: do not use the figure layout."
        return "\n\nFigures available (copy exactly):\n" + "\n".join(sorted(set(found)))

    # 4. Writing ---------------------------------------------------------------------------

    def write_deck(self, model, effort, run: Run, pool: Pool, material: str, emit: Events) -> str:
        spec = run.spec
        story = run.story
        assert isinstance(story, DeckStory)
        self._stage(emit, "write", "Writing the slides", "running")
        story_text = "\n".join(
            [f"Deck title: {story.title}", f"Subtitle: {story.subtitle}"]
            + [
                f"{n}. [{s.layout}] {s.title} — job: {s.job} {''.join(f'[{r}]' for r in s.refs)}"
                for n, s in enumerate(story.slides, 1)
            ]
        )
        theme = spec.theme if spec.theme in fmt.THEMES else "paper"
        if run.study:
            story_text = story.model_dump_json()
        system = (P.STUDY_WRITE if run.study else P.DECK_WRITE).format(
            brief=spec.brief or "(none)",
            audience=spec.audience,
            use=run.use,
            use_rule=P.USE_RULES[run.use],
            language=P.LANGUAGE.format(language=run.language),
            format=(STUDY_DECK_FORMAT if run.study else DECK_FORMAT)
            + ("\n\n" + READ_NOTE if run.use == "read" else ""),
            memo=self._study_text(run)
            if run.study
            else (self._memo_text(run.memo) if run.memo else ""),
            story=story_text,
            theme=theme,
        )
        if run.plan:
            system += "\nUse the approved slide titles in order:\n" + _outline_text(run.plan)
        if story.requested_minutes is not None:
            system += f"\nWrite speaker notes for a talk lasting {story.requested_minutes} minutes."
        system, prompt = material_request(material, system + self._figures(material))
        text = self._text(model, system, prompt, effort, max_tokens=24_000)
        deck = _unfence(text)
        slides = fmt.split_slides(deck)
        if len(slides) < len(run.plan or story.slides):
            # Ran out of room: ask for the rest, from where it stopped.
            more = self._text(
                model,
                system,
                prompt
                + "\n\nYou wrote these slides so far:\n\n"
                + deck
                + f"\n\nContinue with slide {len(slides) + 1}. Output only the remaining slides.",
                effort,
                max_tokens=16_000,
            )
            slides += fmt.split_slides(_unfence(more))
        slides = self._title_marker([fmt.strip_labels(slide) for slide in slides], theme)
        _validate_count(story, len(slides), "written deck")
        if not _matches_plan(run, slides):
            raise ModelError("The written deck did not follow the approved outline.")
        self._stage(emit, "write", "Writing the slides", "done", f"{len(slides)} slides")
        return "\n\n---\n\n".join(slides)

    @staticmethod
    def _title_marker(slides: list[str], theme: str) -> list[str]:
        if not slides:
            return slides
        first = fmt.parse_slide(slides[0])
        attrs = {"design": fmt.DESIGN, "theme": theme}
        accent = first.attrs.get("accent")
        if accent:
            attrs["accent"] = accent
        slides[0] = fmt.with_marker(slides[0], "title", **attrs)
        return slides

    def fix_format(self, model, effort, run: Run, deck: str, emit: Events) -> str:
        """Repair slides in parallel for up to two rounds and keep the renderer's fallback."""

        read = run.use == "read"
        notes = run.use == "talk"
        guide = (STUDY_DECK_FORMAT if run.study else DECK_FORMAT) + (
            "\n\n" + READ_NOTE if read else ""
        )
        errors: dict[int, str] = {}
        for _ in range(2):
            found = fmt.check_deck(
                deck, read=read, notes_required=notes, scientific=bool(run.study)
            )
            if not found:
                break
            slides = fmt.split_slides(deck)

            def fix(
                item: tuple[int, list[str]], slides: list[str] = slides
            ) -> tuple[str | None, str | None]:
                n, problems = item
                system = P.FIX_SLIDES.format(format=guide, problems="")
                if run.study:
                    system += "\n" + P.SCIENCE_TRUTH
                prompt = (
                    f"Slide {n} problems:\n"
                    + "\n".join(f"- {p}" for p in problems)
                    + f"\n\nSlide {n} as written:\n{slides[n - 1]}"
                    + "\n\nRewrite it now."
                )
                try:
                    # A format fix is mechanical: little thinking, so its 4,000 tokens are
                    # spent on the slide (high thinking ran out of room and asked again).
                    again = fmt.split_slides(
                        _unfence(self._text(model, system, prompt, "low", 4000))
                    )
                except ModelError as error:
                    logger.info("Fixing slide %d failed: %s", n, error)
                    return None, str(error) or "The format repair did not finish."
                if not again or not _keeps_heading(run, slides[n - 1], again):
                    return None, None
                text = fmt.strip_labels(again[0])
                if not source_images_kept(slides[n - 1], text):
                    return None, None
                if n == 1:
                    text = self._title_marker(
                        [text], fmt.parse_slide(slides[0]).attrs.get("theme", "paper")
                    )[0]
                return text, None

            fixed = warm_map(fix, found, WORKERS)
            changed = False
            for (n, _), (text, request_error) in zip(found, fixed, strict=True):
                if request_error is None:
                    errors.pop(n, None)
                else:
                    errors[n] = request_error
                if text is not None and text != slides[n - 1]:
                    slides[n - 1], changed = text, True
            deck = "\n\n---\n\n".join(slides)
            if not changed:
                break
        left = fmt.check_deck(
            deck, read=read, notes_required=run.use == "talk", scientific=bool(run.study)
        )
        if not run.study and any("units long" in p for _, problems in left for p in problems):
            slides = fmt.split_slides(deck)
            for n, problems in left:
                if any("units long" in p for p in problems):
                    slides[n - 1] = fmt.trim_to_notes(slides[n - 1], first=n == 1, read=read)
            deck = "\n\n---\n\n".join(slides)
        run.format_repair_errors = errors
        return deck

    def _record_slide_format(self, run: Run, built: BuiltOutput) -> None:
        """Report the format of the deck being saved, after all repairs and fallbacks."""
        remaining = fmt.check_deck(
            built.content,
            read=run.use == "read",
            notes_required=run.use == "talk",
            scientific=bool(run.study),
        )
        if not remaining:
            return
        notes = []
        for n, problems in remaining:
            label, reason = f"Slide {n} still breaks the format", problems[0]
            if error := run.format_repair_errors.get(n):
                label, reason = f"Slide {n} format repair unavailable", f"{error} {reason}"
            notes.append({"step": "format", "label": label, "reason": reason[:600]})
        self.skipped.extend(notes)

    def write_report(self, model, effort, run: Run, pool: Pool, material: str, emit: Events) -> str:
        spec = run.spec
        story = run.story
        assert isinstance(story, ReportStory)
        self._stage(emit, "write", "Writing the report", "running")
        story_text = "\n".join(
            [f"Title: {story.title}", f"Executive summary heading: {story.summary_heading}"]
            + [f"  - {p}" for p in story.summary_points]
            + [
                f"{n}. {s.heading} — job: {s.job}"
                + (f" — exhibits: {'; '.join(s.exhibits)}" if s.exhibits else "")
                + " "
                + "".join(f"[{r}]" for r in s.refs)
                for n, s in enumerate(story.sections, 1)
            ]
        )
        system = P.REPORT_WRITE.format(
            brief=spec.brief or "(none)",
            audience=spec.audience,
            length=f"Default length when the brief gives none: {self._length(run)}.",
            language=P.LANGUAGE.format(language=run.language),
            format=REPORT_FORMAT,
            memo=self._memo_text(run.memo) if run.memo else "",
            story=story_text,
        )
        if run.plan:
            system += (
                "\nUse exactly these approved section headings in order. "
                "The first section is the summary.\n" + _outline_text(run.plan)
            )
        system, prompt = material_request(material, system + self._figures(material))
        text = _unfence(self._text(model, system, prompt, effort, max_tokens=24_000))
        if not text.lstrip().startswith("<!-- report: v2 -->"):
            text = "<!-- report: v2 -->\n" + text.lstrip()
        if not _matches_plan(run, split_document(text, "report")[1]):
            raise ModelError("The written report did not follow the approved outline.")
        problems = fmt.check_report(text)
        if problems:
            # One revision of the whole report for what its form gets wrong.
            fix = P.FIX_REPORT.format(
                format=REPORT_FORMAT, problems="\n".join(f"- {p}" for p in problems)
            )
            try:
                fixed = _unfence(
                    self._text(
                        model,
                        system,
                        prompt + "\n\n" + fix + "\n\nYour report:\n\n" + text,
                        effort,
                        max_tokens=24_000,
                    )
                )
                if (
                    fixed.count("\n## ") >= text.count("\n## ") - 1
                    and _matches_plan(run, split_document(fixed, "report")[1])
                ):
                    text = fixed
            except ModelError as error:
                logger.info("Fixing the report's form failed: %s", error)
                self.skipped.append({
                    "step": "format", "label": "Report format repair unavailable",
                    "reason": str(error),
                })
            left = fmt.check_report(text)
            if left:
                self.skipped.append(
                    {
                        "step": "format",
                        "label": "The report's form",
                        "reason": "; ".join(left)[:600],
                    }
                )
        text = fmt.cite_exhibit_rows(fmt.cite_captions(fmt.ensure_summary_mark(text)))
        self._stage(emit, "write", "Writing the report", "done")
        return text

    # 5. Verifying -------------------------------------------------------------------------

    def verify(
        self,
        model,
        run: Run,
        parts: list[str],
        pool: Pool,
        emit: Events,
        material: str | None = None,
    ) -> list[Draft]:
        """Warm with the first part, then check and repair the remaining parts in parallel."""

        label = "Checking every claim against its source"
        self._stage(emit, "verify", label, "running", f"{len(parts)} slides")
        by_ref = pool.by_ref()

        rest = "\n".join(parts[1:])

        def check(text: str) -> Draft:
            if run.spec.kind == "report":
                text = fmt.cite_exhibit_rows(fmt.cite_captions(fmt.table_separators(text)))
            text, _ = check_citations(text, set(by_ref))
            return Draft(text, self._claims(model, text, by_ref, rest, material=material))

        def check_and_fix(text: str) -> Draft:
            draft = check(text)
            if not any(issue.verdict in FIXABLE for issue in draft.issues or []):
                return draft
            title = fmt.MARK.match(text) and fmt.parse_slide(text).layout == "title"
            fixed = self._fix_claims(
                model, [draft], by_ref, rest if title else "", material=material
            )
            if not fixed:
                return draft
            if run.plan or run.spec.kind == "report":
                preamble, repaired = split_document(fixed[0], run.spec.kind)
                if preamble or len(repaired) != 1 or not _keeps_heading(run, text, repaired):
                    return draft
            return check(fixed[0])

        drafts = warm_map(
            check_and_fix,
            parts,
            WORKERS,
            lambda done, total: self._progress(
                emit, "verify", label, f"{done} of {total} slides checked"
            ),
        )
        left = sum(
            1
            for d in drafts
            for i in d.issues or []
            if i.verdict in ("unsupported", "contradicted")
        )
        self._stage(
            emit,
            "verify",
            label,
            "done",
            f"{left} claim(s) still flagged" if left else "all claims supported",
        )
        return drafts

    def _claim_text(self, text: str, rest: str = "") -> str:
        marked = _marked(text)
        body = marked.body
        lines = body.split("\n")
        if marked.layout == "title":
            # The title slide's meta line (audience · date) states no claim.
            content = [n for n, line in enumerate(lines) if line.strip()]
            if len(content) >= 3:
                lines[content[2]] = ""
            lines = [_lend_refs(line, rest) for line in lines]
        checked = check_heading_refs(body, lines)
        if self.scientific_context:
            # A numbered step's own number is not a claim to source.
            checked = re.sub(r"(?m)^([ \t]*)\d+[.)]\s+", r"\1", checked)
        return checked

    def _claims(
        self,
        model,
        text: str,
        by_ref: dict[int, Evidence],
        rest: str = "",
        *,
        material: str | None = None,
        prior: Verdicts | None = None,
        inventory: Verdicts | None = None,
    ) -> Verdicts:
        """One claim check per part. A title is checked against the passages its own slide or
        section cites (it states their conclusion). The title slide states the whole deck's
        conclusion, so a figure on it is checked against the passages the other slides (``rest``)
        cite for that same figure, not only its own. Interpretation and recommendation that
        follow from cited facts are the author's judgement, not findings; what is flagged is a
        cited claim its passages do not state, and a specific fact (a number) with no source."""

        body = _marked(text).body
        checked = self._claim_text(text, rest)
        checker = copy(self.ask)
        checker.gateway = _MaterialCheckerGateway(
            self,
            material if material is not None else self._material(list(by_ref.values())),
        )
        if self.scientific_context:
            checker.gateway.material += (
                "\nActual image readings (linked asset IDs identify the source figures above):\n"
                + json.dumps(self.scientific_context.get("figure_readings", []), ensure_ascii=False)
            )
        checked = check_part(
            checker, model,
            STUDIO_CHECK + ("\n" + P.SCIENCE_TRUTH if self.scientific_context else ""),
            checked,
            by_ref, limit=self.limits.sentences, prior=prior or {}, inventory=inventory,
        )
        for claim in checked.values():
            label = claim.get("label", claim["verdict"])
            plain = re.sub(r"\s*\[(?:\?|\d+)\]", "", claim["text"]).strip()
            # An image reference is provenance, so its id and crop coordinates are not facts.
            said = re.sub(r"!\[([^\]]*)\]\(gunther-asset:[^)]*\)", r"\1", plain)
            if label in {"not", "partly"}:
                claim["issue"] = Issue(plain[:300], "unsupported", claim.get("reason") or "").out()
            elif label == "unsourced" and re.search(r"\d", said):
                issue = Issue(plain[:300], "unsourced", "A specific fact with no source.")
                claim["issue"] = issue.out()
        # Syntax findings come from the current section independently of its model verdicts.
        held = {row["issue"]["claim"][:80] for row in checked.values() if "issue" in row}
        findings = []
        for line in fmt.uncited_lines([ln for ln in body.split("\n") if not ln.startswith(":::")]):
            plain = re.sub(r"\s*\[(?:\?|\d+)\]", "", line).strip()
            if plain[:80] not in held and not re.match(r"^(Source|Sources|来源)\s*[:：]", plain):
                issue = Issue(plain[:300], "unsourced", "A number with no citation.")
                findings.append(finding(issue.out()))
        return checked | verdicts_of(*findings)

    def _cover(
        self, model, drafts: list[Draft], pool: Pool, material: str | None = None
    ) -> list[Draft]:
        """The subtitle is the deck's core message: after the slides are final, it is made to
        state what their titles say (claim fixes can move a title away from it), then the
        title slide is checked against its sources again."""

        if not drafts or fmt.parse_slide(drafts[0].text).layout != "title":
            return drafts
        cover = fmt.parse_slide(drafts[0].text)
        body = [line for line in cover.body if line.strip()]
        if not cover.title or not body:
            return drafts
        titles = [fmt.parse_slide(d.text).title for d in drafts[1:]]
        titles = [t for t in titles if t]
        rest = "\n".join(d.text for d in drafts[1:])
        cited = [
            line.strip()
            for line in rest.split("\n")
            if re.search(r"\[\d+\]", line)
            and fmt.numbers_in(re.sub(r"\s*\[\d+\]", "", line)) & fmt.numbers_in(" ".join(titles))
        ][:30]
        system = P.COVER.format(
            title=cover.title,
            subtitle=body[0],
            titles="\n".join(f"{n}. {t}" for n, t in enumerate(titles, 2)),
            cited="\n".join(cited) or "(none)",
        )
        system, prompt = material_request(
            material if material is not None else self._material(list(pool.by_ref().values())),
            system + "\n\nReply now.",
        )
        try:
            line = _unfence(self._text(model, system, prompt, "low", 2000)).strip()
        except ModelError as error:
            logger.info("Aligning the subtitle failed: %s", error)
            self.skipped.append(
                {"step": "verify", "label": "Subtitle alignment unavailable", "reason": str(error)}
            )
            return drafts
        line = line.splitlines()[0].strip() if line else ""
        line = re.sub(r"^(Subtitle|副标题)\s*[:：]\s*", "", line)
        if not line or line == body[0] or line.startswith("#"):
            return drafts
        text = drafts[0].text.replace(body[0], line, 1)
        by_ref = pool.by_ref()
        text, _ = check_citations(text, set(by_ref))
        draft = Draft(text, self._claims(model, text, by_ref, rest, material=material))
        if any(i.verdict in ("unsupported", "contradicted") for i in draft.issues or []):
            logger.info("The aligned subtitle is not supported; keeping the old one")
            return drafts
        return [draft, *drafts[1:]]

    def _refit(
        self,
        model,
        effort,
        run: Run,
        drafts: list[Draft],
        pool: Pool,
        material: str | None = None,
    ) -> list[Draft]:
        """Claim fixes can lengthen a slide past its budget: the format is checked again, and a
        slide that changed is checked against its sources once more."""

        joined = "\n\n---\n\n".join(d.text for d in drafts)
        if not fmt.check_deck(
            joined, read=run.use == "read", notes_required=False, scientific=bool(run.study)
        ):
            return drafts
        prior_errors = run.format_repair_errors
        fitted = fmt.split_slides(self.fix_format(model, effort, run, joined, _no_events))
        if len(fitted) != len(drafts):
            run.format_repair_errors = prior_errors
            return drafts
        changed = [i for i, (d, t) in enumerate(zip(drafts, fitted, strict=True)) if d.text != t]
        by_ref = pool.by_ref()

        rest = "\n".join(fitted[1:])

        def check(text: str) -> Draft:
            return Draft(text, self._claims(model, text, by_ref, rest, material=material))

        again = warm_map(check, [fitted[i] for i in changed], WORKERS)
        for index, draft in zip(changed, again, strict=True):
            drafts[index] = draft
        return drafts

    def _chart_fallback(self, draft: Draft, index: int) -> Draft:
        """Keep the reader's data and citations when the Checker cannot license a chart."""
        layout = fmt.parse_slide(draft.text).layout
        is_chart = layout in ("chart", *CHARTS)
        source = D.visible_content(draft.text) if is_chart else draft.text
        tables = list(enumerate(MARKDOWN_TABLE.finditer(source)))

        def exhibit(match: re.Match[str]) -> str:
            attrs = match["attrs"]
            if not fmt.EXHIBIT_CHART.search(attrs):
                return match[0]
            inside = [
                (i, table) for i, table in tables
                if match.start("body") <= table.start() < match.end("body")
            ]
            if _chart_rows_supported(inside, draft.coverage):
                return match[0]
            return match[0].replace(attrs, fmt.EXHIBIT_CHART.sub("", attrs).rstrip(), 1)

        if is_chart:
            if _chart_rows_supported(tables, draft.coverage):
                return draft
            fallback = "compare" if layout in CHARTS else "table"
            text = fmt.with_marker(fmt.without_design(draft.text), fallback)
        else:
            text = fmt.EXHIBIT.sub(exhibit, draft.text)
        if text == draft.text:
            return draft
        self.skipped.append({
            "step": "verify",
            "label": f"Part {index + 1} drawn as a table",
            "reason": "The Checker did not support every chart row against its citations.",
        })
        return replace(draft, text=text)

    def _fix_claims(
        self,
        model,
        drafts: list[Draft],
        by_ref,
        rest: str = "",
        *,
        material: str | None = None,
    ) -> list[str] | None:
        parts = []
        refs: set[int] = set()
        for number, draft in enumerate(drafts, 1):
            flagged = [i for i in draft.issues or [] if i.verdict in FIXABLE]
            parts.append(
                f"Part {number}:\n{draft.text}\n\nUnsupported in part {number}:\n"
                + "\n".join(f'- "{i.claim}" ({i.note})' for i in flagged)
            )
            lent = "\n".join(_lend_refs(line, rest) for line in draft.text.split("\n"))
            refs |= {int(n) for n in re.findall(r"\[(\d+)\]", lent if rest else draft.text)}
            refs |= _stating(flagged, by_ref)
        passages = "\n\n".join(self._passage(by_ref[r], 3000) for r in sorted(refs) if r in by_ref)
        system = P.FIX_CLAIMS.format(parts="\n\n=====\n\n".join(parts), passages=passages)
        if self.scientific_context:
            system = (
                "Repair only the unsupported scientific statements listed below. Correct them "
                "from clean evidence or omit them. Do not preserve unwarranted confidence, "
                "causal claims or interpretations; retain images, essential explanations "
                "and format.\n"
                + P.SCIENCE_TRUTH
                + "\n"
                + "\n\n=====\n\n".join(parts)
                + "\nPassages:\n"
                + passages
            )
        system, prompt = material_request(
            material if material is not None else self._material(list(by_ref.values())),
            system + "\n\nRewrite them now.",
        )
        try:
            # Repairing listed sentences from given passages is constrained work: on DeepSeek
            # "medium" rounds up to high thinking, which this does not need.
            text = _unfence(self._text(model, system, prompt, "low", 16_000))
        except ModelError as error:
            logger.info("Fixing unsupported claims failed: %s", error)
            self.skipped.append(
                {"step": "verify", "label": "Claim repair unavailable", "reason": str(error)}
            )
            return None
        pieces = [p.strip() for p in re.split(r"\n-{3,}\n", "\n" + text + "\n") if p.strip()]
        if len(pieces) != len(drafts):
            logger.info("Claim fix returned %d parts for %d", len(pieces), len(drafts))
            if len(drafts) == 1:
                self.skipped.append({
                    "step": "verify", "label": "Claim repair unavailable",
                    "reason": "The repair did not return a single revised part.",
                })
                return None

            # Asked again one part at a time; a part whose fix fails keeps its text.
            def single(draft: Draft) -> str:
                one = self._fix_claims(model, [draft], by_ref, rest, material=material)
                return one[0] if one else draft.text

            return warm_map(single, drafts, WORKERS)
        out = []
        for draft, piece in zip(drafts, pieces, strict=True):
            piece = re.sub(r"\A\s*Part \d+:\s*\n", "", piece)
            # A slide keeps its marker even when the fixer dropped it.
            marker = fmt.MARK.match(draft.text)
            if marker and not fmt.MARK.match(piece):
                piece = marker.group(0).strip() + "\n" + piece
            out.append(piece if source_images_kept(draft.text, piece) else draft.text)
        return out

    def _fit_checked(
        self, model, effort, run: Run, drafts: list[Draft], pool: Pool, material: str,
    ) -> list[Draft]:
        """Refit verified slides and record any remaining scientific problems."""

        if not run.study:
            # The subtitle restates what the action titles claim; a scientific deck's
            # titles may name topics, so its subtitle stays as written and reviewed.
            aligned = self._cover(model, drafts, pool, material)
            if _matches_plan(run, [d.text for d in aligned]):
                drafts = aligned
        drafts = self._refit(model, effort, run, drafts, pool, material)
        if not run.study:
            return drafts
        healed = []
        for draft in drafts:
            fixed, repaired = S.heal_images(draft.text, run.figure_readings or [], pool.items)
            for note in repaired:
                self._science_limit(run, note)
            healed.append(replace(draft, text=fixed) if repaired else draft)
        deck = "\n\n---\n\n".join(d.text for d in healed)
        # Claim repairs may reword a slide that named a panel: that is delivered as an open
        # problem, never a lost build.
        known = set(run.limitations or [])
        for problem in self._science_code(run, deck, pool):
            if problem not in known:
                self._science_limit(run, problem)
                if run.review_status == "ready":
                    run.review_status = "open_problems"
        return healed

    @staticmethod
    def _checked_progress(run: Run, drafts: list[Draft], *, refit_pending: bool) -> dict[str, Any]:
        return {
            "slides": [{"text": draft.text, "verdicts": draft.verdicts} for draft in drafts],
            "limitations": list(run.limitations or []),
            "review_status": run.review_status,
            **({"refit_pending": True} if refit_pending else {}),
        }

    # 4. Designing -------------------------------------------------------------------------

    def design_deck(
        self, model, run: Run, drafts: list[Draft], emit: Events, *, only: list[int]
    ) -> list[Draft]:
        """The deck's style, then every slide designed on its own. The words are final by now:
        a design may not change one. It never fails a build: whatever goes wrong, the slides
        stay as they are and the template draws them."""

        label = "Designing the slides"
        self._stage(emit, "design", label, "running")
        try:
            slides = [d.text for d in drafts]
            first = fmt.parse_slide(slides[0])
            style = run.spec.deck_style
            if style not in fmt.STYLES:
                style = D.pick_style(
                    self, model, title=first.title, subtitle=first.body[0] if first.body else "",
                    brief=run.spec.brief, audience=run.spec.audience,
                )
            designed, count, notes = D.design_slides(
                self,
                model,
                D.apply_style(slides, style),
                style,
                workers=WORKERS,
                only=only,
                progress=lambda done, total: self._progress(
                    emit, "design", label, f"{done} of {total} slides designed"
                ),
            )
        except Exception as error:  # noqa: BLE001 - designing never fails a build
            logger.warning("Designing the slides failed: %s", error)
            self.skipped.append(
                {"step": "design", "label": "Slides were not designed", "reason": str(error)[:300]}
            )
            self._stage(emit, "design", label, "done", "not designed")
            return drafts
        self.skipped += notes
        plain = len(drafts) - count
        self._stage(
            emit,
            "design",
            label,
            "done",
            f"{count} slides designed" + (f", {plain} drawn plainly" if plain else ""),
        )
        return [replace(d, text=text) for d, text in zip(drafts, designed, strict=True)]

    # The whole run ------------------------------------------------------------------------

    @usage.job("outputs")
    def _prepare(
        self,
        model: ModelInfo,
        effort: Effort | None,
        spec: Spec,
        pool: Pool,
        notes: Notes,
        emit: Events,
        *,
        purpose: str | None = None,
        fixed_outline: Sequence[Planned] | None = None,
        scope: Mapping[str, Any] | None = None,
        web: bool = False,
        resume: dict[str, Any] | None = None,
        checkpoint: Checkpoint | None = None,
    ) -> tuple[Run, str, dict[str, Any], dict[str, Any]]:
        """Read and understand the material for a build or a standalone Understand."""

        if not (pool.items or notes.knowledge or notes.discussions):
            raise ModelError("There is no material to work from: choose some sources first.")
        use = self.use or ("read" if re.search(READ_HINT, spec.brief, re.I) else "talk")
        plan = tuple(Planned(p.heading.strip(), p.goal, id=p.id) for p in fixed_outline or ())
        ids = [p.id for p in plan if p.id]
        if len(set(ids)) != len(ids) or any(len(p.heading.splitlines()) != 1 for p in plan):
            raise ModelError("Approved outline headings must be single lines with unique part IDs.")
        brief = spec.brief
        if spec.kind == "report" and spec.style in STYLES:
            brief += f"\n\nReport style ({spec.style}): {STYLES[spec.style]}"
        run = Run(replace(spec, brief=brief), use if spec.kind == "slides" else "",
                  self._language(spec, pool), plan=plan)
        printed = S.pool_print(pool.items)
        resumed = resume or {}
        eye = vision_model(self.gateway, model, effort) if spec.kind == "slides" else None
        material_key = digest([
            printed, scope, asdict(notes), model.ref, effort, web, spec.kind,
            [eye[0].ref, eye[1]] if eye else None,
        ])
        request_key = digest([
            asdict(spec), run.use, purpose, [(p.id, p.heading, p.goal) for p in plan],
        ])
        # A changed request can keep its reading of the same material, but not the draft.
        same_material = resumed.get("material_key") == material_key
        studied = resumed if same_material else {}
        saved = studied if resumed.get("request_key") == request_key else {}
        # A figure's reading is kept for that figure, whatever else changed.
        self.reusable_figures = [*self.reusable_figures, *(resumed.get("figure_readings") or [])]
        state: dict[str, Any] = {
            "passages": len(pool),
            "pool": printed,
            "kind": spec.kind,
            "brief": spec.brief,
            "material_key": material_key,
            "request_key": request_key,
        }
        # The purpose is the model's decision, from the brief and a short look at the material.
        if spec.kind == "slides":
            with checkpoint_stage(emit, state, checkpoint, "purpose") as publish:
                if purpose in ("explain_study", "general"):
                    run.purpose = S.Purpose(purpose=purpose, reason="Kept the saved purpose")
                elif saved.get("purpose"):
                    run.purpose = S.Purpose.model_validate(saved["purpose"])
                else:
                    self._stage(publish, "purpose", "Understanding the request", "running")
                    run.purpose = self._json(
                        model,
                        S.Purpose,
                        P.PURPOSE,
                        f"Brief: {spec.brief or '(none)'}\nThis deck's purpose so far: "
                        + json.dumps(resumed.get("previous_purpose"))
                        + "\nMaterial overview:\n"
                        + S.overview(pool.items),
                        "low",
                    )
                    self._stage(
                        publish, "purpose", "Understanding the request", "done", run.purpose.reason,
                    )
                state["purpose"] = run.purpose.model_dump()
            emit({"type": "purpose", **state["purpose"]})
        with checkpoint_stage(emit, state, checkpoint, "read") as publish:
            material = self.read(model, run, pool, notes, publish, kept=saved.get("read"))
            if run.read_digest:
                state["read"] = {"notes": self._notes_text(notes), "digest": run.read_digest}
        if run.purpose and run.purpose.purpose == "explain_study":
            run.limitations = []
            with checkpoint_stage(emit, state, checkpoint, "study") as publish:
                kept = (
                    S.StudyMap.model_validate(studied["study_map"])
                    if studied.get("study_map")
                    else None
                )
                if kept and not S.map_problems(kept, pool.items):
                    run.study = kept
                else:
                    run.study = self.study_map(model, effort, run, pool, material, publish)
                state["study_map"] = run.study.model_dump()
            with checkpoint_stage(emit, state, checkpoint, "figures") as publish:
                run.figure_readings = self.read_figures(model, effort, run, pool, publish)
                warned = [item.ref for item in pool.items if S.warnings_of(item)]
                if warned:
                    self._science_limit(
                        run,
                        f"{len(warned)} passages have extraction warnings; a value from them is "
                        "stated only when clean text or a figure reading confirms it.",
                    )
                state["figure_readings"] = [r.model_dump() for r in run.figure_readings]
                state["limitations"] = run.limitations
            coverage_key = state["coverage_key"] = digest(self._study_text(run))
            with checkpoint_stage(emit, state, checkpoint, "coverage") as publish:
                kept_plan = (
                    S.CoveragePlan.model_validate(studied["coverage_plan"])
                    if studied.get("coverage_plan") and studied.get("coverage_key") == coverage_key
                    else None
                )
                if kept_plan and not S.coverage_problems(kept_plan, pool.items):
                    run.coverage_plan = kept_plan
                    self._stage(
                        publish, "coverage", "Planning source coverage", "done", "kept from before"
                    )
                else:
                    run.coverage_plan = self.coverage_plan(
                        model, effort, run, pool, material, publish
                    )
                state["coverage_plan"] = run.coverage_plan.model_dump()
            self.scientific_context = {**state, "purpose": "explain_study"}
            if run.study.language:
                run.language = run.study.language
        else:
            with checkpoint_stage(emit, state, checkpoint, "memo") as publish:
                if saved.get("memo"):
                    run.memo = Memo.model_validate(saved["memo"])
                    self._stage(
                        publish, "memo", "Finding what is worth saying", "done", "kept from before"
                    )
                else:
                    run.memo = self.memo(model, effort, run, material, publish)
                state["memo"] = run.memo.model_dump()
            if run.memo.language:
                run.language = run.memo.language
        return run, material, state, saved

    def make(
        self,
        model: ModelInfo,
        effort: Effort | None,
        spec: Spec,
        pool: Pool,
        notes: Notes,
        events: Events | None = None,
        *,
        fallback_title: str = "",
        purpose: str | None = None,
        fixed_outline: Sequence[Planned] | None = None,
        scope: Mapping[str, Any] | None = None,
        web: bool = False,
        resume: dict[str, Any] | None = None,
        checkpoint: Checkpoint | None = None,
        **_ignored: Any,
    ) -> BuiltOutput:
        """Reuse completed stages only when their material and request still match."""

        emit = events or _no_events
        run, material, state, saved = self._prepare(
            model, effort, spec, pool, notes, emit, purpose=purpose, fixed_outline=fixed_outline,
            scope=scope, web=web, resume=resume, checkpoint=checkpoint,
        )
        plan = run.plan
        same_science = (
            saved.get("coverage_key") == state.get("coverage_key")
            and saved.get("coverage_plan") == state.get("coverage_plan")
        )
        reuse_story = bool(
            saved.get("storyline")
            and (same_science if run.study else saved.get("memo") == state.get("memo"))
        )
        with checkpoint_stage(emit, state, checkpoint, "story") as publish:
            if reuse_story:
                schema = DeckStory if spec.kind == "slides" else ReportStory
                run.story = schema.model_validate(saved["storyline"])
                _validate_storyline(run, run.story)
                self._stage(publish, "story", "Planning the storyline", "done", "kept from before")
            else:
                run.story = self.storyline(model, effort, run, material, publish)
            state["storyline"] = run.story.model_dump()
        looked: dict[str, Any] | None = None
        snapshot: dict[str, Any] | None = None
        if spec.kind == "slides":
            reused_draft = bool(run.study and reuse_story and saved.get("draft"))
            with checkpoint_stage(emit, state, checkpoint, "write", "science") as publish:
                if reused_draft:
                    text = saved["draft"]
                    run.limitations = state["limitations"] = list(saved.get("limitations") or [])
                    run.review_status = saved.get("review_status") or "unavailable"
                    run.reviews = []
                    kinds = {
                        "scientific": S.ScientificReview,
                        "learner": S.LearnerReview,
                        "coverage": S.CoverageReview,
                    }
                    for saved_review in saved.get("reviews") or []:
                        kind = kinds.get(saved_review.get("kind"))
                        if kind is not None and saved_review.get("review") is not None:
                            run.reviews.append(kind.model_validate(saved_review["review"]))
                    self._stage(publish, "write", "Writing the slides", "done", "kept from before")
                    self._stage(
                        publish,
                        "science",
                        "Reviewing the scientific explanation",
                        "done",
                        "kept from before",
                    )
                else:
                    text = self.write_deck(model, effort, run, pool, material, emit)
                    text = self.fix_format(model, effort, run, text, emit)
                    if run.study:
                        # A resumable draft includes the scientific review of its source content.
                        text = self.scientific_gate(
                            model, effort, run, text, pool, material, publish,
                        )
                if run.study:
                    state["draft"] = text
                    state["reviews"] = []
                    for review in run.reviews or []:
                        kind = (
                            "scientific"
                            if isinstance(review, S.ScientificReview)
                            else "learner"
                            if isinstance(review, S.LearnerReview)
                            else "coverage"
                        )
                        state["reviews"].append({"kind": kind, "review": review.model_dump()})
                    state["review_status"] = run.review_status
                    self.scientific_context.update(
                        {
                            "coverage_plan": run.coverage_plan.model_dump()
                            if run.coverage_plan
                            else None,
                            "reviews": state["reviews"],
                            "review_status": run.review_status,
                        }
                    )
            checked = saved.get("checked") if reused_draft else None
            needs_refit = not checked or checked.get("refit_pending", False)
            verify_checkpoint = checkpoint if run.study else None
            with checkpoint_stage(emit, state, verify_checkpoint, "verify") as publish:
                if checked:
                    # The claims of this same draft were checked before the run stopped.
                    drafts = [
                        Draft(
                            row["text"],
                            read_verdicts(row, _marked(row["text"]).body)
                            if row.get("verdicts") is not None or row.get("issues") is not None
                            else None,
                        )
                        for row in checked["slides"]
                    ]
                    run.limitations = list(checked.get("limitations") or [])
                    state["limitations"] = run.limitations
                    run.review_status = checked.get("review_status") or run.review_status
                    self._stage(
                        publish,
                        "verify",
                        "Checking every claim against its source",
                        "done",
                        "kept from before",
                    )
                else:
                    drafts = self.verify(
                        model, run, fmt.split_slides(text), pool, publish, material,
                    )
                if run.study:
                    state["checked"] = self._checked_progress(
                        run, drafts, refit_pending=needs_refit,
                    )
            if needs_refit:
                with checkpoint_stage(emit, state, verify_checkpoint):
                    drafts = self._fit_checked(model, effort, run, drafts, pool, material)
                    if run.study:
                        state["checked"] = self._checked_progress(run, drafts, refit_pending=False)
            checked = [self._chart_fallback(d, i) for i, d in enumerate(drafts)]
            if self.design and checked:
                only = [i for i, draft in enumerate(drafts) if draft is checked[i]]
                checked = self.design_deck(model, run, checked, emit, only=only)
            drafts = checked
            title = (
                (fmt.parse_slide(drafts[0].text).title or "Slide 1")
                if drafts else (run.story.title or fallback_title)
            )

            def assemble_deck(parts: list[Draft]) -> BuiltOutput:
                _validate_count(run.story, len(parts), "assembled deck")
                if not _matches_plan(run, [d.text for d in parts]):
                    raise ModelError("The deck no longer follows the approved outline.")
                planned = [
                    Planned(slide.title or f"Slide {n}", layout=slide.layout)
                    for n, slide in enumerate((fmt.parse_slide(d.text) for d in parts), 1)
                ]
                if plan:
                    planned = [
                        replace(p, layout=s.layout) for p, s in zip(plan, planned, strict=True)
                    ]
                return assemble_output(spec, title, "", parts, pool, planned, model, effort)

            with checkpoint_stage(emit, state, None):
                built = assemble_deck(drafts)
                try:
                    built, looked, snapshot = look(
                        self, built, drafts, model=model, effort=effort, use=run.use, pool=pool,
                        assemble=assemble_deck, emit=emit, unfence=_unfence, material=material,
                    )  # fmt: skip
                except Exception:  # noqa: BLE001 - looking never fails a build
                    logger.exception("The look at the slides failed")
                    reason = "The slide review did not finish."
                    self.skipped.append(
                        {"step": "look", "label": "Slide review unavailable", "reason": reason}
                    )
                    self._stage(emit, "look", "Looking at the slides", "failed", reason)
            self._record_slide_format(run, built)
        else:
            text = self.write_report(model, effort, run, pool, material, emit)
            preamble, sections = split_document(text, "report")
            if not sections:
                raise ModelError("The report came back without sections.")
            with checkpoint_stage(emit, state, None, "verify") as publish:
                drafts = self.verify(model, run, sections, pool, publish, material)
            with checkpoint_stage(emit, state, None):
                drafts = [self._chart_fallback(d, i) for i, d in enumerate(drafts)]
                outline = list(plan) or [
                    Planned(re.sub(r"^##\s*", "", d.text.splitlines()[0]).strip()[:160])
                    for d in drafts
                ]
                title = run.story.title or fallback_title
                built = assemble_output(spec, title, preamble, drafts, pool, outline, model, effort)
                count = len(split_document(built.content, "report")[1])
                _validate_count(run.story, count, "assembled report")
                if count != len(outline):
                    raise ModelError("The assembled report does not match its outline.")
        return replace(
            built,
            approach=self._saved_approach(run, pool, len(drafts)) | {
                "question": spec.brief,
                "ref_map": {str(ref): n for n, ref in enumerate(built.pool_refs, 1)},
            },
            skill={
                "name": "studio",
                "version": 1,
                "skipped": self.skipped,
                "trail": self.trail,
                **({"look": looked} if looked else {}),
                **({"renderCheck": snapshot} if snapshot else {}),
            },
        )

    @staticmethod
    def _saved_approach(run: Run, pool: Pool, pages: int | None = None) -> dict[str, Any]:
        return {
            "method": "studio",
            # The fields an approach has everywhere (Approach.saved_as reads them).
            "question": run.spec.brief,
            "purpose": run.purpose.purpose if run.purpose else "general",
            "purpose_reason": run.purpose.reason if run.purpose else None,
            # A scientific deck's argument and checks; absent on every other artifact.
            "study_map": run.study.model_dump() if run.study else None,
            "coverage_plan": run.coverage_plan.model_dump() if run.coverage_plan else None,
            "figure_readings": [r.model_dump() for r in run.figure_readings or []]
            if run.study
            else None,
            "limitations": (run.limitations or []) if run.study else None,
            "review_status": run.review_status,
            "allowed_assets": S.allowed_assets(pool.items) if run.study else None,
            "structure": "",
            "structure_reason": "",
            "pages": pages if run.spec.kind == "slides" else None,
            "supplements": [],
            "gaps": run.study.limitations
            if run.study
            else ([t.issue for t in run.memo.tensions] if run.memo else []),
            "use": run.use or None,
            "answer": run.study.contribution
            if run.study
            else (run.memo.answer if run.memo else ""),
            "memo": run.memo.model_dump() if run.memo else None,
            "storyline": run.story.model_dump() if run.story else None,
            "reviews": [r.model_dump() for r in run.reviews or []],
        }


def _chart_rows_supported(
    tables: list[tuple[int, re.Match[str]]], coverage: Mapping[str, Any] | None
) -> bool:
    if len(tables) != 1:
        return False
    index, table = tables[0]
    rows = [
        (row_index, row)
        for row_index, line in enumerate(collapse_citations(table[0]).split("\n"))
        for row in fmt.read_table([line.strip()])[0]
    ]
    if len(rows) < 3 or not 2 <= len(rows[0][1]) <= 4:
        return False
    (_, head), *data = rows
    supported = {
        tuple(claim["tableRow"]) for claim in (coverage or {}).get("claims", [])
        if claim.get("tableRow") and claim["verdict"] == "supported"
    }
    for row_index, (label, *cells) in data:
        if (index, row_index) not in supported:
            return False
        if len(cells) != len(head) - 1 or not fmt.CITE.sub("", label).strip(" *_`"):
            return False
        if not all(
            fmt.number_cell(cell) and CITATION_GROUP.search(cell + " " + label) for cell in cells
        ):
            return False

    return True


def source_images_kept(before: str, after: str) -> bool:
    pattern = r"!\[[^\]]*\]\(gunther-asset:([A-Za-z0-9_-]+(?:#[^)\s]+)?)\)"
    return set(re.findall(pattern, before)) <= set(re.findall(pattern, after))


def replace_layout(slide: StorySlide, layout: str) -> StorySlide:
    return slide.model_copy(update={"layout": layout})


def _stating(issues, by_ref: dict[int, Evidence], most: int = 6) -> set[int]:
    """Passages that state the figures of claims flagged as having no source, so the fix can
    cite them. Small whole numbers match too much to tell; a sign is a direction a passage may
    state in words ("down 9.5%")."""

    wanted = {
        n.lstrip("-")
        for issue in issues
        if issue.verdict == "unsourced"
        for n in fmt.numbers_in(issue.claim)
        if "." in n or len(re.sub(r"\D", "", n)) >= 3
    }
    found: list[int] = []
    for ref, item in by_ref.items():
        stated = {n.lstrip("-") for n in fmt.numbers_in(item.context or item.text)}
        if ref is not None and wanted & stated:
            found.append(ref)
    return set(found[:most])


def _lend_refs(line: str, rest: str, most: int = 6) -> str:
    """The line with the citations other slides give for the same figures added (for checking
    only). A line with no figure, or whose figures no other slide cites, is left as it is."""

    plain = re.sub(r"\s*\[\d+\]", "", line)
    wanted = fmt.numbers_in(plain)
    if not wanted:
        return line
    lent: list[str] = []
    for other in rest.split("\n"):
        cited = re.findall(r"\[(\d+)\]", other)
        if cited and wanted & fmt.numbers_in(re.sub(r"\s*\[\d+\]", "", other)):
            lent += cited
    own = re.findall(r"\[(\d+)\]", line)
    extra = [n for n in dict.fromkeys(lent) if n not in own][:most]
    return line.rstrip() + "".join(f" [{n}]" for n in extra) if extra else line


__all__ = ["StudioAgents", "tokens"]
