import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock

from sqlalchemy import select

from gunther.database import Base, create_database_engine, create_session_factory
from gunther.migrations import run_migrations
from gunther.models import ProcessingJob
from gunther.schemas import CreateKnowledgeBaseInput, CreateSourceInput
from gunther.service import KnowledgeService


def make_service(tmp_path: Path):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}")
    run_migrations(engine, Base.metadata)
    sessions = create_session_factory(engine)
    return engine, sessions, KnowledgeService(sessions)


def test_one_service_queues_identical_concurrent_import_only_once(tmp_path: Path):
    entered, release = Event(), Event()
    calls_lock = Lock()
    calls = 0

    def find_capture(_session):
        nonlocal calls
        with calls_lock:
            calls += 1
        entered.set()
        assert release.wait(timeout=5)

    engine, sessions, service = make_service(tmp_path)
    payload = CreateSourceInput(
        title="Concurrent note",
        kind="note",
        content="Retrieval practice -> improves -> durable memory",
    )
    second_started = Event()

    def second_import():
        second_started.set()
        return service.import_source(payload, find_existing_capture=find_capture)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                service.import_source, payload, find_existing_capture=find_capture,
            )
            assert entered.wait(timeout=5)
            second = executor.submit(second_import)
            assert second_started.wait(timeout=5)

            # Keep the first lookup open until the second request reaches the content-hash lock.
            time.sleep(0.15)
            assert calls == 1
            release.set()

            results = [first.result(timeout=5), second.result(timeout=5)]

        assert sorted(result.duplicate for result in results) == [False, True]
        assert results[0].source.id == results[1].source.id
        assert len(service.list_sources()) == 1
        assert calls == 1
        with sessions() as session:
            analysis_jobs = session.scalars(select(ProcessingJob.id).where(
                ProcessingJob.kind == "extract_assertions",
            )).all()
            assert len(analysis_jobs) == 1
    finally:
        release.set()
        engine.dispose()


def test_database_race_falls_back_to_existing_source_and_files_both_memberships(tmp_path: Path):
    barrier = Barrier(2)

    def find_capture(_session):
        barrier.wait(timeout=5)

    engine, sessions, first_service = make_service(tmp_path)
    second_service = KnowledgeService(sessions)
    first_base = first_service.create_knowledge_base(
        CreateKnowledgeBaseInput(
            title="First library",
            question="What belongs in the first library?",
            description="The first destination for a concurrent source.",
        )
    )
    second_base = first_service.create_knowledge_base(
        CreateKnowledgeBaseInput(
            title="Second library",
            question="What belongs in the second library?",
            description="The second destination for a concurrent source.",
        )
    )
    content = "Sleep -> improves -> memory consolidation"
    first_payload = CreateSourceInput(
        title="Shared capture",
        kind="paper",
        content=content,
        knowledge_base_id=first_base.id,
    )
    second_payload = CreateSourceInput(
        title="Shared capture",
        kind="paper",
        content=content,
        knowledge_base_id=second_base.id,
    )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                first_service.import_source, first_payload, find_existing_capture=find_capture,
            )
            second = executor.submit(
                second_service.import_source, second_payload, find_existing_capture=find_capture,
            )
            results = [first.result(timeout=10), second.result(timeout=10)]

        assert sorted(result.duplicate for result in results) == [False, True]
        assert results[0].source.id == results[1].source.id
        assert len(first_service.list_sources()) == 1
        assert [
            source.id for source in first_service.list_knowledge_base_sources(first_base.id)
        ] == [results[0].source.id]
        assert [
            source.id for source in first_service.list_knowledge_base_sources(second_base.id)
        ] == [results[0].source.id]
    finally:
        engine.dispose()
