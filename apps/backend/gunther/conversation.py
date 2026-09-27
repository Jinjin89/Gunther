from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol

from openai import OpenAI


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
    mode: Literal["local", "deepseek"]


class KnowledgeResponder(Protocol):
    mode: Literal["local", "deepseek"]

    def respond(
        self,
        question: str,
        claims: list[GroundingClaim],
        history: list[tuple[str, str]],
    ) -> ResponderResult: ...


def _display_predicate(value: str) -> str:
    return value.replace("_", " ")


class LocalKnowledgeResponder:
    mode: Literal["local"] = "local"

    def respond(
        self,
        question: str,
        claims: list[GroundingClaim],
        history: list[tuple[str, str]],
    ) -> ResponderResult:
        del history
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


class DeepSeekKnowledgeResponder:
    mode: Literal["deepseek"] = "deepseek"

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.fallback = LocalKnowledgeResponder()

    def respond(
        self,
        question: str,
        claims: list[GroundingClaim],
        history: list[tuple[str, str]],
    ) -> ResponderResult:
        if not claims:
            return self.fallback.respond(question, claims, history)

        evidence = "\n".join(
            f"[{index}] {claim.subject} {_display_predicate(claim.predicate)} "
            f"{claim.object}. Source: {claim.source_title}; quote: {claim.quote!r}; "
            f"status={claim.status}; confidence={claim.confidence:.2f}"
            for index, claim in enumerate(claims, start=1)
        )
        recent_history = "\n".join(f"{role}: {content}" for role, content in history[-6:])
        try:
            response = self.client.responses.create(
                model=self.model,
                store=False,
                instructions=(
                    "You are Gunther, an evidence-first knowledge partner. Answer only from the "
                    "provided evidence. Cite claims inline as [1], [2], and so on. Clearly "
                    "separate what is supported, what is an inference, and what remains unknown. "
                    "Be concise but useful. Never invent a source or citation."
                ),
                input=(
                    f"Recent session context:\n{recent_history or '(new session)'}\n\n"
                    f"Question:\n{question}\n\nEvidence:\n{evidence}"
                ),
            )
            content = response.output_text.strip()
            citation_numbers = [int(value) for value in re.findall(r"\[(\d+)\]", content)]
            if (
                not content
                or not citation_numbers
                or any(value < 1 or value > len(claims) for value in citation_numbers)
            ):
                return self.fallback.respond(question, claims, history)
            return ResponderResult(content=content, mode=self.mode)
        except Exception:
            return self.fallback.respond(question, claims, history)


def create_knowledge_responder(
    api_key: str | None, model: str, base_url: str
) -> KnowledgeResponder:
    if api_key:
        return DeepSeekKnowledgeResponder(api_key, model, base_url)
    return LocalKnowledgeResponder()
