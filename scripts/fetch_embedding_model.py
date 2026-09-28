"""Fetch the semantic search model into apps/backend/models/ (once per checkout).

multilingual-e5-small (MIT licence), the int8-quantized ONNX export from
Xenova/multilingual-e5-small at a pinned revision. Every file is checked against
its SHA-256 before it is moved into place, so a changed or truncated download
never reaches the app. The desktop build bundles the same folder.

    npm run models:fetch
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "apps" / "backend" / "models" / "multilingual-e5-small"
REPOSITORY = "Xenova/multilingual-e5-small"
REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
# saved name: (path in the repository, SHA-256)
FILES = {
    "model.onnx": (
        "onnx/model_quantized.onnx",
        "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193",
    ),
    "tokenizer.json": (
        "tokenizer.json",
        "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
    ),
}
LICENSE = """multilingual-e5-small
https://huggingface.co/intfloat/multilingual-e5-small (MIT License)
ONNX export: https://huggingface.co/{repository}/tree/{revision}
Wang et al., "Multilingual E5 Text Embeddings: A Technical Report", 2024.
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name, (remote, expected) in FILES.items():
        target = destination / name
        if target.is_file() and sha256(target) == expected:
            print(f"{name}: already present")
            continue
        url = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{remote}"
        partial = target.with_name(f"{name}.part")
        print(f"{name}: downloading {url}")
        with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
        actual = sha256(partial)
        if actual != expected:
            partial.unlink()
            raise SystemExit(f"{name}: checksum mismatch ({actual}); nothing was installed")
        partial.replace(target)
    (destination / "LICENSE.txt").write_text(
        LICENSE.format(repository=REPOSITORY, revision=REVISION), encoding="utf-8"
    )
    print(f"Model ready in {destination}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--destination", type=Path, default=DESTINATION)
    fetch(parser.parse_args(argv).destination)
    return 0


if __name__ == "__main__":
    sys.exit(main())
