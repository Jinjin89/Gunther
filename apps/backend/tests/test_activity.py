"""The activity list behind the top bar: the registry, and the routes that serve it."""

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from gunther.activity import Activity
from gunther.config import Settings
from gunther.main import create_app
from tests.test_listen_again import SIDECAR_TOKEN

HEADERS = {"X-Gunther-Token": SIDECAR_TOKEN}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def track(activity: Activity, id: str = "output:lib", stop: Any = None, **fields: Any) -> Any:
    return activity.track(id, "output", "Building slides", "Cells", open=None, stop=stop, **fields)


def items(activity: Activity) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in activity.snapshot()["items"]}


def test_a_tracked_job_shows_and_only_real_changes_bump_the_version() -> None:
    activity = Activity()
    tracked = track(activity, detail="Planning")
    first = activity.snapshot()
    [item] = first["items"]
    assert item["title"] == "Building slides" and item["state"] == "running"
    assert item["detail"] == "Planning" and item["progress"] is None
    assert item["canStop"] is False and item["endedAt"] is None and item["error"] is None
    assert item["startedAt"].endswith("+00:00")

    tracked.update(detail="Writing section 1 of 3", progress=0.333)
    second = activity.snapshot()
    assert second["version"] > first["version"]
    assert second["items"][0]["progress"] == 0.33
    # The same again, or a hair of progress, changes nothing.
    tracked.update(detail="Writing section 1 of 3", progress=0.334)
    assert activity.snapshot()["version"] == second["version"]


def test_done_and_failed_end_the_entry_and_a_finished_entry_stays_put() -> None:
    activity = Activity()
    good, bad = track(activity, "output:a"), track(activity, "output:b", stop=lambda: None)
    good.done("Slides ready")
    bad.failed("Couldn't build slides", "The model timed out.")
    found = items(activity)
    assert found["output:a"]["state"] == "done" and found["output:a"]["endedAt"]
    assert found["output:b"]["state"] == "failed" and found["output:b"]["canStop"] is False
    assert found["output:b"]["error"] == "The model timed out."
    version = activity.snapshot()["version"]
    good.update(detail="Back to life?")
    good.failed("Nope", "late")
    assert activity.snapshot()["version"] == version
    assert items(activity)["output:a"]["title"] == "Slides ready"


def test_stopped_removes_and_a_newer_job_with_the_same_id_replaces_the_entry() -> None:
    activity = Activity()
    gone = track(activity, "answer:1")
    gone.stopped()
    assert activity.snapshot()["items"] == []

    old = track(activity, "output:lib")
    new = track(activity, "output:lib", detail="Planning")
    assert len(activity.snapshot()["items"]) == 1
    # The old job's late calls can't touch the new entry.
    old.update(detail="stale", state="running")
    old.done("Slides ready")
    old.stopped()
    now = items(activity)["output:lib"]
    assert now["detail"] == "Planning" and now["state"] == "running"
    new.done("Slides ready")
    assert items(activity)["output:lib"]["state"] == "done"


def test_finished_entries_are_dropped_after_an_hour_and_beyond_the_newest_twenty() -> None:
    clock = Clock()
    activity = Activity(clock)
    track(activity, "output:old").done("Slides ready")
    clock.now += timedelta(minutes=61)
    running = track(activity, "output:running")
    assert set(items(activity)) == {"output:running"}
    for number in range(25):
        clock.now += timedelta(seconds=1)
        track(activity, f"answer:{number}").done("Answer ready")
    found = items(activity)
    assert len(found) == 21 and "output:running" in found
    assert "answer:4" not in found and "answer:5" in found and "answer:24" in found
    running.done("Slides ready")


