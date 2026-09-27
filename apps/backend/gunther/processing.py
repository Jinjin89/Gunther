"""Leased, restart-safe processing. Publication and job completion commit together."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import timedelta
from threading import Event

from sqlalchemy import and_, or_, select, update

from gunther.asset_service import AssetService, extract_asset_content
from gunther.content import parse_content
from gunther.database import session_scope
from gunther.document_parser import DoclingParser
from gunther.knowledge_index import KnowledgeIndex, new_id
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


class ProcessingWorker:
    def __init__(
        self,
        index: KnowledgeIndex,
        assets: AssetService,
        document_parser: DoclingParser | None = None,
    ):
        self.index = index
        self.sessions = index.sessions
        self.assets = assets
        self.document_parser = document_parser
        self.stopping = Event()

    def claim(self) -> tuple[str, str] | None:
        now = utc_now()
        with session_scope(self.sessions) as session:
            eligible = or_(
                and_(ProcessingJob.state == "queued", ProcessingJob.available_at <= now),
                and_(ProcessingJob.state == "running", ProcessingJob.lease_until < now),
            )
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
                else:
                    head = session.get(SourceIndexHead, source_id)
                    revision_id = job.revision_id or (head.revision_id if head else None)
                    model_id = job.model_id
                    blocks = list(
                        session.scalars(
                            select(ContentBlock)
                            .where(
                                ContentBlock.revision_id == revision_id,
                                ContentBlock.kind != "heading",
                            )
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
                parser = "gunther-asset-v1"
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
                else:
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
                                    vector_json=json.dumps(vector),
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
                    job.error = f"Processing failed ({type(error).__name__}). Original preserved."
                    job.available_at = utc_now() + timedelta(seconds=5 * 2**job.attempts)
                    job.lease_token, job.lease_until = None, None
                    job.updated_at = utc_now()

    def run_once(self) -> bool:
        claimed = self.claim()
        if claimed:
            self.process(*claimed)
        return bool(claimed)

    async def run(self) -> None:
        while not self.stopping.is_set():
            try:
                await asyncio.to_thread(self.index.backfill, 10)
                claimed = await asyncio.to_thread(self.claim)
                if not claimed:
                    await asyncio.sleep(1)
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
                logger.warning("Knowledge worker unavailable (%s)", type(error).__name__)
                await asyncio.sleep(2)
