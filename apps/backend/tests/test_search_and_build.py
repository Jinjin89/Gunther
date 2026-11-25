import hashlib
from dataclasses import replace

from fake_models import FakeProvider, agent_replies
from test_key_results import ask, checks, create_session
from test_outputs import SIDECAR, app_for, library
from test_studio_pipeline import request_text

from gunther.artifact_workspace import evidence_from_citation
from gunther.evidence import KeyResultMatch, Tool
from gunther.library_lookup import (
    SearchHit,
    annotate_library_sources,
    pack_searched,
    resolve_library_sources,
)
from gunther.models import Asset, SessionMessage, SourceIndexHead, SourcePaper, WebSnapshot, utc_now
from gunther.schemas import ConversationCitationOut


def web_hit(title, url, *, text=None):
    return evidence_from_citation(ConversationCitationOut(
        id=title, kind="web", url=url, source_id="", source_title=title,
        quote=text or title, locator="public web", status="provisional", confidence=1,
    ))


def library_hit(source_id, n):
    return evidence_from_citation(ConversationCitationOut(
        id=f"passage_{n}", source_id=source_id, source_title=f"Library hit {n}",
        quote=f"Passage number {n}", locator=f"Paragraph {n}", status="provisional", confidence=1,
        block_id=f"block_{n}", source_revision_id="revision",
    ))


def saved_web_snapshot(session, source_id, original, final):
    digest = hashlib.sha256(original.encode()).hexdigest()
    asset = Asset(
        id=f"asset_{source_id}", content_hash=digest, original_name="capture.html",
        media_type="text/html", size_bytes=1, relative_path=f"{digest}.html",
    )
    session.add(asset)
    session.flush()
    session.add(WebSnapshot(
        id=f"web_{source_id}", source_id=source_id, asset_id=asset.id, original_url=original,
        final_url=final, captured_at=utc_now(), status=200, content_type="text/html",
        content_hash=digest,
    ))


def test_search_pack_caps_dedupes_and_prioritizes_uncited_web_hits():
    local = [library_hit("source", n) for n in range(11)]
    newer = local[1].citation.model_copy(update={"source_revision_id": "new"})
    changed_passage = replace(local[1], payload=(newer, None))
    web = [web_hit(f"Web hit {n}", f"https://example.org/{n}") for n in range(15)]
    duplicate = web_hit("More relevant excerpt", "HTTPS://EXAMPLE.ORG:443/1#section")
    more = replace(duplicate, title="T" * 300, locator="L" * 300, text="S" * 1000)
    results = pack_searched(
        [SearchHit(hit, "library query", True) for hit in local]
        + [SearchHit(changed_passage, "repeat passage", True)]
        + [SearchHit(hit, "web query", False) for hit in web]
        + [SearchHit(more, "Q" * 500, True)],
        [changed_passage, web_hit("Another excerpt", "https://EXAMPLE.org/0#cite")],
        set(),
    )
    assert [hit.kind for hit in results] == ["web"] * 12 + ["library"] * 8
    first = results[0]
    assert first.kept and first.source_id is None
    assert (len(first.title), len(first.locator), len(first.snippet), len(first.query)) == (
        200, 240, 700, 400,
    )
    assert [hit.title for hit in results[1:12]] == [f"Web hit {n}" for n in range(2, 13)]
    assert [hit.title for hit in results[12:]] == [
        f"Library hit {n}" for n in (0, 2, 3, 4, 5, 6, 7, 8)
    ]


def test_search_pack_preserves_the_query_and_decision_for_a_passage_also_read_whole():
    item = library_hit("source", 1)
    [hit] = pack_searched(
        [SearchHit(item, "", True), SearchHit(item, "trial temperature", False)], [],
    )
    assert (hit.query, hit.kept) == ("trial temperature", False)


def test_ask_saves_searches_and_key_result_refs_without_reusing_searches(tmp_path, monkeypatch):
    fake = FakeProvider(agent_replies(
        "The second passage [2]. The first passage [1].",
        {"actions": [
            {"action": "search_library", "query": "trial records"},
            {"action": "search_web", "query": "outside trial records"},
            {"action": "answer"},
        ]},
        relevant=[1, 2, 3, 4], checked=checks("supported", "supported"),
    ))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base, sources = library(client, notes={"Trial": "This trial recorded its observations."})
        service = client.app.state.knowledge_service
        first, second, other = [library_hit(sources["Trial"], n) for n in range(3)]
        library_tool = Tool(
            "search_library", "Recorded passages", lambda _: [first, second, other], "library",
            key_results=lambda _: [
                KeyResultMatch("used", "Read this checked sentence.", ((second,),)),
                KeyResultMatch("unused", "Another checked sentence.", ((other,),)),
            ],
        )
        web = [web_hit(title, f"https://example.org/{n}")
               for n, title in enumerate(("Gathered web candidate", "Rejected web candidate"))]
        web_tool = Tool("search_web", "Recorded hits", lambda _: web, "web", grade=True)
        monkeypatch.setattr(service, "_library_tool", lambda *args, **kwargs: library_tool)
        monkeypatch.setattr(
            service, "_web_tool", lambda enabled: (web_tool if enabled else None, None),
        )
        chat = create_session(client, base)
        answer = ask(client, chat, web=True)
        context = answer["context"]
        assert [(hit["title"], hit["query"], hit["kept"]) for hit in context["searched"]] == [
            (web[0].title, "outside trial records", True),
            (web[1].title, "outside trial records", False),
            (other.title, "trial records", True),
        ]
        assert [item["ref"] for item in answer["citations"]] == [2, 1]
        assert context["keyResultsUsed"] == [
            {"id": "used", "text": "Read this checked sentence.", "refs": [1]},
        ]
        reopened = client.get(f"/api/sessions/{chat}", headers=SIDECAR).raise_for_status().json()
        assert reopened["messages"][-1]["context"]["searched"] == context["searched"]

        sent = len(fake.requests)
        fake.replies[:] = [agent_replies("The existing passage [2].", {"action": "answer"})]
        follow_up = ask(client, chat, content="What did the answer say?")
        assert follow_up["context"]["searched"] == []
        prompts = "\n".join(request_text(request) for request in fake.requests[sent:])
        assert "The second passage" in prompts
        assert all(hit.title not in prompts for hit in (*web, other))


