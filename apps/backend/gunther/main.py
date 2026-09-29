import asyncio
import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import event, select
from sqlalchemy.orm import ORMExecuteState, Session, sessionmaker

from gunther import vector_index
from gunther.api import router
from gunther.asset_service import AssetService
from gunther.config import PROJECT_ROOT, Settings, get_settings
from gunther.conversation import create_knowledge_responder
from gunther.database import (
    Base,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from gunther.device_auth import (
    AuthPrincipal,
    DeviceAuthError,
    DeviceAuthService,
    parse_bearer_authorization,
)
from gunther.digest import create_digest_writer
from gunther.document_parser import DoclingParser
from gunther.embedding import OnnxEmbedder, default_model_directory, model_is_installed
from gunther.extraction import create_extractor
from gunther.knowledge_api import router as knowledge_router
from gunther.lecture import create_lecture_summarizer
from gunther.library_folders import WATCHED_MODELS, LibraryFolders
from gunther.library_root import LibraryRootConflict, prepare_library_root
from gunther.library_topics import create_topic_writer
from gunther.migrations import run_migrations
from gunther.mobile_gateway_runtime import MobileGatewayRuntime
from gunther.models import WorkspaceIdentity
from gunther.ocr import OcrProvider, create_ocr_provider
from gunther.online_search import create_online_search
from gunther.pairing_exchange_guard import PairingExchangeGuard
from gunther.processing import ProcessingWorker
from gunther.request_body_limit import RequestBodyLimitMiddleware
from gunther.service import KnowledgeService
from gunther.storage_api import router as storage_router
from gunther.storage_budget import StorageBudget
from gunther.trash import TrashService
from gunther.trash_api import router as trash_router
from gunther.web_capture import (
    PinnedHttpFetcher,
    SystemWebResolver,
    WebFetcher,
    WebResolver,
)


def _semantic_search(
    settings: Settings, sessions: sessionmaker[Session]
) -> tuple[OnnxEmbedder | None, str | None]:
    """The local embedder, or None with the reason Settings shows."""

    if not settings.semantic_search:
        return None, "Turned off (SEMANTIC_SEARCH=false)."
    with session_scope(sessions) as session:
        if vector_index.extension_version(session) is None:
            return None, "This Python cannot load the sqlite-vec extension."
    model_dir = settings.embedding_model_path or default_model_directory(PROJECT_ROOT)
    if model_dir is None or not model_is_installed(model_dir):
        return None, "The embedding model is not installed. Run npm run models:fetch."
    return OnnxEmbedder(model_dir, settings.embedding_model_version), None


def _refresh_folders_on_change(
    sessions: sessionmaker[Session], folders: LibraryFolders
) -> None:
    """Ask the library folders to catch up whenever a watched row is written."""

    @event.listens_for(sessions, "after_flush")
    def _flushed(session: Session, _context: object) -> None:
        if any(
            isinstance(item, WATCHED_MODELS)
            for item in (*session.new, *session.dirty, *session.deleted)
        ):
            folders.request_sync()

    @event.listens_for(sessions, "do_orm_execute")
    def _bulk(state: ORMExecuteState) -> None:
        mapper = state.bind_mapper
        if (state.is_update or state.is_delete) and mapper and mapper.class_ in WATCHED_MODELS:
            folders.request_sync()


def create_app(
    settings: Settings | None = None,
    *,
    allow_sidecar_auth: bool = True,
    mobile_gateway: MobileGatewayRuntime | None = None,
    web_capture_fetcher: WebFetcher | None = None,
    web_capture_resolver: WebResolver | None = None,
    ocr_provider: OcrProvider | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    engine = create_database_engine(active_settings.database_url)
    run_migrations(engine, Base.metadata)
    sessions = create_session_factory(engine)
    library_folders: LibraryFolders | None = None
    storage_problem: str | None = None
    if active_settings.library_root is not None:
        try:
            with session_scope(sessions) as session:
                workspace_id = session.scalar(select(WorkspaceIdentity.workspace_id))
            if not workspace_id:
                raise LibraryRootConflict("This workspace has no identity yet")
            library_root = prepare_library_root(
                active_settings.library_root,
                workspace_id,
                previous_assets_dir=active_settings.previous_assets_dir,
                previous_recordings_dir=active_settings.previous_recordings_dir,
            )
            library_folders = LibraryFolders(
                sessions,
                library_root.path,
                assets_dir=library_root.assets_dir,
                recordings_dir=library_root.recordings_dir,
            )
            _refresh_folders_on_change(sessions, library_folders)
        except (LibraryRootConflict, OSError) as error:
            storage_problem = str(error)
            logging.getLogger(__name__).error("Library folders are off: %s", error)
            # Nothing moves and nothing mixes: originals stay where they were.
            active_settings = active_settings.model_copy(
                update={
                    "assets_dir": active_settings.previous_assets_dir
                    or active_settings.assets_dir,
                    "recordings_dir": active_settings.previous_recordings_dir
                    or active_settings.recordings_dir,
                }
            )
    device_auth = DeviceAuthService(sessions)
    extractor = create_extractor(
        active_settings.deepseek_api_key,
        active_settings.deepseek_model,
        active_settings.deepseek_base_url,
    )
    responder = create_knowledge_responder(
        active_settings.deepseek_api_key,
        active_settings.deepseek_model,
        active_settings.deepseek_base_url,
    )
    knowledge_service = KnowledgeService(sessions, extractor, responder)
    topic_writer = create_topic_writer(
        active_settings.deepseek_api_key,
        active_settings.deepseek_model,
        active_settings.deepseek_base_url,
    )
    embedder, off_reason = _semantic_search(active_settings, sessions)
    knowledge_service.index.embedder = embedder
    knowledge_service.index.semantic_off_reason = off_reason
    online_search = create_online_search(
        active_settings.openai_api_key,
        active_settings.openai_web_search_model,
    )
    lecture_summarizer = create_lecture_summarizer(
        active_settings.openai_api_key,
        active_settings.openai_summary_model,
        active_settings.deepseek_api_key,
        active_settings.deepseek_model,
        active_settings.deepseek_base_url,
    )
    digest_writer = create_digest_writer(
        active_settings.ai_summaries,
        openai_api_key=active_settings.openai_api_key,
        openai_model=active_settings.openai_summary_model,
        deepseek_api_key=active_settings.deepseek_api_key,
        deepseek_model=active_settings.deepseek_model,
        deepseek_base_url=active_settings.deepseek_base_url,
        images=active_settings.ai_summary_images,
    )
    knowledge_service.index.digest_method = digest_writer.method if digest_writer else None
    knowledge_service.index.digest_vision = bool(digest_writer and digest_writer.vision)
    knowledge_service.index.digest_off_reason = (
        None if digest_writer else "setting" if active_settings.ai_summaries == "off" else "no_key"
    )
    local_ocr = ocr_provider or create_ocr_provider(
        active_settings.ocr_provider,
        tesseract_command=active_settings.ocr_tesseract_command,
        pdftoppm_command=active_settings.ocr_pdftoppm_command,
        languages=active_settings.ocr_tesseract_languages,
    )
    storage_budget = StorageBudget(
        (active_settings.assets_dir, active_settings.recordings_dir),
        quota_bytes=active_settings.storage_quota_bytes,
        min_free_bytes=active_settings.storage_min_free_bytes,
    )
    worker = ProcessingWorker(knowledge_service.index, AssetService(
        sessions, active_settings.assets_dir, knowledge_service, local_ocr, storage_budget,
    ), document_parser=(DoclingParser(
        active_settings.docling_python, active_settings.docling_artifacts_path,
    ) if active_settings.docling_python and active_settings.docling_artifacts_path else None),
        digest_writer=digest_writer)

    trash_service = TrashService(
        sessions,
        assets_dir=active_settings.assets_dir,
        recordings_dir=active_settings.recordings_dir,
        retention_days=active_settings.trash_retention_days,
    )

    async def purge_trash() -> None:
        """Delete what has rested in Trash past the retention period, twice a day."""
        while True:
            try:
                await asyncio.to_thread(trash_service.purge_expired)
            except Exception:  # The next pass retries; each batch deletes atomically.
                logging.getLogger(__name__).warning("Trash cleanup failed", exc_info=True)
            await asyncio.sleep(12 * 60 * 60)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if active_settings.seed_demo:
            knowledge_service.seed_if_empty()
        # In-memory SQLite uses a single shared connection: tests drive run_once
        # explicitly rather than allowing concurrent transactions on that connection.
        background = not active_settings.database_url.endswith(":memory:")
        task = (asyncio.create_task(worker.run()) if active_settings.processing_worker_enabled
                and background else None)
        trash_task = asyncio.create_task(purge_trash()) if background else None
        if library_folders and background:
            library_folders.start()
        try:
            yield
        finally:
            if library_folders:
                library_folders.stop()
            if trash_task:
                trash_task.cancel()
            worker.stopping.set()
            if task:
                await task
            engine.dispose()

    application = FastAPI(
        title=active_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.knowledge_service = knowledge_service
    application.state.processing_worker = worker
    application.state.trash_service = trash_service
    application.state.topic_writer = topic_writer
    application.state.library_folders = library_folders
    application.state.storage_problem = storage_problem
    application.state.online_search = online_search
    application.state.lecture_summarizer = lecture_summarizer
    application.state.device_auth = device_auth
    application.state.settings = active_settings
    application.state.allow_sidecar_auth = allow_sidecar_auth
    application.state.mobile_gateway = mobile_gateway or MobileGatewayRuntime(enabled=False)
    application.state.pairing_exchange_guard = PairingExchangeGuard()
    application.state.web_capture_fetcher = web_capture_fetcher or PinnedHttpFetcher()
    application.state.web_capture_resolver = web_capture_resolver or SystemWebResolver()
    application.state.ocr_provider = local_ocr
    application.state.storage_budget = storage_budget
    application.add_middleware(RequestBodyLimitMiddleware)
    allowed_origins = list(dict.fromkeys(active_settings.cors_origins))
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def protect_local_api(request: Request, call_next):
        """Authenticate local sidecar or revocable device credentials.

        CORS response headers alone do not stop a browser from submitting a
        state-changing request. Origin validation therefore happens before the
        route is called. A bearer token is accepted only from Authorization;
        the legacy query fallback remains exclusively a sidecar-token channel.
        """

        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return JSONResponse(status_code=403, content={"detail": "Origin not allowed"})

        async def call_with_workspace_guard():
            """Reject a queued write if this process now serves another workspace.

            Clients attach the workspace identity captured with durable outbox
            content.  Checking it immediately before route dispatch closes the
            restore/profile-switch gap without changing ordinary API callers.
            """

            expected_workspace_id = request.headers.get("x-gunther-workspace-id")
            if expected_workspace_id is not None:
                try:
                    actual_workspace_id = device_auth.workspace().workspace_id
                except DeviceAuthError:
                    return JSONResponse(
                        status_code=503,
                        content={"detail": "Workspace identity is unavailable"},
                    )
                if not secrets.compare_digest(
                    expected_workspace_id, actual_workspace_id
                ):
                    return JSONResponse(
                        status_code=409,
                        content={"detail": "Workspace identity changed"},
                    )
            return await call_next(request)

        if request.method == "OPTIONS":
            return await call_next(request)

        pairing_exchange_path = (
            f"{active_settings.api_prefix.rstrip('/')}/pairing/exchange"
        )
        if request.method == "POST" and request.url.path == pairing_exchange_path:
            if any(
                (
                    request.headers.get("authorization"),
                    request.headers.get("x-gunther-token"),
                    request.query_params.get("token"),
                )
            ):
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            request.state.auth_principal = None
            return await call_next(request)

        authorization = request.headers.get("authorization")
        sidecar_candidates = [
            value
            for value in (
                request.headers.get("x-gunther-token"),
                request.query_params.get("token"),
            )
            if value
        ]
        if authorization is not None:
            if sidecar_candidates:
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            bearer = parse_bearer_authorization(authorization)
            if bearer is None:
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            try:
                principal = device_auth.authenticate_device(bearer)
            except DeviceAuthError:
                principal = None
            if principal is None:
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            if not principal.has_scope("api:access"):
                return JSONResponse(status_code=403, content={"detail": "Forbidden"})
            request.state.auth_principal = principal
            return await call_with_workspace_guard()

        if not allow_sidecar_auth:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

        expected = active_settings.auth_token
        if sidecar_candidates and expected:
            authorized = any(
                secrets.compare_digest(candidate, expected)
                for candidate in sidecar_candidates
            )
            if not authorized:
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            request.state.auth_principal = AuthPrincipal(kind="sidecar")
        elif sidecar_candidates or expected:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        else:
            request.state.auth_principal = AuthPrincipal(kind="development")

        return await call_with_workspace_guard()

    application.include_router(router, prefix=active_settings.api_prefix)
    application.include_router(knowledge_router, prefix=active_settings.api_prefix)
    application.include_router(trash_router, prefix=active_settings.api_prefix)
    application.include_router(storage_router, prefix=active_settings.api_prefix)
    return application


app = None if os.environ.get("GUNTHER_DESKTOP_SIDECAR") == "1" else create_app()
