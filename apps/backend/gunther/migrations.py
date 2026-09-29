"""Small, dependency-free schema migrations for Gunther's SQLite database.

Add a migration by appending a :class:`Migration` to ``MIGRATIONS``. Upgrade
functions receive the active connection and the application's current
``MetaData``. They should use idempotent checks, such as
``add_column_if_missing``, because a previously unversioned database may
already contain some or all of a change.

The baseline deliberately calls ``MetaData.create_all(checkfirst=True)``. On a
new database it creates the current schema; on a legacy database it adopts the
existing tables without replacing them or deleting their data. Every later
migration then runs in version order.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine, MetaData, Table, inspect, text
from sqlalchemy.engine import Connection

from gunther.database import connection_transaction
from gunther.source_identity import source_fingerprint

MIGRATION_TABLE = "gunther_schema_migrations"


class MigrationError(RuntimeError):
    """Base error for invalid migration definitions or database state."""


class MigrationExecutionError(MigrationError):
    """A migration failed and its transaction was rolled back."""

    def __init__(self, migration: Migration) -> None:
        super().__init__(f"Migration {migration.version} ({migration.name}) failed")
        self.migration = migration


MigrationUpgrade = Callable[[Connection, MetaData], None]


@dataclass(frozen=True, slots=True)
class Migration:
    """One immutable, ordered schema change."""

    version: int
    name: str
    upgrade: MigrationUpgrade


@dataclass(frozen=True, slots=True)
class MigrationRecord:
    """One successfully committed row from the migration history."""

    version: int
    name: str
    applied_at: str


def _create_current_schema(connection: Connection, metadata: MetaData) -> None:
    metadata.create_all(bind=connection, checkfirst=True)


def _create_recording_lifecycle_tables(connection: Connection, metadata: MetaData) -> None:
    """Add durable recording sessions to databases already baselined at v1."""

    for table_name in ("recording_sessions", "recording_chunks"):
        table = metadata.tables.get(table_name)
        if table is not None:
            table.create(bind=connection, checkfirst=True)


def _create_asset_store(connection: Connection, metadata: MetaData) -> None:
    assets = metadata.tables.get("assets")
    if assets is not None:
        assets.create(bind=connection, checkfirst=True)
    if inspect(connection).has_table("sources"):
        add_column_if_missing(
            connection,
            "sources",
            "asset_id",
            "TEXT REFERENCES assets(id) ON DELETE SET NULL",
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_sources_asset_id ON sources (asset_id)"
        )


def _add_recording_recovery_checkpoints(
    connection: Connection, _metadata: MetaData
) -> None:
    """Persist transcript/UI progress for recording recovery after a restart."""

    if not inspect(connection).has_table("recording_sessions"):
        return
    columns = (
        ("transcript", "TEXT NOT NULL DEFAULT ''"),
        ("duration_seconds", "INTEGER NOT NULL DEFAULT 0"),
        ("moments_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("recording_context", "TEXT NOT NULL DEFAULT 'lecture'"),
        ("knowledge_base_id", "TEXT"),
        ("checkpoint_revision", "INTEGER NOT NULL DEFAULT 0"),
        ("checkpoint_hash", "TEXT"),
        ("checkpointed_at", "DATETIME"),
    )
    for column_name, definition in columns:
        add_column_if_missing(
            connection,
            "recording_sessions",
            column_name,
            definition,
        )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_recording_sessions_knowledge_base_id "
        "ON recording_sessions (knowledge_base_id)"
    )


def _separate_source_identity_from_body_hash(
    connection: Connection, _metadata: MetaData
) -> None:
    """Make title and kind part of Source identity while preserving exact retries."""

    required = {"id", "title", "kind", "content", "content_hash"}
    if not inspect(connection).has_table("sources"):
        return
    available = {
        str(column["name"]) for column in inspect(connection).get_columns("sources")
    }
    if not required.issubset(available):
        # Some deliberately partial legacy schemas are adopted only so their
        # unrelated data can be inspected. There is nothing safe to rehash.
        return

    rows = connection.execute(
        text("SELECT id, title, kind, content FROM sources ORDER BY id")
    ).mappings().all()
    for row in rows:
        connection.execute(
            text("UPDATE sources SET content_hash = :placeholder WHERE id = :id"),
            {"placeholder": f"__gunther_source_v5__{row['id']}", "id": row["id"]},
        )
    for row in rows:
        connection.execute(
            text("UPDATE sources SET content_hash = :fingerprint WHERE id = :id"),
            {
                "fingerprint": source_fingerprint(
                    str(row["title"]), str(row["kind"]), str(row["content"])
                ),
                "id": row["id"],
            },
        )


def _add_capture_idempotency(connection: Connection, _metadata: MetaData) -> None:
    """Give durable mobile outbox retries an exactly-once Quick Note boundary."""

    if not inspect(connection).has_table("notebook_notes"):
        return
    add_column_if_missing(
        connection,
        "notebook_notes",
        "client_capture_id",
        "VARCHAR(128)",
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_notebook_notes_client_capture_id "
        "ON notebook_notes (client_capture_id)"
    )


def _create_workspace_device_identity(connection: Connection, metadata: MetaData) -> None:
    """Create the durable workspace and revocable device-authentication boundary."""

    for table_name in (
        "workspace_identity",
        "paired_devices",
        "device_pairing_sessions",
    ):
        table = metadata.tables.get(table_name)
        if table is None:
            # Migration framework tests and deliberately partial legacy schemas
            # may use unrelated metadata. Production startup always supplies
            # Base.metadata with the complete device-identity boundary.
            return
        table.create(bind=connection, checkfirst=True)

    workspace_table = metadata.tables["workspace_identity"]
    workspace_count = int(
        connection.execute(text("SELECT COUNT(*) FROM workspace_identity")).scalar_one()
    )
    if workspace_count > 1:
        raise MigrationError("Gunther supports exactly one workspace identity per database")
    if workspace_count == 0:
        connection.execute(
            workspace_table.insert().values(
                singleton_key="primary",
                workspace_id=f"wsp_{secrets.token_hex(16)}",
                display_name="My Gunther Workspace",
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )


def _create_web_snapshot_provenance(connection: Connection, metadata: MetaData) -> None:
    """Add the immutable URL-to-Asset-to-Source provenance boundary."""

    table = metadata.tables.get("web_snapshots")
    if table is not None:
        table.create(bind=connection, checkfirst=True)


def _add_web_capture_request_identity(
    connection: Connection, _metadata: MetaData
) -> None:
    """Persist new capture intent without inventing intent for existing v8 rows."""

    if not inspect(connection).has_table("web_snapshots"):
        return
    add_column_if_missing(
        connection,
        "web_snapshots",
        "request_fingerprint",
        "VARCHAR(64)",
    )


def _create_artifact_history(connection: Connection, metadata: MetaData) -> None:
    """Create immutable, workspace-bound Output versions for existing databases."""

    for table_name in ("artifacts", "artifact_unit_bindings"):
        table = metadata.tables.get(table_name)
        if table is not None:
            table.create(bind=connection, checkfirst=True)


# Output request identity arrived after the first Output tables; a table created
# before it is repaired in place rather than set aside.
ARTIFACT_REQUEST_IDENTITY_COLUMNS = frozenset({"client_request_id", "request_fingerprint"})


def _stored_column_drift(connection: Connection, table: Table) -> tuple[set[str], set[str]]:
    """Return the stored table's missing columns and unknown columns that block inserts."""

    inspector = inspect(connection)
    if not inspector.has_table(table.name):
        return set(), set()
    stored = {column["name"]: column for column in inspector.get_columns(table.name)}
    expected = {column.name for column in table.columns}
    blocking = {
        name
        for name, column in stored.items()
        if name not in expected and not column["nullable"] and column.get("default") is None
    }
    return expected - stored.keys(), blocking


