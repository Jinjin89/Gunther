"""Studio request state, draft response schemas and outline checks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from gunther.llm import ModelError
from gunther.output_types import SHAPES, OutputType, output_type_for
from gunther.outputs import Planned, Spec, heading_of
from gunther.studio import science as S


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

    def parts(self, summary_box: bool) -> list[StorySection]:
        if not summary_box:
            return self.sections
        summary = StorySection(
            heading=self.summary_heading.strip() or "Summary", id=self.summary_id,
            job=self.summary_job,
        )
        return [summary, *self.sections]


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

    @property
    def output_type(self) -> OutputType:
        return output_type_for(self.spec.kind, self.spec.output_type)

    @property
    def model_language(self) -> str | None:
        analysis = self.study or self.memo
        return analysis.language if analysis else None


def _no_events(_event: dict[str, Any]) -> None:
    return None


def _matches_plan(run: Run, parts: Sequence[str]) -> bool:
    return not run.plan or [heading_of(part) for part in parts] == [p.heading for p in run.plan]


def _validate_count(run: Run, story: DeckStory | ReportStory, actual: int, stage: str) -> None:
    unit = SHAPES[run.output_type.shape].unit_label
    if actual < 1:
        raise ModelError(f"The {stage} has no {unit}s.")
    count = story.requested_count
    if count is not None and actual != count:
        raise ModelError(f"The {stage} has {actual} {unit}s. The brief asks for {count}.")


def _validate_storyline(run: Run, story: DeckStory | ReportStory) -> None:
    parts = (
        story.slides if isinstance(story, DeckStory) else story.parts(run.output_type.summary_box)
    )
    _validate_count(run, story, len(run.plan or parts), "outline")


def _keeps_heading(run: Run, original: str, repaired: Sequence[str]) -> bool:
    return not run.plan or (len(repaired) == 1 and heading_of(repaired[0]) == heading_of(original))
