"""Shared material prompts and model workers that warm the prompt cache."""

from __future__ import annotations

import contextvars
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from itertools import islice
from threading import Lock, local

from gunther.answer_runs import check_stop
from gunther.studio import prompts as P

WORKERS = 12
_worker = local()


def _mark_worker() -> None:
    _worker.in_pool = True


_pool = ThreadPoolExecutor(WORKERS, thread_name_prefix="studio", initializer=_mark_worker)


def _in_context(function):
    """Run ``function`` in a copy of this thread's context (traces, budgets) on a worker."""

    context = contextvars.copy_context()
    return lambda *args: context.copy().run(function, *args)


def material_request(material: str, task: str) -> tuple[str, str]:
    """One opening for every step reading the same build's material."""

    return P.STUDIO_BASE, "The material:\n\n" + material + P.TASK_BOUNDARY + task


def request_map(function, items, workers: int):
    """Return results in input order, with a per-call limit on pending Studio requests."""

    def call(item):
        check_stop()
        return function(item)

    if getattr(_worker, "in_pool", False):
        # An outer request can hold the last worker while waiting for these results.
        return [call(item) for item in items]

    call_in_context = _in_context(call)
    remaining = iter(items)
    results = []
    pending = {}

    def submit(item):
        pending[_pool.submit(call_in_context, item)] = len(results)
        results.append(None)

    try:
        for item in islice(remaining, workers):
            submit(item)
        while pending:
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                results[pending.pop(future)] = future.result()
            for item in islice(remaining, len(completed)):
                submit(item)
    finally:
        wait(future for future in pending if not future.cancel())
    return results


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

    first = request_map(counted, items[:1], 1)
    return first + request_map(counted, items[1:], workers)
