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
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from dataclasses import dataclass, replace
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field

from gunther import usage
from gunther.agent import Evidence
from gunther.llm import Image, ModelError, ModelInfo
from gunther.model_profiles import Effort
from gunther.outputs import (
    BuiltOutput,
    Draft,
    Events,
    Notes,
    OutputAgents,
    Planned,
    Pool,
    Spec,
    check_citations,
    figure_lines,
    split_document,
)
from gunther.skill_checks import figures_of
from gunther.studio import design as D
from gunther.studio import format as fmt
from gunther.studio import prompts as P
from gunther.studio import science as S
from gunther.studio.look import look, vision_model
from gunther.studio.reference import DECK_FORMAT, READ_NOTE, REPORT_FORMAT, STUDY_DECK_FORMAT

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
    "them 'inference' or 'nonfactual', never 'not', even when the sources do not say them."
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
    layout: str = "points"
    job: str = ""
    refs: list[int] = Field(default_factory=list)
    purpose: str = ""
    role: str = ""
    figures: list[S.ClaimFigure] = Field(default_factory=list)
    takeaway: str = ""


class DeckStory(BaseModel):
    title: str
    subtitle: str = ""
    slides: list[StorySlide] = Field(min_length=2)


class StorySection(BaseModel):
    heading: str
    job: str = ""
    exhibits: list[str] = Field(default_factory=list)
    refs: list[int] = Field(default_factory=list)


class ReportStory(BaseModel):
    title: str
    summary_heading: str = ""
    summary_points: list[str] = Field(default_factory=list)
    sections: list[StorySection] = Field(min_length=1)


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


def _no_events(_event: dict[str, Any]) -> None:
    return None


def _in_context(function):
    """Run ``function`` in a copy of this thread's context (traces, budgets) on a worker."""

    context = contextvars.copy_context()
    return lambda *args: context.copy().run(function, *args)


def material_request(material: str, task: str) -> tuple[str, str]:
    """One opening for every step reading the same build's material."""

    return P.STUDIO_BASE, "The material:\n\n" + material + P.TASK_BOUNDARY + task


