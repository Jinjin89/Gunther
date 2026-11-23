"""Topics for a large library: suggested groups of papers, and topic overviews.

Suggestions cluster the papers not yet filed under a topic. With the local
model each paper is its title-and-abstract vector; without it, a keyword
(TF-IDF) vector of the same text. Clusters are spherical k-means, seeded, so
the same library gives the same suggestions. Each is named by terms shared by
its papers and concentrated in the group; the user renames on accept.

The Analysis model writes a topic overview from its papers' own abstracts.
Each citation links to the abstract used for the synthesis.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from gunther import usage, vector_index
from gunther.content import search_tokens
from gunther.database import session_scope
from gunther.llm import AnalysisNeedsModel, ModelError, ModelGateway, ModelInfo
from gunther.model_profiles import Effort
from gunther.models import (
    ContentBlock,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    Source,
    SourcePaper,
    TopicNode,
    TopicSourceLink,
    TopicSynthesis,
    utc_now,
)
from gunther.papers import title_key
from gunther.topics import topic_source_ids

MIN_PAPERS = 6
MAX_SUGGESTIONS = 15
# How many abstracts a written synthesis reads; larger topics use the most central.
MAX_WRITER_PAPERS = 40


@dataclass(slots=True)
class Paper:
    source_id: str
    title: str
    abstract: str
    year: int | None
    authors: str
    revision_id: str
    abstract_block_ids: list[str]


def _terms(paper: Paper) -> Iterator[str]:
    for text in (paper.title, paper.abstract):
        for clause in re.split(r"[^\w\s\-–—]+", text):
            previous = ""
            for word in clause.split():
                tokens = [t for t in search_tokens(word, include_stopwords=True) if not t.isdigit()]
                yield from tokens
                # Only pair adjacent words, never fragments of one unsegmented word.
                current = tokens[0] if len(tokens) == 1 else ""
                if previous and current:
                    yield f"{previous} {current}"
                previous = current


def _document_frequency(documents: Iterable[Counter[str]]) -> Counter[str]:
    return Counter(term for document in documents for term in document)


def _unfiled_papers(session: Session, base_id: str) -> list[Paper]:
    """One paper per work, among live library sources not placed in a section."""

    filed = select(TopicSourceLink.source_id).where(TopicSourceLink.knowledge_base_id == base_id)
    rows = session.execute(
        select(SourcePaper)
        .join(Source, Source.id == SourcePaper.source_id)
        .join(KnowledgeBaseSource, KnowledgeBaseSource.source_id == Source.id)
        .where(
            KnowledgeBaseSource.knowledge_base_id == base_id,
            Source.trashed_at.is_(None),
            SourcePaper.source_id.not_in(filed),
        )
        .order_by(Source.created_at, Source.id)
    ).scalars()
    papers: list[Paper] = []
    works: set[str] = set()
    for row in rows:
        if row.work_id and row.work_id in works:
            continue
        works.add(row.work_id or row.source_id)
        papers.append(_paper(row))
    return papers


def _paper(row: SourcePaper) -> Paper:
    return Paper(
        row.source_id,
        row.title,
        row.abstract,
        row.year,
        row.authors,
        row.revision_id,
        json.loads(row.abstract_block_ids_json or "[]"),
    )


def _keyword_matrix(documents: Sequence[Counter[str]]) -> Any:
    import numpy

    frequency = _document_frequency(documents)
    vocabulary = {
        term: index
        for index, (term, _) in enumerate(
            item for item in frequency.most_common(4000) if item[1] >= 2
        )
    }
    matrix = numpy.zeros((len(documents), max(1, len(vocabulary))), dtype=numpy.float32)
    for row, document in enumerate(documents):
        for term, count in document.items():
            if term in vocabulary:
                idf = math.log(len(documents) / frequency[term])
                matrix[row, vocabulary[term]] = (1 + math.log(count)) * idf
    return matrix


def _normalized(matrix: Any) -> Any:
    import numpy

    norms = numpy.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / numpy.where(norms == 0, 1, norms)


def _kmeans(matrix: Any, k: int, *, restarts: int = 12, iterations: int = 40) -> Any:
    """Spherical k-means with k-means++ starts; deterministic for a given matrix."""

    import numpy

    points = _normalized(matrix)
    best, best_score = None, -math.inf
    for seed in range(restarts):
        rng = numpy.random.default_rng(seed)
        centres = [points[rng.integers(len(points))]]
        for _ in range(1, k):
            distance = 1 - numpy.max(points @ numpy.stack(centres).T, axis=1)
            weights = numpy.clip(distance, 0, None) ** 2
            total = weights.sum()
            pick = rng.choice(len(points), p=weights / total) if total > 0 else rng.integers(
                len(points)
            )
            centres.append(points[pick])
        centre = numpy.stack(centres)
        labels = numpy.zeros(len(points), dtype=int)
        for _ in range(iterations):
            labels = numpy.argmax(points @ centre.T, axis=1)
            moved = centre.copy()
            for index in range(k):
                members = points[labels == index]
                if len(members):
                    moved[index] = members.mean(axis=0)
            moved = _normalized(moved)
            if numpy.allclose(moved, centre):
                break
            centre = moved
        score = float(numpy.sum(numpy.max(points @ centre.T, axis=1)))
        if score > best_score:
            best, best_score = labels, score
    return best


def _names(
    groups: list[list[Paper]], documents: dict[str, Counter[str]], count: int = 3,
) -> list[list[str]]:
    """Prefer terms shared within a group and more common there than elsewhere."""

    library = _document_frequency(documents.values())
    names = []
    for group in groups:
        shared = _document_frequency(documents[paper.source_id] for paper in group)
        # Compare the fractions of documents containing the term using a common denominator.
        scores = {
            term: hits * len(documents) - library[term] * len(group)
            if len(documents) > len(group) else hits
            for term, hits in shared.items() if hits >= 2
        }
        chosen: list[str] = []
        for term in sorted(scores, key=lambda t: (-scores[t], -shared[t], -t.count(" "), t)):
            if scores[term] <= 0:
                break
            if any(
                f" {term} " in f" {other} " or f" {other} " in f" {term} " for other in chosen
            ):
                continue
            chosen.append(term)
            if len(chosen) == count:
                break
        names.append(chosen)
    return names


def suggest(session: Session, base_id: str, model: str | None) -> dict[str, object]:
    """Groups of unfiled papers that could each become a topic."""

    import numpy

    papers = _unfiled_papers(session, base_id)
    if len(papers) < MIN_PAPERS:
        return {
            "suggestions": [],
            "unfiled": len(papers),
            "method": None,
            "reason": f"Topics are suggested once at least {MIN_PAPERS} papers are not in a "
            "topic yet.",
        }
    vectors = (
        vector_index.paper_vectors(session, model=model, source_ids=[p.source_id for p in papers])
        if model
        else {}
    )
    # The same papers give the same groups, in whatever order they arrived.
    papers.sort(key=lambda paper: (title_key(paper.title), paper.source_id))
    documents = {paper.source_id: Counter(_terms(paper)) for paper in papers}
    if model and len(vectors) >= 0.8 * len(papers):
        papers = [paper for paper in papers if paper.source_id in vectors]
        matrix = numpy.stack([vectors[paper.source_id] for paper in papers])
        method = "semantic"
    else:
        matrix = _keyword_matrix(list(documents.values()))
        method = "keywords"
    k = max(2, min(MAX_SUGGESTIONS, round(math.sqrt(len(papers) / 2))))
    labels = _kmeans(matrix, k)
    points = _normalized(matrix)
    groups: list[tuple[list[Paper], Any]] = []
    for index in range(k):
        members = [paper for paper, label in zip(papers, labels, strict=True) if label == index]
        if len(members) >= 2:
            centre = points[labels == index].mean(axis=0)
            order = numpy.argsort(-(points[labels == index] @ centre))
            groups.append(([members[i] for i in order], centre))
    groups.sort(key=lambda item: len(item[0]), reverse=True)
    names = _names([members for members, _ in groups], documents)
    suggestions = []
    for (members, _), terms in zip(groups, names, strict=True):
        title = " · ".join(term[:1].upper() + term[1:] for term in terms) or members[0].title
        years = [paper.year for paper in members if paper.year]
        suggestions.append({
            "key": hashlib.sha256(
                "\n".join(sorted(paper.source_id for paper in members)).encode()
            ).hexdigest()[:16],
            "title": title[:160],
            "keywords": terms,
            "sourceIds": [paper.source_id for paper in members],
            "examples": [paper.title for paper in members[:3]],
            "years": [min(years), max(years)] if years else None,
        })
    return {"suggestions": suggestions, "unfiled": len(papers), "method": method, "reason": None}


# Overviews ---------------------------------------------------------------------


class ModelTopicWriter:
    """The Analysis model writes a topic's overview from its papers' abstracts."""

    def __init__(self, gateway: ModelGateway, model: ModelInfo, effort: Effort) -> None:
        self.gateway = gateway
        self.model = model
        self.effort = effort
        self.method = model.method

    @usage.job("analysis", "topic")
    def write(self, title: str, description: str, papers: list[Paper]) -> str:
        sources = "\n\n".join(
            f"[{index}] {paper.title} ({paper.year or 'n.d.'}). {paper.abstract[:1500]}"
            for index, paper in enumerate(papers, start=1)
        )
        text = self.gateway.complete(
            self.model,
            system=(
                "You write a literature overview for one topic, only from the numbered "
                "abstracts given. Use Markdown with these sections: ## Overview, "
                "## Main lines of work, ## Where papers agree or differ, ## Open questions. "
                "Cite every claim with the paper numbers, like [2] or [3][5]. Do not cite "
                "anything else or add outside knowledge. Write in the language of the "
                "topic title."
            ),
            messages=f"Topic: {title}\n{description}\n\nAbstracts:\n\n{sources}",
            effort=self.effort,
        ).text
        cited = [int(value) for value in re.findall(r"\[(\d+)\]", text)]
        if not text or not cited or any(not 1 <= value <= len(papers) for value in cited):
            raise ModelError("The overview did not cite the supplied abstracts correctly.")
        return text


def create_topic_writer(gateway: ModelGateway | None) -> ModelTopicWriter | None:
    chosen = gateway.for_role("analysis") if gateway else None
    return ModelTopicWriter(gateway, *chosen) if gateway and chosen else None


def topic_papers(session: Session, base_id: str, topic_id: str) -> list[Paper]:
    source_ids = topic_source_ids(session, base_id, topic_id)
    rows = session.scalars(
        select(SourcePaper).where(SourcePaper.source_id.in_(source_ids))
    ).all()
    seen: set[str] = set()
    papers = []
    for row in sorted(rows, key=lambda row: (row.year or 9999, row.title)):
        if row.work_id and row.work_id in seen:
            continue
        seen.add(row.work_id or row.source_id)
        papers.append(_paper(row))
    return papers


def _overview_topic(session: Session, base_id: str, topic_id: str) -> TopicNode:
    library = session.get(KnowledgeBaseRecord, base_id)
    if library is None or library.trashed_at is not None:
        raise LookupError("Knowledge base was not found")
    topic = session.get(TopicNode, topic_id)
    if topic is None or topic.knowledge_base_id != base_id:
        raise LookupError("Topic was not found in this knowledge base")
    return topic


def write_overview(
    sessions: sessionmaker[Session], base_id: str, topic_id: str,
    writer: ModelTopicWriter | None, model: str | None,
) -> TopicSynthesis:
    with sessions() as session:
        topic = _overview_topic(session, base_id, topic_id)
        title, description = topic.title, topic.description
        papers = topic_papers(session, base_id, topic_id)
        if not papers:
            raise ValueError("File papers under this topic before writing its overview")
        if writer is None:
            raise AnalysisNeedsModel()
        read = [paper for paper in papers if paper.abstract.strip()]
        if not read:
            raise ValueError("File papers with abstracts before writing this overview")
        if len(read) > MAX_WRITER_PAPERS:
            vectors = (
                vector_index.paper_vectors(
                    session, model=model, source_ids=[paper.source_id for paper in read]
                )
                if model
                else {}
            )
            if vectors:
                import numpy

                known = [paper for paper in read if paper.source_id in vectors]
                matrix = _normalized(numpy.stack([vectors[p.source_id] for p in known]))
                centre = matrix.mean(axis=0)
                central = numpy.argsort(-(matrix @ centre))[:MAX_WRITER_PAPERS]
                read = sorted((known[i] for i in central), key=lambda p: (p.year or 9999, p.title))
            else:
                read = read[-MAX_WRITER_PAPERS:]

        citations = []
        for number, paper in enumerate(read, start=1):
            block = (
                session.get(ContentBlock, paper.abstract_block_ids[0])
                if paper.abstract_block_ids
                else None
            )
            source = session.get(Source, paper.source_id)
            citations.append({
                "number": number,
                "sourceId": paper.source_id,
                "sourceTitle": source.title if source else paper.title,
                "blockId": block.id if block else None,
                "revisionId": paper.revision_id,
                "quote": paper.abstract[:1500],
            })

    years = [paper.year for paper in papers if paper.year]
    span = f", {min(years)}–{max(years)}" if years and min(years) != max(years) else (
        f", {years[0]}" if years else ""
    )
    lines = [f"# {title}", ""]
    if description.strip():
        lines += [description.strip(), ""]
    shared = _names([papers], {paper.source_id: Counter(_terms(paper)) for paper in papers})[0]
    lines += [
        f"{len(papers)} paper{'s' if len(papers) != 1 else ''}{span}."
        + (f" Shared terms: {', '.join(shared)}." if shared else ""),
        "",
    ]
    with usage.job("analysis", "topic", ref=topic_id):
        written = writer.write(title, description, read)
    if len(read) < len(papers):
        lines += [f"_Written from {len(read)} abstracts across {len(papers)} filed papers._", ""]
    lines += [written, "", "## Papers", ""]
    lines += [
        f"{number}. {paper.title} ({paper.year or 'n.d.'})"
        + (f" — {paper.authors}" if paper.authors else "")
        for number, paper in enumerate(read, start=1)
    ]
    markdown = "\n".join(lines).rstrip() + "\n"

    with session_scope(sessions) as session:
        _overview_topic(session, base_id, topic_id)
        row = session.get(TopicSynthesis, topic_id)
        if row is None:
            row = TopicSynthesis(topic_id=topic_id, markdown=markdown, method=writer.method)
            session.add(row)
        row.markdown = markdown
        row.citations_json = json.dumps(citations, ensure_ascii=False)
        row.method = writer.method
        row.source_count = len(papers)
        row.created_at = utc_now()
    return row


def overview_out(row: TopicSynthesis) -> dict[str, object]:
    return {
        "topicId": row.topic_id,
        "markdown": row.markdown,
        "citations": json.loads(row.citations_json or "[]"),
        "method": row.method,
        "sourceCount": row.source_count,
        "createdAt": row.created_at.isoformat() + "Z",
    }
