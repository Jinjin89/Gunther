"""Transcribe a saved recording again, when the user asks (the Transcribe again button).

The saved audio is decoded and heard again by the Recording job's model the way the
minute check hears it: windows of two minutes with the text before them as context, the
last sentence of each held back and heard again with what follows. A long recording is
heard in stretches of ten minutes, three at a time, and each seam between two stretches
is heard again as one request. The Analysis model then says where paragraphs start; it
never writes text.

Nothing is written until all of it was heard: then the recording's transcript and every
source made from it are replaced at once, each source keeping its earlier text as an
earlier revision. Jobs live in memory, one running at a time: when the app quits, a job
in progress is gone and the button is there again.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from gunther import listen_copy, speech_registry, vad
from gunther.audio_decode import AudioUnreadable, decode_to_copy
from gunther.database import session_scope
from gunther.knowledge_index import KnowledgeIndex
from gunther.models import RecordingSession, Source, utc_now
from gunther.paragraphs import ParagraphsError, group_paragraphs
from gunther.qwen_stream import BYTES_PER_MS
from gunther.realtime import RELISTEN_PAD_MS, ListenError, relisten_audio
from gunther.source_identity import source_fingerprint

logger = logging.getLogger(__name__)

# How the audio is cut up.
STRETCH_MS = 600_000
MIN_STRETCH_MS = 120_000
WINDOW_MS = 120_000
# A window that ends this close to the stretch's end simply runs to it.
TAIL_MS = 30_000
MAX_WINDOW_MS = 180_000
MAX_SEAM_MS = 180_000
PARALLEL_STRETCHES = 3
# Providers that answer text only: windows of at least this long, ending at a pause.
TEXT_WINDOW_MS = 60_000
MAX_TEXT_WINDOW_MS = 150_000
# How much text is sent along as context.
CONTEXT_CHARS = 400
# The most sentences one grouping request may hold (the endpoint's own limit).
GROUPING_MAX = 300

# A request is tried three times, waiting this many seconds before each.
RETRY_WAITS = (0, 5, 20)

# A slow answer is waited for, unlike the live check's minute: Qwen takes about 25 s for
# two minutes of audio even from a fast link, and three stretches are heard at once.
HEAR_TIMEOUT = httpx.Timeout(300.0, connect=20.0)

TRANSCRIPT_HEADING = re.compile(r"^##[ \t]+(?:Full transcript|Transcript)[ \t]*$", re.MULTILINE)


# The transcript in a source ----------------------------------------------------------------


def transcript_section(content: str) -> tuple[str, str, str] | None:
    """``(head, heading_line, body)`` of a recording's content, or None without a transcript.

    The transcript is everything after the first ``## Full transcript`` or ``## Transcript``
    heading, as the desktop reads it. ``head`` is everything before the heading, untouched.
    """

    match = TRANSCRIPT_HEADING.search(content)
    if match is None:
        return None
    return content[: match.start()], match.group(0), content[match.end() :].lstrip("\n").rstrip()


def recording_terms(head: str) -> list[str]:
    """The ``Terms:`` line a saved summary may carry (``parseRecordingContent`` reads the same)."""

    terms: list[str] = []
    for line in head.splitlines():
        match = re.match(r"^Terms:\s*(.+)$", line)
        if match:
            terms.extend(term.strip() for term in match.group(1).split("·") if term.strip())
    return terms


# Text ---------------------------------------------------------------------------------


def _in_ranges(character: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    code = ord(character)
    return any(low <= code <= high for low, high in ranges)


# Han, Hiragana and Katakana, written out because ``re`` has no script classes.
_CJK_RANGES = (
    (0x2E80, 0x2FD5),
    (0x3005, 0x3005),
    (0x3007, 0x3007),
    (0x3021, 0x3029),
    (0x3038, 0x303B),
    (0x3041, 0x3096),
    (0x309D, 0x309F),
    (0x30A1, 0x30FA),
    (0x30FD, 0x30FF),
    (0x31F0, 0x31FF),
    (0x32D0, 0x32FE),
    (0x3300, 0x3357),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xFF66, 0xFF6F),
    (0xFF71, 0xFF9D),
    (0x1B000, 0x1B16F),
    (0x20000, 0x323AF),
)
# CJK and full-width punctuation, and the ellipsis.
_CJK_PUNCTUATION_RANGES = ((0x3000, 0x303F), (0xFF00, 0xFFEF), (0x2026, 0x2026))


def join_sentences(texts: list[str]) -> str:
    """Join sentence texts: no space between CJK characters or after CJK punctuation, else one.

    The same rule as ``joinSentences`` in the desktop's ``liveTranscript.ts``.
    """

    output = ""
    for raw in texts:
        text = raw.strip()
        if not text:
            continue
        if not output:
            output = text
            continue
        last, first = output[-1], text[0]
        tight = _in_ranges(last, _CJK_PUNCTUATION_RANGES) or (
            _in_ranges(last, _CJK_RANGES) and _in_ranges(first, _CJK_RANGES)
        )
        output += ("" if tight else " ") + text
    return output


def clock_label(ms: int) -> str:
    """``mm:ss``, or ``hh:mm:ss`` after an hour: the same as the desktop's ``clockLabel``."""

    total = max(0, ms // 1000)
    hours, minutes, seconds = total // 3600, total // 60 % 60, total % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{total // 60:02d}:{seconds:02d}"


# What was heard -----------------------------------------------------------------------


@dataclass
class Heard:
    """One sentence heard again, on the recording clock; ``window`` says which request."""

    start_ms: int
    end_ms: int
    text: str
    window: str


@dataclass
class Window:
    """One stretch of audio that was heard (a minute-check window, or a seam)."""

    id: str
    start_ms: int
    end_ms: int


@dataclass
class Stretch:
    """What hearing one stretch of the recording gave."""

    sentences: list[Heard] = field(default_factory=list)
    windows: list[Window] = field(default_factory=list)


# ``hear(start_ms, end_ms, before)`` answers like ``relisten_audio``: ``{"text", "sentences"}``,
# the sentences being ``{start_ms, end_ms, text}`` on the recording clock (none without word
# times). The runner supplies the model, the terms and the audio.
Hear = Callable[[int, int, str], Awaitable[dict[str, Any]]]


def stretches(total_ms: int) -> list[tuple[int, int]]:
    """The recording cut into stretches of 10 minutes; a last one under 2 joins the one before."""

    if total_ms <= 0:
        return []
    cuts = list(range(0, total_ms, STRETCH_MS))
    spans = [(start, min(start + STRETCH_MS, total_ms)) for start in cuts]
    if len(spans) > 1 and spans[-1][1] - spans[-1][0] < MIN_STRETCH_MS:
        last = spans.pop()
        spans[-1] = (spans[-1][0], last[1])
    return spans


def _tail(text: str) -> str:
    return text[-CONTEXT_CHARS:]


def _timed(
    answer: dict[str, Any], start_ms: int, end_ms: int, window: str
) -> tuple[list[Heard], bool]:
    """The sentences of an answer, and whether they carry word times.

    An answer with text but no sentences is one sentence that fills the window.
    """

    heard = [
        Heard(int(item["start_ms"]), int(item["end_ms"]), str(item["text"]).strip(), window)
        for item in answer.get("sentences") or []
        if str(item.get("text", "")).strip() and int(item["end_ms"]) > start_ms
    ]
    if heard:
        return sorted(heard, key=lambda item: item.start_ms), True
    text = str(answer.get("text") or "").strip()
    return ([Heard(start_ms, end_ms, text, window)] if text else []), False


async def hear_stretch(
    hear: Hear,
    start_ms: int,
    end_ms: int,
    context: str = "",
    progress: Callable[[int], Awaitable[None]] | None = None,
) -> Stretch:
    """Windows of two minutes one after another, as the minute check does.

    Each window is heard with the confirmed text before it as context. All its sentences
    but the last are kept and the next window starts where the kept ones end, so the last
    sentence is heard again with what follows it. The stretch's last window keeps
    everything. A one-sentence answer for a window that is not the last is asked once
    more with a longer window; if it is still one sentence it is kept. The confirmed point
    always moves forward. Silence (an empty answer) just moves on.
    """

    result = Stretch()
    confirmed = start_ms
    kept_text = context
    while confirmed < end_ms:
        window_end = min(confirmed + WINDOW_MS, end_ms)
        if end_ms - window_end < TAIL_MS:
            window_end = end_ms
        window_id = f"w-{confirmed}"
        answer = await hear(confirmed, window_end, _tail(kept_text))
        sentences, timed = _timed(answer, confirmed, window_end, window_id)
        if timed and len(sentences) == 1 and window_end < end_ms:
            longer = min(confirmed + MAX_WINDOW_MS, end_ms)
            if longer > window_end:
                window_end = longer
                answer = await hear(confirmed, window_end, _tail(kept_text))
                sentences, timed = _timed(answer, confirmed, window_end, window_id)
        result.windows.append(Window(window_id, confirmed, window_end))
        last = window_end >= end_ms
        if not sentences:
            confirmed = window_end
        else:
            kept = sentences if last or not timed or len(sentences) == 1 else sentences[:-1]
            result.sentences.extend(kept)
            kept_text = join_sentences([kept_text, *(item.text for item in kept)])
            confirmed = window_end if last else kept[-1].end_ms
            if confirmed <= result.windows[-1].start_ms:
                confirmed = window_end
        if progress:
            await progress(min(confirmed, end_ms) - start_ms)
    return result


async def mend_seams(hear: Hear, heard: list[Stretch]) -> Stretch:
    """Join the stretches, hearing each seam again.

    The last sentence before a seam and the first after it are heard in one request, so the
    model alone decides whether the words around the cut are one sentence or two; no pause
    decides anything. The answer replaces the two. An empty answer keeps them; a seam
    longer than the listen-again limit is left alone.
    """

    merged = Stretch()
    for stretch in heard:
        merged.windows.extend(stretch.windows)
        incoming = list(stretch.sentences)
        if merged.sentences and incoming:
            before_seam, after_seam = merged.sentences[-1], incoming[0]
            start, end = before_seam.start_ms, after_seam.end_ms
            if 0 < end - start <= MAX_SEAM_MS:
                context = join_sentences([item.text for item in merged.sentences[:-1]])
                answer = await hear(start, end, _tail(context))
                taken = {window.id for window in merged.windows}
                window_id = f"w-{start}"
                while window_id in taken:
                    window_id += "+"
                fresh, timed = _timed(answer, start, end, window_id)
                if timed and fresh:
                    merged.windows.append(Window(window_id, start, end))
                    merged.sentences[-1:] = fresh
                    incoming = incoming[1:]
        merged.sentences.extend(incoming)
    merged.windows.sort(key=lambda window: (window.start_ms, window.end_ms))
    return merged


def text_windows(
    pieces: list[tuple[int, int]], min_ms: int = TEXT_WINDOW_MS
) -> list[tuple[int, int]]:
    """Windows for providers that answer text only.

    ``pieces`` are the stretches of speech the pause detector found (ms). A window starts
    at its first piece and ends at a piece end once it is at least ``min_ms`` long, so it
    never ends inside a phrase. The last window may be shorter. A window never runs past
    the listen-again limit, so a long silence between two pieces ends the window before it.
    """

    windows: list[tuple[int, int]] = []
    start: int | None = None
    end = 0
    for piece_start, piece_end in sorted(pieces):
        if piece_end <= piece_start:
            continue
        if start is not None and piece_end - start > MAX_TEXT_WINDOW_MS:
            windows.append((start, end))
            start = None
        if start is None:
            start = piece_start
            end = 0
        end = max(end, piece_end)
        if end - start >= min_ms:
            windows.append((start, end))
            start = None
    if start is not None:
        windows.append((start, end))
    return windows


async def hear_text_run(
    hear: Hear,
    windows: list[tuple[int, int]],
    context: str = "",
    progress: Callable[[int], Awaitable[None]] | None = None,
) -> Stretch:
    """Windows that were cut at pauses, heard one after another: each is one sentence."""

    result = Stretch()
    kept_text = context
    for start, end in windows:
        window_id = f"w-{start}"
        answer = await hear(start, end, _tail(kept_text))
        text = str(answer.get("text") or "").strip()
        result.windows.append(Window(window_id, start, end))
        if text:
            result.sentences.append(Heard(start, end, text, window_id))
            kept_text = join_sentences([kept_text, text])
        if progress:
            await progress(end - windows[0][0])
    return result


# Paragraphs ---------------------------------------------------------------------------

Group = Callable[[list[str], str], list[int]]


def _clean_starts(starts: object, count: int) -> list[int]:
    """Zero-based starts: whole numbers inside the range, sorted, no repeats, and 0."""

    picked = {0}
    if isinstance(starts, list):
        picked |= {
            value
            for value in starts
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < count
        }
    return sorted(picked)


def paragraphs_by_window(sentences: list[Heard]) -> list[list[int]]:
    """One paragraph per run of sentences from the same window: where grouping starts."""

    paragraphs: list[list[int]] = []
    for index, sentence in enumerate(sentences):
        if paragraphs and sentences[paragraphs[-1][-1]].window == sentence.window:
            paragraphs[-1].append(index)
        else:
            paragraphs.append([index])
    return paragraphs


def regroup(
    sentences: list[Heard],
    group: Group,
    maximum: int = GROUPING_MAX,
) -> list[list[int]]:
    """Paragraphs as the Analysis model groups them, in chunks of up to ``maximum`` sentences.

    Starts from one paragraph per window and goes through the recording the way the
    desktop's ``nextRegroupChunk`` / ``applyRegroup`` do after Stop: each chunk begins at a
    paragraph start, holds whole paragraphs, and is split again where ``group`` says; the
    text before the chunk is sent as context. ``group(texts, before)`` returns zero-based
    paragraph starts. Paragraphs are lists of indexes into ``sentences``.
    """

    paragraphs = paragraphs_by_window(sentences)
    from_ms = 0
    while True:
        chunk = _next_chunk(sentences, paragraphs, from_ms, maximum)
        if chunk is None:
            return paragraphs
        first, count, indexes = chunk
        previous = (
            join_sentences([sentences[i].text for i in paragraphs[first - 1]]) if first else ""
        )
        starts = _clean_starts(
            group([sentences[i].text for i in indexes], _tail(previous)), len(indexes)
        )
        made = [
            indexes[start : starts[number + 1] if number + 1 < len(starts) else len(indexes)]
            for number, start in enumerate(starts)
        ]
        paragraphs[first : first + count] = made
        from_ms = sentences[indexes[-1]].start_ms + 1


def _next_chunk(
    sentences: list[Heard], paragraphs: list[list[int]], from_ms: int, maximum: int
) -> tuple[int, int, list[int]] | None:
    """``(first paragraph, paragraph count, sentence indexes)`` of the next run to group."""

    index = 0
    while index < len(paragraphs):
        own = paragraphs[index]
        if not own or sentences[own[0]].start_ms < from_ms or len(own) > maximum:
            index += 1
            continue
        end, count = index, 0
        while end < len(paragraphs) and count + len(paragraphs[end]) <= maximum:
            count += len(paragraphs[end])
            end += 1
        run = [i for paragraph in paragraphs[index:end] for i in paragraph]
        if len(run) < 2:
            index = max(end, index + 1)
            continue
        return index, end - index, run
    return None


# The structured transcript ---------------------------------------------------------------


def _unique(base: str, taken: set[str]) -> str:
    name = base
    while name in taken:
        name += "+"
    taken.add(name)
    return name


def transcript_state(
    sentences: list[Heard],
    windows: list[Window],
    paragraphs: list[list[int]],
    *,
    model: str,
    at: str,
    grouped: bool,
) -> dict[str, Any]:
    """The ``TranscriptState`` JSON (version 1) the desktop reads.

    Every window is ``checked`` and every paragraph frozen. ``grouped`` False means no
    Analysis model grouped them.
    """

    taken: set[str] = set()
    ids = [_unique(f"s-{sentence.start_ms}", taken) for sentence in sentences]
    taken = set()
    state_paragraphs = [
        {
            "id": _unique(f"p-{sentences[indexes[0]].start_ms}", taken),
            "sentenceIds": [ids[i] for i in indexes],
            "frozen": True,
        }
        for indexes in paragraphs
        if indexes
    ]
    return {
        "version": 1,
        "sentences": [
            {
                "id": ids[index],
                "startMs": sentence.start_ms,
                "endMs": sentence.end_ms,
                "text": sentence.text,
                "windowId": sentence.window,
            }
            for index, sentence in enumerate(sentences)
        ],
        "windows": [
            {
                "id": window.id,
                "startMs": window.start_ms,
                "endMs": window.end_ms,
                "live": [],
                "state": "checked",
                "pass": "minute",
                "model": model,
                "at": at,
            }
            for window in windows
        ],
        "paragraphs": state_paragraphs,
        "live": [],
        "draft": None,
        "confirmedUntilMs": max((sentence.end_ms for sentence in sentences), default=0),
        "checking": None,
        "grouping": {"state": "idle" if grouped else "off"},
        "gaps": [],
    }


def render(state: dict[str, Any]) -> str:
    """``[clock] paragraph`` blocks with a blank line between, like the desktop renderTranscript."""

    by_id = {sentence["id"]: sentence for sentence in state["sentences"]}
    blocks: list[str] = []
    for paragraph in state["paragraphs"]:
        members = [by_id[i] for i in paragraph["sentenceIds"] if i in by_id]
        text = re.sub(r"\s+", " ", join_sentences([m["text"] for m in members])).strip()
        if text:
            blocks.append(f"[{clock_label(members[0]['startMs'])}] {text}")
    return "\n\n".join(blocks)


# Pauses in the audio ----------------------------------------------------------------------


def speech_pieces(directory: Path, recording_id: str, total_ms: int) -> list[tuple[int, int]]:
    """Stretches of speech (ms) in the listen copy, cut at pauses by the pause detector.

    Without the detector's model, fixed pieces of a minute (the live fallback).
    """

    detector = vad.new_detector()
    segmenter: vad.Segmenter | vad.FixedBlocks
    segmenter = (
        vad.Segmenter(detector)
        if detector is not None
        else vad.FixedBlocks(TEXT_WINDOW_MS * BYTES_PER_MS)
    )
    pieces: list[vad.Piece] = []
    step = 1_000 * BYTES_PER_MS
    pcm_path, _ = listen_copy.copy_paths(directory, recording_id)
    with open(pcm_path, "rb") as handle:
        while block := handle.read(step):
            pieces.extend(segmenter.feed(block))
    pieces.extend(segmenter.flush())
    return [(start // BYTES_PER_MS, min(end // BYTES_PER_MS, total_ms)) for start, end in pieces]


# The job ----------------------------------------------------------------------------------


class RetranscribeError(Exception):
    """This recording can't be transcribed again; the message is plain words for the user."""


class RecordingNotFound(LookupError):
    """No recording has this id."""


@dataclass
class Job:
    """One recording being transcribed again: ``waiting``, ``running``, ``done``, ``failed``
    or ``stopped`` (the person stopped it)."""

    recording_id: str
    state: str = "waiting"
    heard_ms: int = 0
    total_ms: int = 0
    error: str | None = None
    # Its entry in the top bar's activity list, when there is one.
    tracked: Any = None
    # Writing the new transcript: too late to stop, or it would be half written.
    saving: bool = False

    def note(self, **fields: Any) -> None:
        if self.tracked is not None:
            self.tracked.update(**fields)

    def note_heard(self) -> None:
        """Tell the activity list how much has been heard."""

        if self.total_ms > 0:
            self.note(
                progress=min(1.0, self.heard_ms / self.total_ms),
                detail=f"{_minutes(self.heard_ms)} of {_minutes(self.total_ms)} heard",
            )

    def out(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "heardMs": self.heard_ms,
            "totalMs": self.total_ms,
            "error": self.error,
        }


IDLE = {"state": "idle", "heardMs": 0, "totalMs": 0, "error": None}


def _minutes(ms: int) -> str:
    """``m:ss``, or ``h:mm:ss`` from an hour."""

    total = max(0, ms // 1000)
    hours, minutes, seconds = total // 3600, total // 60 % 60, total % 60
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{total // 60}:{seconds:02d}"


def _iso(moment: datetime) -> str:
    return moment.replace(tzinfo=UTC).isoformat(timespec="seconds")


async def with_retries(call: Callable[[], Awaitable[Any]], waits: tuple[float, ...]) -> Any:
    """Try ``call`` after each wait in turn; the last ``ListenError`` is raised."""

    last: Exception | None = None
    for wait in waits:
        if wait:
            await asyncio.sleep(wait)
        try:
            return await call()
        except ListenError as error:
            last = error
    assert last is not None
    raise last


def _joined(heard: list[Stretch]) -> Stretch:
    """Stretches put one after another with no seams to repair."""

    joined = Stretch()
    for stretch in heard:
        joined.sentences.extend(stretch.sentences)
        joined.windows.extend(stretch.windows)
    return joined


def _sources(session: Session, recording_id: str) -> list[Source]:
    """The library sources made from this recording (not those in Trash)."""

    return list(
        session.scalars(
            select(Source)
            .where(
                Source.trashed_at.is_(None),
                Source.content.contains(f"Local recording: {recording_id}"),
            )
            .order_by(Source.created_at, Source.id)
        )
    )


class Retranscriber:
    """Transcribes recordings again when asked, one at a time.

    ``state`` is the application's ``app.state``: the speech registry, the models and the
    knowledge index are read from it each time, because settings replace them live.
    """

    def __init__(self, state: Any, sessions: sessionmaker[Session], recordings_dir: Path) -> None:
        self.state = state
        self.sessions = sessions
        self.recordings_dir = recordings_dir
        self.retry_waits = RETRY_WAITS
        self.jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._task_of: dict[str, asyncio.Task[None]] = {}
        self._stopping: set[str] = set()
        self._one_at_a_time: asyncio.Lock | None = None

    def status(self, recording_id: str) -> dict[str, Any]:
        job = self.jobs.get(recording_id)
        return job.out() if job else dict(IDLE)

    async def start(self, recording_id: str) -> dict[str, Any]:
        """Start transcribing a recording again; one already waiting or running goes on.

        Raises ``RecordingNotFound``, or ``RetranscribeError`` when it can't be done now.
        """

        job = self.jobs.get(recording_id)
        if job is not None and job.state in ("waiting", "running"):
            return job.out()
        plan = await asyncio.to_thread(self._plan, recording_id)
        job = Job(recording_id)
        activity = getattr(self.state, "activity", None)
        if activity is not None:
            job.tracked = activity.track(
                f"transcribe:{recording_id}",
                "transcribe",
                "Waiting to transcribe again",
                plan["title"],
                state="waiting",
                detail="Waiting for another recording",
                open={"view": "item", "itemType": "source", "id": plan["source_id"]}
                if plan["source_id"]
                else None,
                stop=lambda: self.stop(recording_id),
            )
        self.jobs[recording_id] = job
        if self._one_at_a_time is None:
            self._one_at_a_time = asyncio.Lock()
        task = asyncio.create_task(self._run(job, plan))
        self._tasks.add(task)
        self._task_of[recording_id] = task
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda done: self._forget(recording_id, done))
        return job.out()

    def _forget(self, recording_id: str, task: asyncio.Task[None]) -> None:
        if self._task_of.get(recording_id) is task:
            del self._task_of[recording_id]
            self._stopping.discard(recording_id)

    def stop(self, recording_id: str) -> bool:
        """End a waiting or running job quietly. False when there is none. Call on the loop."""

        task = self._task_of.get(recording_id)
        if task is None or task.done() or self.jobs[recording_id].saving:
            return False
        self._stopping.add(recording_id)
        # Said now, not when the task notices: a task cancelled before it ever ran never does.
        job = self.jobs[recording_id]
        job.state = "stopped"
        if job.tracked is not None:
            job.tracked.stopped()
        task.cancel()
        return True

    async def wait(self) -> None:
        """Until every job has finished (for tests)."""

        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _plan(self, recording_id: str) -> dict[str, Any]:
        choice, problem = speech_registry.resolve(self.state.speech_registry, "recording")
        if choice is None:
            raise RetranscribeError(
                f"Set a Recording model in Settings → Transcription first ({problem})."
                if problem
                else "Set a Recording model in Settings → Transcription first."
            )
        with session_scope(self.sessions) as session:
            recording = session.get(RecordingSession, recording_id)
            if recording is None:
                raise RecordingNotFound(recording_id)
            if recording.status != "completed":
                raise RetranscribeError("This recording hasn't finished yet.")
            path = self.recordings_dir / recording.file_name
            sources = _sources(session, recording_id)
            section = transcript_section(sources[0].content) if sources else None
            terms = recording_terms(section[0]) if section else []
            title = recording.title
            source_id = sources[0].id if sources else None
        if not path.is_file() or path.stat().st_size == 0:
            raise RetranscribeError("The audio of this recording isn't saved.")
        return {
            "path": path,
            "choice": choice,
            "terms": terms,
            "title": title,
            "source_id": source_id,
        }

    async def _run(self, job: Job, plan: dict[str, Any]) -> None:
        assert self._one_at_a_time is not None
        try:
            async with self._one_at_a_time:
                await self._work(job, plan)
        except asyncio.CancelledError:
            # A stop ends the job quietly; shutdown (``close``) cancels and is not hidden.
            if job.tracked is not None:
                job.tracked.stopped()
            if job.recording_id in self._stopping:
                job.state = "stopped"
                return
            raise

    async def _work(self, job: Job, plan: dict[str, Any]) -> None:
        job.state = "running"
        job.note(state="running", title="Transcribing again", detail="Reading the audio")
        began = time.monotonic()
        logger.info("Transcribing %s again with %s", job.recording_id, plan["choice"].model)
        try:
            sentences, windows = await self._listen(job, plan)
            job.note(progress=None, detail="Finding paragraphs")
            paragraphs, grouped = await self._paragraphs(job, sentences)
            state = transcript_state(
                sentences,
                windows,
                paragraphs,
                model=plan["choice"].model,
                at=_iso(utc_now()),
                grouped=grouped,
            )
            job.saving = True
            job.note(detail="Saving the transcript")
            await asyncio.to_thread(self._write, job.recording_id, state)
        except asyncio.CancelledError:
            raise
        except (ListenError, AudioUnreadable, RetranscribeError) as error:
            job.state, job.error = "failed", str(error) or type(error).__name__
            logger.warning("Transcribing %s again failed: %s", job.recording_id, job.error)
        except Exception as error:
            logger.warning("Transcribing %s again failed", job.recording_id, exc_info=True)
            job.state, job.error = "failed", f"Unexpected problem: {error}"
        else:
            job.state = "done"
            logger.info(
                "Transcribed %s again: %d sentences, %d paragraphs in %.0f s",
                job.recording_id,
                len(sentences),
                len(paragraphs),
                time.monotonic() - began,
            )
        finally:
            listen_copy.remove_copy(self.recordings_dir, job.recording_id)
        if job.tracked is not None:
            if job.state == "done":
                job.tracked.done("Transcript updated")
            else:
                job.tracked.failed("Transcribing again failed", job.error or "")

    async def _listen(self, job: Job, plan: dict[str, Any]) -> tuple[list[Heard], list[Window]]:
        """Decode the audio and hear all of it again."""

        recording_id, choice = job.recording_id, plan["choice"]
        total_ms = await asyncio.to_thread(
            decode_to_copy, plan["path"], self.recordings_dir, recording_id
        )
        job.total_ms = total_ms
        job.note_heard()

        async def hear(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
            end_ms = min(end_ms, total_ms)
            padded = max(0, start_ms - RELISTEN_PAD_MS)
            pcm, _held = await asyncio.to_thread(
                listen_copy.read_range,
                self.recordings_dir,
                recording_id,
                padded,
                end_ms + RELISTEN_PAD_MS,
            )

            async def once() -> dict[str, Any]:
                return await relisten_audio(
                    choice,
                    pcm,
                    audio_start_ms=padded,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    terms=plan["terms"],
                    before=before,
                    after="",
                    timeout=HEAR_TIMEOUT,
                )

            return await with_retries(once, tuple(self.retry_waits))

        done: dict[int, int] = {}

        def reporter(key: int) -> Callable[[int], Awaitable[None]]:
            async def report(heard_ms: int) -> None:
                done[key] = heard_ms
                job.heard_ms = sum(done.values())
                job.note_heard()

            return report

        gate = asyncio.Semaphore(PARALLEL_STRETCHES)

        async def limited(work: Awaitable[Stretch]) -> Stretch:
            async with gate:
                return await work

        if choice.kind == "qwen":
            works = [
                hear_stretch(hear, start, end, "", reporter(number))
                for number, (start, end) in enumerate(stretches(total_ms))
            ]
        else:
            pieces = await asyncio.to_thread(
                speech_pieces, self.recordings_dir, recording_id, total_ms
            )
            runs: list[list[tuple[int, int]]] = []
            for window in text_windows(pieces):
                if runs and window[0] - runs[-1][0][0] < STRETCH_MS:
                    runs[-1].append(window)
                else:
                    runs.append([window])
            works = [
                hear_text_run(hear, run, "", reporter(number)) for number, run in enumerate(runs)
            ]
        jobs = [asyncio.ensure_future(limited(work)) for work in works]
        try:
            heard = list(await asyncio.gather(*jobs))
        except BaseException:
            for item in jobs:
                item.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            raise
        merged = await mend_seams(hear, heard) if choice.kind == "qwen" else _joined(heard)
        if not merged.sentences:
            raise RetranscribeError("Nothing was heard in the audio.")
        merged.sentences.sort(key=lambda item: (item.start_ms, item.end_ms))
        job.heard_ms = total_ms
        return merged.sentences, merged.windows

    async def _paragraphs(self, job: Job, sentences: list[Heard]) -> tuple[list[list[int]], bool]:
        """Paragraphs by the Analysis model; without one, or when it fails after its
        retries, one paragraph per window, so what was heard is never thrown away."""

        chosen = self.state.models.for_role("analysis")
        if chosen is None:
            return paragraphs_by_window(sentences), False
        model, effort = chosen

        def group(texts: list[str], before: str) -> list[int]:
            last: ParagraphsError | None = None
            for wait in self.retry_waits:
                if wait:
                    time.sleep(wait)
                try:
                    return group_paragraphs(self.state.models, model, effort, texts, before)
                except ParagraphsError as error:
                    last = error
            assert last is not None
            raise last

        try:
            return await asyncio.to_thread(regroup, sentences, group), True
        except ParagraphsError as error:
            logger.warning(
                "Paragraphs for %s fell back to one per window: %s", job.recording_id, error
            )
            return paragraphs_by_window(sentences), False

    def _write(self, recording_id: str, state: dict[str, Any]) -> None:
        """The recording and every source made from it, in one transaction."""

        text = render(state)
        with session_scope(self.sessions) as session:
            recording = session.get(RecordingSession, recording_id)
            if recording is None:
                raise RetranscribeError("The recording was deleted while it was being heard.")
            index: KnowledgeIndex = self.state.knowledge_service.index
            for source in _sources(session, recording_id):
                head, heading, _old = transcript_section(source.content) or (
                    source.content.rstrip() + "\n\n",
                    "## Transcript",
                    "",
                )
                source.content = f"{head}{heading}\n\n{text}"
                source.content_hash = source_fingerprint(source.title, source.kind, source.content)
                session.flush()
                index.add_source(session, source)
            recording.transcript = text
            recording.transcript_segments_json = json.dumps(state, ensure_ascii=False)
        folders = getattr(self.state, "library_folders", None)
        if folders is not None:
            folders.request_sync()
