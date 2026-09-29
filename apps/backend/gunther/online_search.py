from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from openai import OpenAI

from gunther.schemas import WebSearchOut, WebSearchSourceOut


class OnlineSearchProvider(Protocol):
    mode: Literal["openai", "not_configured"]

    def search(self, query: str) -> WebSearchOut: ...


@dataclass
class DisabledOnlineSearch:
    mode: Literal["not_configured"] = "not_configured"

    def search(self, query: str) -> WebSearchOut:
        return WebSearchOut(
            query=query,
            answer="",
            mode=self.mode,
            message="Online search is off. Add an OpenAI API key in Settings to turn it on.",
        )


class OpenAIOnlineSearch:
    mode: Literal["openai"] = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    @staticmethod
    def _sources(response: object) -> list[WebSearchSourceOut]:
        payload = response.model_dump() if hasattr(response, "model_dump") else {}
        sources: list[WebSearchSourceOut] = []
        seen: set[str] = set()
        for output in payload.get("output", []):
            for content in output.get("content", []):
                for annotation in content.get("annotations", []):
                    if annotation.get("type") != "url_citation":
                        continue
                    url = annotation.get("url")
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    sources.append(
                        WebSearchSourceOut(
                            title=annotation.get("title") or url,
                            url=url,
                        )
                    )
            action = output.get("action") or {}
            for source in action.get("sources", []):
                url = source.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                sources.append(
                    WebSearchSourceOut(
                        title=source.get("title") or url,
                        url=url,
                        snippet=source.get("snippet"),
                    )
                )
        return sources[:12]

    def search(self, query: str) -> WebSearchOut:
        try:
            response = self.client.responses.create(
                model=self.model,
                tools=[{"type": "web_search"}],
                include=["web_search_call.action.sources"],
                input=(
                    "Answer this search query concisely. Prefer primary and recent sources, "
                    "state uncertainty, and make every factual claim traceable to a citation.\n\n"
                    f"Query: {query}"
                ),
            )
            answer = response.output_text.strip()
            return WebSearchOut(
                query=query,
                answer=answer,
                sources=self._sources(response),
                mode=self.mode,
            )
        except Exception:
            return WebSearchOut(
                query=query,
                answer="",
                mode="failed",
                message="Online search could not be reached. Your local results are unchanged.",
            )


def create_online_search(api_key: str | None, model: str) -> OnlineSearchProvider:
    return OpenAIOnlineSearch(api_key, model) if api_key else DisabledOnlineSearch()
