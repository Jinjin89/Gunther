"""Shared material prompts and model workers that warm the prompt cache."""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from gunther.studio import prompts as P

WORKERS = 12


def _in_context(function):
    """Run ``function`` in a copy of this thread's context (traces, budgets) on a worker."""

    context = contextvars.copy_context()
    return lambda *args: context.copy().run(function, *args)


def material_request(material: str, task: str) -> tuple[str, str]:
    """One opening for every step reading the same build's material."""

    return P.STUDIO_BASE, "The material:\n\n" + material + P.TASK_BOUNDARY + task


def warm_map(function, items, workers: int, progress=None):
    """Finish the first item before submitting the rest, preserving context and order.
    ``progress(done, total)`` hears each finished item, one at a time."""

    items = list(items)
    if not items:
        return []
    lock, finished = Lock(), [0]

    def counted(item):
        out = function(item)
        if progress is not None:
            with lock:
                finished[0] += 1
                progress(finished[0], len(items))
        return out

    first = counted(items[0])
    if len(items) == 1:
        return [first]
    with ThreadPoolExecutor(workers) as pool:
        return [first, *pool.map(_in_context(counted), items[1:])]
