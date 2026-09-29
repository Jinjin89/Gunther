"""Leased, restart-safe processing. Publication and job completion commit together.

Summaries written by a model (``digest`` jobs) run in a lane of their own, so a
slow model never holds up reading and indexing what was just captured.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import timedelta
from threading import Event

from sqlalchemy import and_, or_, select, update

from gunther import digest, papers, vector_index
from gunther.asset_service import AssetService, extract_asset_content
from gunther.content import parse_content
from gunther.database import session_scope
from gunther.document_parser import DoclingParser
from gunther.knowledge_index import ASSET_PARSER, KnowledgeIndex, embeddable, new_id
from gunther.models import (
    Asset,
    BlockEmbedding,
    ContentBlock,
    ProcessingJob,
    Source,
    SourceIndexHead,
    utc_now,
)

logger = logging.getLogger(__name__)

IDLE_POLL_SECONDS = 1.0
POLL_STEP_SECONDS = 0.5
MAX_RETRY_SECONDS = 60.0
FAILURE_LOG_EVERY = 30
# Jobs that may wait on a model over the network.
AI_KINDS = ("digest",)


def retry_delay_seconds(failures: int) -> float:
    """Exponential backoff for an unavailable worker: 2, 4, 8 … capped at a minute."""
    return min(MAX_RETRY_SECONDS, 2.0 * 2 ** max(0, min(failures - 1, 6)))


class ProcessingWorker:
    def __init__(
        self,
        index: KnowledgeIndex,
        assets: AssetService,
        document_parser: DoclingParser | None = None,
        digest_writer: digest.DigestWriter | None = None,
    ):
        self.index = index
        self.sessions = index.sessions
        self.assets = assets
        self.document_parser = document_parser
        self.digest_writer = digest_writer
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
                    source_content = source.content
                elif kind == "digest":
                    image_path = (
                        self.assets.download(asset.id)[0]
                        if asset and asset.media_type in digest.VISION_TYPES
                        else None
                    )
                    request = digest.prepare(
                        session, source, job.revision_id, asset_path=image_path
                    )
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
                parsed = parse_content(
                    source_content + "\n\n## Extracted content\n\n" + extracted.text
                )
                warning = extracted.limitation
                parser = ASSET_PARSER
                if self.document_parser and media_type == "application/pdf":
                    try:
                        structured = self.document_parser.parse(path)
                        if not any(b.kind not in {"heading", "figure"} for b in structured):
                            raise ValueError("No structured document content")
                        covered = {
                            b.anchor.get("page")
                            for b in structured
                            if b.kind not in {"heading", "figure"} and b.anchor.get("page")
                        }
                        extras = [b for b in parsed if b.anchor.get("page") not in covered]
                        for block in extras:
                            block.parent = None
                        parsed = structured + extras
                        parser = self.document_parser.version
                    except Exception as error:
                        warning = (warning or "") + (
                            f" Structured parsing unavailable ({type(error).__name__}); "
                            "using local text/OCR extraction."
                        )
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
                source = session.get(Source, source_id)
                if kind == "parse_asset":
                    self.index.publish(session, source, parsed, parser=parser, warning=warning)
                elif kind == "digest":
                    digest.publish(session, source, request, result)
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
                        f"The model could not write the summary ({type(error).__name__})."
                        if job.kind == "digest"
                        else f"Processing failed ({type(error).__name__}). Original preserved."
                    )
                    job.available_at = utc_now() + timedelta(seconds=5 * 2**job.attempts)
                    job.lease_token, job.lease_until = None, None
                    job.updated_at = utc_now()

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
