"""Mount the API routers in their public route order."""

import logging

from fastapi import APIRouter

from gunther import (
    artifact_conversations_api,
    artifact_read_api,
    ask_api,
    key_results_api,
    knowledge_units_api,
    library_api,
    output_commands_api,
    recording_api,
    source_api,
    system_api,
)

logger = logging.getLogger(__name__)

router = APIRouter()
router.include_router(system_api.router)
router.include_router(library_api.search_router)
router.include_router(source_api.capture_router)
router.include_router(recording_api.router)
router.include_router(library_api.router)
router.include_router(key_results_api.router)
router.include_router(source_api.router)
router.include_router(ask_api.router)
router.include_router(knowledge_units_api.router)
router.include_router(artifact_read_api.router)
router.include_router(output_commands_api.stream_router)
router.include_router(artifact_conversations_api.turn_router)
router.include_router(output_commands_api.router)
router.include_router(artifact_conversations_api.router)
router.include_router(knowledge_units_api.proposal_update_router)
