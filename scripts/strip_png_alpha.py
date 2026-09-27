"""Remove an effectively opaque alpha channel from explicit PNG files.

The tool is deliberately write-protected. It preserves every RGB sample,
rejects genuinely translucent images, verifies the rewritten PNG has exactly
three color bands, and publishes each result with an atomic same-directory
replace.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
from pathlib import Path

from PIL import Image

MAX_FILE_BYTES = 32 * 1024 * 1024
MIN_EFFECTIVELY_OPAQUE_ALPHA = 254


class AlphaRemovalError(RuntimeError):
    """Raised when a file cannot be rewritten without changing its design."""


def _rgb_digest(image: Image.Image) -> str:
    return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


def strip_effectively_opaque_alpha(path: Path) -> dict[str, object]:
    path = path.expanduser().absolute()
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise AlphaRemovalError(f"not a regular file: {path}")
    if path.suffix.lower() != ".png":
        raise AlphaRemovalError(f"not a PNG file: {path}")
    if metadata.st_size > MAX_FILE_BYTES:
        raise AlphaRemovalError(f"PNG exceeds {MAX_FILE_BYTES} bytes: {path}")

    with Image.open(path) as source:
        source.load()
        if source.format != "PNG":
            raise AlphaRemovalError(f"file contents are not PNG: {path}")
        bands = source.getbands()
        if "A" not in bands:
            return {
                "path": str(path),
                "status": "already-rgb",
                "width": source.width,
                "height": source.height,
            }
        alpha = source.getchannel("A")
        alpha_minimum, alpha_maximum = alpha.getextrema()
        if alpha_minimum < MIN_EFFECTIVELY_OPAQUE_ALPHA:
            raise AlphaRemovalError(
                f"PNG contains designed transparency (alpha minimum {alpha_minimum}): {path}"
            )
        original_rgb_digest = _rgb_digest(source)
        converted = source.convert("RGB")
        icc_profile = source.info.get("icc_profile")

    temporary = path.with_name(f".{path.name}.{os.getpid()}.rgb.tmp")
    try:
        converted.save(
            temporary,
            format="PNG",
            optimize=True,
            **({"icc_profile": icc_profile} if icc_profile else {}),
        )
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        with Image.open(temporary) as rewritten:
            rewritten.load()
            if rewritten.getbands() != ("R", "G", "B"):
                raise AlphaRemovalError(f"rewritten PNG still contains alpha: {path}")
            if rewritten.size != converted.size:
                raise AlphaRemovalError(f"rewritten PNG dimensions changed: {path}")
            if _rgb_digest(rewritten) != original_rgb_digest:
                raise AlphaRemovalError(f"rewritten PNG changed RGB samples: {path}")
        os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "path": str(path),
        "status": "converted",
        "width": converted.width,
        "height": converted.height,
        "alphaMinimumBefore": alpha_minimum,
        "alphaMaximumBefore": alpha_maximum,
        "rgbSha256": original_rgb_digest,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="confirm that the listed PNG files may be atomically rewritten",
    )
    args = parser.parse_args()
    if not args.allow_write:
        parser.error("--allow-write is required")

    try:
        results = [strip_effectively_opaque_alpha(path) for path in args.files]
    except (AlphaRemovalError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for result in results:
        print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
