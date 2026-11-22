"""Read the renderer's geometry once for Look and manual visual review."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SlideMeasurement:
    slide_id: str
    overflow: bool
    overflow_reason: str
    small_text: Sequence[Mapping[str, Any]]
    designed_overflow: Sequence[Mapping[str, Any]]
    fallback: str
    missing_images: bool
    scene_issue: str
    overlaps: bool
    missing_glyphs: bool


def measure_slides(
    snapshot: Mapping[str, Any], *, requested_layouts: Sequence[str] = (),
) -> list[SlideMeasurement]:
    measured = []
    for index, slide in enumerate(snapshot.get("slides", [])):
        position = slide.get("index", index)
        requested = requested_layouts[position] if 0 <= position < len(requested_layouts) else ""
        measured.append(SlideMeasurement(
            slide_id=slide.get("id") or f"slide-{position + 1}",
            overflow=bool(slide.get("overflow")),
            overflow_reason=slide.get("overflowWhy") or "",
            small_text=slide.get("smallText") or (),
            designed_overflow=(slide.get("designed") or {}).get("overflow") or (),
            fallback=requested if requested != "text" and slide.get("drawn") == "text" else "",
            missing_images=bool(slide.get("assetFailures")) or any(
                not image["loaded"] for image in slide.get("images", [])
            ),
            scene_issue=slide.get("sceneIssue") or "",
            overlaps=bool(slide.get("overlaps")),
            missing_glyphs=any("LastResort" in font for font in slide.get("resolvedFonts", [])),
        ))
    return measured
