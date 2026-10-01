"""Output versions from the old builder, written straight to the database.

The old builder is gone, but versions it made stay readable, so tests need some. This
repeats how it sealed them. The hashing is deliberately an independent copy, not
``manifest_hash_of``, so the code that reads these versions is not checked against itself.
"""

import hashlib
import json
from uuid import uuid4

from gunther.models import (
    Artifact,
    ArtifactUnitBinding,
    KnowledgeBaseRecord,
    KnowledgeProposal,
    KnowledgeUnit,
    KnowledgeUnitRevision,
    SessionMessage,
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def insert_legacy_version(
    sessions,
    *,
    workspace_id: str,
    base_id: str,
    unit_id: str,
    request_id: str,
    supersedes: str | None = None,
    format: str = "field_guide",
    audience: str = "scientist",
    title: str = "Legacy output",
) -> str:
    """One version of an output the way the old builder saved it; returns its id.

    It pins the unit's current revision, with a binding, and is sealed with the version 1
    manifest (format, audience, title, content hash, pinned units, provenance).
    """

    with sessions() as session:
        unit = session.get(KnowledgeUnit, unit_id)
        head = session.get(KnowledgeUnitRevision, unit.head_revision_id)
        proposal = session.get(KnowledgeProposal, head.source_proposal_id)
        message = session.get(SessionMessage, proposal.message_id)
        library = session.get(KnowledgeBaseRecord, base_id)
        snapshot = {
            "unit_id": unit.id,
            "revision_id": head.id,
            "revision_number": head.revision_number,
            "title": unit.title,
            "content": head.content,
            "content_hash": _sha(head.content),
            "source_proposal_id": proposal.id,
            "source_session_id": proposal.session_id,
            "source_message_id": proposal.message_id,
            "evidence_count": len(json.loads(message.citations_json or "[]")),
        }
        provenance = {
            "schema_version": 1,
            "generator": "gunther.local-template.v1",
            "workspace_id": workspace_id,
            "knowledge_base_id": base_id,
            "knowledge_base_question": library.question,
            "accepted_only": True,
            "accepted_unit_ids": [unit.id],
            "revision_ids": [head.id],
        }
        parent = session.get(Artifact, supersedes) if supersedes else None
        content = (
            f"# {title}\n\n## 1. {unit.title}\n\n{head.content}\n\n"
            f"> Provenance: accepted knowledge unit `{unit.id}`, revision {head.revision_number}."
        )
        content_hash = _sha(content)
        manifest = _sha(
            _canonical(
                {
                    "format": format,
                    "audience": audience,
                    "title": title,
                    "contentHash": content_hash,
                    "acceptedUnitIds": [unit.id],
                    "revisionSnapshot": [snapshot],
                    "provenance": provenance,
                }
            )
        )
        artifact = Artifact(
            id=f"art_{uuid4().hex}",
            workspace_id=workspace_id,
            knowledge_base_id=base_id,
            client_request_id=request_id,
            request_fingerprint=_sha(request_id),
            lineage_id=parent.lineage_id if parent else f"arl_{uuid4().hex}",
            version_number=parent.version_number + 1 if parent else 1,
            supersedes_artifact_id=parent.id if parent else None,
            format=format,
            style=format,
            audience=audience,
            title=title,
            content=content,
            content_hash=content_hash,
            manifest_hash=manifest,
            accepted_unit_ids_json=json.dumps([unit.id], separators=(",", ":")),
            revision_snapshot_json=_canonical([snapshot]),
            provenance_json=_canonical(provenance),
        )
        session.add(artifact)
        session.flush()
        session.add(
            ArtifactUnitBinding(
                id=f"aub_{uuid4().hex}",
                artifact_id=artifact.id,
                position=0,
                unit_id=unit.id,
                revision_id=head.id,
                content_hash=snapshot["content_hash"],
            )
        )
        session.commit()
        return artifact.id
