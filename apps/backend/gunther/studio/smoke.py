"""Import Studio and check a supplied Markdown deck without a model."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

MODULES = ("format", "prompts", "reference", "pipeline", "work")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gunther-backend --studio-smoke", description=__doc__)
    parser.add_argument("deck", type=Path, help="Markdown deck to check")
    args = parser.parse_args(argv)
    try:
        content = args.deck.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        parser.error(str(error))

    loaded = [importlib.import_module(f"gunther.studio.{name}").__name__ for name in MODULES]
    from gunther.studio import format as fmt
    from gunther.studio.reference import DECK_FORMAT, report_format

    slides = fmt.split_slides(content)
    parsed = [fmt.parse_slide(text) for text in slides]
    checked = fmt.check_deck(content)
    ok = not checked and bool(DECK_FORMAT) and bool(report_format(summary_box=True))
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
