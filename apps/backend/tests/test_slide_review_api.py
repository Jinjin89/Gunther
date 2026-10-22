"""A visual-only review saves the inspected candidate and preserves version/source identities."""

from fake_models import FakeProvider, skill_replies
from test_output_skills import SEARCH, SLIDES, SLIDES_APPROACH, SLIDES_OUTLINE
from test_outputs import app_for, body, build, built, library, read_events, workspace
from test_slide_review import Renderer


def test_review_current_renders_save_reopen_idempotency_freshness_and_access(tmp_path):
    fake = FakeProvider(skill_replies(SLIDES_APPROACH, SLIDES_OUTLINE, SLIDES, gather=SEARCH))
    with app_for(tmp_path, fake, skills=True) as client:
        base, _ = library(client)
        original = built(build(client, base, body(kind="slides")))
        renderer = Renderer(tmp_path)
        client.app.state.knowledge_service.slide_renderer = renderer
        fake.replies[:] = ['{"findings":[]}']
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
        assert reviewed["provenance"]["skill"]["visualReview"]["status"] == "clean"
        again = built(read_events(client.post(path, json=payload, headers=headers)))
        assert again["id"] == reviewed["id"]
        assert len(fake.requests) == before_calls + 1 and len(renderer.candidates) == 1
        saved_path = f"/api/knowledge-bases/{base}/artifacts/{reviewed['id']}"
        reopened = client.get(saved_path, headers=headers).json()
        assert (
            reopened["provenance"]["skill"]["renderCheck"]["candidateHash"]
            == reviewed["provenance"]["skill"]["visualReview"]["candidateHash"]
        )
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
