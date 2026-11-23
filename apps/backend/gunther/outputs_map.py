"""The nested Markdown list grammar for mind maps."""

import re

from gunther.checks import claim_text
from gunther.citations import ANY_CITATION, UNSOURCED, collapse_citations

HEADER = "<!-- map: {type_id}; design: v1 -->"
_HEADER = re.compile(r"<!-- map: [a-z][a-z0-9_]*; design: v1 -->")
_NODE = re.compile(r"((?:  )*)- +(\S.*)")


def node_of(line: str) -> tuple[int, str] | None:
    if match := _NODE.fullmatch(line.rstrip()):
        label = claim_text(match[2].replace(UNSOURCED, ""))
        if label:
            return len(match[1]) // 2, label
    return None


def node_spans(content: str) -> tuple[int, list[tuple[int, int]]]:
    from gunther.output_shapes import line_spans

    return line_spans(content, starts=lambda line: line.lstrip().startswith("-"))


def check_map(content: str, *, header: str | None = None) -> list[str]:
    from gunther.output_shapes import split_spans

    preamble, nodes = split_spans(content, node_spans)
    problems = []
    if header is not None:
        if preamble != header:
            problems.append(f"Start with {header}.")
    elif not _HEADER.fullmatch(preamble):
        problems.append("Start with a map marker naming the output type and design version v1.")

    previous = -1
    roots = 0
    for position, line in enumerate(nodes, 1):
        node = node_of(line)
        if node is None:
            problems.append(
                f"Node {position}: use '- ' and non-empty text, with two spaces per level."
            )
            continue
        depth, _ = node
        if depth > previous + 1:
            problems.append(f"Node {position}: a node must have a parent at the previous level.")
        roots += depth == 0
        previous = depth
    if roots != 1:
        problems.append("Use exactly one root node at the left margin.")
    return problems


def check_text(unit: str, *, ancestors: tuple[str, ...] = (), refs=None, rest=None) -> list[str]:
    if not node_of(unit) or not ANY_CITATION.search(collapse_citations(unit)):
        return []
    return [" › ".join((*ancestors, unit.lstrip()[2:].strip()))]
