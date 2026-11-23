"""Store checked results and track the contributions of each origin."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

from sqlalchemy import and_, delete, func, literal, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, aliased

from gunther.evidence import Evidence
from gunther.knowledge_index import new_id
from gunther.models import (
    Artifact,
    ContentBlock,
    KeyResult,
    KeyResultEvidence,
    KeyResultJob,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    KnowledgeSession,
    KnowledgeUnit,
    SessionMessage,
    Source,
    utc_now,
)
from gunther.schemas import (
    KeyResultEvidenceOut,
    KeyResultJobOut,
    KeyResultOriginKind,
    KeyResultOriginOut,
    KeyResultOut,
)
from gunther.service_records import _timestamp

INTERRUPTED = "Key results were interrupted. Retry to resume."


class Original(NamedTuple):
    title: str
    content_hash: str
    asset_id: str | None


def library_sources(
    session: Session, base_id: str, source_ids: Collection[str],
) -> dict[str, Original]:
    return {
        source_id: Original(title, content_hash, asset_id)
        for source_id, title, content_hash, asset_id in session.execute(
            select(Source.id, Source.title, Source.content_hash, Source.asset_id)
            .join(KnowledgeBaseSource, KnowledgeBaseSource.source_id == Source.id)
            .where(
                KnowledgeBaseSource.knowledge_base_id == base_id,
                Source.id.in_(source_ids),
                Source.trashed_at.is_(None),
            )
        )
    }


@dataclass(frozen=True)
class Passage:
    source_id: str
    revision_id: str
    block_id: str
    region_ids: tuple[str, ...]
    locator: str
    quote: str
    original: Original

    def evidence(self) -> Evidence:
        return Evidence("library", self.original.title, self.quote, locator=self.locator)


@dataclass(frozen=True)
class CheckedResult:
    text: str
    same_as: str | None
    passages: tuple[Passage, ...]


def enqueue(session: Session, base_id: str, kind: KeyResultOriginKind, origin_id: str) -> None:
    session.execute(
        insert(KeyResultJob)
        .values(id=new_id("krj"), knowledge_base_id=base_id, origin_kind=kind, origin_id=origin_id)
        .on_conflict_do_nothing(index_elements=["origin_kind", "origin_id"])
    )


def requeue_needs_model(session: Session) -> None:
    session.execute(
        update(KeyResultJob)
        .where(KeyResultJob.state == "needs_model")
        .values(state="queued", updated_at=utc_now(), error=None)
    )


def _queue_saved_answers(session: Session) -> None:
    # Keep saved answers and their immutable revisions in place. Only add one job per unit.
    legacy = (
        select(
            "krj_" + func.lower(func.hex(func.randomblob(16))),
            KnowledgeUnit.knowledge_base_id,
            KnowledgeUnit.id,
            literal("saved_answer"),
        )
        .join(KnowledgeBaseRecord, KnowledgeBaseRecord.id == KnowledgeUnit.knowledge_base_id)
        .where(
            KnowledgeBaseRecord.trashed_at.is_(None),
            KnowledgeUnit.status == "trusted",
            ~select(KeyResultJob.id).where(
                KeyResultJob.origin_kind == "saved_answer",
                KeyResultJob.origin_id == KnowledgeUnit.id,
            ).exists(),
        )
    )
    session.execute(
        insert(KeyResultJob)
        .from_select(
            ["id", "knowledge_base_id", "origin_id", "origin_kind"],
            legacy,
        )
        .on_conflict_do_nothing(index_elements=["origin_kind", "origin_id"])
    )


def claim(
    session: Session, *, configured: bool, lease: timedelta,
) -> tuple[KeyResultJob | None, list[KeyResultJob]]:
    now = utc_now()
    if configured:
        requeue_needs_model(session)
        _queue_saved_answers(session)
    interrupted = list(session.scalars(
        update(KeyResultJob)
        .where(KeyResultJob.state == "running", KeyResultJob.updated_at <= now - lease)
        .values(state="failed", error=INTERRUPTED, updated_at=now)
        .returning(KeyResultJob)
    ))
    running = aliased(KeyResultJob)
    candidate = (
        select(KeyResultJob.id)
        .join(KnowledgeBaseRecord, KnowledgeBaseRecord.id == KeyResultJob.knowledge_base_id)
        .where(
            KeyResultJob.state == "queued",
            KnowledgeBaseRecord.trashed_at.is_(None),
            ~select(running.id).where(
                running.knowledge_base_id == KeyResultJob.knowledge_base_id,
                running.state == "running",
            ).exists(),
        )
        .order_by(KeyResultJob.created_at, KeyResultJob.id)
        .limit(1)
        .scalar_subquery()
    )
    claimed = session.scalar(
        update(KeyResultJob).where(KeyResultJob.id == candidate)
        .values(state="running", attempts=KeyResultJob.attempts + 1, updated_at=now)
        .returning(KeyResultJob)
    )
    return claimed, interrupted


def owned(job: KeyResultJob, now: datetime, lease: timedelta):
    # Attempts only increase, so a late model reply cannot publish into a newer attempt.
    return and_(
        KeyResultJob.id == job.id,
        KeyResultJob.state == "running",
        KeyResultJob.attempts == job.attempts,
        KeyResultJob.updated_at > now - lease,
    )


def related_results(
    session: Session, base_id: str, source_ids: Collection[str],
) -> list[KeyResult]:
    """Filter by library and a shared original source before taking each status's limit."""
    overlapping = select(KeyResultEvidence.id).where(
        KeyResultEvidence.key_result_id == KeyResult.id,
        KeyResultEvidence.source_id.in_(source_ids),
    ).exists()
    return [
        item
        for status, limit in (("kept", 25), ("removed", 15))
        for item in session.scalars(
            select(KeyResult)
            .where(KeyResult.knowledge_base_id == base_id, KeyResult.status == status, overlapping)
            .order_by(KeyResult.created_at.desc(), KeyResult.id.desc())
            .limit(limit)
        )
    ]


