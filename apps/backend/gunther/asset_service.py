from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import struct
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PageObject, PdfReader
from pypdf import filters as pdf_filters
from pypdf.errors import PdfReadError
from pypdf.generic import ArrayObject
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from gunther.database import session_scope
from gunther.models import Asset, Source
from gunther.ocr import OcrError, OcrPage, OcrProvider, UnavailableOcrProvider
from gunther.pdf_text import reflow, vocabulary_of
from gunther.schemas import (
    AssetCaptureOut,
    AssetOut,
    AssetProcessingOut,
    CreateSourceInput,
    ImportResultOut,
    SourceKind,
    SourceSummaryOut,
)
from gunther.service import KnowledgeService
from gunther.storage_budget import StorageBudget

MAX_ASSET_BYTES = 512 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 950_000
MAX_TEXT_INPUT_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_INPUT_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 4_096
MAX_ARCHIVE_CENTRAL_DIRECTORY_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_TOTAL_EXTRACTED_BYTES = 32 * 1024 * 1024
MAX_EPUB_DOCUMENTS = 512
MAX_PDF_INPUT_BYTES = 64 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_PDF_DECOMPRESSED_STREAM_BYTES = 16 * 1024 * 1024
MAX_PDF_CONTENT_STREAMS_PER_PAGE = 128
MAX_OCR_IMAGE_INPUT_BYTES = 32 * 1024 * 1024
MAX_OCR_PDF_PAGES = 100
MAX_OCR_PDF_SECONDS = 60
_ZIP_END_RECORD = struct.Struct("<4s4H2LH")
_ZIP_END_SIGNATURE = b"PK\x05\x06"
_asset_locks_guard = Lock()
_asset_locks: dict[str, Lock] = {}
_pdf_limits_lock = Lock()


class AssetValidationError(ValueError):
    pass


class AssetTooLargeError(AssetValidationError):
    pass


class AssetNotFoundError(LookupError):
    pass


class _ExtractionLimitError(ValueError):
    pass


@dataclass(frozen=True)
class AssetTextExtraction:
    text: str
    limitation: str | None = None
    ocr_status: str = "not_needed"
    ocr_provider: str | None = None


def _asset_lock(content_hash: str) -> Lock:
    with _asset_locks_guard:
        return _asset_locks.setdefault(content_hash, Lock())


def _timestamp(value: datetime) -> str:
    return f"{value.isoformat(timespec='milliseconds')}Z"


