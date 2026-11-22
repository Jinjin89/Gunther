"""Load the declarative skills used by Ask."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from gunther.ask_research import Budget

SKILLS_DIR = Path(__file__).resolve().parent / "skills"

FENCE = re.compile(r"^\s*(```|~~~)")
SECTION = re.compile(r"^## ([a-z][a-z0-9_]*)(?=$|[\s:：])")


class SkillError(ValueError):
    """A skill that cannot be used, and why, naming the file."""


def split_sections(text: str, ids: Collection[str] | None = None) -> tuple[str, dict[str, str]]:
    """The text before the first section, and each section by its id, headings included.

    A section starts at a line ``## <id>`` outside a code fence. With ``ids`` only those
    start one; any other heading belongs to the part it is in.
    """

    head: list[str] = []
    found: dict[str, list[str]] = {}
    current = head
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            match = SECTION.match(line)
            if match and (ids is None or match.group(1) in ids):
                if match.group(1) in found:
                    raise SkillError(f"“## {match.group(1)}” appears twice")
                current = found[match.group(1)] = []
        current.append(line)
    return "\n".join(head).strip(), {key: "\n".join(lines).strip() for key, lines in found.items()}


def _is_ask(root: Path) -> bool:
    """Whether a readable manifest declares an Ask skill."""

    try:
        manifest = tomllib.loads((root / "skill.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return manifest.get("kind") == "ask"


ASK_SECTIONS = ("frame", "plan", "write", "check")
COMMAND = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
CHECKLIST_MAX = 10
BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")
BUDGET_KEYS = ("searches", "reads", "calls", "check_sentences", "leads")


@dataclass(frozen=True)
class AskSkill:
    name: str
    version: int
    command: str  # ASCII, what follows "/"
    title: str  # shown in the menu and on the answer
    description: str  # for the menu and auto-match
    frame: bool
    # "standard", "deep", ...; the first is the default. Empty: Ask's own default Budget.
    budgets: Mapping[str, Budget]
    preamble: str  # SKILL.md text before the first "## " section
    sections: Mapping[str, str]  # "frame", "plan", "write" -> text
    checklist: tuple[str, ...]  # the bullets of "## check"

    def method(self, step: str) -> str:
        """What a step reads of the skill: the preamble, then its section."""

        return "\n".join(part for part in (self.preamble, self.sections.get(step, "")) if part)

    def budget(self, name: str | None = None) -> Budget:
        """The named budget, else the first one, else Ask's default."""

        if name and name in self.budgets:
            return self.budgets[name]
        return next(iter(self.budgets.values()), Budget())


def _budget(table: Any, where: str) -> Budget:
    if not isinstance(table, dict):
        raise SkillError(f"{where} must be a table")
    allowed = (*BUDGET_KEYS, "sub_questions")
    if unknown := [key for key in table if key not in allowed]:
        raise SkillError(f"{where}: unknown settings {', '.join(unknown)}")
    values: dict[str, Any] = {}
    for key in BUDGET_KEYS:
        if key in table:
            if not isinstance(table[key], int) or isinstance(table[key], bool) or table[key] < 0:
                raise SkillError(f"{where}: {key} must be a whole number, 0 or more")
            values[key] = table[key]
    if "sub_questions" in table:
        pair = table["sub_questions"]
        if not (
            isinstance(pair, list)
            and len(pair) == 2
            and all(isinstance(n, int) and not isinstance(n, bool) for n in pair)
            and 1 <= pair[0] <= pair[1] <= 12
        ):
            raise SkillError(f"{where}: sub_questions must be [low, high], from 1 to 12")
        values["sub_questions"] = (pair[0], pair[1])
    return Budget(**values)


def load_ask_skill(root: Path) -> AskSkill:
    """Read and check an Ask skill folder. Anything wrong is a SkillError naming the file."""

    try:
        manifest = tomllib.loads((root / "skill.toml").read_text(encoding="utf-8"))
        text = (root / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SkillError(f"{root.name}: {error}") from error
    where = f"{root.name}/skill.toml"
    for key in ("name", "command", "title", "description"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise SkillError(f"{where}: “{key}” is missing")
    if manifest.get("kind") != "ask":
        raise SkillError(f"{where}: kind must be ask")
    if not isinstance(manifest.get("version"), int):
        raise SkillError(f"{where}: “version” must be a whole number")
    if not COMMAND.match(manifest["command"]):
        raise SkillError(
            f"{where}: command must be 2 to 24 lowercase letters, digits or hyphens, "
            "starting with a letter"
        )
    frame = manifest.get("frame", False)
    if not isinstance(frame, bool):
        raise SkillError(f"{where}: “frame” must be true or false")
    tables = manifest.get("budgets", {})
    if not isinstance(tables, dict):
        raise SkillError(f"{where}: budgets must be tables")
    budgets = {name: _budget(table, f"{where}, budget “{name}”") for name, table in tables.items()}
    try:
        preamble, found = split_sections(text)
    except SkillError as error:
        raise SkillError(f"{root.name}/SKILL.md: {error}") from error
    if unknown := [key for key in found if key not in ASK_SECTIONS]:
        raise SkillError(
            f"{root.name}/SKILL.md: unknown section “## {unknown[0]}”; "
            f"use {', '.join(ASK_SECTIONS)}"
        )
    # Each section without its heading line.
    bodies = {
        key: body.split("\n", 1)[1].strip() if "\n" in body else "" for key, body in found.items()
    }
    checklist = tuple(
        match.group(1)
        for line in bodies.get("check", "").splitlines()
        if (match := BULLET.match(line))
    )
    if len(checklist) > CHECKLIST_MAX:
        raise SkillError(
            f"{root.name}/SKILL.md: the check list has more than {CHECKLIST_MAX} items"
        )
    if frame and "frame" not in bodies:
        raise SkillError(
            f"{root.name}/SKILL.md has no section “## frame”, which a framing skill needs"
        )
    return AskSkill(
        name=manifest["name"],
        version=manifest["version"],
        command=manifest["command"],
        title=manifest["title"],
        description=manifest["description"],
        frame=frame,
        budgets=budgets,
        preamble=preamble,
        sections={key: bodies[key] for key in ("frame", "plan", "write") if key in bodies},
        checklist=checklist,
    )


def load_ask_skills(directory: Path) -> dict[str, AskSkill]:
    """Every Ask skill in a folder of skills, by command."""

    found: dict[str, AskSkill] = {}
    for path in sorted(directory.iterdir()):
        if not path.is_dir() or not _is_ask(path):
            continue
        skill = load_ask_skill(path)
        if skill.command in found:
            raise SkillError(
                f"{path.name}/skill.toml: the command /{skill.command} is already used by "
                f"{found[skill.command].name}"
            )
        found[skill.command] = skill
    return found


@cache
def ask_skills() -> dict[str, AskSkill]:
    """The skills Ask can follow, by command."""

    return load_ask_skills(SKILLS_DIR)
