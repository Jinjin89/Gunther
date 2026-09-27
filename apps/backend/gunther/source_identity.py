from __future__ import annotations

import hashlib
import json


def source_fingerprint(title: str, kind: str, content: str) -> str:
    """Return the stable identity of one user-visible Source capture.

    Original file bytes are deduplicated separately by ``Asset``. Including
    title and kind here prevents two intentionally different captures from
    being collapsed merely because their body text happens to match, while an
    exact request retry remains idempotent.
    """

    canonical = json.dumps(
        {
            "content": content.strip(),
            "kind": kind,
            "title": title.strip(),
            "version": 1,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
