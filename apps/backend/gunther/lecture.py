from __future__ import annotations

import re
from typing import Literal, Protocol

from openai import OpenAI
from pydantic import BaseModel, Field

from gunther.schemas import LectureSummaryOut


class LecturePayload(BaseModel):
    overview: str
    key_points: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)


class LectureSummarizer(Protocol):
    mode: Literal["local", "deepseek", "openai"]

    def summarize(
        self, title: str, transcript: str, duration_seconds: int
    ) -> LectureSummaryOut: ...


def _sentences(transcript: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?。！？])\s+|\n+", transcript)
    return [re.sub(r"\s+", " ", chunk).strip(" -•\t") for chunk in chunks if chunk.strip()]


class LocalLectureSummarizer:
    mode: Literal["local"] = "local"

    def summarize(self, title: str, transcript: str, duration_seconds: int) -> LectureSummaryOut:
        del title, duration_seconds
        sentences = _sentences(transcript)
        overview = " ".join(sentences[:2])[:900]
        key_points = [sentence[:320] for sentence in sentences[:6]]
        actions = [
            sentence[:320]
            for sentence in sentences
            if re.search(
                r"\b(should|must|next|action|homework|practice)\b|需要|应该|下一步|作业|练习",
                sentence,
                re.I,
            )
        ][:5]
        questions = [
            sentence[:320] for sentence in sentences if "?" in sentence or "？" in sentence
        ][:5]
        term_candidates = re.findall(
            r"\b[A-Z][A-Z0-9-]{1,11}\b|\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b",
            transcript,
        )
        terms = list(dict.fromkeys(term_candidates))[:12]
        return LectureSummaryOut(
            overview=overview or transcript[:900],
            key_points=key_points,
            action_items=actions,
            open_questions=questions,
            terms=terms,
            engine=self.mode,
        )


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
        self.fallback = LocalLectureSummarizer()

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
            parsed = response.output_parsed
            if parsed is None:
                return self.fallback.summarize(title, transcript, duration_seconds)
            return LectureSummaryOut(**parsed.model_dump(), engine=self.mode)
        except Exception:
            return self.fallback.summarize(title, transcript, duration_seconds)


def create_lecture_summarizer(
    openai_api_key: str | None,
    openai_model: str,
    deepseek_api_key: str | None,
    deepseek_model: str,
    deepseek_base_url: str,
) -> LectureSummarizer:
    if openai_api_key:
        return AILectureSummarizer(openai_api_key, openai_model, "openai")
    if deepseek_api_key:
        return AILectureSummarizer(
            deepseek_api_key,
            deepseek_model,
            "deepseek",
            deepseek_base_url,
        )
    return LocalLectureSummarizer()
