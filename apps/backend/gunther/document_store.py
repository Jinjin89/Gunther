"""Publish canonical elements and evidence mappings in the revision transaction."""

from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy.orm import Session

from gunther.content import ParsedBlock
from gunther.documents import ParsedDocument, reference_data
from gunther.models import (
    Asset,
    BlockElementMap,
    CitationMention,
    DocumentElement,
    ElementAsset,
    ReferenceEntry,
)


def publish_document(
    session: Session,
    revision_id: str,
    document: ParsedDocument,
    block_ids: list[str],
    blocks: list[ParsedBlock],
) -> None:
    document.validate()
    ids = [f"elm_{uuid4().hex}" for _ in document.elements]
    for ordinal, element in enumerate(document.elements):
        session.add(
            DocumentElement(
                id=ids[ordinal],
                revision_id=revision_id,
                ordinal=ordinal,
                parent_id=ids[element.parent] if element.parent is not None else None,
                kind=element.kind,
                content=element.text,
                locator=element.locator[:240],
                heading_path_json=json.dumps(element.headings, ensure_ascii=False),
                anchor_json=json.dumps(element.anchor),
                payload_json=json.dumps(element.payload, ensure_ascii=False),
            )
        )
        session.flush()
        asset_id = element.payload.get("imageAssetId")
        if asset_id:
            if not isinstance(asset_id, str) or session.get(Asset, asset_id) is None:
                raise ValueError("Document image has no retained asset")
            session.add(ElementAsset(element_id=ids[ordinal], asset_id=asset_id))
    for block_id, block in zip(block_ids, blocks, strict=True):
        ordinal = block.payload.get("elementOrdinal")
        if not isinstance(ordinal, int) or not 0 <= ordinal < len(ids):
            raise ValueError("Evidence block has no canonical element")
        start, stop = block.anchor.get("elementCharStart"), block.anchor.get("elementCharEnd")
        if (
            not isinstance(start, int)
            or not isinstance(stop, int)
            or not 0 <= start <= stop <= len(document.elements[ordinal].text)
        ):
            raise ValueError("Invalid canonical evidence span")
        session.add(
            BlockElementMap(
                block_id=block_id, element_id=ids[ordinal], char_start=start, char_end=stop
            )
        )
    entries, mentions = reference_data(document.elements)
    reference_ids = [f"bib_{uuid4().hex}" for _ in entries]
    for entry_id, entry in zip(reference_ids, entries, strict=True):
        session.add(
            ReferenceEntry(
                id=entry_id,
                revision_id=revision_id,
                element_id=ids[entry["element"]],
                label=entry["label"],
                raw=entry["raw"],
                metadata_json=json.dumps(
                    {"year": entry["year"], "doi": entry["doi"], "provider": "local-v1"}
                ),
            )
        )
    for mention in mentions:
        session.add(
            CitationMention(
                id=f"mnt_{uuid4().hex}",
                revision_id=revision_id,
                element_id=ids[mention["element"]],
                char_start=mention["start"],
                char_end=mention["end"],
                raw=mention["raw"],
                status=mention["status"],
                method=mention["method"],
                targets_json=json.dumps([reference_ids[index] for index in mention["targets"]]),
            )
        )
