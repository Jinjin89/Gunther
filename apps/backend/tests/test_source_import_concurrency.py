import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock

from gunther.conversation import LocalKnowledgeResponder
from gunther.database import Base, create_database_engine, create_session_factory
from gunther.extraction import ExtractionResult, LocalExtractor
from gunther.migrations import run_migrations
from gunther.schemas import CreateKnowledgeBaseInput, CreateSourceInput
from gunther.service import KnowledgeService


class ControlledExtractor(LocalExtractor):
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.calls = 0
        self._calls_lock = Lock()

    def extract(self, title: str, content: str) -> ExtractionResult:
        with self._calls_lock:
            self.calls += 1
        self.entered.set()
        assert self.release.wait(timeout=5)
        return super().extract(title, content)


class BarrierExtractor(LocalExtractor):
    def __init__(self, barrier: Barrier) -> None:
        self.barrier = barrier

    def extract(self, title: str, content: str) -> ExtractionResult:
        self.barrier.wait(timeout=5)
        return super().extract(title, content)


def make_service(tmp_path: Path, extractor: LocalExtractor):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}")
    run_migrations(engine, Base.metadata)
    sessions = create_session_factory(engine)
    return engine, sessions, KnowledgeService(sessions, extractor, LocalKnowledgeResponder())


def test_one_service_extracts_identical_concurrent_import_only_once(tmp_path: Path) -> None:
    extractor = ControlledExtractor()
    engine, _sessions, service = make_service(tmp_path, extractor)
    payload = CreateSourceInput(
        title="Concurrent note",
        kind="note",
        content="Retrieval practice -> improves -> durable memory",
    )
    second_started = Event()

    def second_import():
        second_started.set()
        return service.import_source(payload)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(service.import_source, payload)
            assert extractor.entered.wait(timeout=5)
            second = executor.submit(second_import)
            assert second_started.wait(timeout=5)

            # Keep the first extraction open long enough for the second request to
            # reach the content-hash lock. It must not invoke the extractor itself.
            time.sleep(0.15)
            assert extractor.calls == 1
            extractor.release.set()

            results = [first.result(timeout=5), second.result(timeout=5)]

        assert sorted(result.duplicate for result in results) == [False, True]
        assert results[0].source.id == results[1].source.id
        assert len(service.list_sources()) == 1
        assert extractor.calls == 1
    finally:
        extractor.release.set()
        engine.dispose()


def test_database_race_falls_back_to_existing_source_and_files_both_memberships(
    tmp_path: Path,
) -> None:
    barrier = Barrier(2)
    engine, sessions, first_service = make_service(tmp_path, BarrierExtractor(barrier))
    second_service = KnowledgeService(
        sessions,
        BarrierExtractor(barrier),
        LocalKnowledgeResponder(),
    )
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
            first = executor.submit(first_service.import_source, first_payload)
            second = executor.submit(second_service.import_source, second_payload)
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
