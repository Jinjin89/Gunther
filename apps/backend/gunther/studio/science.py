"""Scientific handoffs. Models own purpose, argument, image interpretation and review.

Code owns source scope, pixel delivery, provenance and the delivery gates. No caption is
promoted to a visual reading and no failed model step is replaced by a guessed answer.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from gunther.agent import Evidence
from gunther.skill_checks import figures_of
from gunther.studio import format as fmt


def _said(value: str) -> str:
    """A field the step must actually fill: blank is not an answer (the gateway asks again)."""

    if not value.strip():
        raise ValueError("must not be blank")
    return value


class Purpose(BaseModel):
    purpose: Literal["explain_study", "general"]
    reason: str


class Method(BaseModel):
    inputs: str = ""
    mechanism: str = ""
    outputs: str = ""
    assumptions: list[str] = Field(default_factory=list)


class Result(BaseModel):
    quantity: str = ""
    unit: str = ""
    baseline: str = ""
    condition: str = ""
    uncertainty: str = ""


class ClaimFigure(BaseModel):
    asset_id: str
    panels: list[str] = Field(default_factory=list)
    why: str = ""


class StudyClaim(BaseModel):
    claim: str
    kind: Literal["finding", "interpretation"] = "finding"
    attribution: Literal["authors", "ours"] = "authors"
    central: bool = True
    experiment: str = ""
    comparison_or_control: str = ""
    measurement: str = ""
    result: Result = Field(default_factory=Result)
    refs: list[int] = Field(default_factory=list)
    figures: list[ClaimFigure] = Field(default_factory=list)
    interpretation: str = ""
    alternatives: list[str] = Field(default_factory=list)
    limits: list[str] = Field(default_factory=list)


class StudyMap(BaseModel):
    language: str = ""
    title: str = ""
    question: str
    prior_gap: str = ""
    contribution: str
    method: Method = Field(default_factory=Method)
    # In argument importance order, decided by the model, not by numeric specificity.
    claims: list[StudyClaim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    study_type: Literal["methods", "experimental", "theory", "review", "other"]

    _filled = field_validator("question", "contribution")(_said)

    @field_validator("claims")
    @classmethod
    def _has_claims(cls, claims: list[StudyClaim]) -> list[StudyClaim]:
        if not any(claim.claim.strip() for claim in claims):
            raise ValueError("a study map states at least one claim the material supports")
        return claims


class PanelReading(BaseModel):
    label: str
    shows: str = ""
    axes: str = ""
    legend: str = ""


class FigureReading(BaseModel):
    asset_id: str
    shows: str
    panels: list[PanelReading] = Field(default_factory=list)
    comparison: str = ""
    key_observation: str = ""
    supports_claim: str = ""
    legible: bool
    uncertain: list[str] = Field(default_factory=list)
    # Only exact values actually readable in the pixels, with units/conditions in prose.
    confirmed_values: list[str] = Field(default_factory=list)

    _filled = field_validator("asset_id", "shows")(_said)

    @model_validator(mode="after")
    def _legible_says_what(self) -> FigureReading:
        # A schematic needs no axes or values, but a legible figure has an observation.
        if self.legible and not self.key_observation.strip():
            raise ValueError("a legible figure states its key observation")
        return self


class ScientificProblem(BaseModel):
    where: str = ""
    problem: str
    fix: str


class ReaderAnswers(BaseModel):
    question_and_contribution: str
    method_or_experiment: str
    figures_and_controls: str
    evidence_for_conclusions: str
    assumptions_limits_and_questions: str

    _filled = field_validator(
        "question_and_contribution",
        "method_or_experiment",
        "figures_and_controls",
        "evidence_for_conclusions",
        "assumptions_limits_and_questions",
    )(_said)


class ScientificReview(BaseModel):
    understood: str = ""
    answers: ReaderAnswers
    verdict: Literal["ready", "revise"]
    problems: list[ScientificProblem] = Field(default_factory=list)


def warnings_of(item: Evidence) -> list[dict]:
    citation = item.payload[0] if isinstance(item.payload, tuple) and item.payload else None
    warnings = list(item.extraction_warnings or getattr(citation, "extraction_warnings", []))
    if "\ufffd" in (item.context or item.text) and not any(
        w.get("kind") == "uncertain_glyph" for w in warnings
    ):
        warnings.append({"kind": "uncertain_glyph", "reason": "Replacement glyph in source text"})
    return warnings


def warning_line(item: Evidence) -> str:
    """The passage's extraction warnings, in a line the writer and reviewer read with it."""

    said = []
    for warning in warnings_of(item):
        kind = warning.get("kind")
        if kind == "uncertain_glyph":
            said.append("some glyphs are unreadable (shown as \ufffd)")
        elif kind == "unverified_glyphs":
            numbers = ", ".join(warning.get("numbers") or [])
            said.append(
                "some characters could not be verified"
                + (f" (numbers not to be trusted: {numbers})" if numbers else "")
            )
        elif kind == "withheld_formula":
            said.append(warning.get("reason") or "an equation was kept as an image")
    said = list(dict.fromkeys(said))
    return ("\nExtraction warnings: " + "; ".join(said) + ".") if said else ""


def overview(items: list[Evidence]) -> str:
    """A bounded overview, in source order; no keyword classifier owns the purpose."""
    seen: set[str] = set()
    lines = []
    for item in items:
        if item.source_key in seen:
            continue
        seen.add(item.source_key)
        same = [e for e in items if e.source_key == item.source_key]
        lines.append(
            f"Title: {item.title}\nFirst passages / abstract: "
            + "\n".join((e.context or e.text)[:900] for e in same[:2])
            + f"\nFigures available: {any(figures_of(e) for e in same)}"
        )
        if len(lines) == 30:
            break
    return "\n\n".join(lines)