def _set_aside_as_legacy(connection: Connection, table_name: str) -> str:
    """Rename a table out of the way with every row, freeing its index names."""

    inspector = inspect(connection)
    legacy_name = f"{table_name}_legacy"
    suffix = 2
    while inspector.has_table(legacy_name):
        legacy_name = f"{table_name}_legacy_{suffix}"
        suffix += 1
    preparer = connection.dialect.identifier_preparer
    connection.exec_driver_sql(
        f"ALTER TABLE {preparer.quote(table_name)} RENAME TO {preparer.quote(legacy_name)}"
    )
    # SQLite index names are global and the renamed table keeps its explicit
    # indexes, whose names the replacement table needs. Dropping an index keeps rows.
    for index in connection.exec_driver_sql(
        f"PRAGMA index_list({preparer.quote(legacy_name)})"
    ).all():
        if index.origin == "c":
            connection.exec_driver_sql(f"DROP INDEX {preparer.quote(index.name)}")
    return legacy_name


def _repair_legacy_artifact_tables(connection: Connection, metadata: MetaData) -> None:
    """Bring Output tables left by a pre-release build to the current shape.

    Migration 10 creates these tables with ``checkfirst``, so a table that an
    earlier build had already created kept its old columns and every query on it
    failed. No row is discarded: missing request identity is backfilled, and any
    other shape is kept under a ``_legacy`` name beside a fresh, empty table.
    """

    artifacts = metadata.tables.get("artifacts")
    bindings = metadata.tables.get("artifact_unit_bindings")
    if artifacts is None or bindings is None:
        return
    artifact_missing, artifact_blocking = _stored_column_drift(connection, artifacts)
    binding_missing, binding_blocking = _stored_column_drift(connection, bindings)

    if artifact_missing and not (
        artifact_missing - ARTIFACT_REQUEST_IDENTITY_COLUMNS
        or artifact_blocking
        or binding_missing
        or binding_blocking
    ):
        added_request_id = add_column_if_missing(
            connection, "artifacts", "client_request_id", "VARCHAR(128)"
        )
        add_column_if_missing(connection, "artifacts", "request_fingerprint", "VARCHAR(64)")
        # Synthetic identity for Outputs made before requests carried one. No client
        # sends a "legacy-" id, so an old row can never answer a new request.
        legacy_ids = connection.execute(
            text(
                "SELECT id FROM artifacts "
                "WHERE client_request_id IS NULL OR request_fingerprint IS NULL"
            )
        ).scalars().all()
        for artifact_id in legacy_ids:
            connection.execute(
                text(
                    "UPDATE artifacts SET "
                    "client_request_id = COALESCE(client_request_id, :request_id), "
                    "request_fingerprint = COALESCE(request_fingerprint, :fingerprint) "
                    "WHERE id = :id"
                ),
                {
                    "id": artifact_id,
                    "request_id": f"legacy-{artifact_id}",
                    "fingerprint": hashlib.sha256(
                        f"gunther.legacy-artifact:{artifact_id}".encode()
                    ).hexdigest(),
                },
            )
        if added_request_id:
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_artifact_workspace_request "
                "ON artifacts (workspace_id, client_request_id)"
            )
    elif artifact_missing or artifact_blocking or binding_missing or binding_blocking:
        # An unrecognised earlier shape: keep it readable, start the current one.
        for table in (artifacts, bindings):
            if inspect(connection).has_table(table.name):
                _set_aside_as_legacy(connection, table.name)
    artifacts.create(bind=connection, checkfirst=True)
    bindings.create(bind=connection, checkfirst=True)


