"""Request accounting and context shared by model-backed jobs."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)
_current: ContextVar[dict[str, str | None] | None] = ContextVar("gunther_usage", default=None)
_UNSET = object()


def current() -> dict[str, str | None]:
    return dict(_current.get() or {"job": "other", "detail": None, "ref": None})


@contextmanager
def job(
    job: str | object = _UNSET,
    detail: str | None | object = _UNSET,
    *,
    ref: str | None | object = _UNSET,
) -> Iterator[None]:
    """Override only supplied fields, restoring the parent even when work fails."""

    context = current()
    for key, value in (("job", job), ("detail", detail), ("ref", ref)):
        if value is not _UNSET:
            context[key] = value
    token = _current.set(context)
    try:
        yield
    finally:
        _current.reset(token)


def record(sessions: sessionmaker[Session], entry: dict[str, Any]) -> None:
    """A short, independent transaction; accounting must never fail an answer."""

    from gunther.database import session_scope
    from gunther.models import ModelUsage

    try:
        with session_scope(sessions) as session:
            session.add(ModelUsage(**entry))
    except Exception:
        logger.warning("Could not record model usage", exc_info=True)
