import asyncio
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from gunther.api import router
from gunther.asset_service import AssetService
from gunther.config import Settings, get_settings
from gunther.conversation import create_knowledge_responder
from gunther.database import Base, create_database_engine, create_session_factory
from gunther.device_auth import (
    AuthPrincipal,
    DeviceAuthError,
    DeviceAuthService,
    parse_bearer_authorization,
)
from gunther.document_parser import DoclingParser
from gunther.extraction import create_extractor
from gunther.knowledge_api import router as knowledge_router
from gunther.knowledge_index import LocalEmbedder
from gunther.lecture import create_lecture_summarizer
from gunther.migrations import run_migrations
from gunther.mobile_gateway_runtime import MobileGatewayRuntime
from gunther.ocr import OcrProvider, create_ocr_provider
from gunther.online_search import create_online_search
from gunther.pairing_exchange_guard import PairingExchangeGuard
from gunther.processing import ProcessingWorker
from gunther.request_body_limit import RequestBodyLimitMiddleware
from gunther.service import KnowledgeService
from gunther.storage_budget import StorageBudget
from gunther.web_capture import (
    PinnedHttpFetcher,
    SystemWebResolver,
    WebFetcher,
    WebResolver,
)


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
    if active_settings.embedding_model_path:
        knowledge_service.index.embedder = LocalEmbedder(
            str(active_settings.embedding_model_path), active_settings.embedding_model_version
        )
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
    ) if active_settings.docling_python and active_settings.docling_artifacts_path else None))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if active_settings.seed_demo:
            knowledge_service.seed_if_empty()
        # In-memory SQLite uses a single shared connection: tests drive run_once
        # explicitly rather than allowing concurrent transactions on that connection.
        task = (asyncio.create_task(worker.run()) if active_settings.processing_worker_enabled
                and not active_settings.database_url.endswith(":memory:") else None)
        try:
            yield
        finally:
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
        allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
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
    return application


app = None if os.environ.get("GUNTHER_DESKTOP_SIDECAR") == "1" else create_app()
