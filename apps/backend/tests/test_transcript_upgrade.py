"""Old recordings heard again and grouped into paragraphs, by themselves.

The pure parts (what is old, windows, seams, paragraphs, the structured transcript) are
tested with a made-up ``hear``. The runner and the endpoints go through the real app with
the Recording model replaced by a function that answers from a timeline, and the Analysis
model by a fake gateway.
"""

import asyncio
import dataclasses
import json
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gunther import transcript_upgrade
from gunther.database import session_scope
from gunther.main import create_app
from gunther.models import (
    ContentBlock,
    RecordingSession,
    Source,
    SourceIndexHead,
    SourceRevision,
    TranscriptUpgrade,
    utc_now,
)
from gunther.realtime import ListenError
from gunther.transcript_upgrade import (
    Heard,
    Stretch,
    Window,
    clock_label,
    hear_stretch,
    hear_text_run,
    is_old_transcript,
    join_sentences,
    mend_seams,
    old_lines,
    paragraphs_by_window,
    regroup,
    render,
    stretches,
    text_windows,
    transcript_section,
    transcript_state,
)
from tests.test_audio_decode import encode
from tests.test_listen_again import SIDECAR, settings_for, with_provider

REC = "rec_" + "a" * 24
OTHER = "rec_" + "b" * 24
THIRD = "rec_" + "c" * 24


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


# What is old --------------------------------------------------------------------------


def test_only_a_transcript_of_stamped_lines_is_old() -> None:
    assert is_old_transcript("[00:00] Today we look at how neural\n[00:03] networks learn")
    assert is_old_transcript("[1:02:03] after an hour\n\n[1:02:06.5] with a fraction")
    assert is_old_transcript("[00:00]\n[00:03] an empty first line is still stamped")
    # Pasted or typed text, a recording with no transcript, or a mix: left alone.
    assert not is_old_transcript("Today we look at how neural networks learn.")
    assert not is_old_transcript("[00:00] stamped\nbut this line is not")
    assert not is_old_transcript("12:30 meeting notes without brackets")
    assert not is_old_transcript("")
    assert not is_old_transcript("  \n\n ")


CONTENT_HEAD = (
    "# Lecture 3\n\n"
    "Duration: 10:00 · Captured: Today · Local recording: " + REC + "\n\n"
    "## Summary\nGradient descent.\nTerms: gradient · loss function\n\n"
    "## Marked moments\n- 0:05 · The key step\n\n"
)


def test_the_transcript_section_is_split_off_and_everything_before_it_is_kept() -> None:
    content = CONTENT_HEAD + "## Transcript\n\n[00:00] one\n[00:03] two\n"
    head, heading, body = transcript_section(content)
    assert head == CONTENT_HEAD
    assert heading == "## Transcript"
    assert body == "[00:00] one\n[00:03] two"
    assert head + heading + "\n\n" + body + "\n" == content
    full = transcript_section("# T\n\n## Full transcript\n\n[00:00] x")
    assert full is not None and full[1] == "## Full transcript"
    assert transcript_section("# T\n\nNo transcript here.") is None
    # The first heading wins; a later one belongs to the transcript.
    nested = transcript_section("## Transcript\n[00:00] a\n## Transcript\n[00:01] b")
    assert nested is not None and nested[2].endswith("[00:01] b")


def test_old_lines_end_where_the_next_one_begins() -> None:
    body = "[00:00] Today we look\n[00:03] at how\n[1:00:00] much later"
    assert old_lines(body) == [
        (0, 3_000, "Today we look"),
        (3_000, 3_600_000, "at how"),
        (3_600_000, 3_603_200, "much later"),
    ]
    assert old_lines(body, 3_700_000)[-1] == (3_600_000, 3_700_000, "much later")


# Text ------------------------------------------------------------------------------------


def test_sentences_join_and_clocks_read_like_the_desktops() -> None:
    # The same examples as liveTranscript.test.ts.
    assert join_sentences(["今天讲梯度下降", "然后看学习率"]) == "今天讲梯度下降然后看学习率"
    assert (
        join_sentences(["好，我们开始。", "Today we use SGD."]) == "好，我们开始。Today we use SGD."
    )
    assert join_sentences(["We use Adam.", "Then we go on."]) == "We use Adam. Then we go on."
    assert join_sentences(["We use Adam", "然后继续"]) == "We use Adam 然后继续"
    assert join_sentences([" a ", "", "b"]) == "a b"
    assert join_sentences(["こんにちは", "カタカナ"]) == "こんにちはカタカナ"
    assert join_sentences(["wait…", "ok"]) == "wait…ok"
    assert clock_label(0) == "00:00"
    assert clock_label(65_999) == "01:05"
    assert clock_label(3_599_000) == "59:59"
    assert clock_label(3_725_000) == "01:02:05"
    assert clock_label(-5) == "00:00"


# The window chain --------------------------------------------------------------------


def timeline(count: int, offset: int = 5_000) -> list[tuple[int, int, str]]:
    return [
        (i * 10_000 + offset, i * 10_000 + offset + 9_000, f"Sentence {i}.") for i in range(count)
    ]


