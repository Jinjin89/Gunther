"""Proposal and saved-knowledge routes."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from gunther.api_common import _service
from gunther.schemas import (
    CreateKnowledgeProposalInput,
    KnowledgeProposalOut,
    KnowledgeUnitOut,
    UpdateKnowledgeProposalInput,
)

router = APIRouter()
proposal_update_router = APIRouter()


@router.post(
    "/sessions/{session_id}/messages/{message_id}/proposal",
    response_model=KnowledgeProposalOut,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_proposal(
    session_id: str,
    message_id: str,
    payload: CreateKnowledgeProposalInput,
    request: Request,
) -> KnowledgeProposalOut:
    try:
        return _service(request).create_knowledge_proposal(session_id, message_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get(
    "/knowledge-bases/{knowledge_base_id}/proposals",
    response_model=list[KnowledgeProposalOut],
)
def knowledge_proposals(
    knowledge_base_id: str,
    request: Request,
    proposal_status: Annotated[
        str | None, Query(alias="status", pattern="^(pending|accepted|held|rejected)$")
    ] = None,
) -> list[KnowledgeProposalOut]:
    return _service(request).list_knowledge_proposals(knowledge_base_id, status=proposal_status)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/units",
    response_model=list[KnowledgeUnitOut],
)
def knowledge_units(knowledge_base_id: str, request: Request) -> list[KnowledgeUnitOut]:
    return _service(request).list_knowledge_units(knowledge_base_id)


@proposal_update_router.patch("/proposals/{proposal_id}", response_model=KnowledgeProposalOut)
def update_knowledge_proposal(
    proposal_id: str,
    payload: UpdateKnowledgeProposalInput,
    request: Request,
) -> KnowledgeProposalOut:
    try:
        return _service(request).update_knowledge_proposal(proposal_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
