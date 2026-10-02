"""Skills: how Gunther's agents make one kind of output, kept as files.

A skill is a folder (see ``skills/report`` and ``skills/slides``):

- ``skill.toml`` names it, says which kind of output it makes, its limits, and its steps in
  order. Each step says what it does (one of the step kinds the runner knows, see
  skill_runner), the tools it may use, the references it reads, the checks that run on
  what it made, and how hard the model thinks.
- ``SKILL.md`` holds the instructions. The text before the first step's heading is the
  preamble, given to every step that keeps it; a step's section starts at ``## <id>``.
- ``references/`` holds tables and examples. ``layouts.md#{layout}`` reads only the section
  ``## <layout>`` of a reference, for the layout of the page being written.

Gunther's own rules (citations, language, the JSON a step returns) are not part of a skill,
and no skill can turn them off.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from gunther.agent import Budget
from gunther.skill_checks import CHECKS

SKILLS_DIR = Path(__file__).resolve().parent / "skills"

STEP_KINDS = ("understand", "structure", "write", "edit_review", "reader_test", "revise")
TOOLS = ("search_library", "search_web", "read_source")
EFFORTS = ("off", "low", "medium", "high", "max", "job")
USES = ("talk", "read")

FENCE = re.compile(r"^\s*(```|~~~)")
SECTION = re.compile(r"^## ([a-z][a-z0-9_]*)(?=$|[\s:：])")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


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


@dataclass(frozen=True)
class SkillStep:
    id: str
    does: str
    label: str
    effort: str
    # The step's section of SKILL.md.
    instructions: str
    tools: tuple[str, ...] = ()
    tool_calls: int = 0
    references: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    # Whether the step also reads the preamble (a fresh reader does not).
    preamble: bool = True


@dataclass(frozen=True)
class Skill:
    name: str
    kind: str  # "report" | "slides"
    version: int
    title: str
    description: str
    preamble: str
    steps: tuple[SkillStep, ...]
    structures: tuple[str, ...] = ()
    # A report style the person picks, and the structure it fixes.
    styles: Mapping[str, str] = field(default_factory=dict)
    layouts: tuple[str, ...] = ()
    uses: tuple[str, ...] = ()
    limits: Mapping[str, int] = field(default_factory=dict)
    check_params: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    # Each reference file's text, by its name.
    files: Mapping[str, str] = field(default_factory=dict)

    def step(self, does: str) -> SkillStep | None:
        return next((step for step in self.steps if step.does == does), None)

    def reference(self, name: str, **values: str) -> str:
        """A reference, or with ``#section`` one section of it; ``{layout}`` and the like
        are filled from ``values``. An empty string when that section is not there."""

        file, _, section = PLACEHOLDER.sub(lambda m: values.get(m.group(1), ""), name).partition(
            "#"
        )
        text = self.files[file]
        if not section:
            return text
        return split_sections(text)[1].get(section, "")

    def instructions(self, step: SkillStep, **values: str) -> str:
        """What a step reads of the skill: the preamble, its section, then its references."""

        parts = [self.preamble] if step.preamble and self.preamble else []
        parts.append(step.instructions)
        for name in step.references:
            text = self.reference(name, **values)
            if text:
                parts.append(f"Reference ({name.split('#')[0]}):\n\n{text}")
        return "\n\n".join(parts)


def _strings(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SkillError(f"{where} must be a list of strings")
    return tuple(value)


def load_skill(root: Path) -> Skill:
    """Read and check a skill folder. Anything wrong is a SkillError naming the file."""

    try:
        manifest = tomllib.loads((root / "skill.toml").read_text(encoding="utf-8"))
        text = (root / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SkillError(f"{root.name}: {error}") from error
    where = f"{root.name}/skill.toml"
    for key in ("name", "kind", "title", "description"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise SkillError(f"{where}: “{key}” is missing")
    if manifest["kind"] not in ("report", "slides"):
        raise SkillError(f"{where}: kind must be report or slides")
    if not isinstance(manifest.get("version"), int):
        raise SkillError(f"{where}: “version” must be a whole number")

    folder = root / "references"
    files = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(folder.glob("*.md") if folder.is_dir() else [])
    }

    raw_steps = manifest.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise SkillError(f"{where}: there are no steps")
    ids = [item.get("id") for item in raw_steps if isinstance(item, dict)]
    try:
        preamble, sections = split_sections(text, set(ids))
    except SkillError as error:
        raise SkillError(f"{root.name}/SKILL.md: {error}") from error
    steps: list[SkillStep] = []
    for item in raw_steps:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise SkillError(f"{where}: every step needs an id")
        at = f"{where}, step “{item['id']}”"
        if item.get("does") not in STEP_KINDS:
            raise SkillError(f"{at}: “does” must be one of {', '.join(STEP_KINDS)}")
        if item.get("effort", "low") not in EFFORTS:
            raise SkillError(f"{at}: effort must be one of {', '.join(EFFORTS)}")
        if item["id"] not in sections:
            raise SkillError(f"{root.name}/SKILL.md has no section “## {item['id']}”")
        tools = _strings(item.get("tools", []), f"{at}: tools")
        if unknown := [tool for tool in tools if tool not in TOOLS]:
            raise SkillError(f"{at}: unknown tools {', '.join(unknown)}")
        checks = _strings(item.get("checks", []), f"{at}: checks")
        if unknown := [check for check in checks if check not in CHECKS]:
            raise SkillError(f"{at}: unknown checks {', '.join(unknown)}")
        references = _strings(item.get("references", []), f"{at}: references")
        for name in references:
            if name.split("#")[0] not in files:
                raise SkillError(f"{at}: there is no references/{name.split('#')[0]}")
        steps.append(
            SkillStep(
                id=item["id"],
                does=item["does"],
                label=str(item.get("label") or item["id"]),
                effort=item.get("effort", "low"),
                instructions=sections[item["id"]],
                tools=tools,
                tool_calls=int(item.get("tool_calls", 0)),
                references=references,
                checks=checks,
                preamble=bool(item.get("preamble", True)),
            )
        )
    order = [step.does for step in steps]
    if len(set(order)) != len(order):
        raise SkillError(f"{where}: a kind of step appears twice")
    if order[:3] != ["understand", "structure", "write"]:
        raise SkillError(f"{where}: the steps must start understand, structure, write")

    sectioned = {key for body in files.values() for key in split_sections(body)[1]}
    structures = _strings(manifest.get("structures", []), f"{where}: structures")
    layouts = _strings(manifest.get("layouts", []), f"{where}: layouts")
    for kind, wanted in (("structure", structures), ("layout", layouts)):
        if missing := [item for item in wanted if item not in sectioned]:
            raise SkillError(f"{where}: no reference describes the {kind} {', '.join(missing)}")
    if not structures:
        raise SkillError(f"{where}: there are no structures")
    styles = dict(manifest.get("styles", {}))
    if bad := [key for key, value in styles.items() if value not in structures]:
        raise SkillError(f"{where}: styles {', '.join(bad)} name an unknown structure")
    uses = _strings(manifest.get("uses", []), f"{where}: uses")
    if any(use not in USES for use in uses):
        raise SkillError(f"{where}: uses must be talk or read")
    if manifest["kind"] == "slides" and not {"title", "takeaways"} <= set(layouts):
        raise SkillError(f"{where}: a deck needs the title and takeaways layouts")
    limits = manifest.get("limits", {})
    if not all(isinstance(value, int) for value in limits.values()):
        raise SkillError(f"{where}: limits must be whole numbers")
    params = manifest.get("checks", {})
    if unknown := [name for name in params if name not in CHECKS]:
        raise SkillError(f"{where}: settings for unknown checks {', '.join(unknown)}")
    return Skill(
        name=manifest["name"],
        kind=manifest["kind"],
        version=manifest["version"],
        title=manifest["title"],
        description=manifest["description"],
        preamble=preamble,
        steps=tuple(steps),
        structures=structures,
        styles=styles,
        layouts=layouts,
        uses=uses,
        limits=dict(limits),
        check_params={name: dict(value) for name, value in params.items()},
        files=files,
    )


def _is_ask(root: Path) -> bool:
    """Whether a skill folder is for Ask (its own loader reads those). A folder that cannot
    be read is not: ``load_skill`` says what is wrong with it."""

    try:
        manifest = tomllib.loads((root / "skill.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return manifest.get("kind") == "ask"


@cache
def builtin_skills() -> dict[str, Skill]:
    """The skills Gunther ships, by name."""

    found = [
        load_skill(path)
        for path in sorted(SKILLS_DIR.iterdir())
        if path.is_dir() and not _is_ask(path)
    ]
    return {skill.name: skill for skill in found}


def skill_for(kind: str) -> Skill:
    """The skill that makes this kind of output."""

    for skill in builtin_skills().values():
        if skill.kind == kind:
            return skill
    raise SkillError(f"There is no skill for {kind}")


# Ask skills -----------------------------------------------------------------------------------
#
# A skill for Ask is settings for the same loop, not code: whether to frame first, how far the
# loop may go, and instructions for the steps. See docs/ASK_RESEARCH_PLAN.md.

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
        key: body.split("\n", 1)[1].strip() if "\n" in body else ""
        for key, body in found.items()
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
