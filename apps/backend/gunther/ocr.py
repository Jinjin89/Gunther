from __future__ import annotations

import csv
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore

MAX_OCR_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_OCR_RASTER_BYTES = 64 * 1024 * 1024
MAX_OCR_REGIONS = 20_000
OCR_TIMEOUT_SECONDS = 30
_OCR_PROCESSES = BoundedSemaphore(2)


class OcrError(RuntimeError):
    """An OCR attempt failed without making the immutable original unusable."""


class OcrUnavailableError(OcrError):
    pass


class OcrLimitError(OcrError):
    pass


@dataclass(frozen=True)
class OcrRegion:
    page_number: int
    left_ppm: int
    top_ppm: int
    width_ppm: int
    height_ppm: int
    text: str
    confidence: float | None = None


@dataclass(frozen=True)
class OcrPage:
    page_number: int
    regions: tuple[OcrRegion, ...]
    provider_name: str


class OcrProvider(ABC):
    """Offline OCR boundary. Providers never mutate the captured original."""

    name: str

    @property
    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @property
    def unavailable_reason(self) -> str | None:
        return None if self.available else "The local OCR provider is unavailable."

    @abstractmethod
    def recognize_image(
        self,
        path: Path,
        *,
        page_number: int = 1,
        timeout_seconds: int = OCR_TIMEOUT_SECONDS,
    ) -> OcrPage:
        raise NotImplementedError

    @abstractmethod
    def recognize_pdf_page(
        self,
        path: Path,
        *,
        page_number: int,
        timeout_seconds: int = OCR_TIMEOUT_SECONDS,
    ) -> OcrPage:
        raise NotImplementedError


class UnavailableOcrProvider(OcrProvider):
    name = "none"

    def __init__(self, reason: str = "No supported local OCR engine was found.") -> None:
        self._reason = reason

    @property
    def available(self) -> bool:
        return False

    @property
    def unavailable_reason(self) -> str:
        return self._reason

    def recognize_image(
        self,
        path: Path,
        *,
        page_number: int = 1,
        timeout_seconds: int = OCR_TIMEOUT_SECONDS,
    ) -> OcrPage:
        del path, page_number, timeout_seconds
        raise OcrUnavailableError(self._reason)

    def recognize_pdf_page(
        self,
        path: Path,
        *,
        page_number: int,
        timeout_seconds: int = OCR_TIMEOUT_SECONDS,
    ) -> OcrPage:
        del path, page_number, timeout_seconds
        raise OcrUnavailableError(self._reason)


def _bounded_process(
    command: list[str],
    *,
    timeout: int = OCR_TIMEOUT_SECONDS,
    environment: dict[str, str] | None = None,
) -> bytes:
    """Run a fixed argv without a shell and bound captured stdout/stderr."""

    with _OCR_PROCESSES, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=stdout,
            stderr=stderr,
            env=environment,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.wait()
            raise OcrError("Local OCR timed out.") from error
        stdout_size = stdout.tell()
        stderr_size = stderr.tell()
        if stdout_size > MAX_OCR_OUTPUT_BYTES or stderr_size > MAX_OCR_OUTPUT_BYTES:
            raise OcrLimitError("Local OCR output exceeded the safe processing limit.")
        stdout.seek(0)
        stderr.seek(0)
        output = stdout.read()
        error_output = stderr.read().decode("utf-8", errors="replace").strip()
        if process.returncode:
            detail = re.sub(r"\s+", " ", error_output)[:500]
            raise OcrError(f"Local OCR failed{f': {detail}' if detail else '.'}")
        return output


def _parse_region_output(
    output: bytes, *, page_number: int, provider_name: str = "macos_vision"
) -> OcrPage:
    regions: list[OcrRegion] = []
    decoded = output.decode("utf-8", errors="replace")
    for raw_line in decoded.splitlines():
        if len(regions) >= MAX_OCR_REGIONS:
            raise OcrLimitError("Local OCR returned too many text regions.")
        parts = raw_line.split("\t", 6)
        if len(parts) != 7:
            continue
        try:
            _reported_page, left, top, width, height = (int(value) for value in parts[:5])
            confidence = float(parts[5])
        except ValueError:
            continue
        text = re.sub(r"\s+", " ", parts[6]).strip()
        if not text:
            continue
        values = (left, top, width, height)
        if (
            any(value < 0 or value > 1_000_000 for value in values)
            or width == 0
            or height == 0
            or left + width > 1_000_000
            or top + height > 1_000_000
        ):
            continue
        regions.append(
            OcrRegion(
                page_number=page_number,
                left_ppm=left,
                top_ppm=top,
                width_ppm=width,
                height_ppm=height,
                text=text,
                confidence=max(0.0, min(1.0, confidence)),
            )
        )
    return OcrPage(
        page_number=page_number,
        regions=tuple(regions),
        provider_name=provider_name,
    )