def warm_map(function, items, workers: int):
    """Finish the first item before submitting the rest, preserving context and order."""

    items = list(items)
    if not items:
        return []
    first = function(items[0])
    if len(items) == 1:
        return [first]
    with ThreadPoolExecutor(workers) as pool:
        return [first, *pool.map(_in_context(function), items[1:])]


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
    """Makes a report or a deck with the studio method. Checking reuses OutputAgents."""

    def __init__(
        self,
        gateway,
        limits=None,
        *,
        use: str | None = None,
        tool=None,
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
        self.tool = tool
        self.trail: list[dict[str, Any]] = []
        self._started: dict[str, float] = {}
        self._active_stages: list[str] = []
        self._stage_calls: dict[str, int] = {}
        self._counter_lock = Lock()
        self.reusable_figures = list(reusable_figures or [])

    # Plumbing -----------------------------------------------------------------------------

    def _stage(self, emit: Events, step: str, label: str, state: str, detail: str = "") -> None:
        event: dict[str, Any] = {"type": "stage", "id": step, "label": label, "state": state}
        if detail:
            event["detail"] = detail
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

    def _text(self, model, system, prompt, effort, max_tokens=16_000) -> str:
        if self._active_stages:
            self._count_model_call(self._active_stages[-1])
        # A thinking model spends its budget thinking first: never ask for less than this.
        room = max(max_tokens, 16_000)
        try:
            completion = self.gateway.complete(
                model, system=system, messages=prompt, effort=effort, max_tokens=room
            )
        except ModelError as error:
            if "ran out of room" not in str(error) or effort in (None, "off", "low"):
                raise
            logger.info("Out of room while thinking; asking again with less thinking")
            self._count_model_call()
            completion = self.gateway.complete(
                model, system=system, messages=prompt, effort="low", max_tokens=room
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

    def read(self, model, run: Run, pool: Pool, notes: Notes, emit: Events) -> str:
        """The material as the memo step reads it: whole when it fits, else notes per part."""

        items = sorted(pool.items, key=lambda item: item.ref)
        whole = self._material(items)
        extra = self._notes_text(notes)
        if tokens(whole) <= DIRECT_TOKENS:
            return extra + whole
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
        # Keep raw passages too: writers and checks previously fetched them after
        # reading notes. No step may lose evidence to make its opening match.
        return (
            extra
            + "Sources: "
            + "; ".join(sources)
            + "\n\nNotes from the material:\n"
            + "\n".join(lines)
            + "\n\n"
            + whole
        )

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
        revisions: dict[str, str] = {}
        for item in pool.items:
            for figure in figures_of(item):
                revisions.setdefault(figure["assetId"], figure.get("revisionId") or "")
        self._stage(emit, "figures", "Looking at the figures", "running")

        def read(asset_id: str) -> S.FigureReading | None:
            reusable = next(
                (
                    row
                    for row in self.reusable_figures
                    if (row.get("asset_id"), row.get("source_revision_id"), row.get("vision_model"))
                    == (asset_id, revisions.get(asset_id), eye.ref)
                ),
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
            passages = [
                e for e in pool.items if any(f["assetId"] == asset_id for f in figures_of(e))
            ]
            # The study map step already bounded these IDs to the chosen passages and revision.
            try:
                path, media, _name = self.asset_reader(asset_id)
                self._count_model_call("figures")
                reading, _ = self.gateway.complete_json(
                    eye,
                    S.FigureReading,
                    system=P.FIGURE_READ,
                    prompt=f"asset_id: {asset_id}\nLinked claims: "
                    + "\n".join(
                        c.model_dump_json()
                        for c in run.study.claims
                        if any(f.asset_id == asset_id for f in c.figures)
                    )
                    + "\nCaption, provenance and linked passages:\n"
                    + self._material(passages),
                    images=[Image.from_path(path, media)],
                    effort=eye_effort or "low",
                    schema_at="end",
                )
                if reading.asset_id != asset_id:
                    raise ValueError("The figure reading returned a different asset identity.")
                reading.source_revision_id = revisions.get(asset_id, "")
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
        return self._json(model, S.ScientificReview, *material_request(material, task), "medium")

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
                effort="medium",
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
            return self._json(model, S.CoverageReview, *material_request(material, task), "medium")
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
                self._text(model, *material_request(material, task), effort, 24_000)
            )
        except ModelError as error:
            logger.info("The targeted scientific revision failed: %s", error)
            return rejected(str(error))

        pieces = fmt.split_slides(revised)
        if structural:
            if len(pieces) < 2:
                return rejected(f"revision returned {len(pieces)} slides")
            candidate = self.fix_format(model, effort, run, revised, _no_events)
            if not source_images_kept(deck, candidate):
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

        Content is settled here, before design. Revision is targeted unless the reviewer finds a
        structural omission; an unavailable review or remaining content problem is never ready."""

        assert run.study is not None
        step, label = "science", "Reviewing the scientific explanation"
        deck = S.apply_source_cover(deck, pool.items)
        self._stage(emit, step, label, "running")
        if run.reviews is None:
            run.reviews = []
        review: S.ScientificReview | None = None
        comparison: S.CoverageReview | None = None
        learner: S.LearnerReview | None = None
        code: list[str] = []
        for round_ in range(2):
            try:
                review = self.scientific_review(model, run, deck, material)
            except ModelError as error:
                logger.info("The scientific review failed: %s", error)
                review = None
                self.skipped.append(
                    {"step": step, "label": "Scientific review unavailable", "reason": str(error)}
                )
            code = S.code_problems(
                run.study, run.figure_readings or [], deck, pool.items, plan=run.coverage_plan
            )
            for problem in S.cover_problems(deck, pool.items):
                self._science_limit(run, problem)
            if review is not None:
                run.reviews.append(review)
                emit({"type": "review", "round": round_ + 1, **review.model_dump()})
            learner, _images = self._learner_review(model, effort, run, deck, pool, emit)
            comparison = (
                self._compare_learner(model, run, deck, learner, material)
                if learner is not None
                else None
            )
            if comparison is not None:
                run.reviews.append(comparison)
                emit(
                    {
                        "type": "coverage_comparison",
                        "round": round_ + 1,
                        **comparison.model_dump(),
                    }
                )
            content_problems: list[S.ScientificProblem | str] = list(
                review.problems or [] if review is not None else []
            )
            if comparison is not None:
                content_problems += comparison.coverage_problems + comparison.learner_problems
            content_problems += code
            ready = bool(
                comparison
                and comparison.verdict == "ready"
                and not comparison.coverage_problems
                and not comparison.learner_problems
                and review is not None
                and review.verdict == "ready"
                and not review.problems
                and not code
            )
            if ready or round_ == 1:
                break
            deck = self._revise_science(model, effort, run, deck, pool, material, content_problems)
            deck = S.apply_source_cover(deck, pool.items)
            code = S.code_problems(
                run.study, run.figure_readings or [], deck, pool.items, plan=run.coverage_plan
            )
            for problem in S.cover_problems(deck, pool.items):
                self._science_limit(run, problem)
        if code:
            reason = "; ".join(code)
            self._science_limit(run, reason)
            self._stage(emit, step, label, "failed", reason)
            raise ModelError("The deck still breaks a scientific rule after revision: " + reason)
        unavailable = review is None or learner is None or comparison is None
        open_problems: list[S.ScientificProblem] = []
        if review is not None:
            open_problems += review.problems
        if comparison is not None:
            open_problems += comparison.coverage_problems + comparison.learner_problems
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
        elif (review and review.verdict != "ready") or (
            comparison
            and (comparison.verdict != "ready" or open_problems)
        ):
            run.review_status = "open_problems"
            for problem in open_problems:
                self._science_limit(run, f"Review: {problem.where}: {problem.problem}")
            if learner and learner.unexplained:
                self._science_limit(
                    run,
                    "Independent learner could not explain: " + "; ".join(learner.unexplained),
                )
            self._stage(emit, step, label, "done", "open problems recorded")
        else:
            run.review_status = "ready"
            self._stage(emit, step, label, "done", "ready")
        return deck


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
        spec = run.spec
        brief = spec.brief
        minutes = re.search(r"(\d+)\s*(?:-|\s)?\s*(?:min|minute|分钟)", brief, re.I)
        pages = re.search(r"(\d+)\s*(?:slides|pages|页|张)", brief, re.I)
        if spec.kind == "slides":
            if pages:
                return f"exactly {pages.group(1)} slides, as the brief asks"
            if run.study:
                duration = f" within {minutes.group(1)} minutes" if minutes else ""
                return (
                    "as many slides as needed to teach the central method and source figures"
                    + duration
                    + "; split complex figures into explanatory sequences, never shrink evidence"
                )
            if minutes:
                count = max(5, min(20, round(int(minutes.group(1)) * 0.9)))
                return f"about {count} slides for {minutes.group(1)} minutes"
            return "as many slides as the argument needs, usually 8–12; never pad"
        words = re.search(r"(\d+)\s*(?:words|字)", brief, re.I)
        if words:
            return f"about {words.group(1)} words"
        return "as long as the argument needs, usually 1,500–3,000 words in 4–7 sections"

    def storyline(self, model, effort, run: Run, material: str, emit: Events):
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
        figures = self._figures(material)
        system, prompt = material_request(material, system + figures)
        story = self._json(model, schema, system, prompt, effort)
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
                    "medium",
                )
            except ModelError as error:
                logger.info("Revising the storyline failed: %s", error)
                break
        if isinstance(story, DeckStory):
            story = self._tidy_deck_story(story)
        self._stage(emit, "story", "Planning the storyline", "done", story.title)
        emit({"type": "storyline", "storyline": story.model_dump()})
        # The planned titles, as the live outline the app already shows while a build runs.
        planned = (
            [{"heading": s.title, "goal": s.job} for s in story.slides]
            if isinstance(story, DeckStory)
            else [{"heading": story.summary_heading or "Summary", "goal": ""}]
            + [{"heading": s.heading, "goal": s.job} for s in story.sections]
        )
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
        return DeckStory(title=story.title, subtitle=story.subtitle, slides=slides)

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
                model, Review, *material_request(material, system + "\n\nReview it now."), "medium"
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
        system, prompt = material_request(material, system + self._figures(material))
        text = self._text(model, system, prompt, effort, max_tokens=24_000)
        deck = _unfence(text)
        slides = fmt.split_slides(deck)
        if len(slides) < len(story.slides):
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
        """Each slide that breaks the format is written again on its own, all such slides at
        once, for at most three rounds; a slide that still breaks it keeps its words and is
        drawn by the renderer's fallback."""

        read = run.use == "read"
        notes = run.use == "talk"
        guide = (STUDY_DECK_FORMAT if run.study else DECK_FORMAT) + (
            "\n\n" + READ_NOTE if read else ""
        )
        for _ in range(2):
            found = fmt.check_deck(
                deck, read=read, notes_required=notes, scientific=bool(run.study)
            )
            if not found:
                break
            slides = fmt.split_slides(deck)

            def fix(item: tuple[int, list[str]], slides: list[str] = slides) -> str | None:
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
                    again = fmt.split_slides(
                        _unfence(self._text(model, system, prompt, effort, 4000))
                    )
                except ModelError as error:
                    logger.info("Fixing slide %d failed: %s", n, error)
                    return None
                if not again:
                    return None
                text = fmt.strip_labels(again[0])
                if not source_images_kept(slides[n - 1], text):
                    return None
                if n == 1:
                    text = self._title_marker(
                        [text], fmt.parse_slide(slides[0]).attrs.get("theme", "paper")
                    )[0]
                return text

            fixed = warm_map(fix, found, WORKERS)
            changed = False
            for (n, _), text in zip(found, fixed, strict=True):
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
            left = fmt.check_deck(
                deck, read=read, notes_required=run.use == "talk", scientific=bool(run.study)
            )
        if left:
            self.skipped.append(
                {
                    "step": "format",
                    "label": f"{len(left)} slide(s) still break the format",
                    "reason": "; ".join(f"slide {n}: {p[0]}" for n, p in left)[:600],
                }
            )
        return deck

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
            length=f"Length: {self._length(run)}.",
            language=P.LANGUAGE.format(language=run.language),
            format=REPORT_FORMAT,
            memo=self._memo_text(run.memo) if run.memo else "",
            story=story_text,
        )
        system, prompt = material_request(material, system + self._figures(material))
        text = _unfence(self._text(model, system, prompt, effort, max_tokens=24_000))
        if not text.lstrip().startswith("<!-- report: v2 -->"):
            text = "<!-- report: v2 -->\n" + text.lstrip()
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
                if fixed.count("\n## ") >= text.count("\n## ") - 1:
                    text = fixed
            except ModelError as error:
                logger.info("Fixing the report's form failed: %s", error)
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

        self._stage(emit, "verify", "Checking every claim against its source", "running")
        by_ref = pool.by_ref()

        rest = "\n".join(parts[1:])

        def check(text: str) -> Draft:
            text, _ = check_citations(text, set(by_ref))
            issues, coverage = self._claims(model, text, by_ref, rest, material=material)
            issues += self._chart_issues(text, by_ref)
            return Draft(text, issues, coverage)

        def check_and_fix(text: str) -> Draft:
            draft = check(text)
            if not any(issue.verdict in FIXABLE for issue in draft.issues or []):
                return draft
            title = fmt.MARK.match(text) and fmt.parse_slide(text).layout == "title"
            fixed = self._fix_claims(
                model, [draft], by_ref, rest if title else "", material=material
            )
            return check(fixed[0]) if fixed else draft

        drafts = warm_map(check_and_fix, parts, WORKERS)
        for index, draft in enumerate(drafts):
            if any(issue.note.startswith("Chart value") for issue in draft.issues or []):
                # A chart whose values its sources do not state is drawn as a table.
                drafts[index] = Draft(
                    fmt.with_marker(draft.text, "table"), draft.issues, draft.coverage
                )
                self.skipped.append(
                    {
                        "step": "verify",
                        "label": f"Part {index + 1} drawn as a table",
                        "reason": "Some chart values were not found in their cited sources.",
                    }
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
            "Checking every claim against its source",
            "done",
            f"{left} claim(s) still flagged" if left else "all claims supported",
        )
        return drafts

    def _claims(
        self,
        model,
        text: str,
        by_ref: dict[int, Evidence],
        rest: str = "",
        *,
        material: str | None = None,
    ):
        """One claim check per part. A title is checked against the passages its own slide or
        section cites (it states their conclusion). The title slide states the whole deck's
        conclusion, so a figure on it is checked against the passages the other slides (``rest``)
        cite for that same figure, not only its own. Interpretation and recommendation that
        follow from cited facts are the author's judgement, not findings; what is flagged is a
        cited claim its passages do not state, and a specific fact (a number) with no source."""

        from gunther.outputs import Issue, _marked, section_hash

        marked = _marked(text)
        body = marked.body
        lines = body.split("\n")
        if marked.layout == "title":
            # The title slide's meta line (audience · date) states no claim.
            content = [n for n, line in enumerate(lines) if line.strip()]
            if len(content) >= 3:
                lines[content[2]] = ""
            lines = [_lend_refs(line, rest) for line in lines]
        refs = "".join(f"[{n}]" for n in dict.fromkeys(re.findall(r"\[(\d+)\]", body)))
        for at, line in enumerate(lines):
            if re.match(r"^#{1,2}\s", line):
                if refs and not re.search(r"\[\d+\]", line):
                    lines[at] = line.rstrip() + " " + refs
                break
        checked = "\n".join(lines)
        if self.scientific_context:
            # A numbered step's own number is not a claim to source.
            checked = re.sub(r"(?m)^([ \t]*)\d+[.)]\s+", r"\1", checked)
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
        _, check, _, _ = checker.check_sentences(
            model,
            STUDIO_CHECK + ("\n" + P.SCIENCE_TRUTH if self.scientific_context else ""),
            checked,
            by_ref,
            limit=self.limits.sentences,
        )
        coverage = (
            check._coverage
            if check is not None
            else {"status": "failed", "considered": 0, "checked": 0, "unchecked": 0, "claims": []}
        )
        coverage["answerHash"] = section_hash(text)
        issues = []
        for claim in coverage.get("claims", []):
            verdict, sentence = claim["verdict"], claim["text"]
            plain = re.sub(r"\s*\[(?:\?|\d+)\]", "", sentence).strip()
            # An image reference is provenance, not a claim: its asset id and crop coordinates
            # carry digits that would otherwise read as an uncited number. The caption stays.
            said = re.sub(r"!\[([^\]]*)\]\(gunther-asset:[^)]*\)", r"\1", plain)
            if verdict in ("not", "partly"):
                issues.append(Issue(plain[:300], "unsupported", claim.get("reason") or ""))
            elif verdict == "unsourced" and re.search(r"\d", said):
                issues.append(Issue(plain[:300], "unsourced", "A specific fact with no source."))
        # A number with no citation on its line (a recommendation repeating a figure, a table
        # row): the claim fix cites the passage it comes from, or drops the number.
        held = {issue.claim[:80] for issue in issues}
        for line in fmt.uncited_lines([ln for ln in body.split("\n") if not ln.startswith(":::")]):
            plain = re.sub(r"\s*\[(?:\?|\d+)\]", "", line).strip()
            if plain[:80] not in held and not re.match(r"^(Source|Sources|来源)\s*[:：]", plain):
                issues.append(Issue(plain[:300], "unsourced", "A number with no citation."))
        return issues, coverage

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
            return drafts
        line = line.splitlines()[0].strip() if line else ""
        line = re.sub(r"^(Subtitle|副标题)\s*[:：]\s*", "", line)
        if not line or line == body[0] or line.startswith("#"):
            return drafts
        text = drafts[0].text.replace(body[0], line, 1)
        by_ref = pool.by_ref()
        text, _ = check_citations(text, set(by_ref))
        issues, coverage = self._claims(model, text, by_ref, rest, material=material)
        if any(i.verdict in ("unsupported", "contradicted") for i in issues):
            logger.info("The aligned subtitle is not supported; keeping the old one")
            return drafts
        return [Draft(text, issues + self._chart_issues(text, by_ref), coverage), *drafts[1:]]

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
        fitted = fmt.split_slides(self.fix_format(model, effort, run, joined, _no_events))
        if len(fitted) != len(drafts):
            return drafts
        changed = [i for i, (d, t) in enumerate(zip(drafts, fitted, strict=True)) if d.text != t]
        by_ref = pool.by_ref()

        rest = "\n".join(fitted[1:])

        def check(text: str) -> Draft:
            issues, coverage = self._claims(model, text, by_ref, rest, material=material)
            return Draft(text, issues + self._chart_issues(text, by_ref), coverage)

        again = warm_map(check, [fitted[i] for i in changed], WORKERS)
        for index, draft in zip(changed, again, strict=True):
            drafts[index] = draft
        return drafts

    @staticmethod
    def _chart_issues(text: str, by_ref: dict[int, Evidence]):
        from gunther.outputs import Issue

        issues = []
        for value, refs in fmt.chart_numbers(text):
            sources = " ".join((by_ref[r].context or by_ref[r].text) for r in refs if r in by_ref)
            if not refs or fmt.normal_number(value) not in fmt.numbers_in(sources):
                issues.append(
                    Issue(value, "unsupported", "Chart value not found in its cited source.")
                )
        return issues

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
            text = _unfence(self._text(model, system, prompt, "medium", 16_000))
        except ModelError as error:
            logger.info("Fixing unsupported claims failed: %s", error)
            return None
        pieces = [p.strip() for p in re.split(r"\n-{3,}\n", "\n" + text + "\n") if p.strip()]
        if len(pieces) != len(drafts):
            logger.info("Claim fix returned %d parts for %d", len(pieces), len(drafts))
            if len(drafts) == 1:
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

    # 4. Designing -------------------------------------------------------------------------

    def design_deck(self, model, run: Run, drafts: list[Draft], emit: Events) -> list[Draft]:
        """The deck's style, then every slide designed on its own. The words are final by now:
        a design may not change one. It never fails a build: whatever goes wrong, the slides
        stay as they are and the template draws them."""

        label = "Designing the slides"
        self._stage(emit, "design", label, "running")
        try:
            slides = [d.text for d in drafts]
            first = fmt.parse_slide(slides[0])
            style = D.pick_style(
                self,
                model,
                title=first.title,
                subtitle=first.body[0] if first.body else "",
                brief=run.spec.brief,
                audience=run.spec.audience,
            )
            designed, count, notes = D.design_slides(
                self, model, D.apply_style(slides, style), style, workers=WORKERS
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
        resume: dict[str, Any] | None = None,
        checkpoint: Any = None,
        **_ignored: Any,
    ) -> BuiltOutput:
        """``resume``: what an earlier, failed run of the same request finished (its memo and
        storyline), used when the pool still has the same passages; ``checkpoint(state)`` hears
        that state after each of those steps, so a failure later does not lose them."""

        emit = events or _no_events
        if not pool.items:
            raise ModelError("There is no material to work from: choose some sources first.")
        use = self.use or ("read" if re.search(READ_HINT, spec.brief, re.I) else "talk")
        run = Run(spec, use if spec.kind == "slides" else "", self._language(spec, pool))
        printed = S.pool_print(pool.items)
        saved = (
            resume
            if resume
            and resume.get("passages") == len(pool)
            and resume.get("brief") == spec.brief
            and resume.get("pool", printed) == printed
            else {}
        )
        state: dict[str, Any] = {
            "passages": len(pool),
            "pool": printed,
            "kind": spec.kind,
            "brief": spec.brief,
        }
        # The purpose is the model's decision, from the brief and a short look at the material.
        if spec.kind == "slides":
            if saved.get("purpose"):
                run.purpose = S.Purpose.model_validate(saved["purpose"])
            else:
                self._stage(emit, "purpose", "Understanding the request", "running")
                run.purpose = self._json(
                    model,
                    S.Purpose,
                    P.PURPOSE,
                    f"Brief: {spec.brief or '(none)'}\nThis deck's purpose so far: "
                    + json.dumps((resume or {}).get("previous_purpose"))
                    + "\nMaterial overview:\n"
                    + S.overview(pool.items),
                    "low",
                )
                self._stage(
                    emit, "purpose", "Understanding the request", "done", run.purpose.reason
                )
            state["purpose"] = run.purpose.model_dump()
            emit({"type": "purpose", **state["purpose"]})
            if checkpoint:
                checkpoint(dict(state))
        material = self.read(model, run, pool, notes, emit)
        if run.purpose and run.purpose.purpose == "explain_study":
            run.limitations = list(saved.get("limitations") or [])
            kept = S.StudyMap.model_validate(saved["study_map"]) if saved.get("study_map") else None
            if kept and not S.map_problems(kept, pool.items):
                run.study = kept
            else:
                run.study = self.study_map(model, effort, run, pool, material, emit)
            state["study_map"] = run.study.model_dump()
            if checkpoint:
                checkpoint(dict(state))
            revisions = {
                figure["assetId"]: figure.get("revisionId") or ""
                for item in pool.items
                for figure in figures_of(item)
            }
            if "figure_readings" in saved:
                run.figure_readings = []
                for row in saved["figure_readings"]:
                    try:
                        reading = S.FigureReading.model_validate(row)
                    except ValueError:
                        continue
                    if (
                        reading.asset_id in revisions
                        and (
                            not revisions.get(reading.asset_id)
                            or reading.source_revision_id
                            == revisions[reading.asset_id]
                        )
                        and reading.vision_model in ("", model.ref)
                    ):
                        run.figure_readings.append(reading)
            else:
                run.figure_readings = self.read_figures(model, effort, run, pool, emit)
                warned = [item.ref for item in pool.items if S.warnings_of(item)]
                if warned:
                    self._science_limit(
                        run,
                        f"{len(warned)} passages have extraction warnings; a value from them is "
                        "stated only when clean text or a figure reading confirms it.",
                    )
            state["figure_readings"] = [r.model_dump() for r in run.figure_readings]
            state["limitations"] = run.limitations
            kept_plan = (
                S.CoveragePlan.model_validate(saved["coverage_plan"])
                if saved.get("coverage_plan")
                else None
            )
            if kept_plan and not S.coverage_problems(kept_plan, pool.items):
                run.coverage_plan = kept_plan
                self._stage(
                    emit, "coverage", "Planning source coverage", "done", "kept from before"
                )
            else:
                run.coverage_plan = self.coverage_plan(
                    model, effort, run, pool, material, emit
                )
            state["coverage_plan"] = run.coverage_plan.model_dump()
            self.scientific_context = {**state, "purpose": "explain_study"}
            if checkpoint:
                checkpoint(dict(state))
            if run.study.language:
                run.language = run.study.language
        else:
            if saved.get("memo"):
                run.memo = Memo.model_validate(saved["memo"])
                self._stage(
                    emit, "memo", "Finding what is worth saying", "done", "kept from before"
                )
            else:
                run.memo = self.memo(model, effort, run, material, emit)
            state["memo"] = run.memo.model_dump()
            if checkpoint:
                checkpoint(dict(state))
            if run.memo.language:
                run.language = run.memo.language
        same_science = (
            saved.get("study_map") == state.get("study_map")
            and saved.get("coverage_plan") == state.get("coverage_plan")
        )
        reuse_story = bool(
            saved.get("storyline")
            and (same_science if run.study else saved.get("memo") == state.get("memo"))
        )
        if reuse_story:
            schema = DeckStory if spec.kind == "slides" else ReportStory
            run.story = schema.model_validate(saved["storyline"])
            self._stage(emit, "story", "Planning the storyline", "done", "kept from before")
        else:
            run.story = self.storyline(model, effort, run, material, emit)
        state["storyline"] = run.story.model_dump()
        if checkpoint:
            checkpoint(dict(state))
        looked: dict[str, Any] | None = None
        snapshot: dict[str, Any] | None = None
        if spec.kind == "slides":
            if run.study and reuse_story and saved.get("draft"):
                text = S.apply_source_cover(saved["draft"], pool.items)
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
                self._stage(emit, "write", "Writing the slides", "done", "kept from before")
                self._stage(
                    emit,
                    "science",
                    "Reviewing the scientific explanation",
                    "done",
                    "kept from before",
                )
            else:
                text = self.write_deck(model, effort, run, pool, material, emit)
                text = self.fix_format(model, effort, run, text, emit)
                if run.study:
                    # Content is settled before claim checking and visual design.
                    text = self.scientific_gate(model, effort, run, text, pool, material, emit)
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
                    if checkpoint:
                        checkpoint(dict(state))
            parts = fmt.split_slides(text)
            drafts = self.verify(model, run, parts, pool, emit, material)
            if not run.study:
                # The subtitle restates what the action titles claim; a scientific deck's
                # titles may name topics, so its subtitle stays as written and reviewed.
                drafts = self._cover(model, drafts, pool, material)
            drafts = self._refit(model, effort, run, drafts, pool, material)
            if run.study:
                deck = "\n\n---\n\n".join(d.text for d in drafts)
                left = S.code_problems(
                    run.study,
                    run.figure_readings or [],
                    deck,
                    pool.items,
                    plan=run.coverage_plan,
                )
                for problem in S.cover_problems(deck, pool.items):
                    self._science_limit(run, problem)
                if left:
                    raise ModelError("The checked deck broke a scientific rule: " + "; ".join(left))
            if self.design and drafts:
                drafts = self.design_deck(model, run, drafts, emit)
            outline = [
                Planned(
                    fmt.parse_slide(d.text).title or f"Slide {n}",
                    layout=fmt.parse_slide(d.text).layout,
                )
                for n, d in enumerate(drafts, 1)
            ]
            title = outline[0].heading if outline else (run.story.title or fallback_title)

            def assemble_deck(parts: list[Draft]) -> BuiltOutput:
                planned = [
                    Planned(
                        fmt.parse_slide(d.text).title or f"Slide {n}",
                        layout=fmt.parse_slide(d.text).layout,
                    )
                    for n, d in enumerate(parts, 1)
                ]
                return self.assemble(spec, title, "", parts, pool, planned, model, effort)

            built = assemble_deck(drafts)
            try:
                built, looked, snapshot = look(
                    self, built, drafts, model=model, effort=effort, use=run.use, pool=pool,
                    assemble=assemble_deck, emit=emit, unfence=_unfence, material=material,
                )  # fmt: skip
            except Exception as error:  # noqa: BLE001 - looking never fails a build
                logger.warning("The look at the slides failed: %s", error)
        else:
            text = self.write_report(model, effort, run, pool, material, emit)
            preamble, sections = split_document(text, "report")
            if not sections:
                raise ModelError("The report came back without sections.")
            drafts = self.verify(model, run, sections, pool, emit, material)
            # A claim fix may rewrite an exhibit: its caption and rows cite their sources again.
            drafts = [
                replace(
                    d,
                    text=fmt.cite_exhibit_rows(fmt.cite_captions(fmt.table_separators(d.text))),
                )
                for d in drafts
            ]
            # A sentence repeating a figure cited elsewhere in the report cites it too.
            # (Sections hold blank lines themselves: they are joined on a line of their own.)
            cut = "\n\n<!-- section break -->\n\n"
            whole = cut.join(d.text for d in drafts)
            fixed = fmt.cite_repeated_numbers(whole).split(cut)
            if len(fixed) == len(drafts):
                drafts = [replace(d, text=t) for d, t in zip(drafts, fixed, strict=True)]
            # What is still uncited cites the passages that state its figures, preferring
            # those its own section cites.
            stated = {r: (e.context or e.text) for r, e in pool.by_ref().items() if r is not None}
            drafts = [replace(d, text=fmt.cite_stated_numbers(d.text, stated)) for d in drafts]
            outline = [
                Planned(re.sub(r"^##\s*", "", d.text.splitlines()[0]).strip()[:160]) for d in drafts
            ]
            title = run.story.title or fallback_title
            built = self.assemble(spec, title, preamble, drafts, pool, outline, model, effort)
        approach = {
            "method": "studio",
            # The fields an approach has everywhere (Approach.saved_as reads them).
            "question": spec.brief,
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
            "pages": len(drafts) if spec.kind == "slides" else None,
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
            "ref_map": {str(ref): n for n, ref in enumerate(built.pool_refs, 1)},
        }
        return replace(
            built,
            approach=approach,
            skill={
                "name": "studio",
                "version": 1,
                "skipped": self.skipped,
                "trail": self.trail,
                **({"look": looked} if looked else {}),
                **({"renderCheck": snapshot} if snapshot else {}),
            },
        )


def source_images_kept(before: str, after: str) -> bool:
    pattern = r"!\[[^\]]*\]\(gunther-asset:([A-Za-z0-9_-]+)\)"
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


def _unfence(text: str) -> str:
    text = text.strip()
    fenced = re.match(r"\A```[a-z]*\n(?P<body>.*)\n```\s*\Z", text, re.S)
    return fenced.group("body") if fenced else text


__all__ = ["StudioAgents", "tokens"]
