"""Factual checks for Studio drafts and saved output parts."""

import logging
import re

from pydantic import BaseModel, Field

from gunther.agent import AskAgent
from gunther.checks import (
    PENDING,
    Verdicts,
    audit_verdict,
    claim_view,
    complete,
    finding,
    has_stage,
    merge_verdicts,
    verdict,
    verdicts_of,
)
from gunther.citations import collapse_citations, leads_in
from gunther.claim_checks import check_claims, check_evidence_hash, checker_inventory
from gunther.evidence import Evidence
from gunther.llm import ModelError, ModelInfo
from gunther.output_shapes import SHAPES, OutputUnit
from gunther.outputs import Issue, _marked, _plain, cited_sentences

logger = logging.getLogger(__name__)

class Unsupported(BaseModel):
    # Numbers of the sentences whose cited passages do not state them; may be empty.
    unsupported: list[int] = Field(default_factory=list, max_length=40)
    supported: list[int] = Field(default_factory=list, max_length=40)

SUPPORT = """\
You are the support-check step of Gunther, a research assistant. Numbered sentences \
follow, each with the passages it cites. List the numbers of the sentences that state \
something the cited passages do not say: a fact, figure, name or cause that is not in \
them, or the opposite of what they say. A sentence that fairly restates or summarises \
its passages is supported. Return both "supported" and "unsupported" lists. Every
numbered sentence must occur in exactly one list; omission is an unchecked claim."""

def support_inventory(text: str, by_ref: dict[int, Evidence]) -> Verdicts:
    rows = {claim["line"]: claim for claim in check_claims(text, by_ref) if "tableRow" in claim}
    items = []
    for start, _, chunk, refs in cited_sentences(text):
        line = text.count("\n", 0, start + len(chunk) - len(chunk.lstrip()))
        table = rows.get(line, {})
        items.append(verdict("support", {
            "text": chunk.strip(), "refs": refs, "evidenceHash": check_evidence_hash(refs, by_ref),
            **{key: table[key] for key in ("tableRow", "tableHeader") if key in table},
        }))
    return verdicts_of(*items)


def output_claim_inventory(
    kind: str, unit: str | OutputUnit, by_ref: dict[int, Evidence], prior: Verdicts | None = None,
    *,
    rest: str | None = None, saved: bool = False, scientific: bool = False,
) -> tuple[str, Verdicts]:
    """Project a unit once, then check that projection against the ordinary evidence pool."""
    shape = SHAPES[kind]
    parts = shape.check_text(unit, refs=by_ref if saved else None, rest=rest)
    if shape.pipeline == "direct":
        parts = [collapse_citations(part) for part in parts]
        spans = []
        start = 0
        # Each projected claim stays together, including its question and citations.
        for part in parts:
            spans.append((start, start + len(part)))
            start += len(part) + 1
        text = "\n".join(parts)
        return text, checker_inventory(text, by_ref, spans=spans)
    text = "\n".join(parts)
    if scientific:
        text = re.sub(r"(?m)^([ \t]*)\d+[.)]\s+", r"\1", text)
    return text, checker_inventory(text, by_ref, prior)


def check_part(
    checker: AskAgent, model: ModelInfo, question: str, text: str,
    by_ref: dict[int, Evidence], *, limit: int, prior: Verdicts | None = None,
    inventory: Verdicts | None = None,
) -> Verdicts:
    """Check the active claims and keep prior outcomes keyed by claim and evidence."""

    previous = prior or {}
    active = checker_inventory(text, by_ref, previous) if inventory is None else inventory
    claims = [claim_view(row) for row in merge_verdicts(active, previous).values()]
    checker.check_claim_records(model, question, text, by_ref, limit=limit, claims=claims)
    checked = verdicts_of(*(verdict("checker", row) for row in claims))
    return merge_verdicts(active, previous, checked)

