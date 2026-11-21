"""A visual-only review saves the inspected candidate and preserves version/source identities."""

import pytest
from fake_models import FakeProvider
from test_output_skills import saved_skill_output
from test_outputs import app_for, built, library, read_events, workspace
from test_slide_review import Renderer


@pytest.mark.parametrize(
    "failure, expected_status, calls",
    [(None, "clean", 1), ("render", "unavailable", 0), ("image", "needs_review", 2)],
)
def test_review_current_renders_save_reopen_idempotency_freshness_and_access(
    tmp_path, failure, expected_status, calls
):
    class OverflowingSlide(Renderer):
        def capture(self, candidate, use="talk"):
            snapshot = super().capture(candidate, use)
            snapshot["slides"][0]["overflow"] = True
            return snapshot

    fake = FakeProvider()
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        original = saved_skill_output(client, base, kind="slides")
        renderer = (
            OverflowingSlide(tmp_path) if failure == "image"
            else Renderer(tmp_path, failure=failure == "render")
        )
        client.app.state.knowledge_service.slide_renderer = renderer
        fake.replies[:] = ["unreadable" if failure == "image" else '{"findings":[]}']
        before_calls = len(fake.requests)
        path = f"/api/knowledge-bases/{base}/artifacts/{original['id']}/visual-review/stream"
        payload = {"clientRequestId": "visual-review-once"}
        headers = workspace(client)
        response = client.post(path, json=payload, headers=headers)
        reviewed = built(read_events(response))
        assert reviewed["versionNumber"] == original["versionNumber"] + 1
        assert reviewed["content"] == original["content"]
        assert reviewed["citations"] == original["citations"]
        assert reviewed["outline"] == original["outline"]
        assert reviewed["sections"] == original["sections"]
        visual = reviewed["provenance"]["skill"]["visualReview"]
        assert visual["status"] == expected_status
        again = built(read_events(client.post(path, json=payload, headers=headers)))
        assert again["id"] == reviewed["id"]
        assert len(fake.requests) - before_calls == calls
        assert len(renderer.candidates) == 1
        saved_path = f"/api/knowledge-bases/{base}/artifacts/{reviewed['id']}"
        reopened = client.get(saved_path, headers=headers).json()
        assert (
            reopened["provenance"]["skill"]["renderCheck"]["candidateHash"]
            == reviewed["provenance"]["skill"]["visualReview"]["candidateHash"]
        )
        if failure == "render":
            assert visual["reason"] == "Browser unavailable"
            return
        if failure == "image":
            skill = reopened["provenance"]["skill"]
            assert skill["renderCheck"]["status"] == "captured"
            assert skill["visualReview"] == visual
            assert visual["imageReview"] == "unavailable"
            assert "shape Gunther could not read" in visual["reason"]
            assert any("overflows" in finding["problem"] for finding in visual["findings"])
            return
        workspace_id = headers["X-Gunther-Workspace-Id"]
        image = saved_path + f"/renders/capture1/contact.png?workspace_id={workspace_id}"
        assert client.get(image, headers=headers).status_code == 200
        assert client.get(image.replace(workspace_id, "wrong"), headers=headers).status_code == 404
        cross = image.replace(reviewed["id"], original["id"])
        assert client.get(cross, headers=headers).status_code == 404
        changed = client.post(
            saved_path + "/edits",
            headers=headers,
            json={
                "clientRequestId": "change-style-only",
                "content": reviewed["content"].replace("design: v1", "design: v1; accent: teal"),
            },
        ).json()
        assert changed["provenance"]["skill"]["visualReview"]["status"] == "stale"
        assert changed["citations"] == reviewed["citations"]
