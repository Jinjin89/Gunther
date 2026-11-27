"""Wire the library, Ask and Artifacts operations to one service instance."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from threading import Lock

from sqlalchemy.orm import Session, sessionmaker

from gunther.answer_runs import AnswerRuns
from gunther.artifact_conversations import ArtifactConversations
from gunther.artifact_read import ArtifactReader
from gunther.artifact_turns import ArtifactTurns
from gunther.artifact_view import AgentLimits
from gunther.ask_turns import AskTurns
from gunther.evidence_service import EvidenceService
from gunther.extraction import ModelExtractor
from gunther.knowledge_index import KnowledgeIndex
from gunther.knowledge_units import KnowledgeUnits
from gunther.library_service import LibraryService
from gunther.llm import ModelGateway
from gunther.model_profiles import Effort
from gunther.online_search import DisabledOnlineSearch, OnlineSearchProvider
from gunther.output_commands import OutputCommands
from gunther.output_material import OutputMaterialService
from gunther.schemas import OutputVoicesOut
from gunther.source_service import SourceService

logger = logging.getLogger(__name__)


class KnowledgeService(
    SourceService,
    LibraryService,
    AskTurns,
    EvidenceService,
    KnowledgeUnits,
    ArtifactReader,
    OutputMaterialService,
    OutputCommands,
    ArtifactConversations,
    ArtifactTurns,
):
    def __init__(
        self,
        sessions: sessionmaker[Session],
        extractor: ModelExtractor | None = None,
        *,
        answer_runs: AnswerRuns | None = None,
    ) -> None:
        self.sessions = sessions
        self.answer_runs = answer_runs if answer_runs is not None else AnswerRuns()
        self.output_voices: Callable[[], OutputVoicesOut] | None = None
        self.extractor = extractor
        # Every model that is set up (see llm); a conversation may pick any of them.
        self.models: ModelGateway | None = None
        self.web_search: OnlineSearchProvider = DisabledOnlineSearch()
        # Settings → Developer: keep how each answer is made (see trace).
        self.tracing = False
        self.slide_renderer = None
        # Reads a stored asset: id -> (path, media type, name). Set by the app.
        self.asset_reader: Callable[[str], tuple[Path, str, str]] | None = None
        # (plan effort, check effort) for Ask's own steps; the exam sets it, nothing else.
        self.agent_efforts: tuple[Effort, Effort] | None = None
        # Reads a public web page for the agent: URL -> (text, title). Set by the app; it
        # applies the same public-address rules as capturing a page.
        self.page_reader: Callable[[str], tuple[str, str | None]] | None = None
        self.index = KnowledgeIndex(sessions)
        self._source_import_locks_guard = Lock()
        self._source_import_locks: dict[str, tuple[Lock, int]] = {}
        # How far one turn of the Artifacts agent may go; the exam and tests may change it.
        self.artifact_limits = AgentLimits()
        # Listing and importing serialize creation of missing conversation rows.
        self._artifact_conversations_lock = Lock()