def test_home_filing_resolves_web_citations_and_hits_by_url_doi_and_arxiv(tmp_path, monkeypatch):
    fake = FakeProvider(agent_replies(
        "The captured page [1].", {"action": "search_web", "query": "archived studies"},
        {"action": "answer"}, checked=checks("supported"),
    ))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base, sources = library(client, notes={
            "Captured": "Saved web reading", "Paper": "Paper note", "Other paper": "Another paper",
        })
        other, _ = library(client, "Other", notes={})
        captured, paper = sources["Captured"], sources["Paper"]
        service = client.app.state.knowledge_service
        original = "https://example.org/landing"
        final = "https://example.org/reading"
        doi = "10.12345/research"
        arxiv = "2405.01234"
        arxiv_url = f"https://example.net/pdf/{arxiv}v4.pdf"
        other_doi = "10.12345/other-paper"
        web = [
            web_hit("Cited page", f"{final}#figure"),
            web_hit("Original URL", "HTTPS://EXAMPLE.ORG:443/landing#top"),
            web_hit("DOI URL", "https://example.net/doi/10.12345%2FRESEARCH"),
            web_hit("arXiv URL", arxiv_url),
            web_hit("Ordinary URL", f"https://example.net/stories/{arxiv}"),
        ]
        with service.sessions.begin() as session:
            saved_web_snapshot(session, captured, original, final)
            session.add(SourcePaper(
                source_id=paper, revision_id=session.get(SourceIndexHead, paper).revision_id,
                title="Paper", doi=doi, arxiv_id=arxiv,
            ))
            other_paper = sources["Other paper"]
            session.add(SourcePaper(
                source_id=other_paper,
                revision_id=session.get(SourceIndexHead, other_paper).revision_id,
                title="Other paper", doi=other_doi,
            ))
        with service.sessions() as session:
            identifiers = [
                (original,), (final,), (None, f"doi:{doi.upper()}"), (None, f"arXiv:{arxiv}v2"),
            ]
            assert resolve_library_sources(session, base, identifiers) == dict(zip(
                identifiers, [captured, captured, paper, paper], strict=True,
            ))
            assert resolve_library_sources(session, other, identifiers) == {}
            conflicting = web_hit("Mixed identifiers", arxiv_url).citation.model_copy(
                update={"source_metadata": {"doi": other_doi}},
            )
            exact = conflicting.model_copy(update={"url": final})
            annotate_library_sources(session, base, [conflicting, exact])
            assert conflicting.library_source_id == other_paper
            assert exact.library_source_id == captured

        tool = Tool("search_web", "Recorded hits", lambda _: web, "web", grade=True)
        monkeypatch.setattr(service, "_web_tool", lambda enabled: (tool if enabled else None, None))
        chat = create_session(client, "@home")
        answer = ask(client, chat, web=True)

        def read_answer():
            return client.get(f"/api/sessions/{chat}", headers=SIDECAR).raise_for_status().json()[
                "messages"
            ][-1]

        def matches(shown):
            items = [*shown["citations"], *shown["context"]["searched"]]
            return [item.get("librarySourceId") for item in items]

        def stored_payloads():
            with service.sessions() as session:
                message = session.get(SessionMessage, answer["id"])
                return message.citations_json, message.context_json

        assert matches(answer) == matches(read_answer()) == [None] * 5
        saved = stored_payloads()
        assert all("library_source_id" not in payload for payload in saved)
        client.post(f"/api/sessions/{chat}/file", headers=SIDECAR, json={
            "knowledgeBaseId": base,
        }).raise_for_status()
        assert matches(read_answer()) == [captured, captured, paper, paper, None]
        client.post(f"/api/sources/{captured}/trash", headers=SIDECAR).raise_for_status()
        assert matches(read_answer()) == [None, None, paper, paper, None]
        client.post(f"/api/trash/source/{captured}/restore", headers=SIDECAR).raise_for_status()
        assert matches(read_answer()) == [captured, captured, paper, paper, None]
        assert stored_payloads() == saved
