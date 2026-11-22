"""Studio request state, draft response schemas and outline checks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from gunther.llm import ModelError
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
