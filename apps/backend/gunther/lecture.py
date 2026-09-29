from __future__ import annotations

from pydantic import BaseModel, Field

from gunther.llm import ModelError, ModelGateway, ModelInfo
from gunther.model_profiles import Effort
from gunther.schemas import LectureSummaryOut


class LecturePayload(BaseModel):
    overview: str
    key_points: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)


class LectureSummaryError(RuntimeError):
    """The model did not write a summary. Nothing stands in for it."""


class AILectureSummarizer:
    """Study notes from a recording's transcript, by the Analysis model."""

    def __init__(self, gateway: ModelGateway, model: ModelInfo, effort: Effort) -> None:
        self.gateway = gateway
        self.model = model
        self.effort = effort
        self.mode = model.method

    def summarize(self, title: str, transcript: str, duration_seconds: int) -> LectureSummaryOut:
        try:
            parsed, _ = self.gateway.complete_json(
                self.model,
                LecturePayload,
                system=(
                    "Turn a lecture transcript into faithful study notes. Do not add facts that "
                    "were not said. Keep disagreements and uncertainty visible. Return a concise "
                    "overview, 3-8 key points, explicit action items, unanswered questions, and "
                    "important technical terms. Preserve the transcript language."
                ),
                prompt=(
                    f"Lecture: {title}\nDuration: {duration_seconds} seconds\n\n"
                    f"Transcript:\n{transcript}"
                ),
                effort=self.effort,
            )
        except ModelError as error:
            raise LectureSummaryError(str(error)) from error
        if not parsed.overview.strip():
            raise LectureSummaryError("empty response")
        return LectureSummaryOut(**parsed.model_dump(), engine=self.model.display)


def create_lecture_summarizer(gateway: ModelGateway | None) -> AILectureSummarizer | None:
    """A model for recording summaries, or ``None`` when none is set up."""

    chosen = gateway.for_role("analysis") if gateway else None
    return AILectureSummarizer(gateway, *chosen) if gateway and chosen else None
