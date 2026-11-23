"""Assemble output text, citation numbers, and the saved outline."""

from collections.abc import Iterable, Mapping, Sequence

from gunther.citations import check_citations, renumber_citations
from gunther.evidence import Evidence
from gunther.llm import ModelError, ModelInfo
from gunther.model_profiles import Effort
from gunther.output_types import SHAPES, output_type_for
from gunther.outputs import (
    BuiltOutput,
    Draft,
    Planned,
    Pool,
    Spec,
    chart_unverified,
    join_document,
    layout_of,
    numbered_check,
    title_of,
    unmarked,
    with_layout,
)
from gunther.schemas import ConversationCitationOut
from gunther.skill_checks import CHARTS
from gunther.slide_scene import rebind_graphics_hash


def project_outline(
    outline: Sequence[Planned], *, include_ids: bool = True, include_layout: bool = True,
) -> list[dict[str, str]]:
    return [
        {"heading": item.heading, "goal": item.goal}
        | ({"id": item.id} if include_ids and item.id else {})
        | ({"layout": item.layout} if include_layout and item.layout else {})
        for item in outline
    ]


def number_output_citations(
    texts: Iterable[str], by_ref: Mapping[int, Evidence], *,
    saved: Mapping[int, Evidence] | None = None,
) -> tuple[dict[int, int], list[ConversationCitationOut]]:
    """Keep the saved numbers and add newly cited passages in order of first use."""

    held = saved or {}
    numbers = {ref: ref for ref in held}
    citations = [item.payload[0] for item in held.values()]
    next_ref = max(numbers, default=0)
    valid = set(by_ref)
    for text in texts:
        _, used = check_citations(text, valid)
        for ref in used:
            if ref not in numbers:
                next_ref += 1
                numbers[ref] = next_ref
                citations.append(by_ref[ref].payload[0].model_copy(update={"ref": next_ref}))
    return numbers, citations


def number_output_part(text: str, numbers: dict[int, int]) -> str:
    numbered = renumber_citations(text, numbers)
    return rebind_graphics_hash(text, numbered, numbers)


def assemble_output(
    spec: Spec, title: str, preamble: str, drafts: Sequence[Draft], pool: Pool,
    outline: Sequence[Planned], model: ModelInfo, effort: Effort | None,
    notes: Sequence[str] = (),
) -> BuiltOutput:
    """Build one document with its passages numbered in reading order."""

    by_ref = pool.by_ref()
    for index, (draft, plan) in enumerate(zip(drafts, outline, strict=True)):
        # The rendered layout controls chart checks, with the plan as a fallback.
        drawn = (layout_of(draft.text)[0] if spec.kind == "slides" else None) or plan.layout
        if drawn not in CHARTS:
            continue
        _, reason = chart_unverified(
            with_layout(unmarked(draft.text, spec), drawn),
            draft.issues,
            draft.coverage,
            by_ref,
            index,
        )
        if reason is not None:
            raise ModelError("Chart values must be verified before saving. " + reason)
    raw = join_document(preamble, [draft.text for draft in drafts], spec.kind)
    numbers, citations = number_output_citations([raw], by_ref)
    valid = set(by_ref)
    final = [number_output_part(check_citations(draft.text, valid)[0], numbers) for draft in drafts]
    content = join_document(preamble, final, spec.kind)
    if SHAPES[spec.kind].pipeline == "direct":
        problems = output_type_for(spec.kind, spec.output_type).direct_problems(content)
        if problems:
            raise ModelError("\n".join(problems))
    checks = [
        numbered_check(draft, text, numbers)
        for text, draft in zip(final, drafts, strict=True)
        if draft.issues is not None
    ]
    return BuiltOutput(
        title=title_of(content) or title,
        content=content,
        outline=project_outline(outline),
        citations=citations,
        checks=checks,
        model=model,
        effort=effort,
        notes=tuple(notes),
        pool_refs=tuple(numbers),
    )
