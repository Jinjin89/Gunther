"""Store checked results and track the contributions of each origin."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

from sqlalchemy import and_, case, delete, func, literal, or_, select, tuple_, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, aliased

from gunther import key_result_index
from gunther.content import search_tokens
from gunther.document_structure import evidence_eligible
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
    SourceIndexHead,
    SourceRevision,
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
    head_revision_id: str | None


def library_sources(
    session: Session, base_id: str, source_ids: Collection[str],
) -> dict[str, Original]:
    return {
        source_id: Original(title, content_hash, asset_id, head_revision_id)
        for source_id, title, content_hash, asset_id, head_revision_id in session.execute(
            select(
                Source.id, Source.title, Source.content_hash, Source.asset_id,
                SourceIndexHead.revision_id,
            )
            .select_from(Source)
            .join(KnowledgeBaseSource, KnowledgeBaseSource.source_id == Source.id)
            .outerjoin(SourceIndexHead, SourceIndexHead.source_id == Source.id)
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


PassageKey = tuple[str, str, str, str, str]


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


def _ids(values: Collection[str]):
    return select(func.json_each(json.dumps(list(values))).table_valued("value").c.value)


def usable_links(*conditions, source_ids: Collection[str] | None = None):
    """The evidence of complete, usable origin groups. Conditions scope whole groups."""
    link = KeyResultEvidence
    original = case(
        (link.source_asset_id.is_not(None), Source.asset_id == link.source_asset_id),
        else_=and_(Source.asset_id.is_(None), Source.content_hash == link.source_content_hash),
    )
    in_scope = Source.id.in_(_ids(source_ids)) if source_ids is not None else literal(True)
    usable = and_(
        Source.trashed_at.is_(None), KnowledgeBaseSource.id.is_not(None), in_scope,
        evidence_eligible(ContentBlock.kind, ContentBlock.payload_json), original,
    )
    checked = (
        select(
            link.id, link.key_result_id, link.source_id, link.origin_kind, link.origin_id,
            func.min(case((usable, 1), else_=0)).over(
                partition_by=(link.key_result_id, link.origin_kind, link.origin_id)
            ).label("usable"),
        )
        .select_from(link)
        .join(KeyResult, KeyResult.id == link.key_result_id)
        .join(KnowledgeBaseRecord, KnowledgeBaseRecord.id == KeyResult.knowledge_base_id)
        .outerjoin(Source, Source.id == link.source_id)
        .outerjoin(KnowledgeBaseSource, and_(
            KnowledgeBaseSource.source_id == Source.id,
            KnowledgeBaseSource.knowledge_base_id == KeyResult.knowledge_base_id,
        ))
        .outerjoin(SourceRevision, and_(
            SourceRevision.id == link.revision_id, SourceRevision.source_id == Source.id,
        ))
        .outerjoin(ContentBlock, and_(
            ContentBlock.id == link.block_id, ContentBlock.revision_id == SourceRevision.id,
        ))
        .where(KnowledgeBaseRecord.trashed_at.is_(None), *conditions)
        .subquery()
    )
    return select(
        checked.c.id, checked.c.key_result_id, checked.c.source_id,
        checked.c.origin_kind, checked.c.origin_id,
    ).where(checked.c.usable == 1).subquery()


def _related(base_id: str, source_ids: Collection[str]):
    links = usable_links(KeyResult.knowledge_base_id == base_id)
    return select(KeyResult).where(
        KeyResult.id.in_(select(links.c.key_result_id).where(links.c.source_id.in_(_ids(source_ids))))
    )


def related_removed(
    session: Session, base_id: str, source_ids: Collection[str],
) -> list[tuple[str, str]]:
    return [(result_id, sentence) for result_id, sentence in session.execute(
        _related(base_id, source_ids).where(KeyResult.status == "removed")
        .with_only_columns(KeyResult.id, KeyResult.text).order_by(KeyResult.id)
    )]


def _fuse(first: Sequence[str], second: Sequence[str], limit: int) -> list[str]:
    # Reciprocal rank fusion, as in passage retrieval.
    scores: dict[str, float] = defaultdict(float)
    for ranking in (first, second):
        for rank, result_id in enumerate(ranking):
            scores[result_id] += 1 / (60 + rank)
    return sorted(scores, key=scores.__getitem__, reverse=True)[:limit]


def related_results(
    session: Session, base_id: str, source_ids: Collection[str],
) -> list[KeyResult]:
    """Rank shared-source results using the query prepared before this session opened."""
    query: key_result_index.Query | None = session.info.get(key_result_index.QUERY_INFO)
    if query is None or not source_ids:
        return []
    kept_ids = list(session.scalars(
        _related(base_id, source_ids).where(KeyResult.status == "kept")
        .with_only_columns(KeyResult.id).order_by(KeyResult.id)
    ))
    kept = _fuse(
        key_result_index.keyword(session, query.terms, kept_ids),
        key_result_index.semantic(session, query, kept_ids),
        25,
    )
    removed = related_removed(session, base_id, source_ids)
    terms = set(query.terms)
    lexical = [(k, len(terms.intersection(search_tokens(sentence)))) for k, sentence in removed]
    lexical.sort(key=lambda hit: hit[1], reverse=True)
    removed_ids = {result_id for result_id, _ in removed}
    suppressed = _fuse(
        [result_id for result_id, matches in lexical if matches],
        [result_id for result_id in query.removed_rank if result_id in removed_ids],
        15,
    )
    ranked = kept + suppressed
    results = {item.id: item for item in session.scalars(
        select(KeyResult).where(KeyResult.id.in_(ranked))
    )}
    return [results[result_id] for result_id in ranked if result_id in results]


def _passage_key(passage: Passage | KeyResultEvidence) -> PassageKey:
    original = (
        (passage.original.asset_id or passage.original.content_hash) if isinstance(passage, Passage)
        else (passage.source_asset_id or passage.source_content_hash)
    )
    return passage.source_id, passage.revision_id, passage.block_id, passage.quote, original


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
    known: dict[str, dict[tuple[str, str], set[PassageKey]]] = defaultdict(
        lambda: defaultdict(set),
    )
    for link in session.scalars(
        select(KeyResultEvidence).where(KeyResultEvidence.key_result_id.in_(by_id))
    ):
        known[link.key_result_id][link.origin_kind, link.origin_id].add(_passage_key(link))

    added: dict[str, set[PassageKey]] = defaultdict(set)
    for item in checked:
        if any(
            (source := current.get(p.source_id)) is None
            or source.asset_id != p.original.asset_id
            or (source.asset_id is None and source.content_hash != p.original.content_hash)
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
            key_result_index.write_fts(session, result.id, result.text)
            by_text[item.text] = result
        if result.status != "kept":
            continue
        keys = {_passage_key(p) for p in item.passages}
        if keys in known[result.id].values():
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
        known[result.id][job.origin_kind, job.origin_id] = added[result.id]
        result.updated_at = utc_now()
    return len(added)


def origin_states(
    session: Session, kind: KeyResultOriginKind, ids: Collection[str],
) -> dict[str, KeyResultJobOut]:
    if not ids:
        return {}
    links = usable_links(
        KeyResultEvidence.origin_kind == kind, KeyResultEvidence.origin_id.in_(ids),
    )
    jobs: dict[str, KeyResultJob] = {}
    kept: dict[str, list[str]] = defaultdict(list)
    for job, result_id in session.execute(
        select(KeyResultJob, KeyResult.id)
        .select_from(KeyResultJob)
        .outerjoin(links, and_(
            links.c.origin_kind == KeyResultJob.origin_kind,
            links.c.origin_id == KeyResultJob.origin_id,
        ))
        .outerjoin(KeyResult, and_(
            KeyResult.id == links.c.key_result_id,
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
    # Invalidate any removal toast even if this origin's sentence was already removed.
    removed = list(session.scalars(
        update(KeyResult)
        .where(
            KeyResult.knowledge_base_id == job.knowledge_base_id,
            KeyResult.created_origin_kind == job.origin_kind,
            KeyResult.created_origin_id == job.origin_id,
        )
        .values(status="removed", updated_at=utc_now())
        .returning(KeyResult.id)
    ))
    key_result_index.forget(session, removed)
    session.execute(
        delete(KeyResultEvidence).where(
            KeyResultEvidence.key_result_id.in_(
                select(KeyResult.id).where(KeyResult.knowledge_base_id == job.knowledge_base_id)
            ),
            KeyResultEvidence.origin_kind == job.origin_kind,
            KeyResultEvidence.origin_id == job.origin_id,
        )
    )


def purge(session: Session, base_ids: list[str], source_ids: list[str]) -> None:
    if not base_ids and not source_ids:
        return
    library_results = select(KeyResult.id).where(KeyResult.knowledge_base_id.in_(_ids(base_ids)))
    link = KeyResultEvidence
    groups = select(link.key_result_id, link.origin_kind, link.origin_id).where(
        link.source_id.in_(_ids(source_ids)),
    ).distinct()
    affected = list(session.scalars(select(groups.subquery().c.key_result_id).distinct()))
    key_result_index.forget(session, list(set(affected).union(session.scalars(library_results))))
    if not affected:
        return
    # A group loses its proof if even one required original is permanently deleted.
    # Snapshot the matching groups before deleting any of their evidence rows.
    lost = groups.cte().prefix_with("MATERIALIZED")
    session.execute(delete(link).where(
        tuple_(link.key_result_id, link.origin_kind, link.origin_id).in_(select(lost)),
    ))
    session.execute(delete(KeyResult).where(
        KeyResult.id.in_(_ids(affected)),
        ~select(link.id).where(link.key_result_id == KeyResult.id).exists(),
    ))
    for result_id, sentence in session.execute(
        select(KeyResult.id, KeyResult.text).where(
            KeyResult.status == "kept", KeyResult.id.in_(_ids(affected)),
            KeyResult.knowledge_base_id.not_in(_ids(base_ids)),
        )
    ):
        key_result_index.write_fts(session, result_id, sentence)


def list_kept(
    session: Session, base_id: str, source_ids: Collection[str] | None = None,
) -> list[KeyResultOut]:
    results: dict[str, KeyResult] = {}
    evidence: dict[str, list[KeyResultEvidenceOut]] = defaultdict(list)
    links = usable_links(KeyResult.knowledge_base_id == base_id, source_ids=source_ids)
    for item, link, title, locator in session.execute(
        select(KeyResult, KeyResultEvidence, Source.title, ContentBlock.locator)
        .join(KeyResultEvidence, KeyResult.id == KeyResultEvidence.key_result_id)
        .join(links, links.c.id == KeyResultEvidence.id)
        .join(Source, Source.id == KeyResultEvidence.source_id)
        .join(ContentBlock, ContentBlock.id == KeyResultEvidence.block_id)
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

    if not results:
        return []
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
