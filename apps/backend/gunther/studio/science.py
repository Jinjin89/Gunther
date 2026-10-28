"""Scientific handoffs. Models own purpose, argument, image interpretation and review.

Code owns source scope, pixel delivery, provenance and the delivery gates. No caption is
promoted to a visual reading and no failed model step is replaced by a guessed answer.
"""

from __future__ import annotations

import json
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
    experimental_setup: str = ""
    why_it_addresses_question: str = ""
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
    # Normalised [x, y, width, height] in the original source pixels, not slide coordinates.
    box: list[float] = Field(default_factory=list)

    @field_validator("box")
    @classmethod
    def _box(cls, box: list[float]) -> list[float]:
        if not box:
            return box
        if (
            len(box) != 4
            or any(not 0 <= value <= 1 for value in box)
            or box[2] <= 0.01
            or box[3] <= 0.01
            or box[0] + box[2] > 1.01
            or box[1] + box[3] > 1.01
        ):
            raise ValueError("a panel box is normalised x, y, width, height inside the figure")
        return [round(value, 6) for value in box]


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
    # Set by delivery code; a model never asserts its own source revision.
    source_revision_id: str = ""
    # The vision model that inspected the pixels; readings are reused only when this matches.
    vision_model: str = ""

    _filled = field_validator("asset_id", "shows")(_said)

    @model_validator(mode="after")
    def _legible_says_what(self) -> FigureReading:
        # A schematic needs no axes or values, but a legible figure has an observation.
        if self.legible and not self.key_observation.strip():
            raise ValueError("a legible figure states its key observation")
        return self

class FigurePlan(BaseModel):
    asset_id: str
    central: bool = True
    disposition: Literal["teach", "group", "omit"]
    panels: list[str] = Field(default_factory=list)
    teaching_role: str = ""
    reason: str = ""

    _filled = field_validator("asset_id")(_said)

    @model_validator(mode="after")
    def _says_why(self) -> FigurePlan:
        if self.disposition in ("group", "omit") and not self.reason.strip():
            raise ValueError(f"Figure {self.asset_id} is {self.disposition}; state the reason")
        return self


class CoveragePlan(BaseModel):
    method_teaching: list[str] = Field(default_factory=list)
    result_dependencies: list[str] = Field(default_factory=list)
    figures: list[FigurePlan] = Field(default_factory=list)
    omitted_content: list[str] = Field(default_factory=list)

    @field_validator("method_teaching", "result_dependencies")
    @classmethod
    def _not_empty(cls, values: list[str]) -> list[str]:
        if not any(value.strip() for value in values):
            raise ValueError("must include at least one substantive step")
        return values


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


class LearnerAnswer(BaseModel):
    answer: str
    evidence: list[int] = Field(min_length=1)

    _filled = field_validator("answer")(_said)


class LearnerReview(BaseModel):
    question_and_contribution: LearnerAnswer
    method_or_experiment: LearnerAnswer
    figures_and_controls: LearnerAnswer
    evidence_for_conclusions: LearnerAnswer
    assumptions_limits_and_questions: LearnerAnswer
    unexplained: list[str] = Field(default_factory=list)


class CoverageReview(BaseModel):
    understood: str = ""
    verdict: Literal["ready", "revise"]
    coverage_problems: list[ScientificProblem] = Field(default_factory=list)
    learner_problems: list[ScientificProblem] = Field(default_factory=list)


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


def source_metadata(items: list[Evidence]) -> list[dict]:
    """Only bibliographic fields the selected source itself retained; absent stays absent."""

    rows, seen = [], set()
    for item in items:
        citation = item.payload[0] if isinstance(item.payload, tuple) and item.payload else None
        data = getattr(citation, "source_metadata", None) or {}
        if not data:
            continue
        key = repr(data)
        if key not in seen:
            seen.add(key)
            rows.append(data)
    return rows


def figure_inventory(items: list[Evidence]) -> list[str]:
    """The selected revision's source figure IDs, independent of a writer's storyline.

    ``figures_of`` carries only ``DocumentElement.kind == "figure"`` records (see
    ``service._studio_metadata``), so inline formula images and decorative assets are excluded.
    Figure-like tables are not converted to assets by the reader; when extraction represents
    them as figure elements they naturally enter this inventory.
    """

    return sorted({f["assetId"] for e in items for f in figures_of(e)})


def crop_reference(asset_id: str, box: list[float]) -> str:
    if len(box) != 4:
        raise ValueError("A panel crop needs normalised x, y, width and height.")
    x, y, width, height = (round(value, 4) for value in box)
    return f"gunther-asset:{asset_id}#x={x},y={y},w={width},h={height}"


