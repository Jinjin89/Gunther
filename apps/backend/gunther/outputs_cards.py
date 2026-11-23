"""Markdown grammar for a set of flashcards or quiz cards."""

import re
from dataclasses import dataclass

from gunther.checks import claim_text

HEADER = "<!-- cards: {type_id}; design: v1 -->"
_HEADER = re.compile(r"<!-- cards: [a-z][a-z0-9_]*; design: v1 -->")
_OPTION = re.compile(r"- \[([ x])\][ \t]+(\S.*)")


@dataclass(frozen=True)
class Card:
    question: str
    answer: str
    why: str = ""


def parse_card(unit: str) -> Card:
    heading, _, body = unit.strip().partition("\n")
    if not heading.startswith("## "):
        raise ValueError('Start the card with a "## " question.')
    question = heading[3:].strip()
    quiz = question == "(quiz)" or question.startswith("(quiz) ")
    if quiz:
        question = question.removeprefix("(quiz)").strip()
    if not claim_text(question):
        raise ValueError("Give the card a non-empty question.")
    if not quiz:
        if not claim_text(body):
            raise ValueError("Give the flashcard a non-empty answer.")
        return Card(question, " ".join(body.split()))

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    why = next((n for n, line in enumerate(lines) if line.startswith("Why:")), len(lines))
    options = [_OPTION.fullmatch(line) for line in lines[:why]]
    if any(option is None for option in options):
        raise ValueError('Use "- [ ] " and "- [x] " for the quiz options before "Why:".')
    answers = [option[2] for option in options if option is not None and option[1] == "x"]
    if len(answers) != 1:
        raise ValueError('Mark exactly one quiz option with "[x]".')
    reason = lines[why:]
    if not reason or any(_OPTION.fullmatch(line) or line.startswith("Why:") for line in reason[1:]):
        raise ValueError('Finish the quiz card with one "Why:" explanation after its options.')
    explanation = " ".join([reason[0].removeprefix("Why:"), *reason[1:]]).strip()
    if not claim_text(explanation):
        raise ValueError('Give the quiz card a non-empty "Why:" explanation.')
    return Card(question, answers[0].strip(), explanation)


def card_problem(unit: str) -> str | None:
    try:
        parse_card(unit)
    except ValueError as error:
        return str(error)
    return None


def check_cards(content: str, *, header: str | None = None) -> list[str]:
    from gunther.output_shapes import split_headed

    preamble, units = split_headed(content)
    problems = []
    if header is not None:
        if preamble != header:
            problems.append(f"Start with {header}.")
    elif not _HEADER.fullmatch(preamble):
        problems.append("Start with a cards marker naming the output type and design version v1.")
    if not units:
        problems.append("Provide at least one card.")
    problems.extend(
        f"Card {n}: {problem}" for n, unit in enumerate(units, 1)
        if (problem := card_problem(unit))
    )
    return problems


def check_text(unit: str, *, refs=None, rest=None) -> list[str]:
    try:
        card = parse_card(unit)
    except ValueError:
        return []
    if not card.why:
        return [f"{card.question} {card.answer}"]
    stop = "" if card.answer.endswith((".", "!", "?", "。", "！", "？")) else "."
    return [f"{card.question} Answer: {card.answer}{stop} {card.why}"]
