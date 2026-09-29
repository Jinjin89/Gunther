"""Topics for a large library: suggested groups of papers, and topic overviews.

Suggestions cluster the papers not yet filed under a topic. With the local
model each paper is its title-and-abstract vector; without it, a keyword
(TF-IDF) vector of the same text. Clusters are spherical k-means, seeded, so
the same library gives the same suggestions. Each is named by the terms that
set it apart from the others (class-based TF-IDF); the user renames on accept.

An overview is one page per topic: every paper with its year, authors and the
opening of its abstract, cited to the abstract's own blocks. When DeepSeek is
configured, it writes a synthesis from those abstracts instead, with the same
citations, and any answer citing a paper it was not given is discarded.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from gunther import vector_index
from gunther.content import search_tokens
from gunther.models import (
    ContentBlock,
    KnowledgeBaseSource,
    Source,
    SourcePaper,
    TopicNode,
    TopicSourceLink,
    TopicSynthesis,
    utc_now,
)
from gunther.papers import title_key
from gunther.topics import subtree_ids, topic_source_ids

MIN_PAPERS = 6
MAX_SUGGESTIONS = 15
# How many abstracts a written synthesis reads; larger topics use the most central.
MAX_WRITER_PAPERS = 40
GENERIC = frozenset(
    {
        "paper", "papers", "study", "studies", "result", "results", "method", "methods",
        "approach", "approaches", "propose", "proposed", "based", "using", "use", "used",
        "show", "shows", "shown", "present", "presents", "new", "novel", "also", "however",
        "two", "one", "three", "first", "well", "work", "different", "may", "many", "high",
        "performance", "provide", "provides", "significant", "significantly", "including",
        "across", "between", "within", "while", "further", "here", "both", "than", "such",
        "all", "has", "have", "been", "not", "more", "most", "which", "into", "over", "only",
        "our", "these", "each", "other", "some", "large", "set", "sets", "abstract",
        "研究", "方法", "结果", "本文", "我们", "提出", "基于",
    }
)


@dataclass(slots=True)
class Paper:
    source_id: str
    title: str
    abstract: str
    year: int | None
    authors: str
    revision_id: str
    abstract_block_ids: list[str]


def _terms(text: str) -> list[str]:
    tokens = [token for token in search_tokens(text) if token not in GENERIC]
    words = [token for token in tokens if token.isascii() and not token.isdigit()]
    return words + [f"{a} {b}" for a, b in zip(words, words[1:], strict=False)] + [
        token for token in tokens if not token.isascii()
    ]


def _unfiled_papers(session: Session, base_id: str) -> list[Paper]:
    """One paper per work, among live library sources not filed under a topic."""

    topics = select(TopicNode.id).where(TopicNode.knowledge_base_id == base_id)
    filed = select(TopicSourceLink.source_id).where(TopicSourceLink.topic_id.in_(topics))
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


def _keyword_matrix(papers: list[Paper]) -> Any:
    import numpy

    documents = [Counter(_terms(f"{paper.title} {paper.abstract}")) for paper in papers]
    frequency = Counter(term for document in documents for term in document)
    vocabulary = {
        term: index
        for index, (term, _) in enumerate(
            item for item in frequency.most_common(4000) if item[1] >= 2
        )
    }
    matrix = numpy.zeros((len(papers), max(1, len(vocabulary))), dtype=numpy.float32)
    for row, document in enumerate(documents):
        for term, count in document.items():
            if term in vocabulary:
                idf = math.log(len(papers) / frequency[term]) + 1
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


def _names(groups: list[list[Paper]], count: int = 3) -> list[list[str]]:
    """The terms that set each group apart from the rest (class-based TF-IDF)."""

    bags = [
        Counter(term for paper in group for term in _terms(f"{paper.title} {paper.abstract}"))
        for group in groups
    ]
    totals: Counter[str] = sum(bags, Counter())
    average = sum(sum(bag.values()) for bag in bags) / max(1, len(bags))
    names = []
    for bag in bags:
        size = sum(bag.values()) or 1
        # Frequent here, rare elsewhere: "model" is common to every group of ML
        # papers, "bert" belongs to one. Phrases read better as names.
        scored = sorted(
            bag,
            key=lambda t: (bag[t] / size)
            * math.log(1 + average / totals[t])
            * (bag[t] / totals[t])
            * (1.3 if " " in t else 1.0),
            reverse=True,
        )
        chosen: list[str] = []
        for term in scored:
            if bag[term] < 2 or any(term in other or other in term for other in chosen):
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
    if model and len(vectors) >= 0.8 * len(papers):
        papers = [paper for paper in papers if paper.source_id in vectors]
        matrix = numpy.stack([vectors[paper.source_id] for paper in papers])
        method = "semantic"
    else:
        matrix = _keyword_matrix(papers)
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
    names = _names([members for members, _ in groups])
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


class TopicWriter(Protocol):
    method: str

    def write(self, title: str, description: str, papers: list[Paper]) -> str | None: ...


class LocalTopicWriter:
    """No model: the overview is the papers' own abstracts, ordered by year."""

    method = "local"

    def write(self, title: str, description: str, papers: list[Paper]) -> str | None:
        return None


