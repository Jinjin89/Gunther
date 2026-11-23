"""Read the claims and original passages that a sealed origin may contribute."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from gunther import key_results
from gunther.artifact_read import _verified_payload, latest_check_records
from gunther.checks import claim_text, claim_view
from gunther.citations import MARKED, UNSOURCED, collapse_citations
from gunther.claim_checks import check_claims
from gunther.document_structure import evidence_eligible
from gunther.evidence import EVIDENCE_CHARS
from gunther.evidence_reader import passage_block
from gunther.models import (
    Artifact,
    BlockElementMap,
    ContentBlock,
    KeyResultJob,
    KnowledgeProposal,
    KnowledgeSession,
    KnowledgeUnit,
    KnowledgeUnitRevision,
    SessionMessage,
)
from gunther.outputs import document_units, section_hash
from gunther.schemas import ConversationCitationOut


@dataclass(frozen=True)
class Claim:
    text: str
    table_header: str
    citations: tuple[ConversationCitationOut, ...]


def _citations(saved: str) -> list[ConversationCitationOut]:
    return [ConversationCitationOut.model_validate(row) for row in json.loads(saved or "[]")]


def _library_citation(citation: ConversationCitationOut) -> bool:
    return citation.kind == "library" and bool(citation.source_id)


def _claims(rows: Iterable[dict], cited: dict[int, ConversationCitationOut]) -> list[Claim]:
    claims = []
    for row in rows:
        text = row.get("text") or ""
        if UNSOURCED in text or MARKED.search(text):
            continue
        citations = tuple(
            cited[ref] for ref in row.get("refs", [])
            if ref in cited and _library_citation(cited[ref])
        )
        if citations and (plain := claim_text(text)):
            claims.append(Claim(plain, claim_text(row.get("tableHeader") or ""), citations))
    return claims


def _ignore_answer(message: SessionMessage, context: dict) -> bool:
    # Older rework replies stored an empty, skipped inventory without the separate flag.
    rework = context.get("rework", (context.get("coverage") or {}).get("status") == "skipped")
    return (
        message.role != "assistant" or rework
        or (context.get("research") or {}).get("state") == "asking"
    )


def enqueue_answer(session: Session, base_id: str, message: SessionMessage) -> None:
    """Called for a saved answer in a live library, including when Home is filed."""
    if not _ignore_answer(message, json.loads(message.context_json or "{}")) and any(
        _library_citation(citation) for citation in _citations(message.citations_json)
    ):
        key_results.enqueue(session, base_id, "answer", message.id)


def _answer_claims(message: SessionMessage, text: str) -> list[Claim]:
    context = json.loads(message.context_json or "{}")
    if _ignore_answer(message, context):
        return []
    citations = _citations(message.citations_json)
    coverage = context.get("coverage") or {}
    if coverage.get("status") == "complete" and text == message.content:
        if coverage.get("answerHash") != hashlib.sha256(text.encode()).hexdigest():
            return []
        # Complete Ask records carry conversation pool refs. The saved answer's text
        # and citation list use reading order, which is how older citation refs worked.
        by_pool = {citation.ref or n: citation for n, citation in enumerate(citations, 1)}
        return _claims(
            (row for row in coverage.get("claims", []) if row.get("verdict") == "supported"),
            by_pool,
        )
    return _claims(
        check_claims(collapse_citations(text), {}), dict(enumerate(citations, 1)),
    )


def _output_claims(
    artifact: Artifact, records: dict[str, dict],
) -> list[Claim]:
    hashes = {section_hash(unit) for unit in document_units(artifact.content, artifact.kind)}
    citations = _citations(artifact.citations_json)
    return _claims(
        (
            claim_view(row)
            for digest, record in records.items() if digest in hashes
            for row in record["verdicts"].values()
            if row["stage"] == "checker" and row.get("label", row["verdict"]) == "supported"
        ),
        {citation.ref or n: citation for n, citation in enumerate(citations, 1)},
    )


def _supported_pairs(claims: list[Claim]) -> set:
    # Sort order and version-specific citation numbers have no bearing on the evidence.
    return {
        (claim.text, claim.table_header, frozenset(
            (c.source_id, c.source_revision_id, c.block_id, c.support_text or c.quote)
            for c in claim.citations
        ))
        for claim in claims
    }


def enqueue_output(
    session: Session, artifact: Artifact, records: dict[str, dict], *, checked_edit: bool = False,
) -> None:
    if artifact.origin not in ("build", "revise", "rebuild") and not (
        checked_edit and artifact.origin == "edit"
    ):
        return
    claims = _output_claims(artifact, records)
    if not claims:
        return
    if artifact.origin in ("revise", "rebuild") and artifact.supersedes_artifact_id:
        parent = session.get(Artifact, artifact.supersedes_artifact_id)
        if parent and _supported_pairs(claims) == _supported_pairs(
            _output_claims(parent, latest_check_records(session, parent))
        ):
            return
    key_results.enqueue(session, artifact.knowledge_base_id, "output", artifact.id)


def _output(session: Session, job: KeyResultJob) -> Artifact | None:
    return session.scalar(select(Artifact).where(
        Artifact.id == job.origin_id, Artifact.knowledge_base_id == job.knowledge_base_id,
        Artifact.trashed_at.is_(None),
    ))


def _saved_answer(session: Session, job: KeyResultJob):
    return session.execute(
        select(KnowledgeUnitRevision.content, SessionMessage)
        .select_from(KnowledgeUnit)
        .join(KnowledgeUnitRevision, KnowledgeUnitRevision.id == KnowledgeUnit.head_revision_id)
        .join(KnowledgeProposal, KnowledgeProposal.id == KnowledgeUnitRevision.source_proposal_id)
        .join(SessionMessage, SessionMessage.id == KnowledgeProposal.message_id)
        .where(
            KnowledgeUnit.id == job.origin_id,
            KnowledgeUnit.knowledge_base_id == job.knowledge_base_id,
            KnowledgeUnit.status == "trusted",
            KnowledgeUnitRevision.unit_id == KnowledgeUnit.id,
            KnowledgeProposal.knowledge_base_id == job.knowledge_base_id,
        )
    ).one_or_none()


def _answer(session: Session, job: KeyResultJob) -> SessionMessage | None:
    return session.scalar(
        select(SessionMessage)
        .join(KnowledgeSession, KnowledgeSession.id == SessionMessage.session_id)
        .where(SessionMessage.id == job.origin_id,
               KnowledgeSession.knowledge_base_id == job.knowledge_base_id)
    )


def read_claims(session: Session, job: KeyResultJob) -> list[Claim]:
    if job.origin_kind == "output":
        artifact = _output(session, job)
        if artifact is None:
            return []
        _verified_payload(session, artifact)
        return _output_claims(artifact, latest_check_records(session, artifact))
    if job.origin_kind == "saved_answer":
        saved = _saved_answer(session, job)
        return _answer_claims(saved[1], saved[0]) if saved else []
    answer = _answer(session, job)
    return _answer_claims(answer, answer.content) if answer else []


def available(session: Session, job: KeyResultJob) -> bool:
    if job.origin_kind == "output":
        return _output(session, job) is not None
    if job.origin_kind == "saved_answer":
        return _saved_answer(session, job) is not None
    return _answer(session, job) is not None


def _pieces(
    session: Session, citation: ConversationCitationOut,
) -> list[tuple[ContentBlock, str]]:
    block = passage_block(session, citation)
    if block is None:
        return []
    quote = citation.quote if citation.quote and citation.quote in block.content else block.content
    # A saved expanded passage may cover several blocks. Use their original text and
    # anchor each one separately so it can still be inspected and used as evidence.
    pieces = []
    if citation.support_spans:
        refs = [span.get("blockId") for span in citation.support_spans]
        by_id = {
            item.id: item for item in session.scalars(select(ContentBlock).where(
                ContentBlock.revision_id == block.revision_id,
                ContentBlock.id.in_([ref for ref in refs if isinstance(ref, str)]),
            ))
        }
        for span in citation.support_spans:
            ref = span.get("blockId")
            start, end = span.get("start"), span.get("end")
            if (
                isinstance(ref, str) and (part := by_id.get(ref)) is not None
                and span.get("revisionId") == block.revision_id
                and isinstance(start, int) and isinstance(end, int)
                and 0 <= start < end <= len(part.content)
            ):
                pieces.append((part, part.content[start:end]))
    return [
        (part, text[:EVIDENCE_CHARS])
        for part, text in pieces or [(block, quote)]
        if text.strip() and evidence_eligible(part.kind, part.payload_json)
    ]


def read_passages(
    session: Session, base_id: str, claims: list[Claim],
) -> dict[str, tuple[key_results.Passage, ...]]:
    citations = {citation.id: citation for claim in claims for citation in claim.citations}
    sources = key_results.library_sources(
        session, base_id, {c.source_id for c in citations.values()},
    )
    # Only the current text reading can be tied to the source's stored content hash.
    # Earlier file readings can still be tied to the immutable original asset.
    pieces = {
        cite.id: _pieces(session, cite) for cite in citations.values()
        if (original := sources.get(cite.source_id)) is not None and (
            original.asset_id is not None
            or cite.source_revision_id is None
            or cite.source_revision_id == original.head_revision_id
        )
    }
    region_ids: dict[str, list[str]] = defaultdict(list)
    for block_id, element_id in session.execute(
        select(BlockElementMap.block_id, BlockElementMap.element_id)
        .where(BlockElementMap.block_id.in_({b.id for found in pieces.values() for b, _ in found}))
        .order_by(BlockElementMap.char_start, BlockElementMap.element_id)
    ):
        region_ids[block_id].append(element_id)
    return {
        cite.id: tuple(
            key_results.Passage(
                cite.source_id, b.revision_id, b.id, tuple(region_ids[b.id]),
                b.locator, text, sources[cite.source_id],
            )
            for b, text in pieces[cite.id]
        )
        for cite in citations.values() if cite.id in pieces
    }
