"""Fetch the voice activity model into apps/backend/models/ (once per checkout).

Silero VAD (MIT licence), the ONNX file at a pinned release tag. It finds the
pauses where live text for SenseVoice and OpenAI-style servers is cut. The file is
checked against its SHA-256 before it is moved into place, so a changed or
truncated download never reaches the app. The desktop build bundles the same folder.

    npm run models:fetch
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "apps" / "backend" / "models" / "silero-vad"
RELEASE = "v6.2.3"
URL = f"https://raw.githubusercontent.com/snakers4/silero-vad/{RELEASE}/src/silero_vad/data/silero_vad.onnx"
NAME = "silero_vad.onnx"
SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
SIZE = 2_327_524
LICENSE = """Silero VAD
https://github.com/snakers4/silero-vad (MIT License), release {release}
Copyright (c) 2020-present Silero Team
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / NAME
    if target.is_file() and sha256(target) == SHA256:
        print(f"{NAME}: already present")
    else:
        partial = target.with_name(f"{NAME}.part")
        print(f"{NAME}: downloading {URL}")
        with urllib.request.urlopen(URL, timeout=60) as response, partial.open("wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
        actual = sha256(partial)
        if actual != SHA256 or partial.stat().st_size != SIZE:
            partial.unlink()
            raise SystemExit(f"{NAME}: checksum mismatch ({actual}); nothing was installed")
        partial.replace(target)
    (destination / "LICENSE.txt").write_text(LICENSE.format(release=RELEASE), encoding="utf-8")
    print(f"Model ready in {destination}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--destination", type=Path, default=DESTINATION)
    fetch(parser.parse_args(argv).destination)
    return 0


if __name__ == "__main__":
    sys.exit(main())
