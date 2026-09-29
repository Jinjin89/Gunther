from __future__ import annotations

from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

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
    def __init__(
        self,
        api_key: str,
        model: str,
        mode: Literal["deepseek", "openai"],
        base_url: str | None = None,
    ) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.mode = mode

    def summarize(self, title: str, transcript: str, duration_seconds: int) -> LectureSummaryOut:
        try:
            response = self.client.responses.parse(
                model=self.model,
                store=False,
                instructions=(
                    "Turn a lecture transcript into faithful study notes. Do not add facts that "
                    "were not said. Keep disagreements and uncertainty visible. Return a concise "
                    "overview, 3-8 key points, explicit action items, unanswered questions, and "
                    "important technical terms. Preserve the transcript language."
                ),
                input=(
                    f"Lecture: {title}\nDuration: {duration_seconds} seconds\n\n"
                    f"Transcript:\n{transcript}"
                ),
                text_format=LecturePayload,
            )
        except Exception as error:
            raise LectureSummaryError(type(error).__name__) from error
        parsed = response.output_parsed
        if parsed is None or not parsed.overview.strip():
            raise LectureSummaryError("empty response")
        return LectureSummaryOut(**parsed.model_dump(), engine=self.mode)


def create_lecture_summarizer(
    openai_api_key: str | None,
    openai_model: str,
    deepseek_api_key: str | None,
    deepseek_model: str,
    deepseek_base_url: str,
) -> AILectureSummarizer | None:
    """A model for recording summaries, or ``None`` when there is no key."""

    if openai_api_key:
        return AILectureSummarizer(openai_api_key, openai_model, "openai")
    if deepseek_api_key:
        return AILectureSummarizer(
            deepseek_api_key,
            deepseek_model,
            "deepseek",
            deepseek_base_url,
        )
    return None
