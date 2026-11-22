"""Read saved artifacts and serve their reviewed render files."""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from gunther.api_common import _service
from gunther.artifact_api_common import WorkspaceHeader, _artifact_http_errors
from gunther.schemas import ArtifactOut, ArtifactSummaryOut

router = APIRouter()


@router.get(
    "/knowledge-bases/{knowledge_base_id}/artifacts",
    response_model=list[ArtifactSummaryOut],
)
def artifacts(
    knowledge_base_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> list[ArtifactSummaryOut]:
    with _artifact_http_errors():
        return _service(request).list_artifacts(knowledge_base_id, workspace_id)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}/renders/{snapshot_id}/{name}",
    response_class=FileResponse,
)
def artifact_render(knowledge_base_id: str, artifact_id: str, snapshot_id: str, name: str,
                    request: Request, workspace_id: Annotated[str, Query(min_length=1,
                                                                       max_length=40)]):
    service = _service(request)
    with _artifact_http_errors():
        shown = service.get_artifact(knowledge_base_id, artifact_id, workspace_id)
        skill = getattr(shown.provenance, "skill", None)
        render = skill.render_check if skill else None
        visual = skill.visual_review if skill else None
        allowed = {render.get("id")} if render else set()
        allowed.update(item.get("snapshotId") for item in (visual or {}).get("history", []))
        if snapshot_id not in allowed or service.slide_renderer is None:
            raise LookupError("Slide render not found.")
        path = service.slide_renderer.file(snapshot_id, name)
        return FileResponse(path, media_type="application/pdf" if name.endswith(".pdf")
                            else "image/png", headers={"Cache-Control": "private, no-store"})


@router.get(
    "/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}",
    response_model=ArtifactOut,
)
def artifact(
    knowledge_base_id: str,
    artifact_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> ArtifactOut:
    with _artifact_http_errors():
        return _service(request).get_artifact(
            knowledge_base_id,
            artifact_id,
            workspace_id,
        )
