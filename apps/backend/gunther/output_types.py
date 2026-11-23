"""The Studio catalog and the types of saved versions it can read."""

from dataclasses import dataclass
from typing import Literal, get_args

from gunther.studio import prompts

ShapeId = Literal["report", "slides"]
OutputOption = Literal["focus", "audience", "length", "language", "use"]
OutputLength = Literal["short", "standard", "long"]
DeckUse = Literal["talk", "read"]
DECK_USES = get_args(DeckUse)


@dataclass(frozen=True)
class ShapeSpec:
    id: ShapeId
    unit_label: str
    max_parts: int


SHAPES = {
    shape.id: shape
    for shape in (ShapeSpec("report", "section", 8), ShapeSpec("slides", "slide", 14))
}


@dataclass(frozen=True)
class OutputType:
    id: str
    label: str
    shape: ShapeId
    description: str
    icon: str
    plan: str = ""
    write: str = ""
    options: tuple[OutputOption, ...] = ("focus", "audience", "length", "language")
    summary_box: bool = False
    require_exhibits: bool = False
    max_parts: int | None = None

    @property
    def part_limit(self) -> int:
        return self.max_parts if self.max_parts is not None else SHAPES[self.shape].max_parts

    def part_limit_error(self, count: int) -> str | None:
        limit = self.part_limit
        if count > limit:
            unit = SHAPES[self.shape].unit_label
            return f"The limit for {self.label} is {limit} {unit}s."
        return None


OUTPUT_TYPES = {
    item.id: item
    for item in (
        OutputType(
            "report", "Report", "report", "Explore a topic or answer a research question.",
            "FileText", plan=prompts.REPORT_PLAN, write=prompts.REPORT_CRAFT,
            summary_box=True, require_exhibits=True,
        ),
        OutputType(
            "briefing", "Briefing doc", "report", "Weigh evidence and make a recommendation.",
            "ClipboardList", plan=f"{prompts.BRIEFING_STYLE}\n\n{prompts.REPORT_PLAN}",
            write=f"{prompts.BRIEFING_STYLE}\n\n{prompts.REPORT_CRAFT}",
            summary_box=True, require_exhibits=True,
        ),
        OutputType(
            "study_guide", "Study guide", "report", "Learn concepts and test your understanding.",
            "BookOpen", plan=prompts.STUDY_GUIDE_RECIPE,
            write=f"{prompts.STUDY_GUIDE_RECIPE}\n{prompts.REPORT_CALLOUT}", max_parts=12,
        ),
        OutputType(
            "faq", "FAQ", "report", "Find answers to the questions readers will ask.",
            "CircleHelp", plan=prompts.FAQ_RECIPE,
            write=f"{prompts.FAQ_RECIPE}\n{prompts.REPORT_CALLOUT}", max_parts=16,
        ),
        OutputType(
            "timeline", "Timeline", "report", "Follow events in chronological order.",
            "CalendarDays", plan=prompts.TIMELINE_RECIPE,
            write=f"{prompts.TIMELINE_RECIPE}\n{prompts.REPORT_CALLOUT}", max_parts=16,
        ),
        OutputType(
            "slides", "Slides", "slides", "Present the findings in a slide deck.",
            "Presentation",
            options=("focus", "audience", "length", "language", "use"),
        ),
    )
}
MAX_OUTPUT_PARTS = max(item.part_limit for item in OUTPUT_TYPES.values())

# A stored style keeps its original bytes. The optional label preserves a retired type's name.
LEGACY_OUTPUT_STYLES = {
    "auto": ("report", None),
    "overview": ("report", None),
    "field_guide": ("report", "Field guide"),
    "teaching_path": ("study_guide", None),
    "decision_brief": ("briefing", None),
}


def output_type_for(shape: str, style: str | None = None) -> OutputType:
    key = LEGACY_OUTPUT_STYLES.get(style or "", (style, None))[0]
    item = OUTPUT_TYPES.get(key or "")
    return item if item is not None and item.shape == shape else OUTPUT_TYPES[shape]


def output_type_label(shape: ShapeId, style: str | None) -> str:
    _, label = LEGACY_OUTPUT_STYLES.get(style or "", (None, None))
    return (label if shape == "report" else None) or output_type_for(shape, style).label
