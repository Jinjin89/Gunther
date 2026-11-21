from __future__ import annotations

import re

from pydantic import BaseModel, Field

from gunther import usage
from gunther.llm import AnalysisNeedsModel, ModelError, ModelGateway


class Qualifier(BaseModel):
    key: str
    value: str


class CandidateAssertion(BaseModel):
    subject_label: str
    subject_type: str
    predicate: str
    object_label: str
    object_type: str
    confidence: float = Field(ge=0, le=1)
    evidence_quote: str
    qualifiers: list[Qualifier]


class ExtractionPayload(BaseModel):
    assertions: list[CandidateAssertion]


def analysis_job_key(source_id: str, content_hash: str) -> str:
    return f"extract_assertions:{source_id}:{content_hash}"


def normalize_predicate(value: str) -> str:
    return re.sub(r"[^\w]+", "_", value.casefold()).strip("_")


class ModelExtractor:
    """The Analysis model reads a source and returns claims backed by its text."""

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    @usage.job("analysis", "extraction")
    def extract(self, title: str, content: str) -> ExtractionPayload:
        chosen = self.gateway.for_role("analysis")
        if chosen is None:
            raise AnalysisNeedsModel()
        model, effort = chosen
        payload, _ = self.gateway.complete_json(
            model,
            ExtractionPayload,
            system=(
                "Extract explicit, evidence-backed knowledge assertions. Preserve exact "
                "source wording in evidence_quote. Use concise canonical entity labels, "
                "snake_case predicates, useful domain entity types, and qualifiers for "
                "context such as species, tissue, method, course, or time. Never add "
                "unsupported facts."
            ),
            prompt=f"Source title: {title}\n\n{content}",
            effort=effort,
        )
        if any(not claim.evidence_quote.strip() or claim.evidence_quote not in content
               for claim in payload.assertions):
            raise ModelError("Analysis returned evidence that was absent from the source.")
        return payload


def create_extractor(gateway: ModelGateway | None) -> ModelExtractor | None:
    return ModelExtractor(gateway) if gateway and gateway.for_role("analysis") else None
