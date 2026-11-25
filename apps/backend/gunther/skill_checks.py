"""Shared figure metadata and validation used by saved slide layouts."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gunther.citations import citation_numbers, has_markers, strip_citations
from gunther.markup import markdown_lines

CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")
WORD = re.compile(r"[A-Za-z0-9][\w'’.%-]*")
# About 1.7 units a Latin word, so that 14 English words come to about 24 Chinese characters.
WORD_UNITS = 1.7
CHARTS = ("bar_chart", "line_chart")


def figures_of(item: Any) -> list[dict[str, Any]]:
    """The figures kept with a passage (its citation's ``figures``), each with an ``assetId``."""

    payload = getattr(item, "payload", None)
    citation = payload[0] if isinstance(payload, tuple) and payload else None
    return [figure for figure in getattr(citation, "figures", []) if figure.get("assetId")]


def units(text: str) -> float:
    """How long a piece of text reads: a CJK character is 1, a Latin word 1.7."""

    plain = strip_citations(text).replace("**", "").replace("__", "")
    return len(CJK.findall(plain)) + WORD_UNITS * len(WORD.findall(CJK.sub(" ", plain)))


@dataclass(frozen=True)
class Page:
    """A section of a report or a slide of a deck, as a check sees it."""

    index: int
    heading: str
    layout: str = ""
    # The whole section, heading and speaker notes included. A deck's layout marker is not
    # part of it: it is the line that says how the slide is drawn, and is given as ``layout``.
    text: str = ""
    link: str = ""

    @property
    def body(self) -> list[str]:
        """The lines under the heading, up to the speaker notes."""

        lines = [line for line, protected in markdown_lines(self.text)[1:] if not protected]
        cut = next((at for at, line in enumerate(lines) if line.startswith("Note:")), len(lines))
        return [line for line in lines[:cut] if line.strip()]


@dataclass(frozen=True)
class Context:
    kind: str  # "report" | "slides"
    use: str | None = None  # a deck's: "talk" | "read"
    limits: Mapping[str, int] | None = None
    evidence: Mapping[int, Any] | None = None


@dataclass(frozen=True)
class Finding:
    check: str
    index: int | None  # the page, counted from 0; None for the whole output
    message: str


def _table(page: Page) -> tuple[list[str], list[list[str]]] | None:
    rows = [line.strip() for line in page.body if line.strip().startswith("|")]
    if len(rows) < 2:
        return None
    cells = [[cell.strip() for cell in row.strip("|").split("|")] for row in rows]
    return cells[0], [row for row in cells[2:] if any(row)]


def diagram_edge_rows(text: str) -> list[list[str]]:
    table = _table(Page(0, "", "diagram", text))
    return table[1] if table else []


def _diagram_width(text: str) -> float:
    plain = re.sub(
        r"\\(.)", r"\1", strip_citations(text).replace("**", "").replace("__", "").replace("`", "")
    )
    return sum(
        1.0 if CJK.fullmatch(char) else 0.28 if char.isspace() else 0.58 if char.isalnum() else 0.4
        for char in plain
    )


def diagram_problem(text: str) -> str | None:
    """Validate a small evidence-labeled DAG described as a three-column Markdown table."""
    page = Page(0, "", "diagram", text)
    table = _table(page)
    if table is None:
        return "A diagram needs one Markdown table with a header and at least one edge."
    header, rows = table

    def normalize(value: str) -> str:
        plain = re.sub(
            r"\\(.)",
            r"\1",
            strip_citations(value).replace("**", "").replace("__", "").replace("`", ""),
        )
        return re.sub(r"\s+", " ", plain.strip()).casefold()

    normalized_headers = [normalize(cell) for cell in header]
    if (
        len(header) != 3
        or any(not label for label in normalized_headers)
        or len(set(normalized_headers)) != 3
    ):
        return (
            "A diagram table needs three distinct, named columns in start, relationship, end order."
        )
    if not 1 <= len(rows) <= 8 or any(len(row) != 3 for row in rows):
        return f"A diagram supports 1 to 8 complete edge rows; this table has {len(rows)}."
    nodes: list[str] = []
    adjacency: dict[str, set[str]] = {}
    indegree: dict[str, int] = {}
    edges: set[tuple[str, str]] = set()
    edge_rows: list[tuple[str, str, str]] = []
    for row in rows:
        source, relation, target = row
        if has_markers(source) or has_markers(target):
            return (
                "Diagram node labels cannot carry citations; cite each relationship cell instead."
            )
        source_key, target_key = normalize(source), normalize(target)
        relation_text = normalize(relation)
        if not source_key or not target_key or not relation_text:
            return "Every diagram edge needs a readable start, relationship, and end label."
        if (
            units(source) > 24
            or units(target) > 24
            or units(relation) > 18
            or _diagram_width(source) > 15
            or _diagram_width(target) > 15
            or _diagram_width(relation) > 13.5
        ):
            return (
                "A diagram label is too long to fit safely; use the original table or split "
                "the graph."
            )
        refs = citation_numbers(relation)
        if not refs:
            return "Every diagram relationship needs at least one numbered source citation."
        if source_key == target_key:
            return "A diagram cannot contain a self edge."
        edge = (source_key, target_key)
        if edge in edges:
            return "A diagram cannot repeat the same directed edge."
        edges.add(edge)
        edge_rows.append((source_key, relation, target_key))
        for node in (source_key, target_key):
            if node not in indegree:
                nodes.append(node)
                indegree[node] = 0
                adjacency[node] = set()
        adjacency[source_key].add(target_key)
        indegree[target_key] += 1
    if not 2 <= len(nodes) <= 6:
        return f"A diagram supports 2 to 6 distinct nodes; this table has {len(nodes)}."
    ready = [node for node in nodes if indegree[node] == 0]
    rank = {node: 0 for node in ready}
    visited = 0
    while ready:
        node = ready.pop(0)
        visited += 1
        for target in adjacency[node]:
            rank[target] = max(rank.get(target, 0), rank[node] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(nodes):
        return "This relationship table contains a cycle; keep it as a table or rewrite its edges."
    layers: dict[int, int] = {}
    for value in rank.values():
        layers[value] = layers.get(value, 0) + 1
    if len(layers) > 3 or max(layers.values(), default=0) > 3:
        return "This graph is too deep or wide to lay out clearly; split it or keep the table."
    by_rank = {level: [node for node in nodes if rank[node] == level] for level in layers}
    centers = {
        node: 210 + (index - (len(by_rank[rank[node]]) - 1) / 2) * 140
        for node in nodes
        for index in [by_rank[rank[node]].index(node)]
    }
    lanes: dict[int, list[float]] = {}
    for source, _, target in edge_rows:
        gap = rank[target] - rank[source]
        if gap != 1:
            return (
                "A relationship skips a graph layer; keep the table or describe one edge at a time."
            )
        center = (centers[source] + centers[target]) / 2
        lane = rank[source]
        if any(abs(center - prior) < 64 for prior in lanes.get(lane, [])):
            return (
                "Relationship labels would overlap in this graph; keep the table or split "
                "the diagram."
            )
        lanes.setdefault(lane, []).append(center)
    return None


def chart_numbers_cited(
    pages: Sequence[Page], _: Mapping[str, Any], context: Context
) -> list[Finding]:
    found = []
    for page in pages:
        if page.layout not in CHARTS or (table := _table(page)) is None:
            continue
        bare = [
            row[0]
            for row in table[1]
            if re.search(r"\d", strip_citations(" ".join(row[1:])))
            and not citation_numbers(" ".join(row), bare_only=True)
        ]
        if bare:
            found.append(
                Finding(
                    "chart_numbers_cited",
                    page.index,
                    "Numbers with no source: " + ", ".join(f"“{label}”" for label in bare),
                )
            )
        if context.evidence is not None:
            for row in table[1]:
                joined = " ".join(row)
                refs = citation_numbers(joined, bare_only=True)
                passages = "\n".join(
                    context.evidence[n].context or context.evidence[n].text
                    for n in refs
                    if n in context.evidence
                )
                # Permit CJK text immediately before a value while avoiding a partial match
                # inside an ASCII identifier such as ``rate12``.
                pattern = r"(?<![A-Za-z0-9_.+\-−–—])[+\-−–—]?\d[\d,]*(?:\.\d+)?%?"

                def normalized(value: str) -> str:
                    return (
                        value.replace("−", "-")
                        .replace("–", "-")
                        .replace("—", "-")
                        .replace(",", "")
                    )

                source_values = {normalized(v) for v in re.findall(pattern, passages)}
                header_unit = "%" if any("%" in cell for cell in table[0][1:]) else ""
                values = [
                    normalized(v)
                    for v in re.findall(pattern, strip_citations(" ".join(row[1:])))
                ]
                missing = [
                    v
                    for v in values
                    if v + (header_unit if not v.endswith("%") else "") not in source_values
                ]
                if missing:
                    found.append(
                        Finding(
                            "chart_numbers_cited",
                            page.index,
                            f"Chart values for “{row[0]}” are absent from cited passages: "
                            f"{', '.join(missing)}. Use sourced values with matching units; "
                            "a derived value needs an explicit formula and operands.",
                        )
                    )
    return found