def _safe_name(file_name: str) -> str:
    name = Path(file_name.replace("\\", "/")).name.strip()
    name = re.sub(r"[^\w.()\[\] -]+", "-", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "original.bin"
    stem = Path(name).stem[:180].strip(" .") or "original"
    suffix = Path(name).suffix[:20].lower()
    return f"{stem}{suffix}"


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            decoded = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" not in decoded:
            return decoded
    utf8_prefix = data.decode("utf-8-sig", errors="ignore")
    if utf8_prefix and "\x00" not in utf8_prefix:
        return utf8_prefix
    return data.decode("latin-1", errors="replace")


def _combine_limitations(*limitations: str | None) -> str | None:
    available = [item.strip() for item in limitations if item and item.strip()]
    return " ".join(available) or None


def _display_bytes(value: int) -> str:
    megabyte = 1024 * 1024
    return f"{value // megabyte} MB" if value >= megabyte else f"{value:,} bytes"


def _character_limited(
    text: str,
    limitation: str | None = None,
) -> tuple[str, str | None]:
    if len(text) <= MAX_EXTRACTED_CHARACTERS:
        return text, limitation
    return (
        text[:MAX_EXTRACTED_CHARACTERS],
        _combine_limitations(
            limitation,
            f"Text extraction was limited to {MAX_EXTRACTED_CHARACTERS:,} characters; "
            "the complete original remains available.",
        ),
    )


def _read_prefix(path: Path, limit: int) -> tuple[bytes, bool]:
    size = path.stat().st_size
    with path.open("rb") as source:
        return source.read(limit), size > limit


def _preflight_archive(path: Path) -> None:
    size = path.stat().st_size
    if size > MAX_ARCHIVE_INPUT_BYTES:
        raise _ExtractionLimitError(
            f"compressed document exceeds the {_display_bytes(MAX_ARCHIVE_INPUT_BYTES)} "
            "extraction limit"
        )

    tail_size = min(size, _ZIP_END_RECORD.size + 65_535)
    with path.open("rb") as source:
        source.seek(size - tail_size)
        tail = source.read(tail_size)
    record_offset = tail.rfind(_ZIP_END_SIGNATURE)
    if record_offset < 0 or len(tail) - record_offset < _ZIP_END_RECORD.size:
        raise BadZipFile("end-of-central-directory record was not found")

    (
        _signature,
        disk_number,
        central_directory_disk,
        entries_on_disk,
        entry_count,
        directory_size,
        directory_offset,
        comment_size,
    ) = _ZIP_END_RECORD.unpack_from(tail, record_offset)
    if record_offset + _ZIP_END_RECORD.size + comment_size != len(tail):
        raise BadZipFile("invalid end-of-central-directory record")
    if disk_number or central_directory_disk or entries_on_disk != entry_count:
        raise _ExtractionLimitError("multi-disk archives are not processed")
    if entry_count == 0xFFFF or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        raise _ExtractionLimitError("ZIP64 archives are not processed")
    if entry_count > MAX_ARCHIVE_MEMBERS:
        raise _ExtractionLimitError(
            f"archive contains more than {MAX_ARCHIVE_MEMBERS:,} members"
        )
    if directory_size > MAX_ARCHIVE_CENTRAL_DIRECTORY_BYTES:
        raise _ExtractionLimitError("archive directory is too large to inspect safely")
    if directory_offset + directory_size > size:
        raise BadZipFile("central directory points outside the archive")


def _read_archive_member(archive: ZipFile, name: str, remaining_budget: int) -> bytes:
    info = archive.getinfo(name)
    allowed = min(MAX_ARCHIVE_MEMBER_BYTES, remaining_budget)
    if info.is_dir():
        return b""
    if info.flag_bits & 0x1:
        raise _ExtractionLimitError("encrypted archive members are not processed")
    if info.file_size > allowed:
        raise _ExtractionLimitError(
            f"archive member exceeds the {_display_bytes(allowed)} extraction budget"
        )
    with archive.open(info) as member:
        data = member.read(allowed + 1)
    if len(data) > allowed or len(data) > info.file_size:
        raise _ExtractionLimitError("archive member expanded beyond its declared safe size")
    return data


class _ReadableHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return "\n".join(
            line.strip()
            for line in re.sub(r"[ \t]+", " ", " ".join(self.parts)).splitlines()
            if line.strip()
        )


def _html_text(data: bytes) -> str:
    parser = _ReadableHtml()
    parser.feed(_decode_text(data))
    return parser.text()


def _docx_text(path: Path) -> tuple[str, str | None]:
    _preflight_archive(path)
    with ZipFile(path) as archive:
        document = ElementTree.fromstring(
            _read_archive_member(
                archive,
                "word/document.xml",
                MAX_ARCHIVE_TOTAL_EXTRACTED_BYTES,
            )
        )
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in document.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t")).strip()
        if text:
            paragraphs.append(text)
    return _character_limited("\n\n".join(paragraphs))


def _epub_text(path: Path) -> tuple[str, str | None]:
    _preflight_archive(path)
    sections: list[str] = []
    extracted_bytes = 0
    extracted_characters = 0
    limitation: str | None = None
    with ZipFile(path) as archive:
        names = sorted(
            name
            for name in archive.namelist()
            if name.lower().endswith((".xhtml", ".html", ".htm"))
        )
        if len(names) > MAX_EPUB_DOCUMENTS:
            raise _ExtractionLimitError(
                f"EPUB contains more than {MAX_EPUB_DOCUMENTS:,} readable documents"
            )
        declared_bytes = sum(archive.getinfo(name).file_size for name in names)
        if declared_bytes > MAX_ARCHIVE_TOTAL_EXTRACTED_BYTES:
            raise _ExtractionLimitError(
                "EPUB readable documents exceed the cumulative decompression budget"
            )
        for name in names:
            data = _read_archive_member(
                archive,
                name,
                MAX_ARCHIVE_TOTAL_EXTRACTED_BYTES - extracted_bytes,
            )
            extracted_bytes += len(data)
            section = _html_text(data)
            if section:
                block = f"## {Path(name).stem}\n\n{section}"
                separator_size = 2 if sections else 0
                remaining = MAX_EXTRACTED_CHARACTERS - extracted_characters - separator_size
                if remaining <= 0:
                    limitation = (
                        f"Text extraction was limited to {MAX_EXTRACTED_CHARACTERS:,} "
                        "characters; the complete original remains available."
                    )
                    break
                if len(block) > remaining:
                    sections.append(block[:remaining])
                    limitation = (
                        f"Text extraction was limited to {MAX_EXTRACTED_CHARACTERS:,} "
                        "characters; the complete original remains available."
                    )
                    break
                sections.append(block)
                extracted_characters += separator_size + len(block)
    return "\n\n".join(sections), limitation


@contextmanager
def _limited_pdf_filters() -> Iterator[None]:
    limit_names = (
        "MAX_DECLARED_STREAM_LENGTH",
        "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH",
        "JBIG2_MAX_OUTPUT_LENGTH",
        "LZW_MAX_OUTPUT_LENGTH",
        "RUN_LENGTH_MAX_OUTPUT_LENGTH",
        "ZLIB_MAX_OUTPUT_LENGTH",
        "FLATE_MAX_BUFFER_SIZE",
    )
    with _pdf_limits_lock:
        original: dict[str, int] = {}
        for name in limit_names:
            current = getattr(pdf_filters, name, None)
            if isinstance(current, int):
                original[name] = current
                setattr(
                    pdf_filters,
                    name,
                    min(current, MAX_PDF_DECOMPRESSED_STREAM_BYTES)
                    if current > 0
                    else MAX_PDF_DECOMPRESSED_STREAM_BYTES,
                )
        try:
            yield
        finally:
            for name, value in original.items():
                setattr(pdf_filters, name, value)


def _validate_pdf_page_streams(page: PageObject) -> None:
    contents = page.get("/Contents")
    if contents is None:
        return
    resolved = contents.get_object()
    streams = list(resolved) if isinstance(resolved, ArrayObject) else [resolved]
    if len(streams) > MAX_PDF_CONTENT_STREAMS_PER_PAGE:
        raise _ExtractionLimitError(
            f"PDF page contains more than {MAX_PDF_CONTENT_STREAMS_PER_PAGE:,} content streams"
        )

    decompressed_bytes = 0
    for stream in streams:
        resolved_stream = stream.get_object()
        data = resolved_stream.get_data()
        decompressed_bytes += len(data)
        if decompressed_bytes > MAX_PDF_DECOMPRESSED_STREAM_BYTES:
            raise _ExtractionLimitError(
                "PDF page content exceeds the cumulative decompression limit"
            )


def _ocr_page_block(page: OcrPage) -> str:
    if not page.regions:
        return ""
    lines = [f"<!-- gunther:page={page.page_number} -->"]
    provider = re.sub(r"[^a-z0-9._-]+", "-", page.provider_name.casefold())[:48] or "local"
    for region in page.regions:
        confidence = (
            f" confidence={region.confidence:.3f}" if region.confidence is not None else ""
        )
        lines.append(
            "<!-- gunther:ocr-region="
            f"{region.left_ppm},{region.top_ppm},{region.width_ppm},{region.height_ppm} "
            f"unit=ppm provider={provider}{confidence} -->"
        )
        lines.append(region.text.strip())
    return "\n".join(lines)


def _pdf_text(path: Path, ocr_provider: OcrProvider) -> AssetTextExtraction:
    if path.stat().st_size > MAX_PDF_INPUT_BYTES:
        raise _ExtractionLimitError(
            f"PDF exceeds the {_display_bytes(MAX_PDF_INPUT_BYTES)} extraction limit"
        )

    pages: list[str] = []
    raw_pages: set[int] = set()  # pages of printed lines, reflowed below
    extracted_characters = 0
    failed_pages = 0
    ocr_attempted_pages = 0
    ocr_failed_pages = 0
    ocr_skipped_pages = 0
    ocr_providers: set[str] = set()
    limitations: list[str] = []
    ocr_deadline = time.monotonic() + MAX_OCR_PDF_SECONDS
    with _limited_pdf_filters():
        reader = PdfReader(path, strict=False)
        page_count = len(reader.pages)
        if page_count > MAX_PDF_PAGES:
            limitations.append(
                f"PDF extraction was limited to the first {MAX_PDF_PAGES:,} pages; "
                "the complete original remains available."
            )
        for page_number in range(1, min(page_count, MAX_PDF_PAGES) + 1):
            try:
                page = reader.pages[page_number - 1]
                _validate_pdf_page_streams(page)
                text = page.extract_text() or ""
            except Exception:
                failed_pages += 1
                continue
            if not text.strip():
                if page_number > MAX_OCR_PDF_PAGES:
                    ocr_skipped_pages += 1
                    continue
                remaining_seconds = int(ocr_deadline - time.monotonic())
                if remaining_seconds < 1:
                    ocr_skipped_pages += 1
                    continue
                ocr_attempted_pages += 1
                try:
                    ocr_page = ocr_provider.recognize_pdf_page(
                        path,
                        page_number=page_number,
                        timeout_seconds=remaining_seconds,
                    )
                    ocr_providers.add(ocr_page.provider_name)
                    block = _ocr_page_block(ocr_page)
                except OcrError as error:
                    ocr_failed_pages += 1
                    limitations.append(
                        f"OCR could not process PDF page {page_number} ({error}); "
                        "the complete original remains available."
                    )
                    continue
                if not block:
                    continue
            else:
                block = f"<!-- gunther:page={page_number} -->\n{text.strip()}"
                raw_pages.add(len(pages))
            separator_size = 2 if pages else 0
            remaining = MAX_EXTRACTED_CHARACTERS - extracted_characters - separator_size
            if remaining <= 0:
                limitations.append(
                    f"Text extraction was limited to {MAX_EXTRACTED_CHARACTERS:,} characters; "
                    "the complete original remains available."
                )
                break
            if len(block) > remaining:
                pages.append(block[:remaining])
                limitations.append(
                    f"Text extraction was limited to {MAX_EXTRACTED_CHARACTERS:,} characters; "
                    "the complete original remains available."
                )
                break
            pages.append(block)
            extracted_characters += separator_size + len(block)
    # Printed lines become paragraphs; the whole document decides hyphenation.
    vocabulary = vocabulary_of("\n".join(pages[index] for index in raw_pages))
    for index in raw_pages:
        marker, _, text = pages[index].partition("\n")
        pages[index] = f"{marker}\n{reflow(text, vocabulary)}"
    if failed_pages:
        limitations.append(
            f"Text could not be safely extracted from {failed_pages:,} PDF "
            f"{'page' if failed_pages == 1 else 'pages'}."
        )
    if ocr_skipped_pages:
        limitations.append(
            f"OCR has a {MAX_OCR_PDF_SECONDS}-second and {MAX_OCR_PDF_PAGES}-page "
            f"per-document processing budget; {ocr_skipped_pages:,} additional blank "
            f"{'page was' if ocr_skipped_pages == 1 else 'pages were'} preserved without OCR."
        )
    if ocr_attempted_pages or ocr_skipped_pages:
        ocr_status = "degraded" if ocr_failed_pages or ocr_skipped_pages else "completed"
    else:
        ocr_status = "not_needed"
    return AssetTextExtraction(
        text="\n\n".join(pages),
        limitation=_combine_limitations(*limitations),
        ocr_status=ocr_status,
        ocr_provider=",".join(sorted(ocr_providers)) or None,
    )


def _image_text(path: Path, ocr_provider: OcrProvider) -> AssetTextExtraction:
    if path.stat().st_size > MAX_OCR_IMAGE_INPUT_BYTES:
        return AssetTextExtraction(
            text="",
            limitation=(
                f"OCR was skipped because the image exceeds the "
                f"{_display_bytes(MAX_OCR_IMAGE_INPUT_BYTES)} processing limit; "
                "the complete original remains available."
            ),
            ocr_status="degraded",
        )
    try:
        page = ocr_provider.recognize_image(path, page_number=1)
    except OcrError as error:
        return AssetTextExtraction(
            text="",
            limitation=f"OCR is degraded ({error}); the complete original remains available.",
            ocr_status="degraded",
        )
    return AssetTextExtraction(
        text=_ocr_page_block(page),
        ocr_status="completed",
        ocr_provider=page.provider_name,
    )


def extract_asset_content(
    path: Path,
    file_name: str,
    media_type: str,
    ocr_provider: OcrProvider | None = None,
) -> AssetTextExtraction:
    """Extract bounded content while retaining structured OCR processing state."""

    provider = ocr_provider or UnavailableOcrProvider()
    suffix = Path(file_name).suffix.casefold()
    image_suffixes = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
    try:
        if suffix == ".pdf" or media_type == "application/pdf":
            return _pdf_text(path, provider)
        if suffix in image_suffixes or media_type.startswith("image/"):
            return _image_text(path, provider)
        if suffix == ".docx":
            text, limitation = _docx_text(path)
            return AssetTextExtraction(text, limitation)
        if suffix == ".epub":
            text, limitation = _epub_text(path)
            return AssetTextExtraction(text, limitation)
        if suffix in {".html", ".htm"} or media_type.startswith("text/html"):
            data, truncated = _read_prefix(path, MAX_TEXT_INPUT_BYTES)
            text, limitation = _character_limited(
                _html_text(data),
                (
                    f"HTML extraction was limited to the first "
                    f"{_display_bytes(MAX_TEXT_INPUT_BYTES)}; the complete original "
                    "remains available."
                    if truncated
                    else None
                ),
            )
            return AssetTextExtraction(text, limitation)
        if (
            suffix in {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml"}
            or media_type.startswith("text/")
        ):
            data, truncated = _read_prefix(path, MAX_TEXT_INPUT_BYTES)
            text, limitation = _character_limited(
                _decode_text(data),
                (
                    f"Text extraction was limited to the first "
                    f"{_display_bytes(MAX_TEXT_INPUT_BYTES)}; the complete original "
                    "remains available."
                    if truncated
                    else None
                ),
            )
            return AssetTextExtraction(text, limitation)
    except _ExtractionLimitError as error:
        return AssetTextExtraction(
            "",
            "Text extraction was skipped to protect device resources "
            f"({error}); the complete original remains available.",
        )
    except (BadZipFile, ElementTree.ParseError, KeyError, OSError, PdfReadError, ValueError):
        return AssetTextExtraction(
            "",
            "Text extraction failed safely; the complete original remains available.",
        )
    except Exception as error:
        return AssetTextExtraction(
            "",
            f"Text extraction failed safely ({type(error).__name__}); "
            "the complete original remains available.",
        )
    return AssetTextExtraction(
        "",
        "The original is preserved; this file type does not expose readable text yet.",
    )


def extract_asset_text(
    path: Path,
    file_name: str,
    media_type: str,
    ocr_provider: OcrProvider | None = None,
) -> tuple[str, str | None]:
    """Return extracted text and a user-readable limitation, if any."""
    result = extract_asset_content(path, file_name, media_type, ocr_provider)
    return result.text, result.limitation


class AssetService:
    """Stream immutable originals to disk, then create the normal reviewable Source."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        assets_dir: Path,
        knowledge_service: KnowledgeService,
        ocr_provider: OcrProvider | None = None,
        storage_budget: StorageBudget | None = None,
    ) -> None:
        self.sessions = sessions
        self.assets_dir = assets_dir
        self.knowledge_service = knowledge_service
        self.ocr_provider = ocr_provider or UnavailableOcrProvider()
        self.storage_budget = storage_budget or StorageBudget((assets_dir,))

    @staticmethod
    def _out(asset: Asset) -> AssetOut:
        return AssetOut(
            id=asset.id,
            content_hash=asset.content_hash,
            original_name=asset.original_name,
            media_type=asset.media_type,
            size_bytes=asset.size_bytes,
            download_url=f"/api/assets/{asset.id}",
            created_at=_timestamp(asset.created_at),
        )

    async def _receive(
        self,
        stream: AsyncIterator[bytes],
        *,
        expected_size: int | None = None,
    ) -> tuple[Path, str, int]:
        if expected_size is not None and expected_size > MAX_ASSET_BYTES:
            raise AssetTooLargeError("Choose a file smaller than 512 MB.")
        incoming = self.assets_dir / ".incoming"
        temporary = incoming / f"{uuid4().hex}.part"
        digest = hashlib.sha256()
        size = 0
        with self.storage_budget.reserve(
            self.assets_dir,
            expected_bytes=expected_size or 0,
        ) as reservation:
            try:
                incoming.mkdir(parents=True, exist_ok=True)
                self.storage_budget.assert_safe_path(incoming)
                with temporary.open("xb") as output:
                    async for chunk in stream:
                        if not chunk:
                            continue
                        if size + len(chunk) > MAX_ASSET_BYTES:
                            raise AssetTooLargeError("Choose a file smaller than 512 MB.")
                        reservation.prepare_write(len(chunk))
                        output.write(chunk)
                        # Make the new logical length visible to concurrent quota scans
                        # before converting this reservation into managed file usage.
                        output.flush()
                        reservation.consume(len(chunk))
                        size += len(chunk)
                        digest.update(chunk)
                    os.fsync(output.fileno())
                if not size:
                    temporary.unlink(missing_ok=True)
                    raise AssetValidationError("The selected file is empty.")
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        return temporary, digest.hexdigest(), size

    def _preserve(
        self,
        temporary: Path,
        content_hash: str,
        size: int,
        file_name: str,
        media_type: str,
    ) -> Asset:
        # The bytes already count toward managed usage in ``.incoming``. A
        # zero-growth reservation revalidates external changes and translates a
        # late ENOSPC/EDQUOT while publishing directories or renaming the file.
        with self.storage_budget.reserve(self.assets_dir):
            return self._preserve_received(
                temporary,
                content_hash,
                size,
                file_name,
                media_type,
            )

    def _preserve_received(
        self,
        temporary: Path,
        content_hash: str,
        size: int,
        file_name: str,
        media_type: str,
    ) -> Asset:
        safe_name = _safe_name(file_name)
        with _asset_lock(content_hash):
            with session_scope(self.sessions) as session:
                existing = session.scalar(select(Asset).where(Asset.content_hash == content_hash))
                if existing:
                    existing_path = self.assets_dir / existing.relative_path
                    existing_path.parent.mkdir(parents=True, exist_ok=True)
                    self.storage_budget.assert_safe_path(existing_path)
                    # The received copy already passed the content hash. Replacing is
                    # atomic and repairs an interrupted or externally damaged original.
                    os.replace(temporary, existing_path)
                    return existing

            relative_path = str(Path(content_hash[:2]) / content_hash / safe_name)
            final_path = self.assets_dir / relative_path
            final_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_budget.assert_safe_path(final_path)
            os.replace(temporary, final_path)
            asset = Asset(
                id=f"ast_{uuid4().hex}",
                content_hash=content_hash,
                original_name=safe_name,
                media_type=media_type[:160] or "application/octet-stream",
                size_bytes=size,
                relative_path=relative_path,
            )
            try:
                with session_scope(self.sessions) as session:
                    session.add(asset)
                    session.flush()
            except IntegrityError:
                try:
                    with session_scope(self.sessions) as session:
                        existing = session.scalar(
                            select(Asset).where(Asset.content_hash == content_hash)
                        )
                except Exception:
                    final_path.unlink(missing_ok=True)
                    raise
                if existing is None:
                    final_path.unlink(missing_ok=True)
                    raise
                if existing.relative_path != relative_path:
                    final_path.unlink(missing_ok=True)
                asset = existing
            except Exception:
                # The file is published before the DB row so readers never observe a
                # row pointing at absent bytes. If the transaction fails, undo that
                # publish to avoid an untracked managed file consuming quota forever.
                final_path.unlink(missing_ok=True)
                raise
            return asset

    async def preserve_bytes(
        self,
        data: bytes,
        *,
        file_name: str,
        media_type: str,
    ) -> Asset:
        """Preserve an already bounded response body through the normal Asset path."""

        async def stream() -> AsyncIterator[bytes]:
            yield data

        temporary, content_hash, size = await self._receive(
            stream(),
            expected_size=len(data),
        )
        try:
            return self._preserve(
                temporary,
                content_hash,
                size,
                file_name,
                media_type,
            )
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _source_content(
        asset: Asset,
        extracted: str,
        limitation: str | None,
        notes: str,
        ocr_status: str = "not_needed",
        ocr_provider: str | None = None,
    ) -> str:
        metadata = (
            "# Original file\n\n"
            f"File: {asset.original_name}\n"
            f"Media type: {asset.media_type}\n"
            f"Size: {asset.size_bytes} bytes\n"
            f"SHA-256: {asset.content_hash}"
        )
        sections = [metadata]
        if notes.strip():
            sections.append(f"## Your context\n\n{notes.strip()}")
        if ocr_status != "not_needed":
            ocr_lines = [f"OCR status: {ocr_status}"]
            if ocr_provider:
                ocr_lines.append(f"OCR provider: {ocr_provider}")
            sections.append("## OCR provenance\n\n" + "\n".join(ocr_lines))
        if limitation:
            sections.append(f"## Processing note\n\n{limitation}")
        if extracted.strip():
            sections.append(f"## Extracted content\n\n{extracted.strip()}")
        return "\n\n".join(sections)[:1_000_000]

    async def capture(
        self,
        stream: AsyncIterator[bytes],
        *,
        title: str,
        kind: SourceKind,
        file_name: str,
        media_type: str,
        knowledge_base_id: str | None,
        notes: str,
        expected_size: int | None = None,
        defer_processing: bool = False,
    ) -> AssetCaptureOut:
        temporary, content_hash, size = await self._receive(
            stream,
            expected_size=expected_size,
        )
        try:
            asset = self._preserve(
                temporary,
                content_hash,
                size,
                file_name,
                media_type,
            )
        finally:
            temporary.unlink(missing_ok=True)

        stored_path = self.assets_dir / asset.relative_path
        if defer_processing:
            from gunther.knowledge_index import enqueue

            def initialize(session: Session, source: Source) -> None:
                source.asset_id = asset.id
                enqueue(session, source.id, "parse_asset", f"parse_asset:{source.id}:v1")

            imported = self.knowledge_service.import_source(
                CreateSourceInput(
                    title=title, kind=kind, knowledge_base_id=knowledge_base_id,
                    content=self._source_content(asset, "", None, notes),
                ),
                initialize_source=initialize, defer_processing=True,
            )
            return AssetCaptureOut(
                asset=self._out(asset), import_result=imported,
                processing=AssetProcessingOut(note="Original saved. Background processing queued."),
            )
        extraction = await asyncio.to_thread(
            extract_asset_content,
            stored_path,
            asset.original_name,
            asset.media_type,
            self.ocr_provider,
        )
        source_content = self._source_content(
            asset,
            extraction.text[:MAX_EXTRACTED_CHARACTERS],
            extraction.limitation,
            notes,
            extraction.ocr_status,
            extraction.ocr_provider,
        )
        imported = self.knowledge_service.import_source(
            CreateSourceInput(
                title=title,
                kind=kind,
                content=source_content,
                knowledge_base_id=knowledge_base_id,
            )
        )
        with session_scope(self.sessions) as session:
            source = session.get(Source, imported.source.id)
            if source is None:
                raise RuntimeError("The captured source disappeared before its asset was linked.")
            source.asset_id = asset.id
        detail = self.knowledge_service.get_source(imported.source.id)
        summary = SourceSummaryOut.model_validate(detail.model_dump())
        return AssetCaptureOut(
            asset=self._out(asset),
            processing=AssetProcessingOut(
                ocr_status=extraction.ocr_status,
                ocr_provider=extraction.ocr_provider,
                note=extraction.limitation,
            ),
            import_result=ImportResultOut(
                source=summary,
                created=imported.created,
                extraction_mode=imported.extraction_mode,
                duplicate=imported.duplicate,
            ),
        )

    def download(self, asset_id: str) -> tuple[Path, str, str]:
        if not re.fullmatch(r"ast_[a-f0-9]{32}", asset_id):
            raise AssetNotFoundError("Asset not found")
        with session_scope(self.sessions) as session:
            asset = session.get(Asset, asset_id)
            if asset is None:
                raise AssetNotFoundError("Asset not found")
            root = self.assets_dir.resolve()
            path = (root / asset.relative_path).resolve()
            if root not in path.parents or not path.is_file():
                raise AssetNotFoundError("The original file is unavailable")
            if path.stat().st_size != asset.size_bytes:
                raise AssetNotFoundError("The original file failed its integrity check")
            digest = hashlib.sha256()
            with path.open("rb") as original:
                while chunk := original.read(1024 * 1024):
                    digest.update(chunk)
            if not secrets.compare_digest(digest.hexdigest(), asset.content_hash):
                raise AssetNotFoundError("The original file failed its integrity check")
            return path, asset.media_type, asset.original_name