class MacOSVisionOcrProvider(OcrProvider):
    """Apple Vision OCR through a fixed packaged native helper; no network is used."""

    name = "macos_vision"

    def __init__(
        self,
        languages: tuple[str, ...] = ("en-US", "zh-Hans"),
        executable: str | None = None,
    ) -> None:
        self.languages = languages
        frozen_root = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else None
        candidates = [
            Path(executable) if executable else None,
            frozen_root / "gunther-vision-ocr" if frozen_root else None,
            Path(__file__).with_name("bin") / "gunther-vision-ocr",
        ]
        self._executable = next(
            (candidate for candidate in candidates if candidate and candidate.is_file()),
            None,
        )
        self._frameworks_present = all(
            Path(f"/System/Library/Frameworks/{name}.framework").exists()
            for name in ("Vision", "PDFKit", "AppKit")
        )

    @property
    def available(self) -> bool:
        return (
            platform.system() == "Darwin"
            and self._executable is not None
            and self._frameworks_present
        )

    @property
    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        return "The packaged Apple Vision OCR helper is unavailable on this device."

    def _recognize(self, path: Path, page_number: int, timeout_seconds: int) -> OcrPage:
        if not self.available or self._executable is None:
            raise OcrUnavailableError(self.unavailable_reason or "Apple Vision is unavailable.")
        output = _bounded_process(
            [
                str(self._executable),
                str(path),
                str(page_number),
                ",".join(self.languages),
            ],
            timeout=timeout_seconds,
        )
        return _parse_region_output(
            output,
            page_number=max(1, page_number),
            provider_name=self.name,
        )

    def recognize_image(
        self,
        path: Path,
        *,
        page_number: int = 1,
        timeout_seconds: int = OCR_TIMEOUT_SECONDS,
    ) -> OcrPage:
        return self._recognize(
            path,
            0 if page_number == 1 else page_number,
            timeout_seconds,
        )

    def recognize_pdf_page(
        self,
        path: Path,
        *,
        page_number: int,
        timeout_seconds: int = OCR_TIMEOUT_SECONDS,
    ) -> OcrPage:
        return self._recognize(path, page_number, timeout_seconds)


def _parse_tesseract_tsv(output: bytes, *, page_number: int) -> OcrPage:
    decoded = output.decode("utf-8", errors="replace")
    rows = list(csv.DictReader(decoded.splitlines(), delimiter="\t"))
    page_width = page_height = 0
    for row in rows:
        if row.get("level") == "1":
            try:
                page_width = int(row.get("width", "0"))
                page_height = int(row.get("height", "0"))
            except ValueError:
                pass
            if page_width and page_height:
                break
    if not page_width or not page_height:
        return OcrPage(page_number=page_number, regions=(), provider_name="tesseract")

    groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in rows:
        text = re.sub(r"\s+", " ", row.get("text", "")).strip()
        if row.get("level") != "5" or not text:
            continue
        key = (row.get("block_num", "0"), row.get("par_num", "0"), row.get("line_num", "0"))
        groups.setdefault(key, []).append(row)
    regions: list[OcrRegion] = []
    for words in groups.values():
        if len(regions) >= MAX_OCR_REGIONS:
            raise OcrLimitError("Local OCR returned too many text regions.")
        try:
            left = min(int(word["left"]) for word in words)
            top = min(int(word["top"]) for word in words)
            right = max(int(word["left"]) + int(word["width"]) for word in words)
            bottom = max(int(word["top"]) + int(word["height"]) for word in words)
            confidences = [float(word["conf"]) for word in words if float(word["conf"]) >= 0]
        except (KeyError, ValueError):
            continue
        regions.append(
            OcrRegion(
                page_number=page_number,
                left_ppm=round(left * 1_000_000 / page_width),
                top_ppm=round(top * 1_000_000 / page_height),
                width_ppm=round((right - left) * 1_000_000 / page_width),
                height_ppm=round((bottom - top) * 1_000_000 / page_height),
                text=" ".join(word["text"].strip() for word in words),
                confidence=(sum(confidences) / len(confidences) / 100 if confidences else None),
            )
        )
    regions.sort(key=lambda region: (region.top_ppm, region.left_ppm))
    return OcrPage(
        page_number=page_number,
        regions=tuple(regions),
        provider_name="tesseract",
    )