def _passage_key(passage: Passage | KeyResultEvidence) -> tuple[str, str, str, str]:
    return passage.source_id, passage.revision_id, passage.block_id, passage.quote


def publish(session: Session, job: KeyResultJob, checked: Sequence[CheckedResult]) -> int:
    current = library_sources(
        session, job.knowledge_base_id, {p.source_id for item in checked for p in item.passages},
    )
    existing = list(session.scalars(
        select(KeyResult).where(
            KeyResult.knowledge_base_id == job.knowledge_base_id,
            or_(
                KeyResult.id.in_({item.same_as for item in checked if item.same_as}),
                KeyResult.text.in_({item.text for item in checked if not item.same_as}),
            ),
        )
    ))
    by_id = {item.id: item for item in existing}
    by_text = {item.text: item for item in existing}
    known: dict[str, set[tuple[str, str, str, str]]] = defaultdict(set)
    for link in session.scalars(
        select(KeyResultEvidence).where(KeyResultEvidence.key_result_id.in_(by_id))
    ):
        known[link.key_result_id].add(_passage_key(link))

    added: dict[str, set[tuple[str, str, str, str]]] = defaultdict(set)
    for item in checked:
        if any(
            (source := current.get(p.source_id)) is None
            or (source.content_hash, source.asset_id)
            != (p.original.content_hash, p.original.asset_id)
            for p in item.passages
        ):
            continue
        result = by_id.get(item.same_as) if item.same_as else by_text.get(item.text)
        if item.same_as and result is None:
            continue
        if result is None:
            result = KeyResult(
                id=new_id("kr"), knowledge_base_id=job.knowledge_base_id,
                text=item.text, status="kept",
                created_origin_kind=job.origin_kind, created_origin_id=job.origin_id,
            )
            session.add(result)
            session.flush()
            by_text[item.text] = result
        if result.status != "kept":
            continue
        keys = {_passage_key(p) for p in item.passages}
        if keys <= known[result.id]:
            continue
        for p in item.passages:
            key = _passage_key(p)
            if key in added[result.id]:
                continue
            # Store the full set checked for this origin, including any shared passages.
            session.add(KeyResultEvidence(
                id=new_id("kre"), key_result_id=result.id, source_id=p.source_id,
                revision_id=p.revision_id, block_id=p.block_id,
                region_ids_json=json.dumps(p.region_ids), quote=p.quote,
                source_content_hash=p.original.content_hash, source_asset_id=p.original.asset_id,
                origin_kind=job.origin_kind, origin_id=job.origin_id,
            ))
            added[result.id].add(key)
        known[result.id].update(keys)
        result.updated_at = utc_now()
    return len(added)


