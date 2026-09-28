"""Transactional structured evidence and scoped hybrid retrieval.

FTS is the always-available baseline. Semantic retrieval adds a local model
(see embedding) and vectors in sqlite-vec (see vector_index); when either is
unavailable, retrieval falls back to keywords and never blocks originals.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Protocol
from uuid import uuid4

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session, sessionmaker

from gunther import vector_index
from gunther.content import ParsedBlock, parse_content, search_tokens
from gunther.database import session_scope
from gunther.models import (
    ContentBlock,
    ProcessingJob,
    Source,
    SourceIndexHead,
    SourceRevision,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Embedder(Protocol):
    model_id: str

    def encode(self, texts: list[str], *, query: bool = False) -> list[list[float]]: ...


@dataclass
class RetrievalHit:
    block: ContentBlock
    source: Source
    score: float
    method: str


def enqueue(
    session: Session,
    source_id: str,
    kind: str,
    key: str,
    *,
    revision_id: str | None = None,
    model_id: str | None = None,
) -> ProcessingJob:
    existing = session.scalar(select(ProcessingJob).where(ProcessingJob.dedupe_key == key))
    if existing:
        return existing
    job = ProcessingJob(
        id=new_id("job"),
        source_id=source_id,
        kind=kind,
        dedupe_key=key,
        revision_id=revision_id,
        model_id=model_id,
    )
    session.add(job)
    return job


class KnowledgeIndex:
    def __init__(self, sessions: sessionmaker[Session], embedder: Embedder | None = None):
        self.sessions = sessions
        self.embedder = embedder
        self.semantic_error: str | None = None
        # Why semantic search is off, when it is; shown in Settings.
        self.semantic_off_reason: str | None = None

    def publish(
        self,
        session: Session,
        source: Source,
        blocks: list[ParsedBlock],
        *,
        parser: str = "gunther-text-v1",
        warning: str | None = None,
    ) -> SourceRevision:
        fingerprint = hashlib.sha256(
            json.dumps(
                {"blocks": [asdict(b) for b in blocks], "parser": parser, "warning": warning},
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        revision = session.scalar(
            select(SourceRevision).where(
                SourceRevision.source_id == source.id, SourceRevision.fingerprint == fingerprint
            )
        )
        if revision is None:
            revision = SourceRevision(
                id=new_id("srev"),
                source_id=source.id,
                fingerprint=fingerprint,
                parser=parser,
                state="partial" if warning or not blocks else "ready",
                warning=warning or ("No readable content was extracted." if not blocks else None),
            )
            session.add(revision)
            session.flush()
            ids = [new_id("blk") for _ in blocks]
            for ordinal, parsed in enumerate(blocks):
                if parsed.parent is not None and not 0 <= parsed.parent < ordinal:
                    raise ValueError("A content block parent must precede its child")
                block = ContentBlock(
                    id=ids[ordinal],
                    revision_id=revision.id,
                    parent_id=ids[parsed.parent] if parsed.parent is not None else None,
                    ordinal=ordinal,
                    kind=parsed.kind,
                    content=parsed.text,
                    locator=parsed.locator[:240],
                    heading_path_json=json.dumps(parsed.headings),
                    anchor_json=json.dumps(parsed.anchor),
                    payload_json=json.dumps(parsed.payload, ensure_ascii=False),
                )
                session.add(block)
                # Flush parents before children for the self-referential foreign key.
                session.flush()
                if parsed.kind != "heading":
                    session.execute(
                        text(
                            "INSERT INTO knowledge_fts(block_id,source_id,revision_id,tokens) "
                            "VALUES (:block,:source,:revision,:tokens)"
                        ),
                        {
                            "block": block.id,
                            "source": source.id,
                            "revision": revision.id,
                            "tokens": " ".join(search_tokens(parsed.text)),
                        },
                    )
        head = session.get(SourceIndexHead, source.id)
        if head:
            head.revision_id = revision.id
        else:
            session.add(SourceIndexHead(source_id=source.id, revision_id=revision.id))
        if self.embedder and blocks:
            enqueue(
                session,
                source.id,
                "embed",
                f"embed:{revision.id}:{self.embedder.model_id}",
                revision_id=revision.id,
                model_id=self.embedder.model_id,
            )
        session.flush()
        return revision

    def add_source(self, session: Session, source: Source) -> None:
        self.publish(session, source, parse_content(source.content))

    def backfill(self, limit: int = 50) -> int:
        """Bounded startup batches; every commit is independently restartable."""
        with session_scope(self.sessions) as session:
            sources = session.scalars(
                select(Source)
                .where(
                    Source.trashed_at.is_(None),
                    ~Source.id.in_(select(SourceIndexHead.source_id)),
                    ~Source.id.in_(
                        select(ProcessingJob.source_id).where(ProcessingJob.kind == "parse_asset")
                    ),
                )
                .limit(limit)
            ).all()
            for source in sources:
                self.add_source(session, source)
            if self.embedder:
                heads = session.scalars(
                    select(SourceIndexHead)
                    .where(
                        ~SourceIndexHead.revision_id.in_(
                            select(ProcessingJob.revision_id).where(
                                ProcessingJob.kind == "embed",
                                ProcessingJob.model_id == self.embedder.model_id,
                                ProcessingJob.revision_id.is_not(None),
                            )
                        )
                    )
                    .limit(limit)
                )
                for head in heads:
                    enqueue(
                        session,
                        head.source_id,
                        "embed",
                        f"embed:{head.revision_id}:{self.embedder.model_id}",
                        revision_id=head.revision_id,
                        model_id=self.embedder.model_id,
                    )
            return len(sources)

    def retrieve(
        self,
        session: Session,
        source_ids: list[str],
        question: str,
        *,
        limit: int = 8,
        block_ids: list[str] | None = None,
    ) -> list[RetrievalHit]:
        if not source_ids or block_ids == []:
            return []
        terms = list(dict.fromkeys(search_tokens(question)))[:32]
        if not terms:
            return []
        clause = " AND f.block_id IN :blocks" if block_ids is not None else ""
        head_join = (
            "JOIN source_index_heads h ON h.source_id=f.source_id AND h.revision_id=f.revision_id "
            if block_ids is None
            else ""
        )
        statement = text(
            "SELECT f.block_id, bm25(knowledge_fts) AS rank FROM knowledge_fts AS f "
            + head_join
            + "WHERE knowledge_fts MATCH :query "
            "AND f.source_id IN :sources" + clause + " ORDER BY rank LIMIT 64"
        ).bindparams(bindparam("sources", expanding=True))
        params: dict[str, object] = {
            "query": " OR ".join('"' + term.replace('"', '""') + '"' for term in terms),
            "sources": source_ids,
        }
        if block_ids is not None:
            statement = statement.bindparams(bindparam("blocks", expanding=True))
            params["blocks"] = block_ids
        rows = session.execute(statement, params).all()
        ranked: dict[str, float] = {}
        methods: dict[str, str] = {}
        for rank, (block_id, _) in enumerate(rows):
            block = session.get(ContentBlock, block_id)
            matched = set(terms).intersection(search_tokens(block.content))
            if len(matched) < min(2, len(terms)):
                continue
            ranked[block_id] = 1 / (60 + rank)
            methods[block_id] = "keyword"
        if self.embedder:
            try:
                query = self.embedder.encode([question], query=True)[0]
                if not query or not all(math.isfinite(value) for value in query):
                    raise ValueError("Invalid query embedding")
                if block_ids is not None:
                    nearest = vector_index.nearest_among(
                        session,
                        model=self.embedder.model_id,
                        query=query,
                        block_ids=block_ids,
                        source_ids=source_ids,
                        k=32,
                    )
                else:
                    heads = session.scalars(
                        select(SourceIndexHead.revision_id).where(
                            SourceIndexHead.source_id.in_(source_ids)
                        )
                    ).all()
                    nearest = vector_index.nearest(
                        session,
                        model=self.embedder.model_id,
                        query=query,
                        revision_ids=heads,
                        k=32,
                    )
                # Each model scores unrelated text differently; E5 rarely goes below 0.7.
                floor = getattr(self.embedder, "min_similarity", 0.75)
                for rank, (block_id, similarity) in enumerate(nearest):
                    if similarity < floor:
                        continue
                    ranked[block_id] = ranked.get(block_id, 0) + 1 / (60 + rank)
                    methods[block_id] = "hybrid" if block_id in methods else "semantic"
                self.semantic_error = None
            except Exception as error:
                self.semantic_error = f"Semantic retrieval unavailable ({type(error).__name__})."
        hits: list[RetrievalHit] = []
        counts: Counter[str] = Counter()
        for block_id, score in sorted(ranked.items(), key=lambda item: item[1], reverse=True):
            block = session.get(ContentBlock, block_id)
            revision = session.get(SourceRevision, block.revision_id)
            source = session.get(Source, revision.source_id)
            if counts[source.id] >= 3:
                continue
            hits.append(RetrievalHit(block, source, score, methods[block_id]))
            counts[source.id] += 1
            if len(hits) >= limit:
                break
        return hits

    def processing(self, session: Session, source_id: str) -> dict[str, object]:
        job = session.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.source_id == source_id, ProcessingJob.kind == "parse_asset")
            .order_by(ProcessingJob.created_at.desc())
        )
        head = session.get(SourceIndexHead, source_id)
        revision = session.get(SourceRevision, head.revision_id) if head else None
        return {
            "state": job.state
            if job and job.state != "completed"
            else (revision.state if revision else "pending"),
            "jobId": job.id if job else None,
            "revisionId": revision.id if revision else None,
            "warning": (job.error if job else None) or (revision.warning if revision else None),
        }