def _check_support(
    checker: AskAgent, model: ModelInfo, body: str, by_ref: dict[int, Evidence],
    prior: Verdicts, *, limit: int, diagram: bool,
) -> Verdicts:
    active = support_inventory(body, by_ref)
    pending = merge_verdicts(active, prior)
    batches: list[list[tuple[str, str]]] = [[]]
    updates = {}
    size = 0
    for position, (key, row) in enumerate(pending.items()):
        if row["verdict"] not in PENDING:
            continue
        refs = row["refs"]
        if any(ref not in by_ref for ref in refs):
            updates[key] = {
                **row, "verdict": "unsupported",
                "issue": Issue(_plain(row["text"])[:300], "unsupported",
                               "Its number has no source in the list.").out(),
            }
            continue
        if position >= 600:
            continue
        passage = "\n".join([
            _plain(row["text"]),
            *(f"    cites {ref}: {by_ref[ref].context[:6000] or by_ref[ref].text[:900]}"
              for ref in refs),
        ])
        if len(passage) > 23_500:
            continue
        if size + len(passage) + 8 > 24_000 or len(batches[-1]) >= max(
            1, min(40, limit)
        ):
            batches.append([])
            size = 0
        batches[-1].append((key, passage))
        size += len(passage) + 8
    for batch in filter(None, batches):
        lines = [f"[{n}] {passage}" for n, (_, passage) in enumerate(batch, 1)]
        try:
            found, _ = checker.gateway.complete_json(
                model,
                Unsupported,
                system=(
                    SUPPORT
                    + (
                        "\n\nDiagram interpretation: each relation row asserts a directed "
                        "From → Relationship/condition → To claim. Source wording may differ, "
                        "but must support relation and direction. Do not infer from ordering "
                        "or accept a row because its entities alone appear in the source."
                        if diagram
                        else ""
                    )
                ),
                prompt="\n".join(lines),
                effort="off",
            )
        except ModelError as error:
            logger.info("The support check failed: %s", error)
            for key, _ in batch:
                updates[key] = {**pending[key], "verdict": "unavailable"}
            continue
        for n, (key, _) in enumerate(batch, 1):
            row = pending[key]
            if n in found.unsupported:
                updates[key] = {
                    **row, "verdict": "unsupported", "issue": Issue(
                        _plain(row["text"])[:300], "unsupported",
                        "The sources it cites do not state this.",
                    ).out(),
                }
            elif n in found.supported:
                updates[key] = {**row, "verdict": "supported"}
    return merge_verdicts(active, prior, updates)

def check_saved_part(
    checker: AskAgent, model: ModelInfo, text: str | OutputUnit, by_ref: dict[int, Evidence],
    prior: Verdicts | None = None, *, limit: int, kind: str = "report",
) -> Verdicts:
    """Check a saved section and resume the unresolved verdicts for the same text."""
    marked = _marked(text.text if isinstance(text, OutputUnit) else text)
    body = marked.body
    previous = prior or {}
    stages = {k: row for k, row in previous.items() if row["stage"] == "finding"}
    prose = SHAPES[kind].pipeline == "planned"
    if prose and (prior is None or has_stage(previous, "audit", "legacy")):
        audit = verdicts_of(audit_verdict(body, "unchecked"))
        if not complete(merge_verdicts(audit, previous)):
            found = checker.unsourced_claims(model, body)
            audit = verdicts_of(audit_verdict(
                body, "unavailable" if found is None else "supported", found
            ))
        stages |= merge_verdicts(audit, previous)
    if prose and (prior is None or has_stage(previous, "support", "audit", "legacy")):
        stages |= _check_support(
            checker, model, body, by_ref, previous, limit=limit, diagram=marked.layout == "diagram"
        )
    projected, active = output_claim_inventory(kind, text, by_ref, previous, saved=True)
    stages |= verdicts_of(*(finding(Issue(
        claim, "unverified", "From the model's own knowledge; no source was found."
    ).out()) for _, _, claim in leads_in(body if prose else projected, 40)))
    checked = check_part(
        checker, model,
        (
            "Verify this output section. For a diagram, interpret each row as a directed "
            "From → Relationship/condition → To assertion. The source need not use the same "
            "literal node labels or mention a drawn arrow, but it must support the concepts, "
            "direction, and relation. Do not accept a relation inferred from ordering alone."
            if marked.layout == "diagram"
            else "Verify this output section"
        ),
        projected, by_ref, limit=limit, prior=previous, inventory=active,
    )
    for row in checked.values():
        label = row.get("label", row["verdict"])
        if label not in {"supported", "nonfactual"} and "issue" not in row:
            outcome = ("unchecked" if row["verdict"] in PENDING else
                       "unsourced" if label == "unsourced" else "unsupported")
            row["issue"] = Issue(
                _plain(row["text"])[:300], outcome,
                row.get("reason") or "The claim was not fully supported.",
            ).out()
    return stages | checked
