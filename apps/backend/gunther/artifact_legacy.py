"""Saved artifact approach data and its legacy projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gunther.outputs import Pool


@dataclass(frozen=True)
class Approach:
    """What the understand step decided, and what the person sees above the outline."""

    question: str
    answer: str
    purpose: str
    structure: str
    structure_reason: str = ""
    use: str | None = None
    pages: int | None = None
    # (pool number, role, why); on a saved version the number is gone (0).
    supplements: tuple[tuple[int, str, str], ...] = ()
    gaps: tuple[str, ...] = ()
    # The supplements as saved: title, url, role, why.
    saved: tuple[dict[str, str], ...] = ()

    def text(self) -> str:
        """The approach as the later steps read it."""

        lines = [
            f"Core question: {self.question}",
            f"Core answer: {self.answer}",
            f"For whom and why: {self.purpose}",
            f"Structure: {self.structure}"
            + (f" ({self.structure_reason})" if self.structure_reason else ""),
        ]
        if self.use:
            lines.append(
                f"Use: {self.use}" + (f", about {self.pages} slides" if self.pages else "")
            )
        for ref, role, why in self.supplements:
            lines.append(f"Supplement [{ref}] ({role}): {why}")
        lines += [
            f"Supplement “{item['title']}” ({item['role']}): {item['why']}" for item in self.saved
        ]
        lines += [f"The material cannot answer: {gap}" for gap in self.gaps]
        return "\n".join(lines)

    def out(self, pool: Pool, cited_urls: set[str]) -> dict[str, Any]:
        """The approach as a version keeps it: supplements only when the text cites them."""

        by_ref = pool.by_ref()
        supplements = [
            {
                "title": by_ref[ref].title,
                "url": by_ref[ref].url or "",
                "role": role,
                "why": why,
                "source_id": by_ref[ref].payload[0].source_id,
                "origin": "web_supplement" if by_ref[ref].kind == "web" else "library_supplement",
            }
            for ref, role, why in self.supplements
            if ref in by_ref and (by_ref[ref].url or by_ref[ref].payload[0].source_id) in cited_urls
        ]
        return {
            "question": self.question,
            "answer": self.answer,
            "purpose": self.purpose,
            "structure": self.structure,
            "structure_reason": self.structure_reason,
            "pages": self.pages,
            "supplements": supplements or list(self.saved),
            "gaps": list(self.gaps),
        }

    @classmethod
    def saved_as(cls, data: dict[str, Any], use: str | None) -> Approach:
        """An approach read back from a version, for revising it."""

        return cls(
            question=str(data.get("question", "")),
            answer=str(data.get("answer", "")),
            purpose=str(data.get("purpose", "")),
            structure=str(data.get("structure", "")),
            structure_reason=str(data.get("structure_reason", "")),
            use=use,
            pages=data.get("pages"),
            gaps=tuple(str(item) for item in data.get("gaps", [])),
            saved=tuple(
                {key: str(item.get(key, "")) for key in ("title", "url", "role", "why")}
                for item in data.get("supplements", [])
                if isinstance(item, dict)
            ),
        )

    def event(self, pool: Pool) -> dict[str, Any]:
        by_ref = pool.by_ref()
        return {
            "question": self.question,
            "answer": self.answer,
            "purpose": self.purpose,
            "structure": self.structure,
            "structureReason": self.structure_reason,
            "use": self.use,
            "pages": self.pages,
            "supplements": [
                {"title": by_ref[ref].title, "url": by_ref[ref].url or "", "role": role, "why": why}
                for ref, role, why in self.supplements
                if ref in by_ref
            ],
            "gaps": list(self.gaps),
        }