class TesseractOcrProvider(OcrProvider):
    """Portable local fallback using a preinstalled Tesseract executable."""

    name = "tesseract"

    def __init__(
        self,
        command: str = "tesseract",
        pdftoppm_command: str = "pdftoppm",
        languages: str = "eng+chi_sim",
    ) -> None:
        self._command = shutil.which(command)
        self._pdftoppm = shutil.which(pdftoppm_command)
        self.languages = languages

    @property
    def available(self) -> bool:
        return self._command is not None

    @property
    def unavailable_reason(self) -> str | None:
        return None if self.available else "Tesseract was not found on this device."

    def _image(self, path: Path, page_number: int, timeout_seconds: int) -> OcrPage:
        if self._command is None:
            raise OcrUnavailableError(self.unavailable_reason or "Tesseract is unavailable.")
        output = _bounded_process(
            [self._command, str(path), "stdout", "-l", self.languages, "--psm", "6", "tsv"],
            timeout=timeout_seconds,
        )
        return _parse_tesseract_tsv(output, page_number=page_number)

    def recognize_image(
        self,
        path: Path,
        *,
        page_number: int = 1,
        timeout_seconds: int = OCR_TIMEOUT_SECONDS,
    ) -> OcrPage:
        return self._image(path, page_number, timeout_seconds)

    def recognize_pdf_page(
        self,
        path: Path,
        *,
        page_number: int,
        timeout_seconds: int = OCR_TIMEOUT_SECONDS,
    ) -> OcrPage:
        if self._pdftoppm is None:
            raise OcrUnavailableError("pdftoppm is required to OCR scanned PDF pages.")
        deadline = time.monotonic() + timeout_seconds
        with tempfile.TemporaryDirectory(prefix="gunther-pdf-ocr-") as directory:
            prefix = Path(directory) / "page"
            _bounded_process(
                [
                    self._pdftoppm,
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-singlefile",
                    "-r",
                    "144",
                    "-png",
                    str(path),
                    str(prefix),
                ],
                timeout=max(1, round(deadline - time.monotonic())),
            )
            raster = prefix.with_suffix(".png")
            if not raster.is_file():
                raise OcrError("The PDF page could not be rasterized for OCR.")
            if raster.stat().st_size > MAX_OCR_RASTER_BYTES:
                raise OcrLimitError("The OCR page raster exceeded the safe processing limit.")
            remaining = round(deadline - time.monotonic())
            if remaining < 1:
                raise OcrError("Local OCR timed out.")
            return self._image(raster, page_number, remaining)


class FallbackOcrProvider(OcrProvider):
    """Try local providers in priority order, including operation-level fallback."""

    name = "local_auto"

    def __init__(self, providers: tuple[OcrProvider, ...]) -> None:
        self.providers = providers

    @property
    def available(self) -> bool:
        return any(provider.available for provider in self.providers)

    @property
    def active_name(self) -> str | None:
        return next((provider.name for provider in self.providers if provider.available), None)

    @property
    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        reasons = [provider.unavailable_reason for provider in self.providers]
        return " ".join(reason for reason in reasons if reason) or "No local OCR engine was found."

    def _attempt(
        self,
        method: str,
        path: Path,
        page_number: int,
        timeout_seconds: int,
    ) -> OcrPage:
        failures: list[str] = []
        deadline = time.monotonic() + timeout_seconds
        for provider in self.providers:
            if not provider.available:
                continue
            try:
                operation = getattr(provider, method)
                remaining = round(deadline - time.monotonic())
                if remaining < 1:
                    break
                return operation(
                    path,
                    page_number=page_number,
                    timeout_seconds=remaining,
                )
            except OcrError as error:
                failures.append(f"{provider.name}: {error}")
        if failures:
            raise OcrError("; ".join(failures))
        raise OcrUnavailableError(self.unavailable_reason or "No local OCR engine was found.")

    def recognize_image(
        self,
        path: Path,
        *,
        page_number: int = 1,
        timeout_seconds: int = OCR_TIMEOUT_SECONDS,
    ) -> OcrPage:
        return self._attempt("recognize_image", path, page_number, timeout_seconds)

    def recognize_pdf_page(
        self,
        path: Path,
        *,
        page_number: int,
        timeout_seconds: int = OCR_TIMEOUT_SECONDS,
    ) -> OcrPage:
        return self._attempt("recognize_pdf_page", path, page_number, timeout_seconds)


def create_ocr_provider(
    mode: str,
    *,
    tesseract_command: str = "tesseract",
    pdftoppm_command: str = "pdftoppm",
    languages: str = "eng+chi_sim",
) -> OcrProvider:
    if mode == "disabled":
        return UnavailableOcrProvider("Local OCR is disabled by configuration.")
    vision = MacOSVisionOcrProvider()
    tesseract = TesseractOcrProvider(tesseract_command, pdftoppm_command, languages)
    if mode == "vision":
        return vision
    if mode == "tesseract":
        return tesseract
    return FallbackOcrProvider((vision, tesseract))
