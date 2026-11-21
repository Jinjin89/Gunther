from __future__ import annotations

import hashlib
import json


def _fingerprint(fields: dict[str, str | int]) -> str:
    canonical = json.dumps(fields, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def source_fingerprint(title: str, kind: str, content: str) -> str:
    """Return the stable identity of one user-visible Source capture.

    Original file bytes are deduplicated separately by ``Asset``. Including
    title and kind here prevents two intentionally different captures from
    being collapsed merely because their body text happens to match, while an
    exact request retry remains idempotent.
    """

    return _fingerprint({
        "content": content.strip(),
        "kind": kind,
        "title": title.strip(),
        "version": 1,
    })


def asset_capture_fingerprint(
    title: str, kind: str, notes: str, *, file_name: str, media_type: str, content_hash: str,
) -> str:
    """Identify an upload by its request fields, independently of its stored reading."""
    return _fingerprint({
        "assetSha256": content_hash,
        "fileName": file_name,
        "mediaType": media_type,
        "title": title.strip(),
        "kind": kind,
        "notes": notes.strip(),
        "version": 1,
    })
