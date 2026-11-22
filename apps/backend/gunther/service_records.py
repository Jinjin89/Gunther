"""Identifiers and timestamps for service records."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

# Home's conversations belong to no library: this stands in for the library id.
# Library ids come from slugs, which never contain "@".
HOME_SCOPE = "@home"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _timestamp(value: datetime) -> str:
    return f"{value.isoformat(timespec='milliseconds')}Z"
