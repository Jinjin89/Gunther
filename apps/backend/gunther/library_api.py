"""Library search, library settings, graph, overview and Inbox routes."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from gunther.api_common import _service
from gunther.database import session_scope
from gunther.library_overview import LibraryOverviewOut, live_library, request_overview
from gunther.schemas import (
    CreateKnowledgeBaseInput,
    InboxItemOut,
    KnowledgeBaseOut,
    KnowledgeGraphOut,
    KnowledgeSearchResultOut,
    OverviewOut,
    UpdateKnowledgeBaseInput,
    WebSearchOut,
)

search_router = APIRouter()
router = APIRouter()


@search_router.get("/search", response_model=list[KnowledgeSearchResultOut])
def search(
    request: Request,
    query: Annotated[str, Query(alias="q", min_length=1, max_length=500)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    knowledge_base_ids: Annotated[list[str] | None, Query(alias="knowledgeBaseId")] = None,
) -> list[KnowledgeSearchResultOut]:
    scope = list(dict.fromkeys(item.strip() for item in knowledge_base_ids or [] if item.strip()))
    if len(scope) > 20 or any(len(item) > 160 for item in scope):
        raise HTTPException(
            status_code=422,
            detail="Search at most 20 knowledge bases, each identified by at most 160 characters",
        )
    return _service(request).search_knowledge(query, limit=limit, knowledge_base_ids=scope or None)


@search_router.get("/search/web", response_model=WebSearchOut)
def web_search(
    request: Request,
    query: Annotated[str, Query(alias="q", min_length=1, max_length=500)],
) -> WebSearchOut:
    # Home shows a short answer above the pages; Ask's agent reads the pages itself.
    return request.app.state.online_search.search(query, answer=True)


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseOut])
def knowledge_bases(request: Request) -> list[KnowledgeBaseOut]:
    return _service(request).list_knowledge_bases()


@router.post(
    "/knowledge-bases",
    response_model=KnowledgeBaseOut,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_base(payload: CreateKnowledgeBaseInput, request: Request) -> KnowledgeBaseOut:
    return _service(request).create_knowledge_base(payload)


@router.patch("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseOut)
def update_knowledge_base(
    knowledge_base_id: str,
    payload: UpdateKnowledgeBaseInput,
    request: Request,
) -> KnowledgeBaseOut:
    try:
        return _service(request).update_knowledge_base(knowledge_base_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/knowledge-bases/{knowledge_base_id}/overview", response_model=LibraryOverviewOut)
def library_overview(
    knowledge_base_id: str, request: Request, retry: bool = False,
) -> LibraryOverviewOut:
    service = _service(request)
    models = service.models
    with session_scope(service.sessions) as session:
        # Serialize the freshness check and enqueue across overlapping Chat opens.
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if live_library(session, knowledge_base_id) is None:
            raise HTTPException(status_code=404, detail="Library was not found")
        return request_overview(
            session, knowledge_base_id, has_model=bool(models and models.for_role("analysis")),
            retry=retry,
        )


@router.get("/overview", response_model=OverviewOut)
def overview(request: Request) -> OverviewOut:
    return _service(request).overview()


@router.get("/graph", response_model=KnowledgeGraphOut)
def graph(request: Request) -> KnowledgeGraphOut:
    return _service(request).get_graph()


@router.get("/inbox", response_model=list[InboxItemOut])
def inbox(
    request: Request,
    inbox_state: Annotated[
        str | None, Query(alias="state", pattern="^(unfiled|needs_review)$")
    ] = None,
    item_type: Annotated[
        str | None,
        Query(alias="itemType", pattern="^(source|quick_note)$"),
    ] = None,
) -> list[InboxItemOut]:
    return _service(request).list_inbox(state=inbox_state, item_type=item_type)
