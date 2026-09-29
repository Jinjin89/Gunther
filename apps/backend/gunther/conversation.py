from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from gunther.llm import ModelError, ModelGateway, ModelInfo, Turn
from gunther.model_profiles import Effort


@dataclass(frozen=True)
class GroundingClaim:
    subject: str
    predicate: str
    object: str
    source_title: str
    quote: str
    locator: str
    status: str
    confidence: float


@dataclass(frozen=True)
class ResponderResult:
    content: str
    mode: Literal["local", "model"]
    # Which model answered (or was asked), at what effort, and anything to admit.
    model_ref: str | None = None
    model_label: str | None = None
    effort: str | None = None
    effort_label: str | None = None
    notes: tuple[str, ...] = ()
    reasoning: str | None = None
    # Why the chosen model's answer is not shown; the quotes stand in.
    error: str | None = None


class KnowledgeResponder(Protocol):
    mode: Literal["local", "model"]

    def respond(
        self,
        question: str,
        claims: list[GroundingClaim],
        history: list[Turn],
        *,
        model: ModelInfo | None = None,
        effort: Effort | None = None,
    ) -> ResponderResult: ...


def _display_predicate(value: str) -> str:
    return value.replace("_", " ")


class LocalKnowledgeResponder:
    mode: Literal["local"] = "local"

    def respond(
        self,
        question: str,
        claims: list[GroundingClaim],
        history: list[Turn],
        *,
        model: ModelInfo | None = None,
        effort: Effort | None = None,
    ) -> ResponderResult:
        del history, model, effort
        if not claims:
            return ResponderResult(
                content=(
                    "I couldn’t find a claim in the selected knowledge scope that directly "
                    "supports "
                    f"an answer to “{question}”.\n\n"
                    "Try widening the source scope, or add a source that states the "
                    "relationship you "
                    "want to investigate. I have left the gap visible instead of filling it with "
                    "unsupported general knowledge."
                ),
                mode=self.mode,
            )

        statements = [
            f"{index}. **{claim.subject}** {_display_predicate(claim.predicate)} "
            f"**{claim.object}**. [{index}]"
            for index, claim in enumerate(claims, start=1)
        ]
        verified = sum(claim.status == "verified" for claim in claims)
        certainty = (
            f"{verified} of these {len(claims)} claims have been human-verified."
            if verified
            else "These claims are still provisional and should be reviewed before reuse."
        )
        return ResponderResult(
            content=(
                f"Here is the strongest evidence-backed answer available for “{question}”:\n\n"
                + "\n".join(statements)
                + f"\n\n**Confidence note.** {certainty} The answer is limited to the selected "
                "sources and keeps unresolved context visible."
            ),
            mode=self.mode,
        )


INSTRUCTIONS = (
    "You are Gunther, an evidence-first knowledge partner. Answer only from the evidence "
    "given with the latest question. Cite claims inline as [1], [2], and so on. Clearly "
    "separate what is supported, what is an inference, and what remains unknown. Be concise "
    "but useful. Never invent a source or citation. Earlier turns are context only; their "
    "citation numbers do not carry over."
)
HISTORY_TURNS = 6


class ModelKnowledgeResponder:
    """Answers with the Ask model, or the one a conversation picked."""

    mode: Literal["model"] = "model"

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway
        self.fallback = LocalKnowledgeResponder()

    def respond(
        self,
        question: str,
        claims: list[GroundingClaim],
        history: list[Turn],
        *,
        model: ModelInfo | None = None,
        effort: Effort | None = None,
    ) -> ResponderResult:
        if model is None:
            chosen = self.gateway.for_role("ask")
            if chosen is None:
                return self.fallback.respond(question, claims, history)
            model, default_effort = chosen
            effort = effort or default_effort
        about = {"model_ref": model.ref, "model_label": model.display, "effort": effort}
        if not claims:
            return replace(self.fallback.respond(question, claims, history), **about)

        evidence = "\n".join(
            f"[{index}] {claim.subject} {_display_predicate(claim.predicate)} "
            f"{claim.object}. Source: {claim.source_title}; quote: {claim.quote!r}; "
            f"status={claim.status}; confidence={claim.confidence:.2f}"
            for index, claim in enumerate(claims, start=1)
        )
        turns = [
            *history[-HISTORY_TURNS:],
            Turn("user", f"Question:\n{question}\n\nEvidence:\n{evidence}"),
        ]
        try:
            completion = self.gateway.complete(
                model, system=INSTRUCTIONS, messages=turns, effort=effort
            )
        except ModelError as error:
            return replace(
                self.fallback.respond(question, claims, history), **about, error=str(error)
            )
        content = completion.text
        citation_numbers = [int(value) for value in re.findall(r"\[(\d+)\]", content)]
        answered = {
            **about,
            "effort_label": completion.effort_label,
            "notes": completion.notes,
            "reasoning": completion.reasoning,
        }
        if not citation_numbers or any(v < 1 or v > len(claims) for v in citation_numbers):
            return replace(
                self.fallback.respond(question, claims, history),
                **answered,
                error=f"{model.display} did not cite the evidence it was given.",
            )
        return ResponderResult(content=content, mode=self.mode, **answered)


def create_knowledge_responder(gateway: ModelGateway | None) -> KnowledgeResponder:
    if gateway is not None and gateway.models:
        return ModelKnowledgeResponder(gateway)
    return LocalKnowledgeResponder()
