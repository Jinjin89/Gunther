import hashlib
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, inspect, select, text

from gunther.database import create_database_engine, create_session_factory, session_scope
from gunther.knowledge_index import KnowledgeIndex
from gunther.migrations import (
    LATEST_SCHEMA_VERSION,
    MIGRATION_TABLE,
    MIGRATIONS,
    Migration,
    MigrationExecutionError,
    add_column_if_missing,
    get_migration_history,
    get_schema_version,
    run_migrations,
)
from gunther.models import Artifact, Base, Source
from gunther.source_identity import source_fingerprint


def make_engine(tmp_path: Path, name: str = "gunther.sqlite"):
    return create_database_engine(f"sqlite+pysqlite:///{tmp_path / name}")


def item_metadata() -> MetaData:
    metadata = MetaData()
    Table(
        "items",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String, nullable=False),
    )
    return metadata


def test_empty_database_is_created_and_versioned(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    try:
        history = run_migrations(engine, item_metadata())

        assert inspect(engine).has_table("items")
        assert inspect(engine).has_table(MIGRATION_TABLE)
        assert [(record.version, record.name) for record in history] == [
            (1, "baseline_current_schema"),
            (2, "durable_recording_lifecycle"),
            (3, "immutable_source_assets"),
            (4, "recording_recovery_checkpoints"),
            (5, "source_capture_identity"),
            (6, "capture_idempotency"),
            (7, "workspace_device_identity"),
            (8, "web_snapshot_provenance"),
            (9, "web_capture_request_identity"),
            (10, "immutable_artifact_history"),
            (11, "structured_knowledge_and_durable_processing"),
            (12, "repair_legacy_artifact_tables"),
            (13, "reversible_trash"),
        ]
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_unversioned_legacy_database_is_baselined_without_data_loss(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
            )
            connection.execute(text("INSERT INTO items (id, name) VALUES (1, 'keep me')"))

        assert get_migration_history(engine) == ()
        run_migrations(engine, item_metadata())

        with engine.connect() as connection:
            row = connection.execute(text("SELECT id, name FROM items")).one()
        assert row == (1, "keep me")
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_repeated_startup_is_idempotent(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    try:
        first_history = run_migrations(engine, item_metadata())
        second_history = run_migrations(engine, item_metadata())

        assert second_history == first_history
        with engine.connect() as connection:
            migration_count = connection.execute(
                text(f"SELECT COUNT(*) FROM {MIGRATION_TABLE}")
            ).scalar_one()
        assert migration_count == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_v10_upgrade_backfills_evidence_without_rewriting_original(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    added = {
        "source_revisions",
        "source_index_heads",
        "content_blocks",
        "block_embeddings",
        "processing_jobs",
        "topic_nodes",
        "topic_evidence_links",
    }
    legacy = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name not in added:
            table.to_metadata(legacy)
    content = "# Methods\nGenome quality requires careful controls."
    fingerprint = source_fingerprint("Original", "note", content)
    try:
        run_migrations(engine, legacy, MIGRATIONS[:10])
        sessions = create_session_factory(engine)
        with session_scope(sessions) as session:
            session.add(
                Source(
                    id="src_legacy",
                    title="Original",
                    kind="note",
                    content=content,
                    content_hash=fingerprint,
                )
            )
        assert not inspect(engine).has_table("content_blocks")
        run_migrations(engine, Base.metadata)
        index = KnowledgeIndex(sessions)
        assert index.backfill() == 1
        assert index.backfill() == 0
        with session_scope(sessions) as session:
            original = session.get(Source, "src_legacy")
            assert (original.content, original.content_hash) == (content, fingerprint)
            assert (
                index.retrieve(session, [original.id], "genome quality")[0].block.content
                == content.split("\n")[1]
            )
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_failed_migration_rolls_back_schema_and_does_not_advance_version(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    try:
        metadata = item_metadata()
        run_migrations(engine, metadata)

        def fail_after_ddl(connection, _metadata: MetaData) -> None:
            connection.exec_driver_sql("CREATE TABLE should_be_rolled_back (id INTEGER)")
            raise RuntimeError("simulated migration failure")

        plan = (*MIGRATIONS, Migration(LATEST_SCHEMA_VERSION + 1, "failing_change", fail_after_ddl))
        with pytest.raises(MigrationExecutionError) as error:
            run_migrations(engine, metadata, plan)

        assert isinstance(error.value.__cause__, RuntimeError)
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
        assert not inspect(engine).has_table("should_be_rolled_back")
    finally:
        engine.dispose()


def test_column_migration_preserves_existing_rows_and_is_safe_if_column_exists(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    try:
        metadata = MetaData()
        Table(
            "documents",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("title", String, nullable=False),
            Column("state", String, nullable=False, server_default="inbox"),
        )
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE documents (id INTEGER PRIMARY KEY, title TEXT NOT NULL)"
            )
            connection.execute(
                text("INSERT INTO documents (id, title) VALUES (7, 'existing source')")
            )

        def add_state(connection, _metadata: MetaData) -> None:
            add_column_if_missing(
                connection,
                "documents",
                "state",
                "TEXT NOT NULL DEFAULT 'inbox'",
            )

        plan = (*MIGRATIONS, Migration(LATEST_SCHEMA_VERSION + 1, "documents_state", add_state))
        run_migrations(engine, metadata, plan)
        run_migrations(engine, metadata, plan)

        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT id, title, state FROM documents WHERE id = 7")
            ).one()
        assert row == (7, "existing source", "inbox")
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION + 1
    finally:
        engine.dispose()


def test_v1_database_adds_recording_lifecycle_without_touching_existing_data(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                text("INSERT INTO legacy_marker (id, value) VALUES (1, 'preserve me')")
            )

        run_migrations(engine, Base.metadata, (MIGRATIONS[0],))
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE recording_chunks")
            connection.exec_driver_sql("DROP TABLE recording_sessions")

        history = run_migrations(engine, Base.metadata)
        repeated_history = run_migrations(engine, Base.metadata)

        inspector = inspect(engine)
        assert inspector.has_table("recording_sessions")
        assert inspector.has_table("recording_chunks")
        assert {index["name"] for index in inspector.get_indexes("recording_sessions")} >= {
            "ix_recording_sessions_status",
            "ix_recording_sessions_updated_at",
        }
        assert {index["name"] for index in inspector.get_indexes("recording_chunks")} >= {
            "ix_recording_chunks_recording_id"
        }
        with engine.connect() as connection:
            marker = connection.execute(
                text("SELECT id, value FROM legacy_marker WHERE id = 1")
            ).one()
        assert marker == (1, "preserve me")
        assert repeated_history == history
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_v2_database_adds_asset_store_and_source_link_without_data_loss(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE sources (id TEXT PRIMARY KEY, title TEXT NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO sources (id, title) VALUES ('src_legacy', 'Keep this source')"
            )
            connection.exec_driver_sql(
                f"CREATE TABLE {MIGRATION_TABLE} ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            connection.execute(
                text(
                    f"INSERT INTO {MIGRATION_TABLE} (version, name, applied_at) VALUES "
                    "(1, 'baseline_current_schema', '2026-01-01T00:00:00Z'), "
                    "(2, 'durable_recording_lifecycle', '2026-01-02T00:00:00Z')"
                )
            )

        run_migrations(engine, Base.metadata)
        run_migrations(engine, Base.metadata)

        inspector = inspect(engine)
        assert inspector.has_table("assets")
        assert any(column["name"] == "asset_id" for column in inspector.get_columns("sources"))
        assert "ix_sources_asset_id" in {
            index["name"] for index in inspector.get_indexes("sources")
        }
        with engine.connect() as connection:
            source = connection.execute(
                text("SELECT id, title, asset_id FROM sources WHERE id = 'src_legacy'")
            ).one()
        assert source == ("src_legacy", "Keep this source", None)
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_v3_database_adds_recording_recovery_fields_without_losing_session(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE recording_sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    file_name TEXT NOT NULL UNIQUE,
                    byte_size INTEGER NOT NULL,
                    next_sequence INTEGER NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    completed_at DATETIME
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO recording_sessions (
                    id, title, status, content_type, file_name, byte_size,
                    next_sequence, created_at, updated_at
                ) VALUES (
                    'rec_111111111111111111111111', 'Keep this recording',
                    'capturing', 'audio/webm',
                    'rec_111111111111111111111111.keep.webm', 42, 2,
                    '2026-01-01T00:00:00', '2026-01-01T00:01:00'
                )
                """
            )
            connection.exec_driver_sql(
                f"CREATE TABLE {MIGRATION_TABLE} ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            connection.execute(
                text(
                    f"INSERT INTO {MIGRATION_TABLE} (version, name, applied_at) VALUES "
                    "(1, 'baseline_current_schema', '2026-01-01T00:00:00Z'), "
                    "(2, 'durable_recording_lifecycle', '2026-01-02T00:00:00Z'), "
                    "(3, 'immutable_source_assets', '2026-01-03T00:00:00Z')"
                )
            )

        first_history = run_migrations(engine, Base.metadata)
        assert run_migrations(engine, Base.metadata) == first_history

        columns = {column["name"] for column in inspect(engine).get_columns("recording_sessions")}
        assert {
            "transcript",
            "duration_seconds",
            "moments_json",
            "recording_context",
            "knowledge_base_id",
            "checkpoint_revision",
            "checkpoint_hash",
            "checkpointed_at",
        } <= columns
        assert "ix_recording_sessions_knowledge_base_id" in {
            index["name"] for index in inspect(engine).get_indexes("recording_sessions")
        }
        with engine.connect() as connection:
            recording = connection.execute(
                text(
                    "SELECT title, byte_size, next_sequence, transcript, duration_seconds, "
                    "moments_json, recording_context, knowledge_base_id, checkpoint_revision "
                    "FROM recording_sessions WHERE id = 'rec_111111111111111111111111'"
                )
            ).one()
        assert recording == (
            "Keep this recording",
            42,
            2,
            "",
            0,
            "[]",
            "lecture",
            None,
            0,
        )
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_v4_database_rekeys_sources_without_losing_identity_or_content(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    content = "Same body, distinct capture context."
    legacy_hash = hashlib.sha256(content.encode()).hexdigest()
    try:
        run_migrations(engine, Base.metadata, MIGRATIONS[:4])
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO sources "
                    "(id, title, kind, content, content_hash, created_at) "
                    "VALUES (:id, :title, :kind, :content, :content_hash, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": "src_legacy_identity",
                    "title": "Legacy title",
                    "kind": "note",
                    "content": content,
                    "content_hash": legacy_hash,
                },
            )

        history = run_migrations(engine, Base.metadata)
        assert run_migrations(engine, Base.metadata) == history
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT title, kind, content, content_hash FROM sources "
                    "WHERE id = 'src_legacy_identity'"
                )
            ).one()
        assert row[:3] == ("Legacy title", "note", content)
        assert row[3] == source_fingerprint("Legacy title", "note", content)
        assert row[3] != legacy_hash
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_v5_database_adds_quick_note_capture_id_without_losing_notes(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE notebook_notes ("
                "id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL)"
            )
            connection.execute(
                text(
                    "INSERT INTO notebook_notes (id, title, content) "
                    "VALUES ('nte_keep', 'Keep me', 'Offline thought')"
                )
            )
            connection.exec_driver_sql(
                f"CREATE TABLE {MIGRATION_TABLE} ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            names = [migration.name for migration in MIGRATIONS[:5]]
            for version, name in enumerate(names, start=1):
                connection.execute(
                    text(
                        f"INSERT INTO {MIGRATION_TABLE} "
                        "(version, name, applied_at) VALUES (:version, :name, '2026-01-01')"
                    ),
                    {"version": version, "name": name},
                )

        run_migrations(engine, Base.metadata)
        columns = {column["name"] for column in inspect(engine).get_columns("notebook_notes")}
        indexes = {index["name"] for index in inspect(engine).get_indexes("notebook_notes")}
        with engine.connect() as connection:
            note = connection.execute(
                text(
                    "SELECT id, title, content, client_capture_id FROM notebook_notes "
                    "WHERE id = 'nte_keep'"
                )
            ).one()
        assert "client_capture_id" in columns
        assert "ix_notebook_notes_client_capture_id" in indexes
        assert note == ("nte_keep", "Keep me", "Offline thought", None)
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_v6_database_creates_one_stable_workspace_and_device_tables(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    try:
        run_migrations(engine, Base.metadata, MIGRATIONS[:6])
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE device_pairing_sessions")
            connection.exec_driver_sql("DROP TABLE paired_devices")
            connection.exec_driver_sql("DROP TABLE workspace_identity")

        history = run_migrations(engine, Base.metadata)
        with engine.connect() as connection:
            first_identity = connection.execute(
                text("SELECT singleton_key, workspace_id, display_name FROM workspace_identity")
            ).one()
        repeated = run_migrations(engine, Base.metadata)
        with engine.connect() as connection:
            second_identity = connection.execute(
                text("SELECT singleton_key, workspace_id, display_name FROM workspace_identity")
            ).one()

        inspector = inspect(engine)
        assert inspector.has_table("workspace_identity")
        assert inspector.has_table("paired_devices")
        assert inspector.has_table("device_pairing_sessions")
        assert first_identity == second_identity
        assert first_identity[0] == "primary"
        assert str(first_identity[1]).startswith("wsp_")
        assert repeated == history
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_v7_database_adds_web_snapshot_provenance_without_losing_sources(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    content = "Existing source remains intact."
    try:
        run_migrations(engine, Base.metadata, MIGRATIONS[:7])
        with engine.begin() as connection:
            # The current baseline creates current metadata even when a historical
            # plan is used, so remove the v8 table to model an actual shipped v7 DB.
            connection.exec_driver_sql("DROP TABLE web_snapshots")
            connection.execute(
                text(
                    "INSERT INTO assets "
                    "(id, content_hash, original_name, media_type, size_bytes, "
                    "relative_path, created_at) VALUES "
                    "('ast_11111111111111111111111111111111', :asset_hash, "
                    "'legacy.html', 'text/html', 6, 'aa/legacy.html', CURRENT_TIMESTAMP)"
                ),
                {"asset_hash": "a" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO sources "
                    "(id, title, kind, content, content_hash, asset_id, created_at) VALUES "
                    "('src_legacy_web_ready', 'Existing source', 'link', :content, "
                    ":content_hash, 'ast_11111111111111111111111111111111', "
                    "CURRENT_TIMESTAMP)"
                ),
                {
                    "content": content,
                    "content_hash": source_fingerprint("Existing source", "link", content),
                },
            )

        history = run_migrations(engine, Base.metadata)
        assert run_migrations(engine, Base.metadata) == history
        inspector = inspect(engine)
        assert inspector.has_table("web_snapshots")
        assert {
            "source_id",
            "asset_id",
            "client_capture_id",
            "request_fingerprint",
            "original_url",
            "final_url",
            "captured_at",
            "status",
            "content_type",
            "content_hash",
        } <= {column["name"] for column in inspector.get_columns("web_snapshots")}
        with engine.connect() as connection:
            source = connection.execute(
                text(
                    "SELECT title, content, asset_id FROM sources WHERE id = 'src_legacy_web_ready'"
                )
            ).one()
        assert source == (
            "Existing source",
            content,
            "ast_11111111111111111111111111111111",
        )
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_v8_web_snapshot_rows_gain_nullable_request_identity_without_data_loss(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    content = "# Web snapshot\n\nExisting v8 capture remains intact."
    asset_id = "ast_22222222222222222222222222222222"
    source_id = "src_existing_v8_web_capture"
    snapshot_id = "wbs_22222222222222222222222222222222"
    try:
        run_migrations(engine, Base.metadata, MIGRATIONS[:8])
        with engine.begin() as connection:
            # Baseline uses current metadata; remove the v9 column to model a
            # database that was genuinely created by the shipped v8 schema.
            connection.exec_driver_sql("ALTER TABLE web_snapshots DROP COLUMN request_fingerprint")
            connection.execute(
                text(
                    "INSERT INTO assets "
                    "(id, content_hash, original_name, media_type, size_bytes, "
                    "relative_path, created_at) VALUES "
                    "(:id, :content_hash, 'legacy-v8.html', 'text/html', 12, "
                    "'bb/legacy-v8.html', CURRENT_TIMESTAMP)"
                ),
                {"id": asset_id, "content_hash": "b" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO sources "
                    "(id, title, kind, content, content_hash, asset_id, created_at) "
                    "VALUES (:id, 'Existing v8 capture', 'link', :content, "
                    ":content_hash, :asset_id, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": source_id,
                    "content": content,
                    "content_hash": source_fingerprint("Existing v8 capture", "link", content),
                    "asset_id": asset_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO web_snapshots "
                    "(id, source_id, asset_id, client_capture_id, original_url, "
                    "final_url, captured_at, status, content_type, content_hash, "
                    "created_at) VALUES "
                    "(:id, :source_id, :asset_id, 'legacy_capture_1234', "
                    "'https://example.com/legacy', 'https://example.com/legacy', "
                    "CURRENT_TIMESTAMP, 200, 'text/html', :content_hash, "
                    "CURRENT_TIMESTAMP)"
                ),
                {
                    "id": snapshot_id,
                    "source_id": source_id,
                    "asset_id": asset_id,
                    "content_hash": "b" * 64,
                },
            )

        history = run_migrations(engine, Base.metadata)
        assert run_migrations(engine, Base.metadata) == history
        assert "request_fingerprint" in {
            column["name"] for column in inspect(engine).get_columns("web_snapshots")
        }
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT source_id, asset_id, client_capture_id, original_url, "
                    "request_fingerprint FROM web_snapshots WHERE id = :id"
                ),
                {"id": snapshot_id},
            ).one()
        assert row == (
            source_id,
            asset_id,
            "legacy_capture_1234",
            "https://example.com/legacy",
            None,
        )
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_v9_database_adds_immutable_artifact_history_without_touching_library(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    try:
        run_migrations(engine, Base.metadata, MIGRATIONS[:9])
        with engine.begin() as connection:
            # Baseline adopts current metadata, so remove the future table to
            # reproduce the schema that the shipped v9 application created.
            connection.exec_driver_sql("DROP TABLE artifact_unit_bindings")
            connection.exec_driver_sql("DROP TABLE artifacts")
            connection.execute(
                text(
                    "INSERT INTO knowledge_bases "
                    "(id, title, eyebrow, subtitle, question, description, color, "
                    "status, created_at, updated_at) VALUES "
                    "('kept-library', 'Kept library', 'Personal knowledge', "
                    "'A field worth shaping', 'What should remain?', "
                    "'Existing content', 'green', 'Outline', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        history = run_migrations(engine, Base.metadata)
        assert run_migrations(engine, Base.metadata) == history
        inspector = inspect(engine)
        assert inspector.has_table("artifacts")
        assert {
            "id",
            "workspace_id",
            "knowledge_base_id",
            "client_request_id",
            "request_fingerprint",
            "lineage_id",
            "version_number",
            "supersedes_artifact_id",
            "format",
            "audience",
            "title",
            "content",
            "content_hash",
            "manifest_hash",
            "accepted_unit_ids_json",
            "revision_snapshot_json",
            "provenance_json",
            "created_at",
        } == {column["name"] for column in inspector.get_columns("artifacts")}
        assert inspector.has_table("artifact_unit_bindings")
        assert {
            "id",
            "artifact_id",
            "position",
            "unit_id",
            "revision_id",
            "content_hash",
        } == {column["name"] for column in inspector.get_columns("artifact_unit_bindings")}
        assert {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("artifacts")
        } >= {
            ("lineage_id", "version_number"),
            ("workspace_id", "client_request_id"),
        }
        assert {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("artifact_unit_bindings")
        } >= {
            ("artifact_id", "position"),
            ("artifact_id", "unit_id"),
        }
        binding_foreign_keys = {
            tuple(foreign_key["constrained_columns"]): foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys("artifact_unit_bindings")
        }
        assert binding_foreign_keys == {
            ("artifact_id",): "artifacts",
            ("unit_id",): "knowledge_units",
            ("revision_id",): "knowledge_unit_revisions",
        }
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT title, description FROM knowledge_bases WHERE id = 'kept-library'")
            ).one() == ("Kept library", "Existing content")
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def _prepare_pre_release_outputs(engine) -> str:
    """Reach v11, then replace the Output tables as a pre-release build left them."""

    run_migrations(engine, Base.metadata, MIGRATIONS[:11])
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE artifact_unit_bindings")
        connection.exec_driver_sql("DROP TABLE artifacts")
        connection.execute(
            text(
                "INSERT INTO knowledge_bases "
                "(id, title, eyebrow, subtitle, question, description, color, "
                "status, created_at, updated_at) VALUES "
                "('cells', 'Cells', 'Personal knowledge', 'A field worth shaping', "
                "'Which markers?', 'Existing content', 'green', 'Outline', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        return connection.execute(
            text("SELECT workspace_id FROM workspace_identity")
        ).scalar_one()


def test_v11_outputs_without_request_identity_are_repaired_in_place(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    try:
        workspace_id = _prepare_pre_release_outputs(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE artifacts ("
                "id VARCHAR(40) PRIMARY KEY, "
                "workspace_id VARCHAR(40) NOT NULL REFERENCES workspace_identity(workspace_id), "
                "knowledge_base_id VARCHAR(160) NOT NULL REFERENCES knowledge_bases(id), "
                "lineage_id VARCHAR(40) NOT NULL, version_number INTEGER NOT NULL, "
                "supersedes_artifact_id VARCHAR(40) REFERENCES artifacts(id), "
                "format VARCHAR(40) NOT NULL, audience VARCHAR(40) NOT NULL, "
                "title VARCHAR(160) NOT NULL, content TEXT NOT NULL, "
                "content_hash VARCHAR(64) NOT NULL, manifest_hash VARCHAR(64) NOT NULL, "
                "accepted_unit_ids_json TEXT NOT NULL, revision_snapshot_json TEXT NOT NULL, "
                "provenance_json TEXT NOT NULL, created_at DATETIME NOT NULL, "
                "UNIQUE (lineage_id, version_number))"
            )
            connection.execute(
                text(
                    "INSERT INTO artifacts VALUES ('art_old', :workspace, 'cells', 'lin_1', 1, "
                    "NULL, 'markdown', 'self', 'Marker review', '# Markers', :hash, :hash, "
                    "'[]', '{}', '{}', CURRENT_TIMESTAMP)"
                ),
                {"workspace": workspace_id, "hash": "a" * 64},
            )

        history = run_migrations(engine, Base.metadata)
        assert run_migrations(engine, Base.metadata) == history

        inspector = inspect(engine)
        assert {"client_request_id", "request_fingerprint"} <= {
            column["name"] for column in inspector.get_columns("artifacts")
        }
        assert any(
            index["unique"] and index["column_names"] == ["workspace_id", "client_request_id"]
            for index in inspector.get_indexes("artifacts")
        )
        assert not inspector.has_table("artifacts_legacy")
        sessions = create_session_factory(engine)
        with session_scope(sessions) as session:
            kept = session.scalars(select(Artifact)).one()
            assert (kept.id, kept.title, kept.content) == ("art_old", "Marker review", "# Markers")
            assert kept.client_request_id == "legacy-art_old"
            assert len(kept.request_fingerprint) == 64
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_unrecognised_output_tables_are_kept_as_legacy_beside_current_ones(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    try:
        workspace_id = _prepare_pre_release_outputs(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE artifacts (id VARCHAR(40) PRIMARY KEY, "
                "knowledge_base_id VARCHAR(160) NOT NULL, title TEXT NOT NULL, "
                "body TEXT NOT NULL)"
            )
            # Same name the current table's index needs.
            connection.exec_driver_sql(
                "CREATE INDEX ix_artifacts_knowledge_base_id ON artifacts (knowledge_base_id)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE artifact_unit_bindings (id VARCHAR(40) PRIMARY KEY, "
                "artifact_id VARCHAR(40) NOT NULL REFERENCES artifacts(id), "
                "unit_id VARCHAR(40) NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO artifacts VALUES ('art_draft', 'cells', 'Draft', 'Early body')"
            )
            connection.exec_driver_sql(
                "INSERT INTO artifact_unit_bindings VALUES ('bind_1', 'art_draft', 'unit_1')"
            )

        run_migrations(engine, Base.metadata)

        inspector = inspect(engine)
        assert {column["name"] for column in inspector.get_columns("artifacts")} == {
            column.name for column in Artifact.__table__.columns
        }
        assert "ix_artifacts_knowledge_base_id" in {
            index["name"] for index in inspector.get_indexes("artifacts")
        }
        assert [
            foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys("artifact_unit_bindings_legacy")
        ] == ["artifacts_legacy"]
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT id, title, body FROM artifacts_legacy")
            ).one() == ("art_draft", "Draft", "Early body")
            assert connection.execute(
                text("SELECT id, artifact_id FROM artifact_unit_bindings_legacy")
            ).one() == ("bind_1", "art_draft")
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        sessions = create_session_factory(engine)
        with session_scope(sessions) as session:
            assert session.scalars(
                select(Artifact).where(Artifact.workspace_id == workspace_id)
            ).all() == []
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_v12_database_gains_trash_columns_without_touching_rows(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    trash_columns = {"trashed_at", "trash_batch_id"}
    try:
        run_migrations(engine, Base.metadata, MIGRATIONS[:12])
        with engine.begin() as connection:
            # Baseline adopts current metadata; remove what v12 did not have.
            for table_name in ("knowledge_bases", "sources", "notebook_notes"):
                for column_name in sorted(trash_columns):
                    connection.exec_driver_sql(f"DROP INDEX ix_{table_name}_{column_name}")
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table_name} DROP COLUMN {column_name}"
                    )
            connection.execute(
                text(
                    "INSERT INTO sources (id, title, kind, content, content_hash, created_at) "
                    "VALUES ('src_kept', 'Kept', 'note', 'Body', :hash, CURRENT_TIMESTAMP)"
                ),
                {"hash": "c" * 64},
            )
        assert "trashed_at" not in {c["name"] for c in inspect(engine).get_columns("sources")}

        run_migrations(engine, Base.metadata)

        inspector = inspect(engine)
        for table_name in ("knowledge_bases", "sources", "notebook_notes"):
            assert trash_columns <= {c["name"] for c in inspector.get_columns(table_name)}
            assert {
                f"ix_{table_name}_trashed_at",
                f"ix_{table_name}_trash_batch_id",
            } <= {index["name"] for index in inspector.get_indexes(table_name)}
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT title, trashed_at FROM sources WHERE id = 'src_kept'")
            ).one() == ("Kept", None)
        assert get_schema_version(engine) == LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()
