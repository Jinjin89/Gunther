"""Where paragraphs start in a spoken transcript, by content (docs/LIVE_TRANSCRIPT_PLAN.md §5.2).

The Analysis model reads numbered sentences and says only which numbers start a paragraph.
It never returns or changes text, so every word on screen still comes from listening.
"""

from __future__ import annotations

import json

from gunther.llm import ModelError, ModelGateway, ModelInfo, _json_text
from gunther.model_profiles import Effort

PARAGRAPHS_PROMPT = (
    "You group the sentences of a spoken transcript (speech recognition output) into paragraphs, "
    "the way a careful editor would. A paragraph holds one point of the talk: one idea, one "
    "example, one step of an explanation. Start a new paragraph where the speaker moves to a new "
    "point, where someone asks a question, or where a different person starts speaking. Keep "
    "sentences about the same point together, even many of them. Avoid a paragraph of one short "
    "sentence unless it is a question, an answer's first sentence, or a different speaker. Never "
    "change, merge or split sentences; only say where paragraphs start. Sentences are numbered "
    "from 1, and sentence 1 always starts a paragraph. The last sentences may be unfinished talk; "
    "group them as they are.\n"
    'Answer with JSON only: {"starts": [numbers of the sentences that start a paragraph]}'
)


class ParagraphsError(RuntimeError):
    """The model did not say where paragraphs start. Nothing stands in for it."""


def paragraph_message(sentences: list[str], before: str) -> str:
    numbered = "\n".join(f"{index + 1}. {text}" for index, text in enumerate(sentences))
    if before.strip():
        return f"Before (context only, already grouped):\n{before}\n\nSentences:\n{numbered}"
    return f"Sentences:\n{numbered}"


def usable_starts(answer: object, count: int) -> list[int]:
    """Zero-based starts from the model's one-based numbers; only integers in range, plus 0."""

    numbers = answer.get("starts") if isinstance(answer, dict) else None
    if not isinstance(numbers, list):
        return []
    picked = {
        number - 1
        for number in numbers
        if isinstance(number, int) and not isinstance(number, bool) and 1 <= number <= count
    }
    return sorted(picked | {0}) if picked else []


def group_paragraphs(
    gateway: ModelGateway,
    model: ModelInfo,
    effort: Effort,
    sentences: list[str],
    before: str,
) -> list[int]:
    try:
        completion = gateway.complete(
            model,
            system=PARAGRAPHS_PROMPT,
            messages=paragraph_message(sentences, before),
            effort=effort,
            json_mode=True,
        )
    except ModelError as error:
        raise ParagraphsError(str(error)) from error
    try:
        answer = json.loads(_json_text(completion.text))
    except ValueError:
        answer = None
    starts = usable_starts(answer, len(sentences))
    if not starts:
        raise ParagraphsError("The model didn't say where paragraphs start")
    return starts
