"""Web search, by Tavily (tavily.com): a search API built for language models.

Ask's agent calls it as a tool and Home's search shows its results. Without a
key the web is simply not offered; nothing pretends to search.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from gunther.schemas import WebSearchOut, WebSearchSourceOut

TAVILY_URL = "https://api.tavily.com"
SEARCH_TIMEOUT_SECONDS = 20.0
SNIPPET_LIMIT = 700

Topic = Literal["general", "news"]


class OnlineSearchProvider(Protocol):
    mode: Literal["tavily", "not_configured"]

    @property
    def available(self) -> bool: ...

    def search(
        self,
        query: str,
        *,
        topic: Topic = "general",
        days: int | None = None,
        answer: bool = False,
    ) -> WebSearchOut: ...


@dataclass
class DisabledOnlineSearch:
    mode: Literal["not_configured"] = "not_configured"

    @property
    def available(self) -> bool:
        return False

    def search(
        self,
        query: str,
        *,
        topic: Topic = "general",
        days: int | None = None,
        answer: bool = False,
    ) -> WebSearchOut:
        del topic, days, answer
        return WebSearchOut(
            query=query,
            answer="",
            mode=self.mode,
            message="Web search is off. Add a Tavily API key in Settings to turn it on.",
        )


def explain_failure(status: int) -> str:
    if status in (401, 403):
        return "Tavily did not accept the API key."
    if status in (429, 432, 433):
        return "Tavily says this key is out of searches or going too fast. Try again later."
    return f"Tavily had a problem ({status})."


class TavilySearch:
    mode: Literal["tavily"] = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        depth: str = "basic",
        max_results: int = 6,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.depth = depth
        self.max_results = max_results
        self._client = client

    @property
    def available(self) -> bool:
        return True

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self._client is not None:
            return self._client.post(f"{TAVILY_URL}/search", json=body, headers=headers)
        with httpx.Client(timeout=SEARCH_TIMEOUT_SECONDS) as client:
            return client.post(f"{TAVILY_URL}/search", json=body, headers=headers)

    def search(
        self,
        query: str,
        *,
        topic: Topic = "general",
        days: int | None = None,
        answer: bool = False,
    ) -> WebSearchOut:
        body: dict[str, Any] = {
            "query": query[:400],
            "search_depth": self.depth,
            "max_results": self.max_results,
            "topic": topic,
            "include_answer": "basic" if answer else False,
        }
        if days and topic == "news":
            body["days"] = days
        elif days:
            body["time_range"] = "week" if days <= 7 else "month" if days <= 31 else "year"

        def failed(message: str) -> WebSearchOut:
            return WebSearchOut(query=query, answer="", mode="failed", message=message)

        try:
            response = self._post(body)
        except httpx.TimeoutException:
            return failed("Web search did not answer in time. Your local results are unchanged.")
        except httpx.HTTPError:
            return failed("Web search could not be reached. Your local results are unchanged.")
        if response.status_code >= 400:
            return failed(explain_failure(response.status_code))
        try:
            payload = response.json()
            results = payload.get("results") or []
        except (ValueError, AttributeError):
            return failed("Web search answered in a shape Gunther could not read.")
        sources: list[WebSearchSourceOut] = []
        seen: set[str] = set()
        for item in results:
            url = str(item.get("url") or "").strip()
            if not url or url in seen or not url.startswith(("http://", "https://")):
                continue
            seen.add(url)
            snippet = " ".join(str(item.get("content") or "").split())[:SNIPPET_LIMIT]
            sources.append(
                WebSearchSourceOut(
                    title=str(item.get("title") or url).strip()[:200],
                    url=url,
                    snippet=snippet or None,
                )
            )
        return WebSearchOut(
            query=query,
            answer=str(payload.get("answer") or "").strip() if answer else "",
            sources=sources,
            mode=self.mode,
        )


def create_online_search(
    api_key: str | None, *, depth: str = "basic", max_results: int = 6
) -> OnlineSearchProvider:
    if not api_key:
        return DisabledOnlineSearch()
    return TavilySearch(api_key, depth=depth, max_results=max_results)


async def check_key(api_key: str) -> tuple[bool, str]:
    """Whether Tavily accepts a key, asked through its free usage endpoint."""

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                f"{TAVILY_URL}/usage", headers={"Authorization": f"Bearer {api_key}"}
            )
    except httpx.TimeoutException:
        return False, "Tavily did not answer in time."
    except httpx.HTTPError:
        return False, "Could not reach Tavily. Check the network."
    if response.status_code >= 400:
        return False, explain_failure(response.status_code)
    try:
        usage = (response.json().get("key") or {}).get("usage")
    except (ValueError, AttributeError):
        usage = None
    used = f" {usage} searches used so far." if isinstance(usage, int) else ""
    return True, f"Connected, and the key works.{used}"
