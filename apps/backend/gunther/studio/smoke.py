"""Packaging smoke: `--studio-smoke` imports every studio module and checks a fixture deck
with the format checker, no model involved. Exit 0 means the frozen helper carries the studio."""

from __future__ import annotations

import importlib
import json
import sys

MODULES = ("format", "prompts", "reference", "pipeline", "work")
FIXTURE = """<!-- layout: title -->
# Orders grew 12% while returns fell

Note: opening.

---

<!-- layout: statement -->
# Returns fell because packaging changed

Note: why.
"""


def main(argv: list[str] | None = None) -> int:
    loaded = [importlib.import_module(f"gunther.studio.{name}").__name__ for name in MODULES]
    from gunther.studio import format as fmt

    slides = fmt.split_slides(FIXTURE)
    parsed = [fmt.parse_slide(text) for text in slides]
    checked = fmt.check_deck(FIXTURE)
    from gunther.studio.reference import DECK_FORMAT, REPORT_FORMAT

    ok = (
        len(slides) == 2
        and all(p.title for p in parsed)
        and bool(DECK_FORMAT)
        and bool(REPORT_FORMAT)
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "modules": loaded,
                "slides": len(slides),
                "layouts": [p.layout for p in parsed],
                "problems": len(checked),
            }
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
