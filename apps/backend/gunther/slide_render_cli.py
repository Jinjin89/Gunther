"""Packaged-helper capture smoke check. Explicit fixture input, no database or model calls."""

from __future__ import annotations

import argparse
import json
import mimetypes
from pathlib import Path

from gunther.outputs import BuiltOutput
from gunther.schemas import ConversationCitationOut
from gunther.slide_render import SlideRenderer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--assets", type=Path)
    args = parser.parse_args(argv)
    data = json.loads(args.input.read_text())

    def asset(identity):
        if not args.assets:
            raise LookupError("No fixture assets supplied.")
        root = args.assets.resolve()
        matches = [p for p in root.glob(identity + ".*") if p.is_file()]
        if len(matches) != 1 or not matches[0].resolve().is_relative_to(root):
            raise LookupError("Fixture image unavailable.")
        path = matches[0]
        return path, mimetypes.guess_type(path.name)[0] or "application/octet-stream", path.name

    built = BuiltOutput(
        data["title"],
        data["content"],
        data["outline"],
        [ConversationCitationOut.model_validate(c) for c in data["citations"]],
        [],
        None,
        None,
    )  # No model is contacted by capture.
    snapshot = SlideRenderer(args.cache, asset).capture(built, data.get("use", "talk"))
    print(
        json.dumps(
            {
                "id": snapshot["id"],
                "candidateHash": snapshot["candidateHash"],
                "rendererHash": snapshot["rendererHash"],
                "browser": snapshot["browser"],
                "distribution": snapshot["distribution"],
                "elapsedSeconds": snapshot["elapsedSeconds"],
            }
        )
    )


if __name__ == "__main__":
    main()