class Recorder:
    """A made-up Recording model: it hears a timeline, cutting what the range cuts."""

    def __init__(self, sentences: list[tuple[int, int, str]], word_times: bool = True) -> None:
        self.sentences = sentences
        self.word_times = word_times
        self.calls: list[tuple[int, int, str]] = []

    async def __call__(self, start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        self.calls.append((start_ms, end_ms, before))
        heard = []
        for begin, end, text in self.sentences:
            if end <= start_ms or begin >= end_ms:
                continue
            cut = begin < start_ms or end > end_ms
            heard.append(
                {
                    "start_ms": max(begin, start_ms),
                    "end_ms": min(end, end_ms),
                    "text": f"{text} (partial)" if cut else text,
                }
            )
        text = " ".join(item["text"] for item in heard)
        return {"text": text, "sentences": heard if self.word_times else []}


def test_each_window_holds_back_its_last_sentence_and_the_next_one_hears_it_again() -> None:
    recorder = Recorder(timeline(29))
    seen: list[int] = []

    async def progress(ms: int) -> None:
        seen.append(ms)

    stretch = run(hear_stretch(recorder, 0, 300_000, "Earlier talk.", progress))
    assert [item.text for item in stretch.sentences] == [f"Sentence {i}." for i in range(29)]
    # Windows of two minutes, each starting where the sentences kept before it end.
    assert recorder.calls[0][:2] == (0, 120_000)
    assert 100_000 < recorder.calls[1][0] < 120_000  # where the sentences kept so far end
    assert recorder.calls[-1][1] == 300_000
    assert all(end - start <= 180_000 for start, end, _ in recorder.calls)
    # Context: the old text at the start, then what was confirmed in this chain.
    assert recorder.calls[0][2] == "Earlier talk."
    assert recorder.calls[1][2].startswith("Earlier talk. Sentence 0. Sentence 1.")
    assert all(len(before) <= 400 for _, _, before in recorder.calls)
    assert seen == sorted(seen) and seen[-1] == 300_000
    assert [window.start_ms for window in stretch.windows][0] == 0
    assert {item.window for item in stretch.sentences} <= {w.id for w in stretch.windows}


def test_a_window_near_the_stretchs_end_runs_to_it() -> None:
    recorder = Recorder([])
    run(hear_stretch(recorder, 0, 140_000))
    assert [call[:2] for call in recorder.calls] == [(0, 140_000)]


def test_silence_moves_on_and_a_window_with_text_only_is_one_sentence() -> None:
    recorder = Recorder([])
    stretch = run(hear_stretch(recorder, 0, 300_000))
    assert stretch.sentences == []
    assert [call[:2] for call in recorder.calls] == [
        (0, 120_000),
        (120_000, 240_000),
        (240_000, 300_000),
    ]

    text_only = Recorder(timeline(30), word_times=False)
    stretch = run(hear_stretch(text_only, 0, 300_000))
    assert [(item.start_ms, item.end_ms) for item in stretch.sentences] == [
        (0, 120_000),
        (120_000, 240_000),
        (240_000, 300_000),
    ]


def test_a_one_sentence_answer_is_asked_again_with_a_longer_window_then_kept() -> None:
    answers = [
        [(0, 100_000, "A long sentence that fills the window.")],
        [(0, 100_000, "A long sentence that fills the window.")],
    ]
    calls: list[tuple[int, int]] = []

    async def hear(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        calls.append((start_ms, end_ms))
        index = len(calls) - 1
        items = answers[index] if index < len(answers) else []
        return {
            "text": " ".join(text for _, _, text in items),
            "sentences": [{"start_ms": a, "end_ms": b, "text": t} for a, b, t in items],
        }

    stretch = run(hear_stretch(hear, 0, 600_000))
    assert calls[:3] == [(0, 120_000), (0, 180_000), (100_000, 220_000)]
    assert [item.text for item in stretch.sentences] == ["A long sentence that fills the window."]


def test_the_last_window_of_a_stretch_keeps_everything() -> None:
    recorder = Recorder(timeline(5))
    stretch = run(hear_stretch(recorder, 0, 60_000))
    assert len(recorder.calls) == 1
    assert [item.text for item in stretch.sentences] == [f"Sentence {i}." for i in range(5)]


# Seams -----------------------------------------------------------------------------------


def stretch_of(*sentences: tuple[int, int, str], window: str) -> Stretch:
    return Stretch(
        [Heard(a, b, text, window) for a, b, text in sentences],
        [Window(window, sentences[0][0], sentences[-1][1])],
    )


def test_a_seam_is_heard_as_one_request_and_replaces_the_two_edge_sentences() -> None:
    first = stretch_of((0, 5_000, "One."), (595_000, 600_000, "So the loss jumps"), window="w-0")
    second = stretch_of(
        (600_000, 603_000, "around and we lower the rate."),
        (604_000, 606_000, "Next."),
        window="w-600000",
    )
    calls: list[tuple[int, int, str]] = []

    async def hear(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        calls.append((start_ms, end_ms, before))
        sentence = {
            "start_ms": 595_000,
            "end_ms": 603_000,
            "text": "So the loss jumps around and we lower the rate.",
        }
        return {"text": sentence["text"], "sentences": [sentence]}

    merged = run(mend_seams(hear, [first, second]))
    assert calls == [(595_000, 603_000, "One.")]
    assert [item.text for item in merged.sentences] == [
        "One.",
        "So the loss jumps around and we lower the rate.",
        "Next.",
    ]
    assert len(merged.windows) == 3  # two stretches' windows and the seam
    assert merged.sentences[1].window in {window.id for window in merged.windows}


def test_an_empty_seam_answer_keeps_both_and_a_long_seam_is_not_asked() -> None:
    first = stretch_of((0, 5_000, "One."), (595_000, 600_000, "So it"), window="w-0")
    second = stretch_of((600_000, 603_000, "goes on."), window="w-600000")

    async def silent(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        return {"text": "", "sentences": []}

    kept = run(mend_seams(silent, [first, second]))
    assert [item.text for item in kept.sentences] == ["One.", "So it", "goes on."]

    far = stretch_of((0, 5_000, "One."), (400_000, 600_000, "A very long one"), window="w-0")
    asked: list[Any] = []

    async def hear(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        asked.append((start_ms, end_ms))
        return {"text": "x", "sentences": [{"start_ms": start_ms, "end_ms": end_ms, "text": "x"}]}

    left = run(mend_seams(hear, [far, second]))
    assert asked == []
    assert [item.text for item in left.sentences] == ["One.", "A very long one", "goes on."]


def test_a_stretch_with_nothing_heard_has_no_seam() -> None:
    first = stretch_of((0, 5_000, "One."), window="w-0")
    empty = Stretch()
    third = stretch_of((1_200_000, 1_205_000, "Later."), window="w-1200000")

    async def hear(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        raise AssertionError("no seam to hear")

    merged = run(mend_seams(hear, [first, empty, third]))
    assert [item.text for item in merged.sentences] == ["One.", "Later."]


def test_a_recording_is_cut_in_ten_minute_stretches_and_a_short_tail_joins() -> None:
    assert stretches(0) == []
    assert stretches(90_000) == [(0, 90_000)]
    assert stretches(1_500_000) == [(0, 600_000), (600_000, 1_200_000), (1_200_000, 1_500_000)]
    assert stretches(1_260_000) == [(0, 600_000), (600_000, 1_260_000)]
    assert stretches(720_000) == [(0, 600_000), (600_000, 720_000)]


# Providers that answer text only ----------------------------------------------------


def test_text_windows_start_and_end_at_pieces_and_are_a_minute_or_more() -> None:
    pieces = [(i * 15_000, i * 15_000 + 14_000) for i in range(10)]
    assert text_windows(pieces) == [(0, 74_000), (75_000, 149_000)]
    assert text_windows(pieces[:6]) == [(0, 74_000), (75_000, 89_000)]
    # A long silence is never inside a window.
    assert text_windows([(0, 14_000), (600_000, 614_000)]) == [(0, 14_000), (600_000, 614_000)]
    assert text_windows([]) == []


def test_each_text_window_is_one_sentence() -> None:
    texts = iter(["First part", "", "Third part"])
    calls: list[tuple[int, int, str]] = []

    async def hear(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        calls.append((start_ms, end_ms, before))
        return {"text": next(texts), "sentences": []}

    stretch = run(hear_text_run(hear, [(0, 62_000), (70_000, 131_000), (140_000, 200_000)], "Old."))
    assert [(item.start_ms, item.end_ms, item.text) for item in stretch.sentences] == [
        (0, 62_000, "First part"),
        (140_000, 200_000, "Third part"),
    ]
    assert calls[0][2] == "Old." and calls[2][2] == "Old. First part"
    assert len(stretch.windows) == 3


# Paragraphs ------------------------------------------------------------------------------


def numbered(count: int, per_window: int = 10) -> list[Heard]:
    return [
        Heard(i * 1_000, i * 1_000 + 900, f"Sentence {i}.", f"w-{i // per_window}")
        for i in range(count)
    ]


def test_grouping_goes_in_chunks_of_at_most_300_sentences_and_applies_the_starts() -> None:
    sentences = numbered(700)
    seen: list[tuple[int, str]] = []

    def group(texts: list[str], before: str) -> list[int]:
        seen.append((len(texts), before))
        return list(range(0, len(texts), 5))

    paragraphs = regroup(sentences, group)
    assert [count for count, _ in seen] == [300, 300, 100]
    assert seen[0][1] == ""
    assert seen[1][1] == "Sentence 295. Sentence 296. Sentence 297. Sentence 298. Sentence 299."
    assert [len(p) for p in paragraphs] == [5] * 140
    assert [i for p in paragraphs for i in p] == list(range(700))


def test_unusable_starts_leave_one_paragraph_and_errors_are_not_hidden() -> None:
    sentences = numbered(12)
    assert regroup(sentences, lambda texts, before: [99, -3, "x"]) == [list(range(12))]
    one_chunk = regroup(sentences, lambda texts, before: [])
    assert [len(p) for p in one_chunk] == [12]

    def broken(texts: list[str], before: str) -> list[int]:
        raise RuntimeError("model said no")

    with pytest.raises(RuntimeError):
        regroup(sentences, broken)


def test_without_grouping_each_window_is_a_paragraph() -> None:
    assert [len(p) for p in paragraphs_by_window(numbered(25))] == [10, 10, 5]


def test_the_structured_transcript_has_the_shape_the_desktop_reads() -> None:
    sentences = numbered(6, per_window=3)
    windows = [Window("w-0", 0, 3_000), Window("w-1", 3_000, 6_000)]
    old = [(0, 2_000, "Sentense zero"), (2_000, 4_000, "one"), (4_000, 6_000, "two")]
    state = transcript_state(
        sentences,
        windows,
        [[0, 1, 2, 3], [4, 5]],
        old,
        model="m",
        at="2026-10-12T00:00:00+00:00",
        grouped=True,
    )
    assert state["version"] == 1
    assert state["draft"] is None and state["checking"] is None and state["live"] == []
    assert state["gaps"] == [] and state["grouping"] == {"state": "idle"}
    assert state["confirmedUntilMs"] == 5_900
    assert [s["id"] for s in state["sentences"]][:2] == ["s-0", "s-1000"]
    assert "changed" not in state["sentences"][0]
    assert all(p["frozen"] for p in state["paragraphs"])
    assert state["paragraphs"][0] == {
        "id": "p-0",
        "sentenceIds": ["s-0", "s-1000", "s-2000", "s-3000"],
        "frozen": True,
    }
    first = state["windows"][0]
    assert first["state"] == "checked" and first["pass"] == "minute" and first["model"] == "m"
    assert [line["text"] for line in first["live"]] == ["Sentense zero", "one"]
    assert render(state) == (
        "[00:00] Sentence 0. Sentence 1. Sentence 2. Sentence 3.\n\n[00:04] Sentence 4. Sentence 5."
    )
    off = transcript_state(
        sentences, windows, [[0, 1, 2], [3, 4, 5]], old, model="m", at="x", grouped=False
    )
    assert off["grouping"] == {"state": "off"}


# The app, the runner and the endpoints ---------------------------------------------


class FakeGateway:
    """The Analysis model: paragraph starts at every sixth sentence, after an optional failure."""

    def __init__(self, fail: int = 0) -> None:
        self.fail = fail
        self.requests = 0
        self.model = SimpleNamespace(display="analysis-model")

    def for_role(self, role: str) -> Any:
        return (self.model, "low") if role == "analysis" else None

    def complete(self, model: Any, **request: Any) -> Any:
        self.requests += 1
        if self.fail:
            self.fail -= 1
            from gunther.llm import ModelError

            raise ModelError("The analysis model is down")
        count = len(re.findall(r"^\d+\. ", request["messages"], flags=re.MULTILINE))
        return SimpleNamespace(text=json.dumps({"starts": list(range(1, count + 1, 6))}))


class FakeRecording:
    """Stands in for ``relisten_audio``: answers from a timeline, or fails on demand."""

    def __init__(self, sentences: list[tuple[int, int, str]]) -> None:
        self.recorder = Recorder(sentences)
        self.fail: str | None = None
        # Only windows starting here or later fail, so earlier ones were answered.
        self.fail_from_ms = 0
        self.requests = 0

    async def __call__(self, choice: Any, pcm: bytes, **request: Any) -> dict[str, Any]:
        self.requests += 1
        if self.fail and request["start_ms"] >= self.fail_from_ms:
            raise ListenError(self.fail)
        assert len(pcm) > 0
        return await self.recorder(request["start_ms"], request["end_ms"], request["before"])


def make_app(tmp_path: Path) -> TestClient:
    client = TestClient(create_app(settings_for(tmp_path)))
    return client


def sessions_of(client: TestClient) -> Any:
    return client.app.state.knowledge_service.sessions


def old_content(recording_id: str, lines: int = 20) -> str:
    body = "\n".join(f"[{clock_label(i * 3_000)}] old words {i}" for i in range(lines))
    head = CONTENT_HEAD.replace(REC, recording_id)
    return head + "## Transcript\n\n" + body


def seed(
    client: TestClient,
    tmp_path: Path,
    recording_id: str = REC,
    *,
    seconds: float = 100.0,
    created: Any = None,
    content: str | None = None,
    status: str = "completed",
    segments: str | None = None,
    audio: bool = True,
    title: str = "Lecture",
) -> str:
    """A recording with its audio and a source holding its transcript; returns the source id."""

    directory = tmp_path / "recordings"
    directory.mkdir(exist_ok=True)
    name = f"{recording_id}.wav"
    if audio:
        encode(directory / name, "wav", "pcm_s16le", seconds, 8_000)
    with session_scope(sessions_of(client)) as session:
        session.add(
            RecordingSession(
                id=recording_id,
                title=title,
                status=status,
                file_name=name,
                content_type="audio/wav",
                transcript="[00:00] old words 0",
                transcript_segments_json=segments,
                created_at=created or utc_now(),
                updated_at=utc_now(),
            )
        )
    created_source = client.post(
        "/api/sources",
        headers=SIDECAR,
        json={
            "title": title,
            "kind": "note",
            "content": old_content(recording_id) if content is None else content,
        },
    )
    assert created_source.status_code == 201
    return created_source.json()["source"]["id"]


@pytest.fixture
def heard(monkeypatch: pytest.MonkeyPatch) -> FakeRecording:
    fake = FakeRecording(timeline(9))
    monkeypatch.setattr(transcript_upgrade, "relisten_audio", fake)
    return fake


def ready(client: TestClient, gateway: FakeGateway | None = None) -> None:
    with_provider(client, "qwen")
    client.app.state.models = gateway or FakeGateway()
    client.app.state.transcript_upgrader.retry_waits = (0, 0, 0)


def upgrader_of(client: TestClient) -> transcript_upgrade.TranscriptUpgrader:
    return client.app.state.transcript_upgrader


def row_of(client: TestClient, recording_id: str = REC) -> dict[str, Any] | None:
    with session_scope(sessions_of(client)) as session:
        row = session.get(TranscriptUpgrade, recording_id)
        if row is None:
            return None
        return {
            "state": row.state,
            "attempts": row.attempts,
            "error": row.error,
            "retry_at": row.retry_at,
            "old": row.old_transcript,
            "model": row.model,
            "sentences": row.sentences_json,
            "heard_ms": row.heard_ms,
            "total_ms": row.total_ms,
        }


def test_an_old_recording_is_heard_again_grouped_and_written_everywhere(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        source_id = seed(client, tmp_path)
        ready(client)
        original = old_content(REC)
        upgrader = upgrader_of(client)
        assert client.get(f"/api/recordings/{REC}/upgrade", headers=SIDECAR).json() == {
            "state": "waiting",
            "reason": None,
            "ahead": 0,
            "heardMs": 0,
            "totalMs": 0,
            "error": None,
        }
        assert run(upgrader.upgrade_one(REC)) is True

        with session_scope(sessions_of(client)) as session:
            source = session.get(Source, source_id)
            recording = session.get(RecordingSession, REC)
            state = json.loads(recording.transcript_segments_json)
            head, heading, _ = transcript_section(original)
            # Everything before the transcript is untouched; the transcript is the new text.
            assert source.content == head + heading + "\n\n" + recording.transcript
            assert source.content.startswith(CONTENT_HEAD.replace(REC, REC) + "## Transcript")
            assert (
                "## Summary\nGradient descent.\nTerms: gradient · loss function" in source.content
            )
            assert "old words" not in source.content
            assert recording.transcript == render(state)
            assert recording.transcript.startswith("[00:05] Sentence 0. Sentence 1.")
            assert "\n\n[01:05] Sentence 6." in recording.transcript
            assert state["version"] == 1 and state["grouping"] == {"state": "idle"}
            assert len(state["sentences"]) == 9
            assert state["windows"][0]["live"][0]["text"] == "old words 0"
            assert all(p["frozen"] for p in state["paragraphs"])
            assert source.content_hash
            revisions = session.scalars(
                select(SourceRevision).where(SourceRevision.source_id == source_id)
            ).all()
            assert len(revisions) == 2
            head_revision = session.get(SourceIndexHead, source_id).revision_id
            old_revision = next(r for r in revisions if r.id != head_revision)
            for revision in revisions:
                blocks = session.scalar(
                    select(func.count())
                    .select_from(ContentBlock)
                    .where(ContentBlock.revision_id == revision.id)
                )
                assert blocks > 0
            old_text = " ".join(
                block.content
                for block in session.scalars(
                    select(ContentBlock).where(ContentBlock.revision_id == old_revision.id)
                )
            )
            assert "old words 3" in old_text
        row = row_of(client)
        assert row["state"] == "done" and row["attempts"] == 0 and row["error"] is None
        assert row["old"].startswith("[00:00] old words 0") and row["model"]
        assert row["sentences"] is None
        # The listen copy is gone, and the status says done.
        assert not list((tmp_path / "recordings").glob("*.listen.*"))
        assert (
            client.get(f"/api/recordings/{REC}/upgrade", headers=SIDECAR).json()["state"] == "done"
        )
        # A second run does nothing.
        before = heard.requests
        assert run(upgrader.upgrade_one(REC)) is False
        assert heard.requests == before and run(upgrader.step()) is False


def test_a_long_recording_is_heard_in_parallel_stretches_with_its_seams_mended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRecording(timeline(77))  # the last one ends at 775 s
    monkeypatch.setattr(transcript_upgrade, "relisten_audio", fake)
    with make_app(tmp_path) as client:
        # 13 minutes: two stretches (0-10 min, 10-13 min) and one seam at 10:00.
        seed(client, tmp_path, seconds=780.0)
        ready(client)
        assert run(upgrader_of(client).upgrade_one(REC)) is True
        with session_scope(sessions_of(client)) as session:
            state = json.loads(session.get(RecordingSession, REC).transcript_segments_json)
        texts = [s["text"] for s in state["sentences"]]
        assert "(partial)" not in " ".join(texts)
        assert texts == [f"Sentence {i}." for i in range(77)]
        assert any(w["id"] == "w-595000" for w in state["windows"])
        assert row_of(client)["total_ms"] > 779_000


def test_without_an_analysis_model_each_window_is_one_paragraph(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        with_provider(client, "qwen")
        client.app.state.models = SimpleNamespace(for_role=lambda role: None)
        assert run(upgrader_of(client).upgrade_one(REC)) is True
        with session_scope(sessions_of(client)) as session:
            state = json.loads(session.get(RecordingSession, REC).transcript_segments_json)
        assert state["grouping"] == {"state": "off"}
        assert len(state["paragraphs"]) == len(state["windows"]) == 1


def test_a_provider_without_word_times_gives_one_paragraph_per_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def text_only(choice: Any, pcm: bytes, **request: Any) -> dict[str, Any]:
        return {"text": f"Heard {request['start_ms']}", "sentences": []}

    monkeypatch.setattr(transcript_upgrade, "relisten_audio", text_only)
    monkeypatch.setattr(
        transcript_upgrade,
        "speech_pieces",
        lambda directory, recording_id, total: [(1_000, 14_000), (20_000, 34_000)],
    )
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        with_provider(client, "compatible")
        client.app.state.models = SimpleNamespace(for_role=lambda role: None)
        assert run(upgrader_of(client).upgrade_one(REC)) is True
        with session_scope(sessions_of(client)) as session:
            state = json.loads(session.get(RecordingSession, REC).transcript_segments_json)
        assert [s["text"] for s in state["sentences"]] == ["Heard 1000"]
        assert [(s["startMs"], s["endMs"]) for s in state["sentences"]] == [(1_000, 34_000)]


# Which recordings are candidates -----------------------------------------------------


def candidate_ids(client: TestClient) -> list[str]:
    with session_scope(sessions_of(client)) as session:
        return [c.recording_id for c in upgrader_of(client).candidates(session)]


def test_only_old_completed_recordings_with_audio_and_a_live_source_are_candidates(
    tmp_path: Path,
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path, REC)
        seed(client, tmp_path, "rec_" + "1" * 24, segments='{"version": 1}')
        seed(
            client,
            tmp_path,
            "rec_" + "2" * 24,
            content="# Typed\n\nLocal recording: "
            + "rec_"
            + "2" * 24
            + "\n\n## Transcript\n\nJust some pasted text.",
        )
        seed(
            client,
            tmp_path,
            "rec_" + "3" * 24,
            content="# None\n\nDuration: 1:00 · Captured: x · Local recording: "
            + "rec_"
            + "3" * 24,
        )
        seed(client, tmp_path, "rec_" + "4" * 24, status="capturing")
        seed(client, tmp_path, "rec_" + "5" * 24, audio=False)
        empty = "rec_" + "6" * 24
        seed(client, tmp_path, empty, audio=False)
        (tmp_path / "recordings" / f"{empty}.wav").write_bytes(b"")
        new_format = "rec_" + "7" * 24
        seed(
            client,
            tmp_path,
            new_format,
            content=old_content(new_format).replace("[00:03]", "\n[00:03]"),
            segments='{"version": 1}',
        )
        assert candidate_ids(client) == [REC]


def test_a_trashed_source_is_not_a_candidate_and_another_live_one_keeps_it(
    tmp_path: Path,
) -> None:
    with make_app(tmp_path) as client:
        first = seed(client, tmp_path)
        assert candidate_ids(client) == [REC]
        extra = client.post(
            "/api/sources",
            headers=SIDECAR,
            json={"title": "Copy", "kind": "note", "content": old_content(REC) + "\n[01:00] more"},
        ).json()["source"]["id"]
        assert client.post(f"/api/sources/{first}/trash", headers=SIDECAR).status_code == 200
        assert candidate_ids(client) == [REC]
        assert client.post(f"/api/sources/{extra}/trash", headers=SIDECAR).status_code == 200
        assert candidate_ids(client) == []
        assert (
            client.get(f"/api/recordings/{REC}/upgrade", headers=SIDECAR).json()["state"] == "none"
        )


def test_every_source_of_a_recording_with_an_old_transcript_gets_the_new_one(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        first = seed(client, tmp_path)
        second = client.post(
            "/api/sources",
            headers=SIDECAR,
            json={"title": "Copy", "kind": "note", "content": old_content(REC) + "\n[01:00] more"},
        ).json()["source"]["id"]
        ready(client)
        assert run(upgrader_of(client).upgrade_one(REC)) is True
        with session_scope(sessions_of(client)) as session:
            transcript = session.get(RecordingSession, REC).transcript
            for source_id in (first, second):
                assert session.get(Source, source_id).content.endswith(transcript)


def test_newest_first_and_a_live_recording_comes_before_everything(
    tmp_path: Path, heard: FakeRecording
) -> None:
    now = utc_now()
    with make_app(tmp_path) as client:
        seed(client, tmp_path, REC, created=now - timedelta(days=3))
        seed(client, tmp_path, OTHER, created=now - timedelta(days=1))
        seed(client, tmp_path, THIRD, created=now - timedelta(days=2))
        ready(client)
        upgrader = upgrader_of(client)
        assert candidate_ids(client) == [OTHER, THIRD, REC]
        order: list[str] = []
        real = upgrader.upgrade_one

        async def recording(recording_id: str) -> bool:
            order.append(recording_id)
            return await real(recording_id)

        upgrader.upgrade_one = recording  # type: ignore[method-assign]

        # A recording being captured and touched a moment ago holds everything back...
        with session_scope(sessions_of(client)) as session:
            live = RecordingSession(
                id="rec_" + "9" * 24,
                title="Live",
                status="capturing",
                file_name="live.webm",
                updated_at=utc_now(),
            )
            session.add(live)
        assert upgrader.holding_back() == "recording"
        assert run(upgrader.step()) is False and order == []
        status = client.get(f"/api/recordings/{OTHER}/upgrade", headers=SIDECAR).json()
        assert status["state"] == "waiting" and status["reason"] == "recording"
        # ...but one nobody has touched for a while does not.
        with session_scope(sessions_of(client)) as session:
            session.get(RecordingSession, "rec_" + "9" * 24).updated_at = utc_now() - timedelta(
                minutes=10
            )
        assert upgrader.holding_back() is None
        status = client.get(f"/api/recordings/{REC}/upgrade", headers=SIDECAR).json()
        assert status["state"] == "waiting" and status["reason"] == "queue" and status["ahead"] == 2
        for _ in range(3):
            assert run(upgrader.step()) is True
        assert order == [OTHER, THIRD, REC]
        assert run(upgrader.step()) is False


def test_nothing_starts_without_a_recording_model(tmp_path: Path, heard: FakeRecording) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        client.app.state.models = FakeGateway()
        registry = client.app.state.speech_registry
        client.app.state.speech_registry = dataclasses.replace(registry, roles={})
        upgrader = upgrader_of(client)
        assert upgrader.holding_back() == "model"
        assert run(upgrader.step()) is False and heard.requests == 0
        status = client.get(f"/api/recordings/{REC}/upgrade", headers=SIDECAR).json()
        assert status["state"] == "waiting" and status["reason"] == "model"
        # The model is set in Settings: the next look finds work.
        with_provider(client, "qwen")
        assert upgrader.holding_back() is None


# Failures ------------------------------------------------------------------------------


def snapshot(client: TestClient, source_id: str) -> tuple[str, str, str | None]:
    with session_scope(sessions_of(client)) as session:
        recording = session.get(RecordingSession, REC)
        return (
            session.get(Source, source_id).content,
            recording.transcript,
            recording.transcript_segments_json,
        )


def test_a_provider_failure_writes_nothing_keeps_the_error_and_retry_tries_it_next(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        source_id = seed(client, tmp_path)
        seed(client, tmp_path, OTHER, created=utc_now() - timedelta(days=5))
        ready(client)
        upgrader = upgrader_of(client)
        before = snapshot(client, source_id)
        heard.fail = "Qwen answered 401: Invalid API key"
        assert run(upgrader.upgrade_one(REC)) is False
        assert snapshot(client, source_id) == before
        assert heard.requests == 3  # tried three times
        row = row_of(client)
        # The model never answered: not counted against the recording.
        assert row["state"] == "failed" and row["attempts"] == 0
        assert "401" in row["error"]
        wait = row["retry_at"] - utc_now()
        assert timedelta(minutes=9) < wait <= timedelta(minutes=10)
        status = client.get(f"/api/recordings/{REC}/upgrade", headers=SIDECAR).json()
        assert status["state"] == "failed" and "401" in status["error"]
        assert not list((tmp_path / "recordings").glob("*.listen.*"))
        # One failing recording does not block the queue: the next one goes.
        assert candidate_ids(client) == [OTHER]

        # Retry puts it first and says it is waiting again.
        heard.fail = None
        retried = client.post(f"/api/recordings/{REC}/upgrade/retry", headers=SIDECAR)
        assert retried.status_code == 200 and retried.json()["state"] == "waiting"
        assert candidate_ids(client) == [REC, OTHER]
        assert run(upgrader.step()) is True
        assert row_of(client)["state"] == "done"
        assert snapshot(client, source_id)[0] != before[0]


def test_retry_is_refused_for_unknown_or_unfailed_recordings(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        url = "/api/recordings/{}/upgrade/retry"
        assert client.post(url.format("rec_" + "f" * 24), headers=SIDECAR).status_code == 404
        assert client.post(url.format("bad id!"), headers=SIDECAR).status_code == 404
        assert client.post(url.format(REC), headers=SIDECAR).status_code == 409
        assert (
            client.get("/api/recordings/rec_" + "f" * 24 + "/upgrade", headers=SIDECAR).json()[
                "state"
            ]
            == "none"
        )


def test_automatic_retries_wait_then_stop_until_the_user_retries(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path, seconds=300.0)
        ready(client)
        upgrader = upgrader_of(client)
        # The first window is answered, a later one fails: something about this recording.
        heard.fail, heard.fail_from_ms = "down", 50_000
        gaps = []
        for _ in range(3):
            assert run(upgrader.upgrade_one(REC)) is False
            row = row_of(client)
            gaps.append(None if row["retry_at"] is None else row["retry_at"] - utc_now())
            with session_scope(sessions_of(client)) as session:  # time passes
                if session.get(TranscriptUpgrade, REC).retry_at is not None:
                    session.get(TranscriptUpgrade, REC).retry_at = utc_now() - timedelta(seconds=1)
        assert timedelta(minutes=9) < gaps[0] <= timedelta(minutes=10)
        assert timedelta(minutes=59) < gaps[1] <= timedelta(hours=1)
        assert gaps[2] is None
        assert row_of(client)["attempts"] == 3
        assert candidate_ids(client) == []  # waits for Retry


def test_when_no_model_answers_nothing_else_is_tried_until_the_wait_ends_or_settings_change(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        seed(client, tmp_path, OTHER, created=utc_now() - timedelta(days=5))
        ready(client)
        upgrader = upgrader_of(client)
        heard.fail = "Qwen could not be reached"
        assert run(upgrader.step()) is True  # it ran, and failed
        assert row_of(client)["state"] == "failed" and row_of(client)["attempts"] == 0
        requests = heard.requests
        # The other recording is not tried while the models are not answering.
        assert run(upgrader.step()) is False
        assert heard.requests == requests and row_of(client, OTHER) is None
        status = client.get(f"/api/recordings/{OTHER}/upgrade", headers=SIDECAR).json()
        assert (status["state"], status["reason"]) == ("waiting", "service")
        # The waits grow, and the recording is never given up on.
        waits = []
        for _ in range(4):
            upgrader._paused_until = None
            with session_scope(sessions_of(client)) as session:
                session.get(TranscriptUpgrade, REC).retry_at = utc_now() - timedelta(seconds=1)
            run(upgrader.upgrade_one(REC))
            waits.append(row_of(client)["retry_at"] - utc_now())
        assert timedelta(minutes=59) < waits[0] <= timedelta(hours=1)
        assert all(timedelta(hours=5) < wait <= timedelta(hours=6) for wait in waits[1:])
        assert row_of(client)["attempts"] == 0
        # A saved setting (or Retry) ends the wait: both go, the one that failed included.
        heard.fail = None
        upgrader.wake()
        assert run(upgrader.step()) is True
        assert run(upgrader.step()) is True
        assert row_of(client)["state"] == "done" and row_of(client, OTHER)["state"] == "done"


def test_a_failed_grouping_keeps_what_was_heard_and_the_next_attempt_only_groups(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        source_id = seed(client, tmp_path)
        gateway = FakeGateway(fail=3)
        ready(client, gateway)
        upgrader = upgrader_of(client)
        before = snapshot(client, source_id)
        assert run(upgrader.upgrade_one(REC)) is False
        assert snapshot(client, source_id) == before
        row = row_of(client)
        assert row["state"] == "failed" and "analysis model is down" in row["error"]
        assert json.loads(row["sentences"])["sentences"]
        requests = heard.requests
        client.post(f"/api/recordings/{REC}/upgrade/retry", headers=SIDECAR)
        assert run(upgrader.step()) is True
        assert heard.requests == requests  # nothing was listened to again
        assert row_of(client)["state"] == "done"


def test_unreadable_audio_and_silence_fail_with_a_plain_message(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path, audio=False)
        (tmp_path / "recordings" / f"{REC}.wav").write_bytes(b"not audio at all" * 100)
        ready(client)
        assert run(upgrader_of(client).upgrade_one(REC)) is False
        assert "couldn't be read" in row_of(client)["error"]

        heard.recorder.sentences = []
        encode(tmp_path / "recordings" / f"{REC}.wav", "wav", "pcm_s16le", 20.0, 8_000)
        client.post(f"/api/recordings/{REC}/upgrade/retry", headers=SIDECAR)
        assert run(upgrader_of(client).upgrade_one(REC)) is False
        assert row_of(client)["error"] == "Nothing was heard in the audio."


def test_progress_is_kept_while_it_runs_and_a_crashed_row_counts_as_not_started(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        ready(client)
        upgrader = upgrader_of(client)
        with session_scope(sessions_of(client)) as session:
            session.add(TranscriptUpgrade(recording_id=REC, state="running", total_ms=1))
        # Left "running" by a crash, and not the one running now: it is waiting.
        assert candidate_ids(client) == [REC]
        assert (
            client.get(f"/api/recordings/{REC}/upgrade", headers=SIDECAR).json()["state"]
            == "waiting"
        )
        seen: list[dict[str, Any]] = []
        real = heard.__call__

        async def watching(choice: Any, pcm: bytes, **request: Any) -> dict[str, Any]:
            seen.append(client.get(f"/api/recordings/{REC}/upgrade", headers=SIDECAR).json())
            return await real(choice, pcm, **request)

        transcript_upgrade.relisten_audio = watching
        try:
            assert run(upgrader.upgrade_one(REC)) is True
        finally:
            transcript_upgrade.relisten_audio = heard
        assert seen[0]["state"] == "running" and seen[0]["totalMs"] > 99_000


def test_deleting_a_recording_forever_removes_its_upgrade_row(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        source_id = seed(client, tmp_path)
        ready(client)
        heard.fail = "down"
        run(upgrader_of(client).upgrade_one(REC))
        assert row_of(client) is not None
        assert client.post(f"/api/sources/{source_id}/trash", headers=SIDECAR).status_code == 200
        assert client.delete(f"/api/trash/source/{source_id}", headers=SIDECAR).status_code == 200
        assert row_of(client) is None
