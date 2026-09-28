"""Block vectors stored in SQLite through the sqlite-vec extension.

There is one ``vec0`` table per vector width (``block_vectors_384`` for the
bundled model), created on first use. ``block_id`` is the key. ``source_id``,
``revision_id`` and ``model`` are metadata columns, so a nearest-neighbour
query is limited to the libraries being asked, and to their current
revisions, before anything is ranked. The search is exact; there is no
approximate index.

Vectors are derived data: re-embedding rebuilds them, and backups need no
special handling. The extension is loaded on every connection by
``database.create_database_engine``. When it cannot load (some Python builds
disable extensions), ``extension_version`` returns None and semantic search
stays off. Keyword search does not depend on it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

TABLE = re.compile(r"block_vectors_(\d+)")
MAX_DIMENSIONS = 4096
# SQLite binds at most 32766 values per statement; stay well under it.
CHUNK = 10_000


def load_extension(dbapi_connection: Any) -> None:
    """Load sqlite-vec into a new connection; failure only disables vectors."""

    try:
        import sqlite_vec

        dbapi_connection.enable_load_extension(True)
        try:
            sqlite_vec.load(dbapi_connection)
        finally:
            dbapi_connection.enable_load_extension(False)
    except Exception:  # checked, and explained, by extension_version()
        return


def extension_version(session: Session) -> str | None:
    try:
        return session.execute(text("SELECT vec_version()")).scalar_one()
    except Exception:
        return None


def table_name(dimensions: int) -> str:
    if not 0 < dimensions <= MAX_DIMENSIONS:
        raise ValueError("Unsupported vector width")
    return f"block_vectors_{dimensions}"


def _tables(session: Session) -> list[str]:
    names = session.execute(
        text("SELECT name FROM sqlite_master WHERE name LIKE 'block_vectors_%'")
    ).scalars()
    return [name for name in names if TABLE.fullmatch(name)]


def _blob(vector: Sequence[float]) -> bytes:
    import numpy

    return numpy.asarray(vector, dtype=numpy.float32).tobytes()


def _chunks(values: Sequence[str]) -> Iterable[list[str]]:
    for offset in range(0, len(values), CHUNK):
        yield list(values[offset : offset + CHUNK])


def store(
    session: Session,
    *,
    source_id: str,
    revision_id: str,
    model: str,
    vectors: list[tuple[str, list[float]]],
) -> None:
    """Write one revision's block vectors, replacing any earlier copy."""

    if not vectors:
        return
    table = table_name(len(vectors[0][1]))
    session.execute(
        text(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING vec0("
            "block_id text primary key, source_id text, revision_id text, model text, "
            f"embedding float[{len(vectors[0][1])}] distance_metric=cosine)"
        )
    )
    # vec0 has no upsert: delete first, so a retried job stays idempotent.
    session.execute(
        text(f"DELETE FROM {table} WHERE block_id IN :blocks").bindparams(
            bindparam("blocks", expanding=True)
        ),
        {"blocks": [block_id for block_id, _ in vectors]},
    )
    session.execute(
        text(
            f"INSERT INTO {table}(block_id, source_id, revision_id, model, embedding) "
            "VALUES (:block, :source, :revision, :model, :embedding)"
        ),
        [
            {
                "block": block_id,
                "source": source_id,
                "revision": revision_id,
                "model": model,
                "embedding": _blob(vector),
            }
            for block_id, vector in vectors
        ],
    )


def nearest(
    session: Session,
    *,
    model: str,
    query: Sequence[float],
    revision_ids: Sequence[str],
    k: int,
) -> list[tuple[str, float]]:
    """The k blocks most similar to the query, among these revisions only."""

    table = table_name(len(query))
    if table not in _tables(session) or not revision_ids:
        return []
    statement = text(
        f"SELECT block_id, distance FROM {table} WHERE embedding MATCH :query AND k = :k "
        "AND model = :model AND revision_id IN :revisions"
    ).bindparams(bindparam("revisions", expanding=True))
    found: list[tuple[str, float]] = []
    for revisions in _chunks(revision_ids):
        rows = session.execute(
            statement,
            {"query": _blob(query), "k": k, "model": model, "revisions": revisions},
        )
        found.extend((block_id, 1.0 - distance) for block_id, distance in rows)
    return sorted(found, key=lambda item: item[1], reverse=True)[:k]


def nearest_among(
    session: Session,
    *,
    model: str,
    query: Sequence[float],
    block_ids: Sequence[str],
    source_ids: Sequence[str],
    k: int,
) -> list[tuple[str, float]]:
    """Rank a given set of blocks (a topic's evidence) against the query."""

    import numpy

    table = table_name(len(query))
    if table not in _tables(session) or not block_ids:
        return []
    statement = text(
        f"SELECT block_id, embedding FROM {table} "
        "WHERE model = :model AND block_id IN :blocks AND source_id IN :sources"
    ).bindparams(bindparam("blocks", expanding=True), bindparam("sources", expanding=True))
    names: list[str] = []
    rows: list[Any] = []
    for blocks in _chunks(block_ids):
        for block_id, embedding in session.execute(
            statement, {"model": model, "blocks": blocks, "sources": list(source_ids)}
        ):
            names.append(block_id)
            rows.append(numpy.frombuffer(embedding, dtype=numpy.float32))
    if not rows:
        return []
    matrix = numpy.vstack(rows)
    wanted = numpy.asarray(query, dtype=numpy.float32)
    norms = numpy.linalg.norm(matrix, axis=1) * (numpy.linalg.norm(wanted) or 1.0)
    similarity = matrix @ wanted / numpy.where(norms == 0, 1.0, norms)
    order = numpy.argsort(-similarity)[:k]
    return [(names[i], float(similarity[i])) for i in order]


def forget_sources(session: Session, source_ids: Sequence[str]) -> None:
    """Drop every vector of these sources, in every table.

    Without the extension the rows stay; search only reads current revisions,
    so they are never returned, and the next run with the extension can clear
    them.
    """

    if extension_version(session) is None:
        return
    for table in _tables(session):
        statement = text(f"DELETE FROM {table} WHERE source_id IN :sources").bindparams(
            bindparam("sources", expanding=True)
        )
        for sources in _chunks(source_ids):
            session.execute(statement, {"sources": sources})

