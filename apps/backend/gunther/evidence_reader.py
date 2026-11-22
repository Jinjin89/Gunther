"""Read passages and their figures within the revision that supplied the evidence."""

import json
from collections.abc import Collection, Iterable
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from gunther.document_structure import evidence_eligible
from gunther.evidence import Evidence, ReadResult, ToolFailure
from gunther.models import (
    Asset,
    BlockElementMap,
    ContentBlock,
    DocumentElement,
    SourceIndexHead,
    SourceRevision,
)
from gunther.schemas import ConversationCitationOut

SECTION_CHARS = 6000
ADJACENT_CHARS = 4000
FIGURE_DISTANCE = 4


def pinned_revision(session: Session, source_id: str, revision_id: str) -> SourceRevision | None:
    return session.scalar(
        select(SourceRevision).where(
            SourceRevision.id == revision_id, SourceRevision.source_id == source_id,
        )
    )


def readable_blocks(revision_id: str, block_ids: Collection[str] | None = None):
    query = (
        select(ContentBlock)
        .where(
            ContentBlock.revision_id == revision_id,
            evidence_eligible(ContentBlock.kind, ContentBlock.payload_json, include_headings=True),
        )
        .order_by(ContentBlock.ordinal)
    )
    return query if block_ids is None else query.where(ContentBlock.id.in_(block_ids))


def passage_block(
    session: Session, citation: ConversationCitationOut, block_ids: Collection[str] | None = None,
) -> ContentBlock | None:
    """Resolve a saved citation's anchor. Older citations may have only a quote."""

    revision_id = citation.source_revision_id or session.scalar(
        select(SourceIndexHead.revision_id).where(SourceIndexHead.source_id == citation.source_id)
    )
    if not revision_id or pinned_revision(session, citation.source_id, revision_id) is None:
        return None
    if citation.block_id:
        if block_ids is not None and citation.block_id not in block_ids:
            return None
        block = session.get(ContentBlock, citation.block_id)
        return block if block and block.revision_id == revision_id else None

    query = readable_blocks(revision_id, block_ids)
    exact = session.scalar(query.where(ContentBlock.content == citation.quote).limit(1))
    if exact is not None:
        return exact
    # A legacy search passage may start partway through a block or span several.
    quote = " ".join(citation.quote.split())
    if len(quote) >= 20:
        for block in session.scalars(query):
            text = " ".join(block.content.split())
            if quote[:60] in text or (len(text) >= 40 and quote.startswith(text[:60])):
                return block
    return None


def section_blocks(
    session: Session, block: ContentBlock, block_ids: Collection[str] | None = None,
) -> list[ContentBlock]:
    """Grow around the passage within its heading, or within three blocks on each side."""

    if not evidence_eligible(block.kind, block.payload_json, include_headings=True):
        return []
    query = readable_blocks(block.revision_id, block_ids)
    if block.heading_path_json and block.heading_path_json != "[]":
        query = query.where(ContentBlock.heading_path_json == block.heading_path_json)
    else:
        query = query.where(ContentBlock.ordinal.between(block.ordinal - 3, block.ordinal + 3))
    blocks = list(session.scalars(query))
    at = next((i for i, item in enumerate(blocks) if item.id == block.id), None)
    if at is None:
        return []
    low = high = at
    size = len(blocks[at].content)
    turn = 0
    while size < SECTION_CHARS:
        if turn % 2 == 0 and high + 1 < len(blocks):
            step, edge = 1, high + 1
        elif low > 0:
            step, edge = -1, low - 1
        elif high + 1 < len(blocks):
            step, edge = 1, high + 1
        else:
            break
        if size + len(blocks[edge].content) + 2 > SECTION_CHARS:
            break
        size += len(blocks[edge].content) + 2
        if step > 0:
            high = edge
        else:
            low = edge
        turn += 1
    return blocks[low : high + 1]