def image_parts(reference: str) -> tuple[str, list[float]] | None:
    """Split a managed image reference into its asset and optional normalised crop."""

    found = re.fullmatch(
        r"gunther-asset:([A-Za-z0-9_-]+)"
        r"(?:#x=(\d+(?:\.\d+)?),y=(\d+(?:\.\d+)?),w=(\d+(?:\.\d+)?),h=(\d+(?:\.\d+)?))?",
        reference,
    )
    if not found:
        return None
    asset_id = found.group(1)
    if found.group(2) is None:
        return asset_id, []
    box = [float(found.group(n)) for n in range(2, 6)]
    if (
        any(not 0 <= value <= 1 for value in box)
        or box[2] <= 0
        or box[3] <= 0
        or box[0] + box[2] > 1
        or box[1] + box[3] > 1
    ):
        return None
    return asset_id, box


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


def coverage_problems(plan: CoveragePlan, items: list[Evidence]) -> list[str]:
    """Every source image is accounted for once by a plan made from the source inventory."""

    inventory = figure_inventory(items)
    rows: dict[str, FigurePlan] = {}
    problems = []
    for figure in plan.figures:
        if figure.asset_id in rows:
            problems.append(f"Coverage plan accounts for figure {figure.asset_id} more than once.")
        rows[figure.asset_id] = figure
    problems += [
        f"Source figure {asset_id} is missing from the coverage plan."
        for asset_id in inventory
        if asset_id not in rows
    ]
    problems += [
        f"Coverage plan names figure {figure.asset_id}, which is not in the selected material."
        for figure in plan.figures
        if figure.asset_id not in inventory
    ]
    problems += [
        f"Central figure {figure.asset_id} must be taught or grouped, not omitted."
        for figure in plan.figures
        if figure.central and figure.disposition == "omit"
    ]
    return problems


def content_problems(
    study: StudyMap,
    readings: list[FigureReading],
    deck: str,
    plan: CoveragePlan | None = None,
) -> list[str]:
    """Central, visually read evidence and panel identity must be visible, not just in notes."""
    useful = {r.asset_id for r in readings if r.legible}
    required = {
        f.asset_id for c in study.claims if c.central for f in c.figures if f.asset_id in useful
    }
    if plan:
        required |= {
            f.asset_id for f in plan.figures if f.central and f.disposition in ("teach", "group")
        } & useful
    slides = fmt.split_slides(deck)
    shown: set[str] = set()
    crops: list[tuple[str, list[float]]] = []
    for slide in slides:
        for reference in re.findall(
            r"\((gunther-asset:[^)]+)\)", "\n".join(fmt.parse_slide(slide).body)
        ):
            parts = image_parts(reference)
            if parts:
                shown.add(parts[0])
                if parts[1]:
                    crops.append(parts)
    problems = [
        f"Central claim needs source image gunther-asset:{asset} visible and explained on a slide."
        for asset in sorted(required - shown)
    ]
    by_asset = {r.asset_id: r for r in readings}
    for claim in study.claims:
        if not claim.central:
            continue
        for linked in claim.figures:
            reading = by_asset.get(linked.asset_id)
            if not reading or not reading.legible:
                continue
            known = {panel.label.casefold() for panel in reading.panels if panel.label}
            wanted = {panel.casefold() for panel in linked.panels if panel} & known
            said = "\n".join(
                "\n".join(fmt.parse_slide(s).body) for s in slides if linked.asset_id in s
            ).casefold()
            linked_boxes = {
                tuple(panel.box)
                for panel in reading.panels
                if panel.label.casefold() in wanted and panel.box
            }
            problems += [
                f"Central panel {linked.asset_id}:{panel} is named in the study map but not "
                "identified on a visible slide."
                for panel in sorted(wanted)
                if not re.search(
                    rf"(?m)(?:panel\s+{re.escape(panel)}|\({re.escape(panel)}\)|"
                    rf"(?:^|[\s·|]){re.escape(panel)}:)(?!\w)",
                    said,
                )
                and not any(
                    crop[0] == linked.asset_id
                    and any(
                        len(box) == 4
                        and all(abs(a - b) <= 0.005 for a, b in zip(crop[1], box, strict=True))
                        for box in linked_boxes
                    )
                    for crop in crops
                )
            ]
    return list(dict.fromkeys(problems))


