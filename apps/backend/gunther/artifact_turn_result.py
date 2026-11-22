"""Finish artifact turns and project their saved version, diffs and replies."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from gunther.artifact_view import _one_line, ranges
from gunther.artifact_workspace import ConversationBrief, Pending, Work, active_work, ready
from gunther.llm import ModelError
from gunther.outputs import BuiltOutput

Audience = Literal["scientist", "student", "collaborator"]

# What the audience of a version is called by the words the user gave (the version keeps only
# three audiences; the words stay in the brief).
STUDENTS = re.compile(
    r"student|undergrad|pupil|learner|school|class\b|freshm|first[- ]year|beginner|novice|"
    r"newcomer|trainee|学生|同学|新生|初学",
    re.IGNORECASE,
)

COLLABORATORS = re.compile(
    r"collaborat|colleague|team|partner|manager|stakeholder|client|funder|board|executive|"
    r"decision[- ]maker|同事|合作|团队|经理|领导|客户",
    re.IGNORECASE,
)


def audience_of(brief: ConversationBrief) -> Audience:
    """Who a version is for, out of the three a version records, from the brief's free text.
    No audience, or one that is neither, is a scientist."""

    words = brief.audience.text if brief.audience else ""
    if STUDENTS.search(words):
        return "student"
    if COLLABORATORS.search(words):
        return "collaborator"
    return "scientist"


# One turn -----------------------------------------------------------------------------------------


@dataclass
class SaveRequest:
    """A finished artifact for the service to seal as a version."""

    work: Work
    built: BuiltOutput
    version_map: list[int]
    summary: str
    # The change-log line after "v4 (deck): ".
    log: str
    changes: list[dict[str, Any]]


@dataclass
class TurnResult:
    outcome: Literal["asked", "answered", "saved", "replied", "paused"]
    reply: str
    question: dict[str, Any] | None = None
    save: SaveRequest | None = None
    changes: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Outcome:
    """What an action did: its line, the note the next decision sees, and a turn's end."""

    label: str
    note: str
    result: TurnResult | None = None


class ArtifactTurnResults:
    # Endings ---------------------------------------------------------------------------------

    def _finish(
        self,
        outcome: str,
        reply: str,
        *,
        question: dict[str, Any] | None = None,
        save: tuple[Work, BuiltOutput, list[int]] | None = None,
    ) -> TurnResult:
        ws = self.workspace
        self.asked = None
        ws.pending = None
        ws.asked_in_a_row = ws.asked_in_a_row + 1 if outcome == "asked" else 0
        if outcome == "asked" and question is not None:
            ws.pending = Pending(
                question=question["text"], options=question["options"], message_id=self.ctx.reply_id
            )
        request = None
        changes: list[dict[str, Any]] = []
        if save is not None:
            work, built, version_map = save
            changes = self._changes(work)
            summary = reply or self._summary(changes)
            request = SaveRequest(
                work, built, version_map, summary, self._log(summary, changes), changes
            )
            reply = summary
        return TurnResult(outcome, reply, question, request, changes, list(self.steps))  # type: ignore[arg-type]

    def _changes(self, work: Work) -> list[dict[str, Any]]:
        """What the turn changed in an artifact, part by part."""

        was = self.started.get(work.key, {"ids": [], "headings": {}, "texts": {}})
        marks = self.touched.get(work.key, {})
        old = {item_id: n for n, item_id in enumerate(was["ids"], start=1)}
        out: list[dict[str, Any]] = []
        for position, item in enumerate(work.outline, start=1):
            part = work.parts.get(item.id)
            text = part.text if part else None
            mark = marks.get(item.id)
            if item.id not in old:
                out.append(
                    {
                        "position": position,
                        "was": None,
                        "heading": item.heading,
                        "what": "added",
                        "note": mark[1] if mark else "",
                    }
                )
                continue
            moved = mark is not None and mark[0] == "moved"
            changed = text != was["texts"].get(item.id) or item.heading != was["headings"].get(
                item.id
            )
            if not (changed or moved):
                continue
            what = (
                "moved"
                if moved and not changed
                else (mark[0] if mark and mark[0] != "moved" else "rewritten")
            )
            out.append(
                {
                    "position": position,
                    "was": old[item.id],
                    "heading": item.heading,
                    "what": what,
                    "note": mark[1] if mark else "",
                }
            )
        here = {item.id for item in work.outline}
        for item_id, n in old.items():
            if item_id not in here:
                out.append(
                    {
                        "position": None,
                        "was": n,
                        "heading": was["headings"].get(item_id, ""),
                        "what": "deleted",
                        "note": "",
                    }
                )
        return out

    @staticmethod
    def _summary(changes: list[dict[str, Any]]) -> str:
        if not changes:
            return "Saved the artifact."
        bits = []
        for what, verb in (
            ("rewritten", "Rewrote"),
            ("edited", "Edited"),
            ("added", "Added"),
            ("moved", "Moved"),
            ("deleted", "Removed"),
        ):
            places = [
                c["position"] if c["position"] is not None else c["was"]
                for c in changes
                if c["what"] == what
            ]
            if places:
                bits.append(f"{verb} {ranges(places)}")
        return ", ".join(bits) + "."

    @staticmethod
    def _log(summary: str, changes: list[dict[str, Any]]) -> str:
        """The change-log line after "v4 (deck): ": the summary, then what changed, by code."""

        def places(*kinds: str) -> list[int]:
            return [
                c["position"] if c["position"] is not None else c["was"]
                for c in changes
                if c["what"] in kinds
            ]

        line = _one_line(summary, 200)
        for word, kinds in (
            ("changed", ("rewritten", "edited")),
            ("added", ("added",)),
            ("moved", ("moved",)),
            ("removed", ("deleted",)),
        ):
            if found := places(*kinds):
                line += f" · {word} {ranges(found)}"
        return line

    def _paused(self) -> TurnResult:
        """The turn's budget is spent. A finished artifact with changes is saved with a summary
        made by code; otherwise the turn says what is left, and "continue" goes on."""

        c = self.ctx
        work = active_work(self.workspace)
        if work is not None and work.outline and not ready(work, self.brief):
            try:
                built, version_map = self._steps(work).assemble()
            except (ValueError, ModelError):
                built = None
            if built is not None and c.saved.get(work.key) != built.content:
                return self._finish("saved", "", save=(work, built, version_map))
        left: list[str] = list(self.not_done)
        if work is not None:
            left += ready(work, self.brief)
        said = " ".join(self.notes[-3:])
        decisions, calls, _ = self._left()
        used = (
            f"{c.limits.calls} model calls"
            if calls <= 0 or self.spent
            else f"{c.limits.decisions} decisions"
            if decisions <= 0
            else "its budget"
        )
        text = f"I stopped: this message used all {used} it may" + (
            f". So far: {said}" if said else ""
        )
        if left:
            text += " Still to do: " + " ".join(dict.fromkeys(left))
        text += " Say “continue” to carry on."
        return self._finish("paused", text)
