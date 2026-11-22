"""Shared provider edits and fingerprints used by the settings routes."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import HTTPException


def find_provider(
    providers: Iterable[dict[str, Any]], provider_id: str
) -> tuple[list[dict[str, Any]], int]:
    drafts = [dict(provider) for provider in providers]
    for index, provider in enumerate(drafts):
        if provider["id"] == provider_id:
            return drafts, index
    raise HTTPException(404, "There is no such provider")


def unique_provider_name(name: str, providers: list[dict[str, Any]]) -> str:
    names = {provider["name"] for provider in providers}
    if name not in names:
        return name
    number = 2
    while f"{name} {number}" in names:
        number += 1
    return f"{name} {number}"


def edit_provider(current: dict[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
    fields = {"name": "name", "base_url": "baseUrl", "api_key": "apiKey", "models": "models"}
    return {**current, **{saved: changes[key] for key, saved in fields.items() if key in changes}}


def provider_fingerprint(provider: dict[str, Any], key: str | None) -> str:
    """The saved fingerprint format for model and transcription checks."""

    return hashlib.sha256(
        json.dumps(
            {
                "kind": provider["kind"],
                "baseUrl": provider["baseUrl"],
                "key": key,
                "models": [model["id"] for model in provider["models"]],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