def _create_structured_knowledge(connection: Connection, metadata: MetaData) -> None:
    for name in (
        "source_revisions", "source_index_heads", "content_blocks", "block_embeddings",
        "processing_jobs", "topic_nodes", "topic_evidence_links",
    ):
        if name in metadata.tables:
            metadata.tables[name].create(bind=connection, checkfirst=True)
    if "content_blocks" not in metadata.tables:
        return
    connection.exec_driver_sql(
        "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5("
        "block_id UNINDEXED, source_id UNINDEXED, revision_id UNINDEXED, tokens)"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS knowledge_fts_delete AFTER DELETE ON content_blocks "
        "BEGIN DELETE FROM knowledge_fts WHERE block_id = old.id; END"
    )


TRASHABLE_TABLES = ("knowledge_bases", "sources", "notebook_notes")


def _add_trash(connection: Connection, _metadata: MetaData) -> None:
    """Let libraries, sources and notes rest in Trash before permanent deletion."""

    for table_name in TRASHABLE_TABLES:
        if not inspect(connection).has_table(table_name):
            continue
        add_column_if_missing(connection, table_name, "trashed_at", "DATETIME")
        add_column_if_missing(connection, table_name, "trash_batch_id", "VARCHAR(40)")
        for column_name in ("trashed_at", "trash_batch_id"):
            # The names SQLAlchemy gives ``index=True``, so new databases match.
            connection.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS ix_{table_name}_{column_name} "
                f"ON {table_name} ({column_name})"
            )


