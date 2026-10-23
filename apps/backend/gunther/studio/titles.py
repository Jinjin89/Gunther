"""Whether a title states a point or only names a topic: one test, shared by the slide and
report format checks (which send such titles back) and the quality eval's linter (which
counts them as defects)."""

from __future__ import annotations

import re
import unicodedata

CITE = re.compile(r"\[(?:\d+(?:\s*[,–-]\s*\d+)*|\?)\]")

AUXILIARIES = frozenset(
    [
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "can",
        "could",
        "will",
        "would",
        "may",
        "might",
        "must",
        "should",
        "shall",
        "isn't",
        "aren't",
        "wasn't",
        "don't",
        "doesn't",
        "can't",
        "won't",
    ]
)
COMMON_VERBS = frozenset(
    [
        "cut",
        "cuts",
        "raise",
        "raises",
        "rise",
        "rises",
        "fall",
        "falls",
        "drop",
        "drops",
        "grow",
        "grows",
        "lead",
        "leads",
        "make",
        "makes",
        "show",
        "shows",
        "need",
        "needs",
        "mean",
        "means",
        "beat",
        "beats",
        "lose",
        "loses",
        "win",
        "wins",
        "keep",
        "keeps",
        "hold",
        "holds",
        "turn",
        "turns",
        "set",
        "sets",
        "let",
        "lets",
        "help",
        "helps",
        "cost",
        "costs",
        "save",
        "saves",
        "drive",
        "drives",
        "bring",
        "brings",
        "give",
        "gives",
        "take",
        "takes",
        "find",
        "finds",
        "put",
        "puts",
        "run",
        "runs",
        "reach",
        "reaches",
        "stay",
        "stays",
        "gain",
        "gains",
        "split",
        "splits",
        "cap",
        "caps",
        "depend",
        "depends",
        "fail",
        "fails",
        "work",
        "works",
        "break",
        "breaks",
        "open",
        "opens",
        "close",
        "closes",
        "build",
        "builds",
        "move",
        "moves",
        "shift",
        "shifts",
        "outpace",
        "outpaces",
        "outperform",
        "outperforms",
        "slow",
        "slows",
        "speed",
        "speeds",
        "double",
        "doubles",
        "triple",
        "triples",
        "halve",
        "halves",
        "shrink",
        "shrinks",
        "expand",
        "expands",
        "matter",
        "matters",
        "explain",
        "explains",
        "require",
        "requires",
        "replace",
        "replaces",
        "limit",
        "limits",
        "support",
        "supports",
        "decide",
        "decides",
        "choose",
        "chooses",
        "ask",
        "asks",
        "answer",
        "answers",
        "promise",
        "promises",
        "signal",
        "signals",
        "offer",
        "offers",
    ]
)
VERB_SUFFIXES = ("ed", "ing", "ize", "ise", "ify", "ises", "izes", "ifies")
CJK_VERBS = (
    "是有为将达增降提超占实推促保稳扩降涨跌升减加使让需要能会可已仍未难靠赖"
    "决定选择取得带来形成成为面临"
)


def is_topic_title(title: str) -> bool:
    """True when a title is a topic label rather than a sentence that carries a point."""

    clean = CITE.sub("", title).replace("**", "").strip()
    if not clean:
        return True
    if _units(clean) < 12:
        return True
    if _has_cjk(clean):
        cjk = [ch for ch in clean if unicodedata.east_asian_width(ch) in ("W", "F")]
        # A CJK title of ten or more characters, or with a clause break, states a point.
        if len(cjk) >= 10 or re.search(r"[，；：,;:]", clean):
            return False
        return len(cjk) < 8 or not (any(ch in CJK_VERBS for ch in cjk) or re.search(r"\d", clean))
    words = re.findall(r"[\w'’%.-]+", clean.lower())
    if len(words) < 4:
        return True
    if len(words) >= 6 or re.search(r"\d", clean):
        return False
    for word in words:
        word = word.replace("’", "'")
        if word in AUXILIARIES or word in COMMON_VERBS:
            return False
        if len(word) > 4 and word.endswith(VERB_SUFFIXES):
            return False
    return True


def _units(text: str) -> int:
    from gunther.studio.format import units

    return units(text)


def _has_cjk(text: str) -> bool:
    return any(unicodedata.east_asian_width(ch) in ("W", "F") for ch in text)
