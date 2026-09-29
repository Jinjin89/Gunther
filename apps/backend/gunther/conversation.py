from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from gunther.llm import ModelInfo, Turn
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


class KnowledgeResponder(Protocol):
    """Lists the evidence as it is. Ask's agent (see agent) writes the real answers;
    this stands in only when no model is set up or the model failed, and says so."""

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