class DeepSeekTopicWriter:
    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.method = f"deepseek:{model}"
        self.model = model

    def write(self, title: str, description: str, papers: list[Paper]) -> str | None:
        sources = "\n\n".join(
            f"[{index}] {paper.title} ({paper.year or 'n.d.'}). {paper.abstract[:1500]}"
            for index, paper in enumerate(papers, start=1)
        )
        try:
            response = self.client.responses.create(
                model=self.model,
                store=False,
                instructions=(
                    "You write a literature overview for one topic, only from the numbered "
                    "abstracts given. Use Markdown with these sections: ## Overview, "
                    "## Main lines of work, ## Where papers agree or differ, ## Open questions. "
                    "Cite every claim with the paper numbers, like [2] or [3][5]. Do not cite "
                    "anything else or add outside knowledge. Write in the language of the "
                    "topic title."
                ),
                input=f"Topic: {title}\n{description}\n\nAbstracts:\n\n{sources}",
            )
            text = response.output_text.strip()
        except Exception:
            return None
        cited = [int(value) for value in re.findall(r"\[(\d+)\]", text)]
        if not text or not cited or any(not 1 <= value <= len(papers) for value in cited):
            return None
        return text


def create_topic_writer(api_key: str | None, model: str, base_url: str) -> TopicWriter:
    return DeepSeekTopicWriter(api_key, model, base_url) if api_key else LocalTopicWriter()


def _opening(text: str, sentences: int = 2) -> str:
    parts = re.findall(r".+?(?:[.!?。！？](?=\s|$)|$)", text.strip())
    return " ".join(part.strip() for part in parts[:sentences] if part.strip())


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


def write_overview(
    session: Session, base_id: str, topic_id: str, writer: TopicWriter, model: str | None
) -> TopicSynthesis:
    topic = session.get(TopicNode, topic_id)
    subtree_ids(session, base_id, topic_id)  # raises LookupError outside this library
    papers = topic_papers(session, base_id, topic_id)
    if not papers:
        raise ValueError("File papers under this topic before writing its overview")
    read = papers
    if len(papers) > MAX_WRITER_PAPERS:
        vectors = (
            vector_index.paper_vectors(
                session, model=model, source_ids=[paper.source_id for paper in papers]
            )
            if model
            else {}
        )
        if vectors:
            import numpy

            known = [paper for paper in papers if paper.source_id in vectors]
            matrix = _normalized(numpy.stack([vectors[p.source_id] for p in known]))
            centre = matrix.mean(axis=0)
            central = numpy.argsort(-(matrix @ centre))[:MAX_WRITER_PAPERS]
            read = sorted((known[i] for i in central), key=lambda p: (p.year or 9999, p.title))
        else:
            read = papers[-MAX_WRITER_PAPERS:]

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
            "quote": _opening(paper.abstract, 3),
        })

    years = [paper.year for paper in papers if paper.year]
    span = f", {min(years)}–{max(years)}" if years and min(years) != max(years) else (
        f", {years[0]}" if years else ""
    )
    lines = [f"# {topic.title}", ""]
    if topic.description.strip():
        lines += [topic.description.strip(), ""]
    shared = _names([papers])[0] if len(papers) >= 2 else []
    lines += [
        f"{len(papers)} paper{'s' if len(papers) != 1 else ''}{span}."
        + (f" Shared terms: {', '.join(shared)}." if shared else ""),
        "",
    ]
    written = writer.write(topic.title, topic.description, read)
    method = writer.method if written else "local"
    if written:
        if len(read) < len(papers):
            lines += [f"_Written from the {len(read)} most central of {len(papers)} papers._", ""]
        lines += [written, "", "## Papers", ""]
        lines += [
            f"{number}. {paper.title} ({paper.year or 'n.d.'})"
            + (f" — {paper.authors}" if paper.authors else "")
            for number, paper in enumerate(read, start=1)
        ]
    else:
        lines += ["## Papers", ""]
        for number, paper in enumerate(read, start=1):
            lines += [f"### {paper.title} ({paper.year or 'n.d.'})", ""]
            if paper.authors:
                lines += [f"_{paper.authors}_", ""]
            if paper.abstract:
                lines += [f"{_opening(paper.abstract)} [{number}]", ""]
        if len(read) < len(papers):
            lines += [f"_And {len(papers) - len(read)} more papers._"]
    markdown = "\n".join(lines).rstrip() + "\n"

    row = session.get(TopicSynthesis, topic_id)
    if row is None:
        row = TopicSynthesis(topic_id=topic_id, markdown=markdown, method=method)
        session.add(row)
    row.markdown = markdown
    row.citations_json = json.dumps(citations, ensure_ascii=False)
    row.method = method
    row.source_count = len(papers)
    row.created_at = utc_now()
    session.flush()
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
