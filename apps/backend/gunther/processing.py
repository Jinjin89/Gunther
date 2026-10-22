"""Leased, restart-safe processing. Publication and job completion commit together.

Summaries and assertion extraction run in a lane of their own, so a slow model
never holds up reading and indexing what was just captured.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import replace
from datetime import timedelta
from difflib import SequenceMatcher
from pathlib import Path
from threading import Event

from sqlalchemy import and_, or_, select, update

from gunther import digest, papers, vector_index
from gunther.asset_service import AssetService, _decode_text, _read_prefix, extract_asset_content
from gunther.audio_decode import audio_duration_ms
from gunther.database import session_scope
from gunther.document_parser import DoclingParser
from gunther.document_structure import organize_document, pdf_metadata
from gunther.documents import ParsedDocument, text_document
from gunther.knowledge_index import ASSET_PARSER, KnowledgeIndex, embeddable, new_id
from gunther.models import (
    Asset,
    BlockEmbedding,
    ContentBlock,
    ProcessingJob,
    RecordingSession,
    Source,
    SourceIndexHead,
    SourceRevision,
    utc_now,
)
from gunther.portable_documents import archive_document, html_document, pdf_raster_images
from gunther.recording_service import RecordingService

logger = logging.getLogger(__name__)

IDLE_POLL_SECONDS = 1.0
POLL_STEP_SECONDS = 0.5
MAX_RETRY_SECONDS = 60.0
FAILURE_LOG_EVERY = 30
# Jobs that may wait on a model over the network.
AI_KINDS = ("digest", "extract_assertions")


def retry_delay_seconds(failures: int) -> float:
    """Exponential backoff for an unavailable worker: 2, 4, 8 … capped at a minute."""
    return min(MAX_RETRY_SECONDS, 2.0 * 2 ** max(0, min(failures - 1, 6)))


def reconcile_fallback(structured: ParsedDocument, fallback: ParsedDocument) -> list:
    """Recover missing text even on partially covered pages; never invent geometry."""

    def normalize(text: str) -> tuple[str, list[int]]:
        chars, offsets = [], []
        for index, char in enumerate(text):
            if char.isspace():
                continue
            for folded in char.casefold():
                chars.append(folded)
                offsets.append(index)
        return "".join(chars), offsets

    pages: dict[object, str] = {}
    for element in structured.elements:
        if element.kind != "figure":
            page = element.anchor.get("page")
            pages[page] = pages.get(page, "") + element.text
    normalized = {page: normalize(text)[0] for page, text in pages.items()}
    recovered = []
    layout_pages = {
        span.get("page")
        for element in structured.elements
        if element.kind not in {"page_header", "page_footer", "metadata"}
        for span in element.anchor.get("spans", [{"page": element.anchor.get("page")}])
    }
    for element in fallback.elements:
        # Native text has no geometry. Appending unmatched strings on a recognized
        # layout page reintroduces intentionally separated headers and chart labels.
        if (
            structured.manifest.get("layout") == "structured"
            and element.anchor.get("page") in layout_pages
        ):
            continue
        text, offsets = normalize(element.text)
        held = normalized.get(element.anchor.get("page"), "")
        if not text or text in held:
            continue
        # Bound alignment cost, and only use long matches to remove duplicated text.
        matches = (
            SequenceMatcher(None, text, held, autojunk=False).get_matching_blocks()
            if len(text) <= 10_000 and len(held) <= 50_000
            else []
        )
        substantial = [match for match in matches if match.size >= 20]
        if sum(match.size for match in substantial) / len(text) >= 0.92:
            continue
        spans = []
        start = 0
        for match in substantial:
            if match.a - start >= 24:
                spans.append((start, match.a))
            start = match.a + match.size
        if len(text) - start >= 24:
            spans.append((start, len(text)))
        if not substantial:
            spans = [(0, len(text))]
        for start, stop in spans:
            left, right = offsets[start], offsets[stop - 1] + 1
            anchor = {**element.anchor, "fallbackCharStart": left, "fallbackCharEnd": right}
            if isinstance(anchor.get("charStart"), int):
                anchor.update(
                    charStart=anchor["charStart"] + left, charEnd=anchor["charStart"] + right
                )
            recovered.append(
                replace(
                    element,
                    text=element.text[left:right],
                    parent=None,
                    anchor=anchor,
                    payload={**element.payload, "extraction": "fallback-gap"},
                )
            )
    if recovered:
        structured.manifest.update(
            fallbackElements=len(recovered),
            fallbackOrder="page; position within partial pages uncertain",
        )
    return recovered


class ProcessingWorker:
    def __init__(
        self,
        index: KnowledgeIndex,
        assets: AssetService,
        document_parser: DoclingParser | None = None,
        digest_writer: digest.DigestWriter | None = None,
        recordings_dir: Path | None = None,
    ):
        self.index = index
        self.sessions = index.sessions
        self.assets = assets
        self.document_parser = document_parser
        self.digest_writer = digest_writer
        self.recordings_dir = recordings_dir
        self.stopping = Event()

    def claim(self, ai: bool | None = None) -> tuple[str, str] | None:
        """The oldest eligible job: any job, or only (``ai``) or never model-bound ones."""

        now = utc_now()
        with session_scope(self.sessions) as session:
            eligible = or_(
                and_(ProcessingJob.state == "queued", ProcessingJob.available_at <= now),
                and_(ProcessingJob.state == "running", ProcessingJob.lease_until < now),
            )
            if ai is not None:
                in_lane = ProcessingJob.kind.in_(AI_KINDS)
                eligible = and_(eligible, in_lane if ai else ~in_lane)
            candidate = (
                select(ProcessingJob.id)
                .where(eligible, ProcessingJob.attempts < 3)
                .order_by(ProcessingJob.created_at)
                .limit(1)
                .scalar_subquery()
            )
            token = new_id("lease")
            claimed = session.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == candidate, eligible)
                .values(
                    state="running",
                    attempts=ProcessingJob.attempts + 1,
                    lease_token=token,
                    lease_until=now + timedelta(seconds=120),
                    updated_at=now,
                    error=None,
                )
                .returning(ProcessingJob.id)
            ).scalar_one_or_none()
            # Exhausted jobs from repeated process crashes must become visible failures.
            session.execute(
                update(ProcessingJob)
                .where(
                    ProcessingJob.state == "running",
                    ProcessingJob.lease_until < now,
                    ProcessingJob.attempts >= 3,
                )
                .values(
                    state="failed", error="Processing was interrupted repeatedly. Retry to resume."
                )
            )
        self.assets.release_abandoned()
        return (claimed, token) if claimed else None

    def heartbeat(self, job_id: str, token: str) -> None:
        with session_scope(self.sessions) as session:
            session.execute(
                update(ProcessingJob)
                .where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.lease_token == token,
                    ProcessingJob.state == "running",
                )
                .values(lease_until=utc_now() + timedelta(seconds=120), updated_at=utc_now())
            )

    def process(self, job_id: str, token: str) -> None:
        try:
            with session_scope(self.sessions) as session:
                job = session.get(ProcessingJob, job_id)
                source = session.get(Source, job.source_id)
                kind, source_id = job.kind, source.id
                asset = session.get(Asset, source.asset_id) if source.asset_id else None
                if kind == "parse_asset":
                    if not asset:
                        raise ValueError("Original file is unavailable")
                    path, _, _ = self.assets.download(asset.id)
                    file_name, media_type = asset.original_name, asset.media_type
                elif kind == "digest":
                    image_path = (
                        self.assets.download(asset.id)[0]
                        if asset and asset.media_type in digest.VISION_TYPES
                        else None
                    )
                    request = digest.prepare(
                        session, source, job.revision_id, asset_path=image_path
                    )
                elif kind == "extract_assertions":
                    source_title, source_content = source.title, source.content
                    source_hash = source.content_hash
                elif kind == "measure_recording":
                    recording_id = job.dedupe_key.split(":")[1]
                    recording = session.get(RecordingSession, recording_id)
                    if recording is None or recording.status != "completed":
                        raise ValueError("Completed recording is unavailable")
                    recording_file, recording_size = recording.file_name, recording.byte_size
                elif kind == "summarize":
                    revision_id = job.revision_id
                    model_id = job.model_id
                    source_title = source.title
                    file_name = asset.original_name if asset else None
                    pdf_path = (
                        self.assets.download(asset.id)[0]
                        if asset and asset.media_type == "application/pdf"
                        else None
                    )
                    blocks = list(
                        session.scalars(
                            select(ContentBlock)
                            .where(ContentBlock.revision_id == revision_id)
                            .order_by(ContentBlock.ordinal)
                        )
                    )
                else:
                    head = session.get(SourceIndexHead, source_id)
                    revision_id = job.revision_id or (head.revision_id if head else None)
                    model_id = job.model_id
                    blocks = list(
                        session.scalars(
                            select(ContentBlock)
                            .where(ContentBlock.revision_id == revision_id, *embeddable())
                            .order_by(ContentBlock.ordinal)
                        )
                    )
            if kind == "parse_asset":
                extracted = extract_asset_content(
                    path, file_name, media_type, self.assets.ocr_provider
                )
                # Metadata and user context are retained, but not indexed as file evidence.
                document = text_document(extracted.text)
                warning = extracted.limitation
                parser = ASSET_PARSER
                suffix = Path(file_name).suffix.lower()
                if suffix in {".docx", ".epub", ".html", ".htm"}:
                    try:
                        document = (
                            archive_document(path, suffix)
                            if suffix in {".docx", ".epub"}
                            else html_document(_decode_text(_read_prefix(path, 8 * 1024 * 1024)[0]))
                        )
                    except Exception as error:
                        warning = (
                            (warning or "")
                            + f" Structured import unavailable ({type(error).__name__}); "
                            "text retained."
                        )
                if self.document_parser and media_type == "application/pdf":
                    try:
                        result = (
                            self.document_parser.parse(path, stopping=self.stopping.is_set)
                            if isinstance(self.document_parser, DoclingParser)
                            else self.document_parser.parse(path)
                        )
                        structured = (
                            result if isinstance(result, ParsedDocument) else ParsedDocument(result)
                        )
                        if not any(
                            b.kind not in {"heading", "figure"} for b in structured.elements
                        ):
                            raise ValueError("No structured document content")
                        extras = reconcile_fallback(structured, document)
                        for block in extras:
                            block.parent = None
                        # Retain page order and rebase parents after inserting fallback pages.
                        indexed = list(enumerate(structured.elements + extras))
                        if extras:
                            indexed.sort(
                                key=lambda pair: (pair[1].anchor.get("page") or 0, pair[0])
                            )
                        positions = {old: new for new, (old, _) in enumerate(indexed)}
                        for new, (_, block) in enumerate(indexed):
                            parent = (
                                positions.get(block.parent) if block.parent is not None else None
                            )
                            block.parent = parent if parent is not None and parent < new else None
                        structured.elements = [block for _, block in indexed]
                        document = structured
                        parser = self.document_parser.version
                        limitations = []
                        if structured.manifest.get("conversionStatus") not in {
                            None,
                            "unknown",
                            "success",
                        }:
                            limitations.append("Some PDF regions could not be parsed.")
                        if structured.manifest.get("missingImages"):
                            limitations.append("Some figure images could not be rendered.")
                        if structured.manifest.get("metadataWarning"):
                            limitations.append(str(structured.manifest["metadataWarning"]))
                        if extras:
                            limitations.append(
                                "Missing pages were retained using text extraction; "
                                "their layout is uncertain."
                            )
                        warning = " ".join(limitations) or None
                    except Exception as error:
                        warning = (warning or "") + (
                            f" Structured parsing unavailable ({type(error).__name__}); "
                            "using local text/OCR extraction."
                        )
                if media_type == "application/pdf" and document.manifest.get("method") != "docling":
                    try:
                        pdf_raster_images(path, document)
                    except Exception as error:
                        document.manifest["images"] = "partial"
                        warning = (
                            warning or ""
                        ) + f" Some images could not be extracted ({type(error).__name__})."
                    warning = (warning or "") + (
                        " Layout parsing is unavailable; heading hierarchy, vector figures "
                        "and table structure may be incomplete. The original PDF is retained."
                    )
                    try:
                        organize_document(document, pdf_metadata(path))
                    except Exception:
                        organize_document(document)
                else:
                    organize_document(document)
                for image in document.assets:
                    retained = asyncio.run(
                        self.assets.preserve_bytes(
                            image.data,
                            file_name=image.name,
                            media_type=image.media_type,
                            staging=(job_id, token),
                        )
                    )
                    for element in document.elements:
                        if element.payload.get("imageKey") == image.key:
                            element.payload["imageAssetId"] = retained.id
                document.manifest.update(parser=parser, warning=warning)
                parsed = document.chunks()
                vectors = []
            elif kind == "embed" and self.index.embedder:
                if model_id != self.index.embedder.model_id:
                    raise ValueError("Embedding model changed; job must retain its model identity")
                vectors = []
                for offset in range(0, len(blocks), 16):
                    if self.stopping.is_set():
                        raise RuntimeError("Worker is stopping")
                    batch = blocks[offset : offset + 16]
                    encoded = self.index.embedder.encode(
                        [
                            " / ".join(json.loads(b.heading_path_json)) + "\n" + b.content
                            for b in batch
                        ]
                    )
                    if len(encoded) != len(batch):
                        raise ValueError("Embedding provider returned an invalid batch")
                    for block, vector in zip(batch, encoded, strict=True):
                        if (
                            not vector
                            or len(vector) > 4096
                            or not all(math.isfinite(v) for v in vector)
                        ):
                            raise ValueError("Embedding provider returned an invalid vector")
                        vectors.append((block.id, vector))
            elif kind == "summarize":
                facts = papers.read_paper(
                    source_title, blocks, papers.pdf_facts(pdf_path) if pdf_path else None
                )
                paper_vector = None
                embedder = self.index.embedder
                if embedder and model_id == embedder.model_id and papers.paper_text(facts):
                    paper_vector = embedder.encode([papers.paper_text(facts)])[0]
                    if not paper_vector or not all(math.isfinite(v) for v in paper_vector):
                        raise ValueError("Embedding provider returned an invalid vector")
            elif kind == "digest":
                result = self.digest_writer.write(request) if self.digest_writer else None
            elif kind == "extract_assertions":
                extraction = self.assets.knowledge_service.extractor.extract(
                    source_title, source_content
                )
            elif kind == "measure_recording":
                if self.recordings_dir is None:
                    raise ValueError("Recording storage is unavailable")
                path, _, _ = RecordingService(
                    self.sessions, self.recordings_dir, self.assets.storage_budget
                ).download(recording_id)
                measured_ms = audio_duration_ms(path, stopping=self.stopping.is_set)
            else:
                raise ValueError("Processing provider is not configured")

            with session_scope(self.sessions) as session:
                # Conditional UPDATE acquires the write lock before publication. A cancelled
                # job or a reclaimed lease can never publish stale output.
                owned = session.execute(
                    update(ProcessingJob)
                    .where(
                        ProcessingJob.id == job_id,
                        ProcessingJob.lease_token == token,
                        ProcessingJob.state == "running",
                        ProcessingJob.lease_until > utc_now(),
                    )
                    .values(updated_at=utc_now())
                ).rowcount
                if not owned:
                    return
                if self.stopping.is_set():
                    return
                source = session.get(Source, source_id)
                if kind == "parse_asset":
                    head = session.get(SourceIndexHead, source_id)
                    previous = session.get(SourceRevision, head.revision_id) if head else None
                    if (
                        previous
                        and json.loads(previous.manifest_json).get("method") == "docling"
                        and document.manifest.get("method") != "docling"
                    ):
                        job = session.get(ProcessingJob, job_id)
                        job.state, job.error = (
                            "failed",
                            "Structured parsing failed; the previous document revision "
                            "was retained.",
                        )
                        job.lease_token, job.lease_until = None, None
                        return
                    self.index.publish(
                        session, source, parsed, parser=parser, warning=warning, document=document
                    )
                elif kind == "digest":
                    digest.publish(session, source, request, result)
                elif kind == "extract_assertions":
                    if source.trashed_at is None and source.content_hash == source_hash:
                        self.assets.knowledge_service.store_source_extraction(
                            session, source, extraction
                        )
                elif kind == "measure_recording":
                    recording = session.get(RecordingSession, recording_id)
                    if (
                        recording is not None and recording.status == "completed"
                        and recording.file_name == recording_file
                        and recording.byte_size == recording_size
                    ):
                        recording.audio_duration_ms = measured_ms
                        recording.updated_at = utc_now()
                elif kind == "summarize":
                    papers.publish(
                        session,
                        source,
                        revision_id,
                        facts,
                        file_name=file_name,
                        model=model_id if paper_vector is not None else None,
                        vector=paper_vector,
                    )
                else:
                    vector_index.store(
                        session,
                        source_id=source_id,
                        revision_id=revision_id,
                        model=model_id,
                        vectors=vectors,
                    )
                    for block_id, vector in vectors:
                        existing = session.scalar(
                            select(BlockEmbedding).where(
                                BlockEmbedding.block_id == block_id,
                                BlockEmbedding.model == self.index.embedder.model_id,
                            )
                        )
                        if existing is None:
                            session.add(
                                BlockEmbedding(
                                    id=new_id("emb"),
                                    block_id=block_id,
                                    model=self.index.embedder.model_id,
                                    dimensions=len(vector),
                                    # The vector itself lives in vector_index.
                                    vector_json="",
                                )
                            )
                job = session.get(ProcessingJob, job_id)
                job.state, job.error = "completed", None
                job.lease_token, job.lease_until = None, None
                job.updated_at = utc_now()
        except Exception as error:
            # Do not return model errors, local paths, or credentials to API clients.
            logger.warning("Knowledge job %s failed (%s)", job_id, type(error).__name__)
            with session_scope(self.sessions) as session:
                job = session.get(ProcessingJob, job_id)
                if job and job.state == "running" and job.lease_token == token:
                    job.state = "failed" if job.attempts >= 3 else "queued"
                    job.error = (
                        # The gateway's own words: which provider, and what to do about it.
                        f"The model could not write the summary: {error}"
                        if isinstance(error, digest.DigestError)
                        else f"The model could not write the summary ({type(error).__name__})."
                        if job.kind == "digest"
                        else f"Processing failed ({type(error).__name__}). Original preserved."
                    )
                    job.available_at = utc_now() + timedelta(seconds=5 * 2**job.attempts)
                    job.lease_token, job.lease_until = None, None
                    job.updated_at = utc_now()

        finally:
            self.assets.release_staged(job_id, token)

    def run_once(self, ai: bool | None = None) -> bool:
        claimed = self.claim(ai)
        if claimed:
            self.process(*claimed)
        return bool(claimed)

    async def _pause(self, seconds: float) -> None:
        """Sleep, but wake promptly when the worker is asked to stop."""
        remaining = seconds
        while remaining > 0 and not self.stopping.is_set():
            step = min(POLL_STEP_SECONDS, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def run(self) -> None:
        await asyncio.gather(self._lane(ai=False), self._lane(ai=True))

    async def _lane(self, *, ai: bool) -> None:
        failures = 0
        while not self.stopping.is_set():
            try:
                if not ai:
                    await asyncio.to_thread(self.index.backfill, 10)
                claimed = await asyncio.to_thread(self.claim, ai)
                if failures:
                    logger.warning("Knowledge worker recovered after %d failed attempts", failures)
                    failures = 0
                if not claimed:
                    await self._pause(IDLE_POLL_SECONDS)
                    continue
                task = asyncio.create_task(asyncio.to_thread(self.process, *claimed))
                while not task.done():
                    await asyncio.wait({task}, timeout=10)
                    if not task.done():
                        await asyncio.to_thread(self.heartbeat, *claimed)
                await task
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # A locked or unavailable database must not become a hot loop or
                # flood backend.log: back off exponentially and log sparingly.
                failures += 1
                if failures == 1 or failures % FAILURE_LOG_EVERY == 0:
                    logger.warning(
                        "Knowledge worker unavailable (%s); attempt %d, retrying",
                        type(error).__name__,
                        failures,
                    )
                await self._pause(retry_delay_seconds(failures))