def _drop_json_vectors(connection: Connection, _metadata: MetaData) -> None:
    """Vectors now live in sqlite-vec (vector_index); drop the JSON copies.

    They belonged to the optional sentence-transformers model, which is gone.
    The bundled model embeds every source again in the background.
    """

    if inspect(connection).has_table("block_embeddings"):
        connection.exec_driver_sql("DELETE FROM block_embeddings WHERE vector_json != ''")


PAPER_TABLES = ("works", "source_papers", "topic_sources", "topic_syntheses")


def _create_paper_structure(connection: Connection, metadata: MetaData) -> None:
    """Papers, their copies, topics of whole papers, and topic overviews."""

    for name in PAPER_TABLES:
        if name in metadata.tables:
            metadata.tables[name].create(bind=connection, checkfirst=True)


def _create_source_digests(connection: Connection, metadata: MetaData) -> None:
    """Summaries written after a source is read."""

    if "source_digests" in metadata.tables:
        metadata.tables["source_digests"].create(bind=connection, checkfirst=True)


# Keep applied entries immutable. New migrations are appended with the next
# consecutive integer; never edit or reorder an entry already shipped.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline_current_schema", _create_current_schema),
    Migration(2, "durable_recording_lifecycle", _create_recording_lifecycle_tables),
    Migration(3, "immutable_source_assets", _create_asset_store),
    Migration(4, "recording_recovery_checkpoints", _add_recording_recovery_checkpoints),
    Migration(5, "source_capture_identity", _separate_source_identity_from_body_hash),
    Migration(6, "capture_idempotency", _add_capture_idempotency),
    Migration(7, "workspace_device_identity", _create_workspace_device_identity),
    Migration(8, "web_snapshot_provenance", _create_web_snapshot_provenance),
    Migration(9, "web_capture_request_identity", _add_web_capture_request_identity),
    Migration(10, "immutable_artifact_history", _create_artifact_history),
    Migration(11, "structured_knowledge_and_durable_processing", _create_structured_knowledge),
    Migration(12, "repair_legacy_artifact_tables", _repair_legacy_artifact_tables),
    Migration(13, "reversible_trash", _add_trash),
    Migration(14, "vectors_in_sqlite_vec", _drop_json_vectors),
    Migration(15, "paper_structure", _create_paper_structure),
    Migration(16, "source_digests", _create_source_digests),
)

LATEST_SCHEMA_VERSION = MIGRATIONS[-1].version


def has_column(connection: Connection, table_name: str, column_name: str) -> bool:
    """Return whether a column exists, for safe/idempotent upgrade functions."""

    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def add_column_if_missing(
    connection: Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> bool:
    """Add one SQLite-compatible column if absent and report whether it changed.

    ``column_definition`` contains only the SQL following the column name, for
    example ``"TEXT NOT NULL DEFAULT ''"``. Inputs are migration constants, not
    user data. Table and column identifiers are quoted by SQLAlchemy's dialect.
    """

    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        raise MigrationError(f"Cannot add {table_name}.{column_name}: table does not exist")
    if has_column(connection, table_name, column_name):
        return False

    preparer = connection.dialect.identifier_preparer
    quoted_table = preparer.quote(table_name)
    quoted_column = preparer.quote(column_name)
    connection.exec_driver_sql(
        f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_column} {column_definition}"
    )
    return True


