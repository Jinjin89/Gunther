"""Scope library evidence and provide the retrieval tools used by Ask and Outputs."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from gunther.evidence import Evidence, ReadResult, Tool, ToolFailure
from gunther.evidence_reader import nearby_figures, read_passage
from gunther.models import (
    Assertion,
    ContentBlock,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    Source,
    SourceIndexHead,
    SourcePaper,
    SourceRevision,
)
from gunther.schemas import ConversationCitationOut
from gunther.service_records import _id

# Libraries at least this large also get paper-level answers (abstracts).
PAPER_OVERVIEW_MIN_SOURCES = 12


PAPER_OVERVIEWS = 4


# How much of a web page Ask reads when it reads further.
READ_PAGE_CHARS = 8000


@dataclass(frozen=True)
class _LibraryScope:
    """What one search of a library may read: sources, their claims, a topic's passages."""

    source_ids: list[str]
    assertions: list[Assertion]
    # Set when the question is about one topic: only these passages may be quoted.
    block_scope: list[str] | None
    topic_evidence: dict


def _around(text: str, snippet: str, limit: int = READ_PAGE_CHARS) -> str:
    """At most ``limit`` characters of a page, centred on where the search result's
    snippet is when it can be found, else from the start."""

    if len(text) <= limit:
        return text
    at = text.find(snippet[:60]) if snippet[:60].strip() else -1
    start = 0 if at < 0 else max(0, min(at - limit // 2, len(text) - limit))
    return text[start : start + limit]


class EvidenceService:
    @staticmethod
    def _query_terms(question: str) -> set[str]:
        stopwords = {
            "after",
            "and",
            "are",
            "about",
            "before",
            "can",
            "check",
            "checking",
            "could",
            "does",
            "evidence",
            "for",
            "from",
            "have",
            "how",
            "into",
            "most",
            "not",
            "should",
            "support",
            "supported",
            "supporting",
            "supports",
            "that",
            "the",
            "their",
            "there",
            "these",
            "them",
            "this",
            "what",
            "when",
            "where",
            "which",
            "with",
            "would",
            "why",
        }
        terms: set[str] = set()
        for token in re.findall(r"[\w]+(?:[-–—][\w]+)*", question.casefold()):
            # Keep a hyphenated concept together: “T-cell” should match “T cell”,
            # while “cell-type” must not degrade into the overly broad “cell”.
            normalized = re.sub(r"[-–—]+", " ", token).strip()
            if " " in normalized or (len(normalized) > 2 and normalized not in stopwords):
                terms.add(normalized)
        return terms

    def _rank_assertions(
        self, assertions: list[Assertion], question: str, limit: int = 5
    ) -> list[Assertion]:
        terms = self._query_terms(question)

        def assertion_text(assertion: Assertion) -> str:
            raw = " ".join(
                (
                    assertion.subject.label,
                    assertion.predicate,
                    assertion.object.label,
                    assertion.source.title,
                    *(link.fragment.content for link in assertion.evidence_links),
                )
            ).casefold()
            return re.sub(r"[^\w]+", " ", raw).strip()

        def score(assertion: Assertion) -> float:
            haystack = assertion_text(assertion)
            matches = sum(term in haystack for term in terms)
            return (
                matches * 3 + (1.2 if assertion.status == "verified" else 0) + assertion.confidence
            )

        ranked = sorted(assertions, key=score, reverse=True)
        matched = [item for item in ranked if any(term in assertion_text(item) for term in terms)]
        return matched[:limit]

    def _web_tool(self, wanted: bool) -> tuple[Tool | None, str | None]:
        """The web as a tool for one question, or why it is not offered."""

        if not self.web_search.available:
            return None, "web search is not set up (Settings → Web search)"
        if not wanted:
            return None, "the Web toggle is off for this message"
        provider = self.web_search

        def search_web(query: str) -> list[Evidence]:
            result = provider.search(query)
            if result.mode == "failed":
                raise ToolFailure(result.message or "Web search failed.")
            found = []
            for source in result.sources:
                host = urlsplit(source.url).netloc.removeprefix("www.")
                text = source.snippet or source.title
                citation = ConversationCitationOut(
                    id=_id("cit"),
                    kind="web",
                    url=source.url,
                    source_id="",
                    source_title=source.title,
                    quote=text,
                    locator=host,
                    status="provisional",
                    confidence=0.0,
                )
                found.append(
                    Evidence(
                        kind="web",
                        title=source.title,
                        text=text,
                        locator=host,
                        url=source.url,
                        payload=(citation, None),
                    )
                )
            return found

        return Tool(
            "search_web",
            "the public web, for facts outside the library",
            search_web,
            "the web",
        ), None

    def _home_source_ids(
        self,
        session: Session,
        selected_source_ids: list[str],
        library_ids: list[str] | None,
    ) -> list[str]:
        """What a Home question may read: every live library's sources and unfiled
        captures, or only the libraries the person pointed at with ``@``."""

        live = select(KnowledgeBaseRecord.id).where(KnowledgeBaseRecord.trashed_at.is_(None))
        alive = select(Source.id).where(Source.trashed_at.is_(None))
        if library_ids:
            query = select(KnowledgeBaseSource.source_id).where(
                KnowledgeBaseSource.knowledge_base_id.in_(library_ids),
                KnowledgeBaseSource.knowledge_base_id.in_(live),
                KnowledgeBaseSource.source_id.in_(alive),
            )
        else:
            filed = select(KnowledgeBaseSource.source_id).where(
                KnowledgeBaseSource.knowledge_base_id.in_(live)
            )
            anywhere = select(KnowledgeBaseSource.source_id)
            query = select(Source.id).where(
                Source.trashed_at.is_(None),
                or_(Source.id.in_(filed), Source.id.not_in(anywhere)),
            )
        found = list(dict.fromkeys(session.scalars(query)))
        if selected_source_ids:
            chosen = set(selected_source_ids)
            return [item for item in found if item in chosen]
        return found

    def _library_scope(
        self, session: Session, source_ids: list[str], block_scope: list[str] | None = None
    ) -> _LibraryScope:
        """The claims and passages a search over these sources may use.

        ``block_scope`` limits it to the passages of one topic (see topics).
        """

        assertions = list(
            session.scalars(
                self._assertion_query()
                .order_by(Assertion.status.asc(), Assertion.confidence.desc())
                .where(Assertion.source_id.in_(source_ids))
            ).all()
        )
        topic_evidence = {}
        if block_scope is not None:
            topic_evidence = {
                (revision.source_id, block.content): block
                for block, revision in session.execute(
                    select(ContentBlock, SourceRevision)
                    .join(SourceRevision, SourceRevision.id == ContentBlock.revision_id)
                    .where(ContentBlock.id.in_(block_scope))
                    .order_by(ContentBlock.id)
                )
            }
        return _LibraryScope(source_ids, assertions, block_scope, topic_evidence)

    def _library_tool(self, scope: _LibraryScope, about: str) -> Tool:
        """Searching the library as a tool. Whoever uses it numbers what it finds."""

        def search_library(query: str) -> list[Evidence]:
            """The library's passages and claims for one search, numbered by the agent."""

            with self.sessions() as session:
                found: list[Evidence] = []
                ranked_assertions = self._rank_assertions(scope.assertions, query)
                citations: list[ConversationCitationOut] = []
                for assertion in ranked_assertions:
                    evidence = next(
                        (
                            item
                            for item in assertion.evidence_links
                            if item.fragment.source_id == assertion.source_id
                            and (
                                scope.block_scope is None
                                or (assertion.source_id, item.fragment.content)
                                in scope.topic_evidence
                            )
                        ),
                        None,
                    )
                    if evidence is None:
                        continue
                    if scope.block_scope is not None:
                        evidence_block = scope.topic_evidence[
                            (assertion.source_id, evidence.fragment.content)
                        ]
                    else:
                        evidence_block = session.scalar(
                            select(ContentBlock)
                            .join(
                                SourceIndexHead,
                                SourceIndexHead.revision_id == ContentBlock.revision_id,
                            )
                            .where(
                                SourceIndexHead.source_id == assertion.source.id,
                                ContentBlock.content == evidence.fragment.content,
                            )
                            .limit(1)
                        )
                    citation = ConversationCitationOut(
                        id=_id("cit"),
                        source_id=assertion.source.id,
                        source_title=assertion.source.title,
                        assertion_id=assertion.id,
                        quote=evidence.fragment.content,
                        locator=evidence.fragment.locator,
                        status=assertion.status,
                        confidence=assertion.confidence,
                        source_revision_id=evidence_block.revision_id if evidence_block else None,
                        block_id=evidence_block.id if evidence_block else None,
                        anchor=json.loads(evidence_block.anchor_json) if evidence_block else {},
                    )
                    citations.append(citation)

                # Original passages participate even when an extracted claim matched.
                # Competing evidence must not disappear behind an assertion-only fallback.
                seen_quotes = {(c.source_id, c.quote) for c in citations}
                for hit in self.index.retrieve(
                    session,
                    scope.source_ids,
                    query,
                    block_ids=scope.block_scope,
                ):
                    if (hit.source.id, hit.block.content) in seen_quotes:
                        continue
                    seen_quotes.add((hit.source.id, hit.block.content))
                    citations.append(
                        ConversationCitationOut(
                            id=_id("cit"),
                            source_id=hit.source.id,
                            source_title=hit.source.title,
                            quote=hit.block.content,
                            locator=hit.block.locator,
                            status="provisional",
                            confidence=0.0,
                            source_revision_id=hit.block.revision_id,
                            block_id=hit.block.id,
                            anchor=json.loads(hit.block.anchor_json),
                        )
                    )

                # In a large library a broad question needs the papers, not only the few
                # passages that match best: add the abstracts of the closest papers.
                if (
                    scope.block_scope is None
                    and len(scope.source_ids) >= PAPER_OVERVIEW_MIN_SOURCES
                ):
                    for paper, block in self.index.nearest_papers(
                        session,
                        scope.source_ids,
                        query,
                        limit=PAPER_OVERVIEWS,
                        exclude={c.source_id for c in citations},
                    ):
                        source = session.get(Source, paper.source_id)
                        citations.append(
                            ConversationCitationOut(
                                id=_id("cit"),
                                source_id=source.id,
                                source_title=source.title,
                                quote=paper.abstract[:900],
                                locator="Abstract",
                                status="provisional",
                                confidence=0.0,
                                source_revision_id=paper.revision_id,
                                block_id=block.id if block else None,
                                anchor=json.loads(block.anchor_json) if block else {},
                            )
                        )

                for citation in citations:
                    paper = session.get(SourcePaper, citation.source_id)
                    if paper and (paper.doi or paper.arxiv_id):
                        citation.work_identifier = (
                            f"doi:{paper.doi.casefold()}"
                            if paper.doi else f"arxiv:{paper.arxiv_id}"
                        )
                    citation.figures = nearby_figures(session, citation)
                    found.append(
                        Evidence(
                            kind="library",
                            title=citation.source_title,
                            text=citation.quote,
                            locator=citation.locator,
                            status=citation.status,
                            confidence=citation.confidence,
                            payload=(citation, None),
                        )
                    )
                return found

        return Tool(
            "search_library",
            f"the user's library ({about}), {len(scope.source_ids)} sources",
            search_library,
            "your library",
            grade=True,
        )

    def _output_search_tool(
        self, session: Session, source_ids: list[str], about: str
    ) -> Tool:
        tool = self._library_tool(self._library_scope(session, source_ids), about)

        def search(query: str) -> list[Evidence]:
            found = []
            for item in tool.run(query):
                citation, claim = item.payload
                found.append(
                    replace(
                        item,
                        payload=(citation.model_copy(update={"source_origin": "selected"}), claim),
                    )
                )
            return found

        return replace(tool, run=search)

    def _ask_reader(
        self, web_on: bool, scope: _LibraryScope | None = None,
    ) -> Callable[[Evidence], str | ReadResult]:
        """Reading more of a source in Ask's pool (read_source): the section around a library
        passage, or the whole web page. Only a source already in the pool is read, and a web
        page only with the Web switch on, through the same rules as capturing a page; the
        address is the one the search result gave, never one the model wrote."""

        def read(item: Evidence) -> str | ReadResult:
            if item.kind == "library":
                with self.sessions() as session:
                    _, snapshot = read_passage(
                        session, item, window="section",
                        source_ids=scope.source_ids if scope else None,
                        block_ids=scope.block_scope if scope else None,
                    )
                return snapshot
            if item.kind == "web" and item.url:
                if not web_on:
                    raise ToolFailure("The web is off for this message")
                if self.page_reader is None:
                    raise ToolFailure("This source cannot be read further")
                from gunther.web_capture import WebCaptureError

                try:
                    text, _ = self.page_reader(item.url)
                except WebCaptureError as error:
                    raise ToolFailure(str(error)) from error
                if not text.strip():
                    raise ToolFailure("The page has no readable text")
                return _around(text, item.text)
            raise ToolFailure("This source cannot be read further")

        return read

    def _source_reader(
        self, source_ids: Collection[str] | None = None, *,
        pinned_keys: Collection[tuple[str, str, str]] = (),
    ) -> Callable[[Evidence], Evidence | None]:
        """Offer the neighboring blocks as a new Artifacts passage."""

        def read(item: Evidence) -> Evidence | None:
            with self.sessions() as session:
                citation, snapshot = read_passage(
                    session, item, window="adjacent", source_ids=source_ids, pinned_keys=pinned_keys
                )
            text = snapshot.text
            if text.strip() == citation.quote.strip():
                return None
            locator = f"{citation.locator} (with context)" if citation.locator else "with context"
            fresh = citation.model_copy(
                update={
                    "id": _id("cit"),
                    "quote": text,
                    "locator": locator,
                    "ref": None,
                    "support_text": text,
                    "support_spans": snapshot.spans,
                }
            )
            return Evidence(
                kind="library",
                title=item.title,
                text=text,
                locator=locator,
                status=item.status,
                confidence=item.confidence,
                payload=(fresh, None),
            )

        return read
