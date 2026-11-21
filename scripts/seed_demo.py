"""Load a repo demo fixture into a local development database."""

import argparse
import json
from pathlib import Path

from gunther.database import (
    Base,
    connection_transaction,
    create_database_engine,
    session_scope,
)
from gunther.extraction import ExtractionPayload
from gunther.migrations import run_migrations
from gunther.models import KnowledgeBaseRecord, Source
from gunther.schemas import CreateSourceInput, UpdateAssertionStatusInput
from gunther.service import KnowledgeService
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="SQLite database to seed")
    parser.add_argument("fixture", type=Path, help="demo fixture JSON")
    args = parser.parse_args()

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    bases = [KnowledgeBaseRecord(**data) for data in fixture["knowledge_bases"]]
    source = CreateSourceInput.model_validate(fixture["source"])
    analysis = ExtractionPayload.model_validate({"assertions": fixture.get("assertions", [])})
    database = args.database.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(f"sqlite+pysqlite:///{database}")
    try:
        run_migrations(engine, Base.metadata)
        with connection_transaction(engine, sqlite_immediate=True) as connection:
            sessions = sessionmaker(bind=connection, expire_on_commit=False)
            with session_scope(sessions) as session:
                for base in bases:
                    if session.get(KnowledgeBaseRecord, base.id) is None:
                        session.add(base)
                has_sources = session.scalar(select(Source.id).limit(1)) is not None
            if not has_sources:
                service = KnowledgeService(sessions)
                result = service.import_source(source)
                if analysis.assertions:
                    with session_scope(sessions) as session:
                        stored = session.get(Source, result.source.id)
                        service.store_source_extraction(session, stored, analysis)
                    service.update_source_assertion_statuses(
                        result.source.id,
                        UpdateAssertionStatusInput(status="verified", reason="Demo fixture import"),
                    )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