def _validate_plan(migrations: Sequence[Migration]) -> tuple[Migration, ...]:
    plan = tuple(migrations)
    versions = [migration.version for migration in plan]
    expected = list(range(1, len(plan) + 1))
    if versions != expected:
        raise MigrationError(
            "Migration versions must be unique, ordered, and consecutive from 1; "
            f"received {versions}"
        )
    if any(not migration.name.strip() for migration in plan):
        raise MigrationError("Migration names must not be empty")
    if len({migration.name for migration in plan}) != len(plan):
        raise MigrationError("Migration names must be unique")
    return plan


def _ensure_migration_table(engine: Engine) -> None:
    with connection_transaction(engine, sqlite_immediate=True) as connection:
        connection.exec_driver_sql(
            f"""
            CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )


def get_migration_history(engine: Engine) -> tuple[MigrationRecord, ...]:
    """Read committed migration history; an untouched database has no history."""

    if not inspect(engine).has_table(MIGRATION_TABLE):
        return ()
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"SELECT version, name, applied_at FROM {MIGRATION_TABLE} "
                "ORDER BY version"
            )
        ).mappings()
        return tuple(
            MigrationRecord(
                version=int(row["version"]),
                name=str(row["name"]),
                applied_at=str(row["applied_at"]),
            )
            for row in rows
        )


def get_schema_version(engine: Engine) -> int:
    """Return the latest successfully committed version, or zero if unversioned."""

    history = get_migration_history(engine)
    return history[-1].version if history else 0


def _validate_history(history: Sequence[MigrationRecord], plan: Sequence[Migration]) -> None:
    if [record.version for record in history] != list(range(1, len(history) + 1)):
        raise MigrationError("Migration history contains a version gap")
    if len(history) > len(plan):
        raise MigrationError(
            f"Database schema version {history[-1].version} is newer than this application"
        )
    for record, migration in zip(history, plan[: len(history)], strict=True):
        if record.name != migration.name:
            raise MigrationError(
                f"Migration {record.version} name mismatch: database has "
                f"{record.name!r}, application expects {migration.name!r}"
            )


def _apply_migration(engine: Engine, metadata: MetaData, migration: Migration) -> None:
    try:
        with connection_transaction(engine, sqlite_immediate=True) as connection:
            existing = connection.execute(
                text(f"SELECT name FROM {MIGRATION_TABLE} WHERE version = :version"),
                {"version": migration.version},
            ).scalar_one_or_none()
            if existing is not None:
                if existing != migration.name:
                    raise MigrationError(
                        f"Migration {migration.version} is already recorded as {existing!r}"
                    )
                return

            current = connection.execute(
                text(f"SELECT COALESCE(MAX(version), 0) FROM {MIGRATION_TABLE}")
            ).scalar_one()
            if int(current) != migration.version - 1:
                raise MigrationError(
                    f"Cannot apply migration {migration.version} after schema version {current}"
                )

            migration.upgrade(connection, metadata)
            connection.execute(
                text(
                    f"INSERT INTO {MIGRATION_TABLE} (version, name, applied_at) "
                    "VALUES (:version, :name, :applied_at)"
                ),
                {
                    "version": migration.version,
                    "name": migration.name,
                    "applied_at": datetime.now(UTC).isoformat(),
                },
            )
    except MigrationError:
        raise
    except Exception as error:
        raise MigrationExecutionError(migration) from error


def run_migrations(
    engine: Engine,
    metadata: MetaData,
    migrations: Sequence[Migration] | None = None,
) -> tuple[MigrationRecord, ...]:
    """Bring a database to the latest planned version and return its history."""

    plan = _validate_plan(MIGRATIONS if migrations is None else migrations)
    _ensure_migration_table(engine)
    _validate_history(get_migration_history(engine), plan)
    for migration in plan:
        _apply_migration(engine, metadata, migration)
    history = get_migration_history(engine)
    _validate_history(history, plan)
    return history
