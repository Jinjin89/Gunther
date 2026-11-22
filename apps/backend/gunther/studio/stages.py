"""Studio stage events and the boundary for completed checkpoints."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from typing import Any

from gunther.answer_runs import check_stop
from gunther.outputs import Events

Checkpoint = Callable[[dict[str, Any]], None]


def stage_event(step: str, label: str, state: str, detail: str | None = None) -> dict[str, Any]:
    if state == "running":
        check_stop()
    event = {"type": "stage", "id": step, "label": label, "state": state}
    if detail is not None:
        event["detail"] = detail
    return event


@contextmanager
def checkpoint_stage(
    emit: Events, state: dict[str, Any], checkpoint: Checkpoint | None, *steps: str,
) -> Iterator[Events]:
    """Check Stop on entry, then save completed work before forwarding its events."""

    check_stop()
    previous = dict(state) if checkpoint is not None else {}
    pending: list[dict[str, Any]] = []

    def report(event: dict[str, Any]) -> None:
        if pending or (
            event.get("type") == "stage"
            and event.get("id") in steps
            and event.get("state") == "done"
        ):
            pending.append(event)
        else:
            emit(event)

    yield report
    if checkpoint is not None and state != previous:
        checkpoint(deepcopy(state))
    check_stop()
    for event in pending:
        emit(event)
