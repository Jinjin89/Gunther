"""One pool for Studio fan-out, including calls made by another Studio request."""

import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from gunther.studio.requests import WORKERS, request_map, warm_map


def test_parallel_calls_share_the_twelve_workers_and_keep_the_smaller_limit():
    release = threading.Event()
    changed = threading.Condition()
    active = Counter()

    def work(group, i):
        with changed:
            active[group] += 1
            changed.notify_all()
        assert release.wait(10)
        return i

    with ThreadPoolExecutor(2) as callers:
        first = callers.submit(request_map, lambda i: work("figures", i), range(16), 4)
        try:
            with changed:
                assert changed.wait_for(lambda: active["figures"] >= 4, timeout=5)
            second = callers.submit(request_map, lambda i: work("checking", i), range(16), WORKERS)
            with changed:
                assert changed.wait_for(lambda: active.total() >= 12, timeout=5)
                assert active == {"figures": 4, "checking": 8}
        finally:
            release.set()
        assert first.result() == second.result() == list(range(16))


def test_nested_warm_map_runs_inline_in_order():
    def nested(_):
        here = threading.get_ident()
        assert warm_map(lambda i: (threading.get_ident(), i), range(3), 3) == [
            (here, i) for i in range(3)
        ]
        return here

    assert request_map(nested, [None], 1)[0] != threading.get_ident()