def test_leaving_by_an_error_never_leaves_a_spinner() -> None:
    activity = Activity()
    try:
        with track(activity, "output:a"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert items(activity)["output:a"]["state"] == "failed"
    assert items(activity)["output:a"]["error"] == "Something went wrong."
    with track(activity, "output:b") as tracked:
        tracked.done("Slides ready")
    assert items(activity)["output:b"]["state"] == "done"


def test_stop_dismiss_and_clear_follow_their_rules() -> None:
    activity = Activity()
    asked: list[str] = []
    track(activity, "output:a", stop=lambda: asked.append("a"))
    track(activity, "output:b")
    track(activity, "output:c").done("Slides ready")
    track(activity, "output:d").failed("Couldn't build slides", "No.")
    track(activity, "transcribe:e", stop=lambda: False)  # e.g. already saving

    assert activity.stop("output:nothing") is None
    assert activity.stop("output:c") is None  # finished: nothing to stop
    assert activity.stop("output:b") is False
    assert activity.stop("transcribe:e") is False
    assert activity.stop("output:a") is True and asked == ["a"]

    assert activity.dismiss("output:nothing") is None
    assert activity.dismiss("output:a") is False
    assert activity.dismiss("output:c") is True
    assert "output:c" not in items(activity)

    activity.clear()
    assert set(items(activity)) == {"output:a", "output:b", "transcribe:e"}
    before = activity.snapshot()["version"]
    activity.clear()
    assert activity.snapshot()["version"] == before


def test_many_jobs_from_many_threads_are_all_held_and_the_version_only_grows() -> None:
    activity = Activity()
    versions: list[int] = []
    ready = threading.Barrier(3)

    def job(number: int) -> None:
        tracked = activity.track(
            f"output:{number}",
            "output",
            "Building slides",
            f"Library {number}",
            open=None,
            stop=None,
        )
        ready.wait(5)
        for step in range(1, 21):
            tracked.update(progress=step / 20, detail=f"Writing section {step} of 20")
            versions.append(activity.snapshot()["version"])
        if number == 2:
            tracked.failed("Couldn't build slides", "Late trouble.")
        else:
            tracked.done("Slides ready")

    threads = [threading.Thread(target=job, args=(number,)) for number in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    found = items(activity)
    assert set(found) == {"output:0", "output:1", "output:2"}
    assert [found[f"output:{n}"]["state"] for n in range(3)] == ["done", "done", "failed"]
    assert all(found[f"output:{n}"]["about"] == f"Library {n}" for n in range(3))
    assert found["output:0"]["progress"] == 1.0
    assert activity.snapshot()["version"] >= max(versions)


def test_waiting_returns_at_once_for_an_old_version_and_wakes_for_a_change() -> None:
    activity = Activity()
    tracked = track(activity)
    version = activity.snapshot()["version"]

    async def scenario() -> tuple[dict, dict, dict, float]:
        stale = await activity.wait_for_change(version - 1, 5)
        timer = threading.Timer(0.1, lambda: tracked.update(detail="Writing section 1 of 2"))
        timer.start()
        began = time.monotonic()
        woken = await activity.wait_for_change(version, 5)
        waited = time.monotonic() - began
        quiet = await activity.wait_for_change(woken["version"], 0.1)
        return stale, woken, quiet, waited

    stale, woken, quiet, waited = asyncio.run(scenario())
    assert stale["version"] == version
    assert woken["version"] > version and woken["items"][0]["detail"] == "Writing section 1 of 2"
    assert waited < 2
    assert quiet["version"] == woken["version"]
    assert activity._waiters == []


# The routes -------------------------------------------------------------------------------


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
        processing_worker_enabled=False,
        auth_token=SIDECAR_TOKEN,
        service_settings_file=tmp_path / "service-settings.json",
    )
    return TestClient(create_app(settings))


def test_the_routes_list_stop_dismiss_and_clear(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        activity: Activity = client.app.state.activity
        asked: list[str] = []
        track(activity, "output:a", stop=lambda: asked.append("a"), detail="Planning")
        track(activity, "output:b")
        track(activity, "output:c").done("Slides ready")

        listed = client.get("/api/activity", headers=HEADERS).json()
        assert {item["id"] for item in listed["items"]} == {"output:a", "output:b", "output:c"}
        first = next(item for item in listed["items"] if item["id"] == "output:a")
        assert first["canStop"] is True and first["startedAt"] and first["open"] is None
        assert set(first) == {
            "id", "kind", "title", "about", "state", "progress", "detail", "error",
            "startedAt", "endedAt", "open", "canStop",
        }  # fmt: skip

        # An old version is answered at once, with the list as it is now.
        stale = client.get(f"/api/activity?after={listed['version'] - 1}", headers=HEADERS)
        assert stale.json()["version"] == listed["version"]

        # The current version waits for a change (made here after a moment).
        threading.Timer(0.2, lambda: activity.dismiss("output:c")).start()
        began = time.monotonic()
        changed = client.get(f"/api/activity?after={listed['version']}", headers=HEADERS).json()
        assert time.monotonic() - began < 5
        assert changed["version"] > listed["version"] and len(changed["items"]) == 2

        # Ids contain a colon; the client sends it encoded.
        assert client.post("/api/activity/output%3Aa/stop", headers=HEADERS).status_code == 204
        assert asked == ["a"]
        assert client.post("/api/activity/output%3Ab/stop", headers=HEADERS).status_code == 409
        assert client.post("/api/activity/output%3Ab/stop", headers=HEADERS).json() == {
            "detail": "This can't be stopped."
        }
        assert client.post("/api/activity/output%3Azzz/stop", headers=HEADERS).status_code == 404

        track(activity, "answer:x").failed("Couldn't write the answer", "Timed out.")
        assert client.post("/api/activity/output%3Aa/dismiss", headers=HEADERS).status_code == 409
        assert client.post("/api/activity/answer%3Ax/dismiss", headers=HEADERS).status_code == 204
        assert client.post("/api/activity/answer%3Ax/dismiss", headers=HEADERS).status_code == 404

        track(activity, "answer:y").done("Answer ready")
        assert client.post("/api/activity/clear", headers=HEADERS).status_code == 204
        left = client.get("/api/activity", headers=HEADERS).json()["items"]
        assert {item["id"] for item in left} == {"output:a", "output:b"}