def map_problems(study: StudyMap, items: list[Evidence]) -> list[str]:
    """Links the study map makes that the material does not hold: every claim cites passages
    in the pool, and its figures belong to those passages."""

    by_ref = {e.ref: e for e in items}
    problems = []
    for claim in study.claims:
        unknown = [r for r in claim.refs if r not in by_ref]
        if not claim.refs or unknown:
            problems.append(
                f"Claim “{claim.claim[:100]}” cites "
                + (f"passages that do not exist: {unknown}" if unknown else "no passage")
                + ". Cite the numbered passages that state it."
            )
            continue
        allowed = {f["assetId"] for r in claim.refs for f in figures_of(by_ref[r])}
        for figure in claim.figures:
            if figure.asset_id not in allowed:
                problems.append(
                    f"Claim “{claim.claim[:100]}” links figure {figure.asset_id}, which is not "
                    "retained with the passages it cites. Link only figures listed with them."
                )
    return problems


def content_problems(study: StudyMap, readings: list[FigureReading], deck: str) -> list[str]:
    """Central, visually read evidence must be visible on a slide, never just in notes."""
    useful = {r.asset_id for r in readings if r.legible}
    required = {
        f.asset_id for c in study.claims if c.central for f in c.figures if f.asset_id in useful
    }
    visible = "\n".join("\n".join(fmt.parse_slide(s).body) for s in fmt.split_slides(deck))
    shown = set(re.findall(r"!\[[^\]]*\]\(gunther-asset:([A-Za-z0-9_-]+)\)", visible))
    return [
        f"Central claim needs source image gunther-asset:{asset} visible and explained on a slide."
        for asset in sorted(required - shown)
    ]


# Warnings that make a passage's numbers doubtful. A withheld equation is not one: its text
# is kept out of the passage, so the passage's own numbers are unaffected.
VALUE_WARNINGS = {"uncertain_glyph", "unverified_glyphs"}


def trusted_numbers(item: Evidence) -> set[str]:
    """The numbers a passage states reliably: none touches an uncertain glyph (U+FFFD), and
    none comes from text whose glyphs could not be verified."""

    text = item.context or item.text
    numbers = {
        fmt.normal_number(m.group())
        for m in fmt.NUMBER.finditer(text)
        if "\ufffd" not in text[max(0, m.start() - 2) : m.end() + 2]
    }
    for warning in warnings_of(item):
        if warning.get("kind") == "unverified_glyphs":
            numbers -= set(warning.get("numbers") or [])
    return numbers


def value_problems(deck: str, items: list[Evidence], readings: list[FigureReading]) -> list[str]:
    """Damaged values are never stated as facts.

    A number on a line citing a passage with doubtful glyphs must be stated reliably by the
    same source (that passage's clean text or another of its passages) or read in the pixels
    of a figure attached to that passage. The scientific review still judges units,
    conditions, meaning and whether comparisons are compatible.
    """
    by_ref = {e.ref: e for e in items}
    problems = []
    for line in fmt.without_design(deck).splitlines():
        if "\ufffd" in line:
            problems.append("An uncertain source glyph is stated in the deck; omit the value.")
        refs = {int(r) for r in re.findall(r"\[(\d+)\]", line)}
        warned = [
            by_ref[r]
            for r in refs
            if r in by_ref and any(w.get("kind") in VALUE_WARNINGS for w in warnings_of(by_ref[r]))
        ]
        if not warned:
            continue
        plain = re.sub(r"^[ \t]*(?:\d+[.)]|[-*])\s+", "", line)
        values = fmt.numbers_in(re.sub(r"\[\d+\]|gunther-asset:[A-Za-z0-9_-]+", "", plain))
        sources = {e.source_key for e in warned}
        assets = {f["assetId"] for e in warned for f in figures_of(e)}
        allowed = set().union(
            *(trusted_numbers(e) for e in items if e.source_key in sources),
            *(
                fmt.numbers_in(value)
                for r in readings
                if r.legible and r.asset_id in assets
                for value in r.confirmed_values
            ),
        )
        if missing := values - allowed:
            problems.append(
                "Unconfirmed value from a passage with unreadable glyphs: "
                + ", ".join(sorted(missing))
                + ". Omit the value and keep the supported point without it."
            )
    return list(dict.fromkeys(problems))


def allowed_assets(items: list[Evidence]) -> list[str]:
    """The source images of the chosen material: the only ones a scientific deck may show."""

    return sorted({f["assetId"] for e in items for f in figures_of(e)})


def scope_problems(deck: str, items: list[Evidence], allowed: list[str] | None = None) -> list[str]:
    """``allowed``: the build's own images, kept with the deck, so a later edit cannot show an
    image that only an earlier, unrelated part of the conversation brought in."""

    allowed = set(allowed_assets(items) if allowed is None else allowed)
    shown = set(re.findall(r"gunther-asset:([A-Za-z0-9_-]+)", deck))
    return (
        ["The deck shows source images from outside the chosen material: remove them."]
        if shown - allowed
        else []
    )


def code_problems(
    study: StudyMap,
    readings: list[FigureReading],
    deck: str,
    items: list[Evidence],
    allowed: list[str] | None = None,
) -> list[str]:
    """What code checks before delivery: central figures shown, values readable, scope kept."""

    return (
        content_problems(study, readings, deck)
        + value_problems(deck, items, readings)
        + scope_problems(deck, items, allowed)
    )


def pool_print(items: list[Evidence]) -> str:
    """Which passages, in which numbering, a build read: a saved study map is reused only for
    the same ones (the same count of different passages would point its refs elsewhere)."""

    import hashlib
    import json

    rows = sorted([item.ref or 0, *item.key] for item in items)
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]
