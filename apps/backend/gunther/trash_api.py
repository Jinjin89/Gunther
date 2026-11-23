from fastapi import APIRouter, HTTPException, Request

from gunther.artifact_api_common import WorkspaceHeader
from gunther.schemas import ApiModel, TrashItemKind, TrashItemOut
from gunther.trash import TrashConflict, TrashService

router = APIRouter()


class EmptyTrashOut(ApiModel):
    deleted: int


def _trash(request: Request) -> TrashService:
    return request.app.state.trash_service


def trash_error(error: Exception) -> HTTPException:
    return HTTPException(
        status_code=409 if isinstance(error, TrashConflict) else 404,
        detail=str(error),
    )


@router.get("/trash", response_model=list[TrashItemOut])
def list_trash(request: Request) -> list[TrashItemOut]:
    return _trash(request).list()


@router.post("/sources/{source_id}/trash", response_model=TrashItemOut)
def trash_source(source_id: str, request: Request) -> TrashItemOut:
    try:
        return _trash(request).trash_source(source_id)
    except (LookupError, TrashConflict) as error:
        raise trash_error(error) from error


@router.post("/notes/{note_id}/trash", response_model=TrashItemOut)
def trash_note(note_id: str, request: Request) -> TrashItemOut:
    try:
        return _trash(request).trash_note(note_id)
    except (LookupError, TrashConflict) as error:
        raise trash_error(error) from error


@router.post("/knowledge-bases/{knowledge_base_id}/trash", response_model=TrashItemOut)
def trash_library(knowledge_base_id: str, request: Request) -> TrashItemOut:
    try:
        return _trash(request).trash_library(knowledge_base_id)
    except (LookupError, TrashConflict) as error:
        raise trash_error(error) from error


@router.post(
    "/knowledge-bases/{library_id}/artifacts/{artifact_id}/trash", response_model=TrashItemOut,
)
def trash_artifact(
    library_id: str, artifact_id: str, request: Request, workspace_id: WorkspaceHeader,
) -> TrashItemOut:
    try:
        return _trash(request).trash_output(workspace_id, library_id, artifact_id)
    except (LookupError, TrashConflict) as error:
        raise trash_error(error) from error


@router.post(
    "/knowledge-bases/{library_id}/artifact-conversations/{conversation_id}/trash",
    response_model=TrashItemOut,
)
def trash_artifact_conversation(
    library_id: str, conversation_id: str, request: Request, workspace_id: WorkspaceHeader,
) -> TrashItemOut:
    try:
        return _trash(request).trash_output(
            workspace_id, library_id, conversation_id, conversation=True,
        )
    except (LookupError, TrashConflict) as error:
        raise trash_error(error) from error


@router.post("/trash/{kind}/{item_id}/restore", response_model=TrashItemOut)
def restore_from_trash(kind: TrashItemKind, item_id: str, request: Request) -> TrashItemOut:
    try:
        return _trash(request).restore(kind, item_id)
    except (LookupError, TrashConflict) as error:
        raise trash_error(error) from error


@router.delete("/trash/{kind}/{item_id}", response_model=TrashItemOut)
def delete_forever(kind: TrashItemKind, item_id: str, request: Request) -> TrashItemOut:
    try:
        return _trash(request).delete_forever(kind, item_id)
    except (LookupError, TrashConflict) as error:
        raise trash_error(error) from error


@router.delete("/trash", response_model=EmptyTrashOut)
def empty_trash(request: Request) -> EmptyTrashOut:
    return EmptyTrashOut(deleted=_trash(request).empty())
