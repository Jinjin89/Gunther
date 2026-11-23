"""Markdown dialogue used for verified audio overviews."""

import re

from gunther.checks import claim_text
from gunther.citations import ANY_CITATION, UNSOURCED, collapse_citations

HEADER = "<!-- audio: {type_id}; design: v1 -->"
_HEADER = re.compile(r"<!-- audio: [a-z][a-z0-9_]*; design: v1 -->")
SPEAKERS = ("A", "B")


def dialogue(line: str) -> tuple[str, str]:
    speaker, _, text = line.partition(":")
    return speaker, text.strip()


def speech(line: str) -> tuple[str, str]:
    speaker, text = dialogue(line)
    return speaker, claim_text(text.replace(UNSOURCED, ""))


def line_problem(line: str) -> str | None:
    speaker, text = speech(line)
    if speaker not in SPEAKERS or not text:
        return "Start the line with A: or B: and give it non-empty speech."
    return None


def spans(content: str) -> tuple[int, list[tuple[int, int]]]:
    from gunther.output_shapes import line_spans

    return line_spans(content, starts=lambda line: line.startswith(("A:", "B:")))


def check_audio(content: str, *, header: str | None = None) -> list[str]:
    from gunther.output_shapes import split_spans

    preamble, lines = split_spans(content, spans)
    problems = []
    if header is not None and preamble != header:
        problems.append(f"Start with {header}.")
    elif header is None and not _HEADER.fullmatch(preamble):
        problems.append("Start with an audio marker naming the type and design version v1.")
    problems.extend(f"Line {n}: {problem}" for n, line in enumerate(lines, 1)
                    if (problem := line_problem(line)))
    if not set(SPEAKERS).issubset(dialogue(line)[0] for line in lines):
        problems.append("Include speech from both A and B.")
    return problems


def check_text(line: str, **_options) -> list[str]:
    _, text = dialogue(line)
    return [text] if ANY_CITATION.search(collapse_citations(text)) else []
