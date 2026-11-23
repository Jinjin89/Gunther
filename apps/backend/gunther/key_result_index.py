"""Transactionally indexed sentences and the local embedding work for key results."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from gunther import vector_index
from gunther.content import search_tokens
from gunther.database import session_scope
from gunther.knowledge_index import Embedder
from gunther.models import KeyResult

logger = logging.getLogger(__name__)
# Match the source embedding batch size and the full-text candidate limit.
EMBED_BATCH = 16
RANK_DEPTH = 64
QUERY_INFO = "key_result_query"


@dataclass(frozen=True)
class Query:
    terms: tuple[str, ...]
    model: str | None = None
    vector: tuple[float, ...] = ()
    removed_rank: tuple[str, ...] = ()
    min_similarity: float = 0.75


def _encode(
    embedder: Embedder, texts: list[str], *, query: bool = False,
) -> list[list[float]]:
    vectors = embedder.encode(texts, query=query)
    if (
        len(vectors) != len(texts)
        or not vectors[0]
        or len(vectors[0]) > vector_index.MAX_DIMENSIONS
        or any(
            len(vector) != len(vectors[0]) or not all(math.isfinite(value) for value in vector)
            for vector in vectors
        )
    ):
        raise ValueError("The embedding model returned an invalid vector batch.")
    return vectors


def prepare_query(
    origin_text: str, embedder: Embedder | None, removed: Sequence[tuple[str, str]],
) -> Query:
    # Bound the FTS expression to the same number of terms as a passage query.
    terms = tuple(dict.fromkeys(search_tokens(origin_text)))[:32]
    if embedder is None:
        return Query(terms)
    try:
        vector = _encode(embedder, [origin_text], query=True)[0]
        minimum = getattr(embedder, "min_similarity", 0.75)
        length = math.hypot(*vector) or 1.0
        scores = []
        # Removed sentences stay out of the indexes. Score this scoped set in memory.
        for offset in range(0, len(removed), EMBED_BATCH):
            batch = removed[offset : offset + EMBED_BATCH]
            vectors = _encode(embedder, [sentence for _, sentence in batch])
            for (result_id, _), candidate in zip(batch, vectors, strict=True):
                similarity = math.fsum(
                    a * b for a, b in zip(vector, candidate, strict=True)
                ) / (length * (math.hypot(*candidate) or 1.0))
                if similarity >= minimum:
                    scores.append((result_id, similarity))
        scores.sort(key=lambda row: row[1], reverse=True)
        return Query(terms, embedder.model_id, tuple(vector), tuple(k for k, _ in scores), minimum)
    except Exception:
        logger.info("Key result semantic matching is unavailable for this origin.", exc_info=True)
        return Query(terms)


def write_fts(session: Session, result_id: str, sentence: str) -> None:
    session.execute(
        text("INSERT INTO key_results_fts(result_id, tokens) VALUES (:id, :tokens)"),
        {"id": result_id, "tokens": " ".join(search_tokens(sentence))},
    )


def forget(session: Session, ids: Sequence[str]) -> None:
    if ids:
        session.execute(
            text(
                "DELETE FROM key_results_fts "
                "WHERE result_id IN (SELECT value FROM json_each(:ids))"
            ),
            {"ids": json.dumps(list(ids))},
        )
        vector_index.forget_key_results(session, ids)


def keyword(session: Session, terms: Sequence[str], ids: Collection[str]) -> list[str]:
    if not terms or not ids:
        return []
    return list(session.scalars(
        text(
            "SELECT result_id FROM key_results_fts WHERE key_results_fts MATCH :query "
            "AND result_id IN (SELECT value FROM json_each(:ids)) "
            "ORDER BY bm25(key_results_fts), result_id LIMIT :limit"
        ),
        {"query": " OR ".join(f'"{term}"' for term in terms),
         "ids": json.dumps(list(ids)), "limit": RANK_DEPTH},
    ))


def semantic(session: Session, query: Query, ids: Sequence[str]) -> list[str]:
    if not ids or not query.vector or not query.model:
        return []
    if not vector_index.extension_version(session):
        return []
    try:
        return [
            result_id for result_id, similarity in vector_index.nearest_key_results(
                session, model=query.model, query=query.vector, ids=ids, k=RANK_DEPTH,
            )
            if similarity >= query.min_similarity
        ]
    except Exception:
        logger.info("Key result vectors could not be searched.", exc_info=True)
        return []


def backfill_vectors(sessions: sessionmaker[Session], embedder: Embedder | None) -> bool:
    """Embed a batch outside SQLite and publish only sentences that are still kept."""
    if embedder is None:
        return False
    with sessions() as session:
        existing = vector_index.indexed_key_results(session, embedder.model_id)
        missing = (
            select(KeyResult.id, KeyResult.text).where(KeyResult.status == "kept")
            .order_by(KeyResult.created_at, KeyResult.id).limit(EMBED_BATCH)
        )
        if existing is not None:
            missing = missing.where(KeyResult.id.not_in(existing))
        rows = session.execute(missing).all()
    if not rows:
        return False
    encoded = _encode(embedder, [sentence for _, sentence in rows])
    with session_scope(sessions) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        current = dict(session.execute(
            select(KeyResult.id, KeyResult.text)
            .where(
                KeyResult.id.in_([result_id for result_id, _ in rows]), KeyResult.status == "kept",
            )
        ).all())
        vectors = [
            (result_id, vector)
            for (result_id, sentence), vector in zip(rows, encoded, strict=True)
            if current.get(result_id) == sentence
        ]
        vector_index.store_key_results(session, model=embedder.model_id, vectors=vectors)
    return True
