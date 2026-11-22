"""Source capture, asset downloads, notes, filing and assertions."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from gunther.api_common import _declared_body_size, _service
from gunther.asset_service import (
    AssetNotFoundError,
    AssetService,
    AssetTooLargeError,
    AssetValidationError,
)
from gunther.schemas import (
    AssertionOut,
    AssetCaptureOut,
    CreateNotebookNoteInput,
    CreateSourceInput,
    FileNotebookNoteInput,
    FileNotebookNoteOut,
    FileSourceInput,
    FileSourceOut,
    ImportResultOut,
    NotebookNoteOut,
    RenameSourceInput,
    SourceAnalysisOut,
    SourceDetailOut,
    SourceKind,
    SourceSummaryOut,
    UpdateAssertionStatusInput,
    UpdateNotebookNoteInput,
    WebCaptureInput,
    WebCaptureOut,
)
from gunther.storage_budget import StorageBudgetError
from gunther.web_capture import (
    WebCaptureConflictError,
    WebCaptureFetchError,
    WebCaptureService,
    WebCaptureTimeoutError,
    WebCaptureTooLargeError,
    WebCaptureValidationError,
)

capture_router = APIRouter()
router = APIRouter()


def _assets(request: Request) -> AssetService:
    service = _service(request)
    return AssetService(
        service.sessions,
        request.app.state.settings.assets_dir,
        service,
        request.app.state.ocr_provider,
        request.app.state.storage_budget,
    )


def _web_captures(request: Request) -> WebCaptureService:
    return WebCaptureService(
        _assets(request),
        _service(request),
        request.app.state.web_capture_resolver,
        request.app.state.web_capture_fetcher,
    )


@capture_router.post(
    "/captures/assets",
    response_model=AssetCaptureOut,
    status_code=status.HTTP_201_CREATED,
)
async def capture_asset(
    request: Request,
    title: Annotated[str, Query(min_length=1, max_length=160)],
    file_name: Annotated[str, Query(alias="fileName", min_length=1, max_length=255)],
    kind: Annotated[SourceKind, Query()] = "file",
    knowledge_base_id: Annotated[str | None, Query(alias="knowledgeBaseId", max_length=160)] = None,
    notes: Annotated[str, Query(max_length=5_000)] = "",
) -> AssetCaptureOut:
    try:
        return await _assets(request).capture(
            request.stream(),
            title=title,
            kind=kind,
            file_name=file_name,
            media_type=request.headers.get("content-type", "application/octet-stream"),
            knowledge_base_id=knowledge_base_id,
            notes=notes,
            expected_size=_declared_body_size(request),
        )
    except AssetTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except AssetValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except StorageBudgetError as error:
        raise HTTPException(status_code=507, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@capture_router.post(
    "/captures/web",
    response_model=WebCaptureOut,
    status_code=status.HTTP_201_CREATED,
)
async def capture_web(payload: WebCaptureInput, request: Request) -> WebCaptureOut:
    try:
        return await _web_captures(request).capture(payload)
    except WebCaptureTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except WebCaptureValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except StorageBudgetError as error:
        raise HTTPException(status_code=507, detail=str(error)) from error
    except WebCaptureConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WebCaptureTimeoutError as error:
        raise HTTPException(status_code=504, detail=str(error)) from error
    except WebCaptureFetchError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@capture_router.get("/assets/{asset_id}", response_class=FileResponse)
def download_asset(asset_id: str, request: Request) -> FileResponse:
    try:
        path, media_type, file_name = _assets(request).download(asset_id)
        return FileResponse(path, media_type=media_type, filename=file_name)
    except AssetNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@capture_router.get("/notes", response_model=list[NotebookNoteOut])
def notebook_notes(
    request: Request,
    note_status: Annotated[
        str | None, Query(alias="status", pattern="^(inbox|filed|archived)$")
    ] = None,
    query: Annotated[str | None, Query(alias="q", max_length=500)] = None,
) -> list[NotebookNoteOut]:
    return _service(request).list_notebook_notes(status=note_status, query=query)


@capture_router.post(
    "/notes",
    response_model=NotebookNoteOut,
    status_code=status.HTTP_201_CREATED,
)
def create_notebook_note(
    payload: CreateNotebookNoteInput,
    request: Request,
) -> NotebookNoteOut:
    return _service(request).create_notebook_note(payload)


@capture_router.patch("/notes/{note_id}", response_model=NotebookNoteOut)
def update_notebook_note(
    note_id: str,
    payload: UpdateNotebookNoteInput,
    request: Request,
) -> NotebookNoteOut:
    try:
        return _service(request).update_notebook_note(note_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@capture_router.post("/notes/{note_id}/file", response_model=FileNotebookNoteOut)
def file_notebook_note(
    note_id: str,
    payload: FileNotebookNoteInput,
    request: Request,
) -> FileNotebookNoteOut:
    try:
        return _service(request).file_notebook_note(note_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/sources", response_model=list[SourceSummaryOut])
def sources(request: Request) -> list[SourceSummaryOut]:
    return _service(request).list_sources()


@router.get("/sources/{source_id}", response_model=SourceDetailOut)
def source(source_id: str, request: Request) -> SourceDetailOut:
    try:
        return _service(request).get_source(source_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/sources/{source_id}", response_model=SourceSummaryOut)
def rename_source(source_id: str, payload: RenameSourceInput, request: Request) -> SourceSummaryOut:
    try:
        return _service(request).rename_source(source_id, payload.title)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sources/{source_id}/file", response_model=FileSourceOut)
def file_source(
    source_id: str,
    payload: FileSourceInput,
    request: Request,
) -> FileSourceOut:
    try:
        return _service(request).file_source(source_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/knowledge-bases/{knowledge_base_id}/sources",
    response_model=list[SourceSummaryOut],
)
def knowledge_base_sources(knowledge_base_id: str, request: Request) -> list[SourceSummaryOut]:
    return _service(request).list_knowledge_base_sources(knowledge_base_id)


@router.post(
    "/sources",
    response_model=ImportResultOut,
    status_code=status.HTTP_201_CREATED,
)
def create_source(
    payload: CreateSourceInput,
    request: Request,
    defer_analysis: Annotated[bool, Query(alias="deferAnalysis")] = False,
) -> ImportResultOut:
    try:
        # The deferred receipt parameter remains valid. Both forms queue Analysis.
        return _service(request).import_source(payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post(
    "/sources/{source_id}/analysis/retry", response_model=SourceAnalysisOut, status_code=202
)
def retry_source_analysis(source_id: str, request: Request) -> SourceAnalysisOut:
    try:
        return _service(request).retry_source_analysis(source_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/assertions", response_model=list[AssertionOut])
def assertions(
    request: Request,
    assertion_status: Annotated[
        str | None, Query(alias="status", pattern="^(provisional|verified|disputed)$")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AssertionOut]:
    return _service(request).list_assertions(status=assertion_status, limit=limit)


@router.patch("/sources/{source_id}/assertions/status", response_model=list[AssertionOut])
def update_source_assertion_statuses(
    source_id: str,
    payload: UpdateAssertionStatusInput,
    request: Request,
) -> list[AssertionOut]:
    try:
        return _service(request).update_source_assertion_statuses(source_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/assertions/{assertion_id}/status", response_model=AssertionOut)
def update_assertion_status(
    assertion_id: str,
    payload: UpdateAssertionStatusInput,
    request: Request,
) -> AssertionOut:
    try:
        return _service(request).update_assertion_status(assertion_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
