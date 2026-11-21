"""The immutable artifact seal used by the service and the checkout backup tool."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any


def artifact_manifest_hash(read_field: Callable[[str], Any]) -> str:
    """Hash the stored v1 or v2 fields using the original canonical JSON encoding."""

    provenance = json.loads(read_field("provenance_json"))
    sealed = {
        "audience": read_field("audience"),
        "title": read_field("title"),
        "contentHash": read_field("content_hash"),
        "acceptedUnitIds": json.loads(read_field("accepted_unit_ids_json")),
        "revisionSnapshot": json.loads(read_field("revision_snapshot_json")),
        "provenance": provenance,
    }
    if provenance.get("schema_version") == 2:
        sealed.update(
            kind=read_field("kind"),
            style=read_field("style"),
            brief=read_field("brief"),
            origin=read_field("origin"),
            citations=json.loads(read_field("citations_json")),
            outline=json.loads(read_field("outline_json")),
            scope=json.loads(read_field("scope_json")),
            inputs=json.loads(read_field("inputs_json")),
        )
    else:
        sealed["format"] = read_field("format")
    payload = json.dumps(sealed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