def origin_states(
    session: Session, kind: KeyResultOriginKind, ids: Collection[str],
) -> dict[str, KeyResultJobOut]:
    if not ids:
        return {}
    jobs: dict[str, KeyResultJob] = {}
    kept: dict[str, list[str]] = defaultdict(list)
    for job, result_id in session.execute(
        select(KeyResultJob, KeyResult.id)
        .select_from(KeyResultJob)
        .outerjoin(KeyResultEvidence, and_(
            KeyResultEvidence.origin_kind == KeyResultJob.origin_kind,
            KeyResultEvidence.origin_id == KeyResultJob.origin_id,
        ))
        .outerjoin(KeyResult, and_(
            KeyResult.id == KeyResultEvidence.key_result_id,
            KeyResult.knowledge_base_id == KeyResultJob.knowledge_base_id,
            KeyResult.status == "kept",
        ))
        .where(KeyResultJob.origin_kind == kind, KeyResultJob.origin_id.in_(ids))
        .distinct().order_by(KeyResult.id)
    ):
        jobs[job.origin_id] = job
        if result_id is not None:
            kept[job.origin_id].append(result_id)
    return {
        job.origin_id: KeyResultJobOut(
            id=job.id, origin_kind=job.origin_kind, origin_id=job.origin_id, state=job.state,
            error=job.error, kept_count=len(kept[job.origin_id]), kept_ids=kept[job.origin_id],
        )
        for job in jobs.values()
    }


def undo(session: Session, job: KeyResultJob) -> None:
    session.execute(
        update(KeyResult)
        .where(
            KeyResult.knowledge_base_id == job.knowledge_base_id,
            KeyResult.created_origin_kind == job.origin_kind,
            KeyResult.created_origin_id == job.origin_id,
            KeyResult.status == "kept",
        )
        .values(status="removed", updated_at=utc_now())
    )
    session.execute(
        delete(KeyResultEvidence).where(
            KeyResultEvidence.key_result_id.in_(
                select(KeyResult.id).where(KeyResult.knowledge_base_id == job.knowledge_base_id)
            ),
            KeyResultEvidence.origin_kind == job.origin_kind,
            KeyResultEvidence.origin_id == job.origin_id,
        )
    )


def list_kept(session: Session, base_id: str) -> list[KeyResultOut]:
    results: dict[str, KeyResult] = {}
    evidence: dict[str, list[KeyResultEvidenceOut]] = defaultdict(list)
    for item, link, title, locator in session.execute(
        select(KeyResult, KeyResultEvidence, Source.title, ContentBlock.locator)
        .join(KeyResultEvidence, KeyResult.id == KeyResultEvidence.key_result_id)
        .outerjoin(Source, Source.id == KeyResultEvidence.source_id)
        .outerjoin(ContentBlock, ContentBlock.id == KeyResultEvidence.block_id)
        .where(KeyResult.knowledge_base_id == base_id, KeyResult.status == "kept")
        .order_by(KeyResult.created_at.desc(), KeyResult.id.desc(),
                  KeyResultEvidence.created_at, KeyResultEvidence.id)
    ):
        results[item.id] = item
        evidence[item.id].append(KeyResultEvidenceOut(
            id=link.id, source_id=link.source_id, revision_id=link.revision_id,
            block_id=link.block_id, region_ids=json.loads(link.region_ids_json),
            source_title=title, locator=locator, quote=link.quote,
        ))

    ids: dict[str, set[str]] = defaultdict(set)
    for item in results.values():
        ids[item.created_origin_kind].add(item.created_origin_id)
    origins = {
        ("answer", message_id): (title, session_id, created_at)
        for message_id, title, session_id, created_at in session.execute(
            select(SessionMessage.id, KnowledgeSession.title, SessionMessage.session_id,
                   SessionMessage.created_at)
            .join(KnowledgeSession, KnowledgeSession.id == SessionMessage.session_id)
            .where(KnowledgeSession.knowledge_base_id == base_id,
                   SessionMessage.id.in_(ids["answer"]))
        )
    }
    for kind, model in (("output", Artifact), ("saved_answer", KnowledgeUnit)):
        origins.update({
            (kind, origin_id): (title, None, created_at)
            for origin_id, title, created_at in session.execute(
                select(model.id, model.title, model.created_at)
                .where(model.knowledge_base_id == base_id, model.id.in_(ids[kind]))
            )
        })
    items = []
    for item in results.values():
        kind, origin_id = item.created_origin_kind, item.created_origin_id
        title, session_id, created_at = origins.get(
            (kind, origin_id), (None, None, item.created_at),
        )
        items.append(KeyResultOut(
            id=item.id, text=item.text, evidence=evidence[item.id],
            origin=KeyResultOriginOut(
                kind=kind, id=origin_id, title=title, session_id=session_id,
                created_at=_timestamp(created_at),
            ),
        ))
    return items
