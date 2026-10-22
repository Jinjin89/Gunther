"""Outline validation and coverage, independent of a presentation's subject or renderer."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field


class OutlineConcern(BaseModel):
    section: int = Field(default=0, ge=0)
    problem: str = Field(max_length=600)
    fix: str = Field(default="", max_length=600)
    severity: str = Field(default="advice", pattern="^(error|advice)$")


class OmittedCard(BaseModel):
    card: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class ContentRequirement(BaseModel):
    requirement: str = Field(min_length=1, max_length=500)
    basis: Literal["brief", "inferred"] = "inferred"
    cards: list[int] = Field(default_factory=list, max_length=40)
    reason: str = Field(default="", max_length=500)


class OutlineReview(BaseModel):
    issues: list[OutlineConcern] = Field(default_factory=list, max_length=30)
    omitted: list[OmittedCard] = Field(default_factory=list, max_length=40)
    requirements: list[ContentRequirement] = Field(default_factory=list, max_length=20)
    factual_pages: list[str] = Field(default_factory=list, max_length=64)


def requested_pages(brief: str) -> int | None:
    """Recognize explicit counts, without treating duration or a range as an exact count."""
    found = list(
        re.finditer(
            r"(?<![\d.\-–])\b(\d+)\s*(?:slides?|pages?)\b|(?<![\d.\-–])(\d+)\s*页", brief, re.I
        )
    )
    exact = []
    for match in found:
        prefix = brief[max(0, match.start() - 20) : match.start()]
        if re.search(
            r"(?:about|around|roughly|approximately|to|至|到|约|大约)\s*$|[\d\s][\-–]\s*$",
            prefix,
            re.I,
        ):
            continue
        exact.append(int(match.group(1) or match.group(2)))
    return exact[-1] if exact else None


def outline_metadata(item: Any) -> dict[str, Any]:
    """Additive metadata: old outlines still serialize exactly as before."""
    values = {
        "id": item.id,
        "message": item.message,
        "question": item.question,
        "requires": list(item.requires),
    }
    return {key: value for key, value in values.items() if value}


def planning_fingerprint(pages: Sequence[Any]) -> str:
    positions = {page.id: index for index, page in enumerate(pages) if page.id}
    value = [
        {
            "heading": p.heading,
            "goal": p.goal,
            "message": p.message,
            "question": p.question,
            "link": p.link,
            "cards": list(p.cards),
            "requires": [positions.get(key, key) for key in p.requires],
        }
        for p in pages
    ]
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def outline_problems(
    pages: Sequence[Any],
    card_count: int,
    maximum: int,
    exact: int | None = None,
    protected: set[int] | None = None,
) -> list[str]:
    problems = []
    if not pages:
        return ["The outline has no pages."]
    if len(pages) > maximum:
        problems.append(
            f"There are {len(pages)} slides; the maximum is {maximum}. Replan; never truncate."
        )
    if exact is not None and len(pages) != exact:
        problems.append(f"The user requested exactly {exact} slides; the outline has {len(pages)}.")
    positions: dict[str, int] = {}
    for index, page in enumerate(pages):
        if not page.heading.strip():
            problems.append(f"Slide {index + 1} has no heading.")
        if page.id:
            if page.id in positions:
                problems.append(f"Slide {index + 1} repeats identity {page.id}.")
            positions[page.id] = index
        invalid = [n for n in page.cards if not 1 <= n <= card_count]
        if invalid:
            problems.append(f"Slide {index + 1} cites unknown material cards {invalid}.")
    problems.extend(dependency_problems(pages))
    covered = {n for page in pages for n in page.cards}
    missing = sorted((protected or set()) - covered)
    if missing:
        problems.append(
            f"Replanning lost previously assigned evidence cards {missing}; "
            "merge their support into retained slides."
        )
    return problems


def dependency_problems(pages: Sequence[Any]) -> list[str]:
    positions = {page.id: index for index, page in enumerate(pages) if page.id}
    return [
        f"Slide {index + 1} needs prerequisite {key} before it."
        for index, page in enumerate(pages)
        for key in page.requires
        if key not in positions or positions[key] >= index
    ]


def coverage_record(
    pages: Sequence[Any], cards: Sequence[Any], review: OutlineReview
) -> dict[str, Any]:
    omitted = {entry.card: entry.reason for entry in review.omitted}
    return {
        "outlineFingerprint": planning_fingerprint(pages),
        "issues": [item.model_dump() for item in review.issues],
        "requirements": [
            {
                **item.model_dump(),
                "slides": [
                    index + 1
                    for index, page in enumerate(pages)
                    if set(item.cards) & set(page.cards)
                ],
            }
            for item in review.requirements
        ],
        "coverage": [
            {
                "card": number,
                "claim": card.claim,
                "refs": list(card.refs),
                "scope": card.scope,
                "evidence": card.evidence,
                "slides": [index + 1 for index, p in enumerate(pages) if number in p.cards],
                "reason": omitted.get(number, ""),
            }
            for number, card in enumerate(cards, 1)
        ],
    }