def read_snapshot(blocks: Iterable[ContentBlock], limit: int = SECTION_CHARS) -> ReadResult:
    pieces: list[str] = []
    spans: list[dict[str, object]] = []
    size = 0
    for block in blocks:
        remaining = limit - size - (2 if pieces else 0)
        if remaining <= 0:
            break
        piece = block.content[:remaining]
        pieces.append(piece)
        size += len(piece) + (2 if len(pieces) > 1 else 0)
        spans.append(
            {
                "blockId": block.id,
                "revisionId": block.revision_id,
                "start": 0,
                "end": len(piece),
                "anchor": json.loads(block.anchor_json),
            }
        )
    return ReadResult("\n\n".join(pieces), spans)


def read_passage(
    session: Session,
    item: Evidence,
    *,
    window: Literal["section", "adjacent"],
    source_ids: Collection[str] | None = None,
    block_ids: Collection[str] | None = None,
    pinned_keys: Collection[tuple[str, str, str]] = (),
) -> tuple[ConversationCitationOut, ReadResult]:
    """Expand eligible pool evidence inside the caller's scope and its pinned revision."""

    citation = item.payload[0] if isinstance(item.payload, tuple) and item.payload else None
    if (
        item.kind != "library"
        or not isinstance(citation, ConversationCitationOut)
        or not citation.source_id
    ):
        raise ToolFailure("Only a passage from the library can be read further.")
    if (
        source_ids is not None
        and citation.source_id not in source_ids
        and item.key not in pinned_keys
    ):
        raise ToolFailure("That passage is outside the current source selection.")
    block = passage_block(session, citation, block_ids)
    if block is None:
        raise ToolFailure("That passage is no longer in its source.")
    if window == "adjacent":
        around = readable_blocks(block.revision_id, block_ids).where(
            ContentBlock.ordinal.between(block.ordinal - 2, block.ordinal + 2)
        )
        return citation, read_snapshot(session.scalars(around), ADJACENT_CHARS)
    return citation, read_snapshot(section_blocks(session, block, block_ids))


def element_positions(revision_id: str):
    return (
        select(BlockElementMap.block_id, DocumentElement.ordinal)
        .join(DocumentElement, DocumentElement.id == BlockElementMap.element_id)
        .where(DocumentElement.revision_id == revision_id)
    )


def figure_reference(
    session: Session, figure: DocumentElement, *, detailed: bool = False,
) -> dict | None:
    payload = json.loads(figure.payload_json or "{}")
    anchor = json.loads(figure.anchor_json or "{}")
    asset_id = payload.get("imageAssetId")
    if not asset_id or session.get(Asset, asset_id) is None:
        return None
    found = {
        "assetId": asset_id,
        "elementId": figure.id,
        "caption": payload.get("caption") or figure.content,
        "page": anchor.get("page"),
        "revisionId": figure.revision_id,
    }
    if detailed:
        found.update(
            region=anchor.get("bbox") or anchor.get("bboxPpm"),
            quality={key: payload[key] for key in ("cropCheck", "glyphCheck") if key in payload},
        )
    return found


def nearby_figures(session: Session, citation: ConversationCitationOut) -> list[dict]:
    if not citation.block_id or not citation.source_revision_id:
        return []
    block = passage_block(session, citation)
    if block is None:
        return []
    anchor = session.execute(
        element_positions(block.revision_id).where(BlockElementMap.block_id == block.id)
        .order_by(DocumentElement.ordinal).limit(1)
    ).first()
    if anchor is None:
        return []
    _, ordinal = anchor
    figures = []
    for figure in session.scalars(
        select(DocumentElement)
        .where(
            DocumentElement.revision_id == block.revision_id,
            DocumentElement.kind == "figure",
            DocumentElement.ordinal.between(ordinal - FIGURE_DISTANCE, ordinal + FIGURE_DISTANCE),
        )
        .order_by(DocumentElement.ordinal)
        .limit(4)
    ):
        found = figure_reference(session, figure)
        page = citation.anchor.get("page")
        if found and (not found["page"] or not page or found["page"] == page):
            figures.append(found)
    return figures