def crop_problems(deck: str, readings: list[FigureReading]) -> list[str]:
    """A crop must come from a panel box actually returned by the image reader."""

    boxes = {
        reading.asset_id: [panel.box for panel in reading.panels if panel.box]
        for reading in readings
    }
    problems = []
    for reference in re.findall(r"\((gunther-asset:[^)]+)\)", fmt.without_design(deck)):
        parts = image_parts(reference)
        if parts is None:
            problems.append(f"Panel crop {reference} is malformed.")
            continue
        if not parts[1]:
            continue
        asset_id, box = parts
        held = boxes.get(asset_id, [])
        if not any(
            len(held_box) == 4
            and all(abs(a - b) <= 0.005 for a, b in zip(box, held_box, strict=True))
            for held_box in held
        ):
            problems.append(
                f"Panel crop {reference} is not retained in that figure's actual pixel reading."
            )
    return list(dict.fromkeys(problems))


def cover_metadata(items: list[Evidence]) -> list[str]:
    """Only source-retained bibliographic fields; missing fields stay missing."""

    rows = source_metadata(items)
    if not rows:
        return []
    fields: dict[str, object] = {}
    for row in rows:
        for key in ("title", "authors", "venue", "year"):
            if row.get(key) not in (None, ""):
                fields[key] = row[key]
    out = [str(fields.get("title") or "")]
    if fields.get("authors"):
        authors = fields["authors"]
        if isinstance(authors, str) and authors.strip().startswith("["):
            try:
                parsed = json.loads(authors)
                if isinstance(parsed, list):
                    authors = parsed
            except ValueError:
                pass
        if isinstance(authors, list):
            names = [str(name).strip() for name in authors if str(name).strip()]
        else:
            names = [name.strip() for name in re.split(r"[;,]| and ", str(authors)) if name.strip()]
        out.append(f"{names[0]} et al." if len(names) > 3 else ", ".join(names) if names else "")
    if fields.get("venue"):
        out.append(str(fields["venue"]))
    if fields.get("year"):
        out.append(str(fields["year"]))
    return out


def apply_source_cover(deck: str, items: list[Evidence]) -> str:
    """The model writes the question; delivery code owns bibliographic metadata."""

    slides = fmt.split_slides(deck)
    if not slides:
        return deck
    first = fmt.parse_slide(slides[0])
    if first.layout != "title":
        return deck
    metadata = cover_metadata(items)
    bibliographic = [field for field in metadata[1:] if field]
    # The whole question stays, however the writer wrapped it; only its own metadata goes.
    # The cover holds one subtitle line and one meta line, so a wrapped question is joined.
    question = " ".join(
        line.strip() for line in first.body if line.strip() and not fmt.cover_meta_line(line)
    )
    body = [question] if question else []
    if bibliographic:
        body.append(" · ".join(bibliographic))
    rebuilt = "#" + (" " if first.title else "") + (
        metadata[0] if metadata and metadata[0].strip() else first.title
    )
    slides[0] = fmt.with_marker(
        "\n".join([rebuilt, *body, *(["Note: " + first.notes] if first.notes else [])]),
        first.layout,
        **first.attrs,
    )
    return "\n\n---\n\n".join(slides)


def cover_problems(deck: str, items: list[Evidence]) -> list[str]:
    """A non-fatal warning if a hand or model edit added an extra cover metadata line."""

    slides = fmt.split_slides(deck)
    if not slides or fmt.parse_slide(slides[0]).layout != "title":
        return []
    lines = [line for line in fmt.parse_slide(slides[0]).body if line.strip()]
    expected = 1 if len(cover_metadata(items)) > 1 else 0
    if sum(fmt.cover_meta_line(line) for line in lines) > expected:
        return [
            "The title slide has unsupported cover metadata; only source-retained "
            "bibliographic fields may appear there."
        ]
    return []


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
    shown = {
        (image_parts(reference) or ("", []))[0]
        for reference in re.findall(r"gunther-asset:[A-Za-z0-9_-]+(?:#[^)\s]+)?", deck)
    }
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
    plan: CoveragePlan | None = None,
) -> list[str]:
    """What code checks before delivery: central figures shown, values readable, scope kept."""

    return (
        content_problems(study, readings, deck, plan)
        + crop_problems(deck, readings)
        + value_problems(deck, items, readings)
        + scope_problems(deck, items, allowed)
        + (coverage_problems(plan, items) if plan else [])
    )


def pool_print(items: list[Evidence]) -> str:
    """Which passages, in which numbering, a build read: a saved study map is reused only for
    the same ones (the same count of different passages would point its refs elsewhere)."""

    import hashlib
    import json

    rows = sorted([item.ref or 0, *item.key] for item in items)
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]
