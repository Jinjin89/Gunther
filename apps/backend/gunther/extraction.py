from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field


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


class ExtractionResult(BaseModel):
    assertions: list[CandidateAssertion]
    mode: Literal["local", "deepseek"]


class Extractor(ABC):
    mode: Literal["local", "deepseek"]

    @abstractmethod
    def extract(self, title: str, content: str) -> ExtractionResult:
        raise NotImplementedError


def _normalize_predicate(value: str) -> str:
    return re.sub(r"^_+|_+$", "", re.sub(r"[^a-z0-9]+", "_", value.strip().lower()))


def _infer_type(label: str, predicate: str, side: Literal["subject", "object"]) -> str:
    if re.fullmatch(r"[A-Z][A-Z0-9-]{1,11}", label) and " " not in label:
        return "Gene"
    if re.search(r"\b(cell|lymphocyte|monocyte|macrophage|neuron)\b", label, re.I):
        return "CellType"
    if re.search(r"\b(course|lesson|module)\b", label, re.I):
        return "LearningUnit"
    if predicate == "marker_of":
        return "Gene" if side == "subject" else "CellType"
    return "Concept"


class LocalExtractor(Extractor):
    mode: Literal["local"] = "local"

    patterns: tuple[tuple[re.Pattern[str], str | None], ...] = (
        (re.compile(r"^(.+?)\s*(?:->|→)\s*([\w -]+?)\s*(?:->|→)\s*(.+)$"), None),
        (re.compile(r"^(.+?)\s*\|\s*([\w -]+?)\s*\|\s*(.+)$"), None),
        (
            re.compile(
                r"^(.+?)\s+(?:is|acts as)\s+(?:an?\s+)?marker\s+(?:for|of)\s+(.+?)[.!]?$",
                re.I,
            ),
            "marker_of",
        ),
        (re.compile(r"^(.+?)\s+(?:requires|depends on)\s+(.+?)[.!]?$", re.I), "depends_on"),
        (re.compile(r"^(.+?)\s+(?:is a|is an)\s+(.+?)[.!]?$", re.I), "is_a"),
        (re.compile(r"^(.+?)\s+uses\s+(.+?)[.!]?$", re.I), "uses"),
        (
            re.compile(
                r"^(.+?)\s+(improves|supports|inhibits|causes|contains|produces)\s+(.+?)[.!]?$",
                re.I,
            ),
            None,
        ),
    )

    def _match(self, line: str) -> CandidateAssertion | None:
        for pattern, fixed_predicate in self.patterns:
            match = pattern.match(line)
            if not match:
                continue
            subject_label = match.group(1).strip()
            predicate = _normalize_predicate(fixed_predicate or match.group(2))
            object_group = 2 if fixed_predicate else 3
            object_label = match.group(object_group).strip()
            return CandidateAssertion(
                subject_label=subject_label,
                subject_type=_infer_type(subject_label, predicate, "subject"),
                predicate=predicate,
                object_label=object_label,
                object_type=_infer_type(object_label, predicate, "object"),
                confidence=0.72,
                evidence_quote=line,
                qualifiers=[],
            )
        return None

    def extract(self, title: str, content: str) -> ExtractionResult:
        del title
        chunks = re.split(r"\r?\n|(?<=[.!?])\s+(?=[A-Z])", content)
        candidates: list[CandidateAssertion] = []
        for chunk in chunks:
            line = re.sub(r"^[-*\d.)\s]+", "", chunk).strip()
            if line and (candidate := self._match(line)):
                candidates.append(candidate)
        return ExtractionResult(assertions=candidates, mode=self.mode)


class DeepSeekExtractor(Extractor):
    mode: Literal["deepseek"] = "deepseek"

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.fallback = LocalExtractor()

    def extract(self, title: str, content: str) -> ExtractionResult:
        try:
            response = self.client.responses.parse(
                model=self.model,
                store=False,
                instructions=(
                    "Extract explicit, evidence-backed knowledge assertions. Preserve exact "
                    "source wording in evidence_quote. Use concise canonical entity labels, "
                    "snake_case predicates, useful domain entity types, and qualifiers for "
                    "context such as species, tissue, method, course, or time. Never add "
                    "unsupported facts."
                ),
                input=f"Source title: {title}\n\n{content}",
                text_format=ExtractionPayload,
            )
            if response.output_parsed is None:
                return self.fallback.extract(title, content)
            return ExtractionResult(assertions=response.output_parsed.assertions, mode=self.mode)
        except Exception:
            return self.fallback.extract(title, content)


def create_extractor(api_key: str | None, model: str, base_url: str) -> Extractor:
    return DeepSeekExtractor(api_key, model, base_url) if api_key else LocalExtractor()
