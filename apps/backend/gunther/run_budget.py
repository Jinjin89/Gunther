"""One accounting path for an Ask run, including final checks and JSON repairs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gunther.llm import REQUEST_ADMISSION, ModelError


@dataclass
class RunBudget:
    searches_limit: int
    reads_limit: int
    gathering_limit: int
    total_limit: int
    searches: int = 0
    reads: int = 0
    model_calls: int = 0
    gathering_calls: int = 0
    phase: str = "gather"
    seen: set[tuple[str, str]] = field(default_factory=set)
    exhausted: list[str] = field(default_factory=list)

    def admit_search(self, tool: str, query: str) -> bool:
        marker = (tool, " ".join(query.casefold().split()))
        if marker in self.seen:
            return False
        if self.searches >= self.searches_limit:
            if "searches" not in self.exhausted:
                self.exhausted.append("searches")
            return False
        self.seen.add(marker)
        self.searches += 1
        return True

    def admit_model(self) -> None:
        if self.model_calls >= self.total_limit or (
            self.phase == "gather" and self.gathering_calls >= self.gathering_limit
        ):
            if "modelCalls" not in self.exhausted:
                self.exhausted.append("modelCalls")
            raise ModelError("The run's model-call budget is exhausted.")
        self.model_calls += 1
        if self.phase == "gather":
            self.gathering_calls += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "used": {
                "searches": self.searches,
                "reads": self.reads,
                "modelCalls": self.model_calls,
                "gatheringCalls": self.gathering_calls,
            },
            "limits": {
                "searches": self.searches_limit,
                "reads": self.reads_limit,
                "modelCalls": self.total_limit,
                "gatheringCalls": self.gathering_limit,
            },
            "exhausted": self.exhausted.copy(),
        }


class BudgetedGateway:
    """Admits stand-in gateways too; the real gateway also meters its repair request."""

    def __init__(self, gateway: Any, budget: RunBudget):
        self.gateway, self.budget = gateway, budget

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        parent = REQUEST_ADMISSION.get()
        self.budget.admit_model()
        if parent is not None:
            parent()
        first = True

        def request() -> None:
            nonlocal first
            if first:
                first = False  # already admitted above
            else:
                self.budget.admit_model()
                if parent is not None:
                    parent()

        token = REQUEST_ADMISSION.set(request)
        try:
            return getattr(self.gateway, method)(*args, **kwargs)
        finally:
            REQUEST_ADMISSION.reset(token)

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("complete", *args, **kwargs)

    def complete_json(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("complete_json", *args, **kwargs)

    def for_role(self, role: str) -> Any:
        finder = getattr(self.gateway, "for_role", None)
        return finder(role) if finder else None
