"""A library's arranged contents: sections, one place per source, order, delete."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gunther.config import Settings
from gunther.database import session_scope
from gunther.main import create_app
from gunther.models import (
    KnowledgeSession,
    Source,
    SourceIndexHead,
    SourcePaper,
    SourceRevision,
    TopicNode,
    TopicSourceLink,
    TopicSynthesis,
)


@pytest.fixture
def client(tmp_path: Path):
    root = tmp_path / "Gunther"
    app = create_app(
        Settings(
            database_url=f"sqlite+pysqlite:///{tmp_path / 'db.sqlite'}",
            assets_dir=tmp_path / "assets",
            recordings_dir=tmp_path / "recordings",
            library_root=root,
            seed_demo=False,
            deepseek_api_key=None,
            processing_worker_enabled=False,
        )
    )
    with TestClient(app) as test_client:
        test_client.root = root
        yield test_client


def library(client: TestClient, title: str = "Course") -> str:
    created = client.post(
        "/api/knowledge-bases",
        json={"title": title, "question": title, "description": title},
    )
    assert created.status_code == 201
    return created.json()["id"]


def source(client, base, title, content="Some text about the lecture.", kind="note") -> str:
    created = client.post(
        "/api/sources",
        json={"title": title, "kind": kind, "content": content, "knowledgeBaseId": base},
    )
    assert created.status_code == 201, created.text
    return created.json()["source"]["id"]


def section(client, base, title, parent=None, **extra) -> str:
    created = client.post(
        f"/api/knowledge-bases/{base}/topics", json={"title": title, "parentId": parent, **extra}
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def contents(client, base) -> dict:
    response = client.get(f"/api/knowledge-bases/{base}/contents")
    assert response.status_code == 200, response.text
    return response.json()


def move(client, base, items, parent=None, index=None):
    return client.post(
        f"/api/knowledge-bases/{base}/contents/move",
        json={
            "items": [{"kind": kind, "id": item} for kind, item in items],
            "parentId": parent,
            "index": index,
        },
    )


def children(data, parent):
    """Ids under a parent in order, sections and sources together."""

    items = [(s["position"], "section", s["id"]) for s in data["sections"]
             if s["parentId"] == parent]
    items += [(p["position"], "source", p["sourceId"]) for p in data["placements"]
              if p["sectionId"] == parent]
    return [(kind, item) for _, kind, item in sorted(items)]


def assert_contiguous(data):
    parents = {None} | {s["id"] for s in data["sections"]}
    for parent in parents:
        positions = sorted(
            [s["position"] for s in data["sections"] if s["parentId"] == parent]
            + [p["position"] for p in data["placements"] if p["sectionId"] == parent]
        )
        assert positions == list(range(len(positions)))


def test_contents_lists_live_sources_with_details(client):
    base = library(client)
    other = library(client, "Other")
    lecture = source(
        client, base, "Lecture 1",
        "# Lecture\n\nDuration: 1:12:04 · Captured: Oct 3\n\nHello", kind="recording",
    )
    short = source(
        client, base, "Lecture 2", "Duration: 58:40 · Captured: Oct 10", kind="recording"
    )
    note = source(client, base, "Notes")
    foreign = source(client, other, "Elsewhere")
    gone = source(client, base, "Trashed one")
    assert client.post(f"/api/sources/{gone}/trash").status_code == 200
    part = section(client, base, "Part A", description="Foundations")
    assert move(client, base, [("source", lecture)], part).status_code == 200

    data = contents(client, base)
    by_id = {row["id"]: row for row in data["sources"]}
    assert set(by_id) == {lecture, short, note}
    assert foreign not in by_id and gone not in by_id
    assert by_id[lecture]["durationSeconds"] == 4324
    assert by_id[short]["durationSeconds"] == 58 * 60 + 40
    assert by_id[note]["durationSeconds"] is None
    assert by_id[note]["kind"] == "note"
    assert by_id[note]["processing"] in {"ready", "reading", "failed"}
    assert data["sections"][0]["title"] == "Part A"
    assert data["sections"][0]["description"] == "Foundations"
    assert data["sections"][0]["passageCount"] == 0
    assert data["placements"] == [{"sourceId": lecture, "sectionId": part, "position": 0}]
    assert client.get("/api/knowledge-bases/kb_missing/contents").status_code == 404


def test_paper_and_page_details_come_from_the_current_version(client):
    base = library(client)
    paper = source(client, base, "A paper")
    with session_scope(client.app.state.knowledge_service.sessions) as session:
        head = session.get(SourceIndexHead, paper)
        if head is None:
            revision = SourceRevision(
                id="rev_test", source_id=paper, fingerprint="f", parser="test",
                manifest_json='{"pageCount": 24}',
            )
            session.add(revision)
            session.flush()
            session.add(SourceIndexHead(source_id=paper, revision_id=revision.id))
            revision_id = revision.id
        else:
            revision_id = head.revision_id
            session.get(SourceRevision, revision_id).manifest_json = '{"pageCount": 24}'
        session.add(
            SourcePaper(
                source_id=paper, revision_id=revision_id, title="A paper",
                authors="Ada Lovelace, Grace Hopper", year=2017,
            )
        )
    row = contents(client, base)["sources"][0]
    assert row["authors"] == "Ada Lovelace et al."
    assert row["year"] == 2017
    assert row["pages"] == 24


def test_move_into_a_section_at_an_index_to_the_end_and_out(client):
    base = library(client)
    a, b, c, d = (source(client, base, name) for name in "ABCD")
    part = section(client, base, "Part")
    data = move(client, base, [("source", a), ("source", b), ("source", c)], part).json()
    assert children(data, part) == [("source", a), ("source", b), ("source", c)]
    # Several at once, in the order given, at an index.
    data = move(client, base, [("source", d), ("source", c)], part, 1).json()
    assert children(data, part) == [("source", a), ("source", d), ("source", c), ("source", b)]
    assert_contiguous(data)
    # Out of any section.
    data = move(client, base, [("source", d)], None).json()
    assert d not in {p["sourceId"] for p in data["placements"]}
    assert children(data, part) == [("source", a), ("source", c), ("source", b)]
    assert_contiguous(data)


def test_sections_move_under_others_and_to_the_top(client):
    base = library(client)
    one, two, three = (section(client, base, name) for name in ("One", "Two", "Three"))
    before = {s["id"]: s["version"] for s in contents(client, base)["sections"]}
    data = move(client, base, [("section", three), ("section", two)], one).json()
    assert children(data, one) == [("section", three), ("section", two)]
    assert children(data, None) == [("section", one)]
    assert_contiguous(data)
    versions = {s["id"]: s["version"] for s in data["sections"]}
    assert versions[two] == before[two] + 1 and versions[three] == before[three] + 1
    data = move(client, base, [("section", two)], None, 0).json()
    assert children(data, None) == [("section", two), ("section", one)]
    assert children(data, one) == [("section", three)]
    assert_contiguous(data)


def test_sections_and_sources_share_one_order(client):
    base = library(client)
    part = section(client, base, "Part")
    outline = source(client, base, "Outline")
    move(client, base, [("source", outline)], part)
    lecture = section(client, base, "Lecture 1", parent=part)
    # A new sub-section goes to the end, after the outline.
    assert children(contents(client, base), part) == [("source", outline), ("section", lecture)]
    data = move(client, base, [("section", lecture)], part, 0).json()
    assert children(data, part) == [("section", lecture), ("source", outline)]


def test_a_section_cannot_go_into_itself_or_a_descendant(client):
    base = library(client)
    top = section(client, base, "Top")
    middle = section(client, base, "Middle", parent=top)
    leaf = section(client, base, "Leaf", parent=middle)
    for moved, target in ((top, top), (top, leaf), (middle, leaf)):
        refused = move(client, base, [("section", moved)], target)
        assert refused.status_code == 422, refused.text
    assert_contiguous(contents(client, base))


def test_other_libraries_and_unknown_ids_are_refused(client):
    base = library(client)
    other = library(client, "Other")
    mine = source(client, base, "Mine")
    theirs = source(client, other, "Theirs")
    foreign_section = section(client, other, "Foreign")
    part = section(client, base, "Part")
    assert move(client, base, [("source", theirs)], part).status_code == 422
    assert move(client, base, [("section", foreign_section)], part).status_code == 422
    assert move(client, base, [("source", mine)], foreign_section).status_code == 422
    assert move(client, base, [("source", "src_missing")], part).status_code == 404
    assert move(client, base, [("section", "topic_missing")], None).status_code == 404
    assert move(client, base, [("source", mine)], "topic_missing").status_code == 404
    assert move(client, "kb_missing", [("source", mine)], None).status_code == 404
    assert contents(client, base)["placements"] == []


def test_a_source_has_one_place_per_library(client):
    base = library(client)
    other = library(client, "Other")
    shared = source(client, base, "Shared")
    assert client.post(f"/api/sources/{shared}/file", json={"knowledgeBaseId": other}).status_code
    first, second = section(client, base, "A"), section(client, base, "B")
    elsewhere = section(client, other, "Elsewhere")
    for target in (first, second):
        filed = client.post(
            f"/api/knowledge-bases/{base}/topics/{target}/sources", json={"sourceIds": [shared]}
        )
        assert filed.status_code == 200, filed.text
    client.post(
        f"/api/knowledge-bases/{other}/topics/{elsewhere}/sources", json={"sourceIds": [shared]}
    )
    data = contents(client, base)
    assert data["placements"] == [{"sourceId": shared, "sectionId": second, "position": 0}]
    assert contents(client, other)["placements"][0]["sectionId"] == elsewhere
    listed = client.get(f"/api/knowledge-bases/{base}/topics").json()
    topics = {t["id"]: t["sourceIds"] for t in listed}
    assert topics[first] == [] and topics[second] == [shared]
    # Taking it out with the old route leaves it not placed.
    assert client.delete(
        f"/api/knowledge-bases/{base}/topics/{second}/sources/{shared}"
    ).status_code == 200
    assert contents(client, base)["placements"] == []


def test_the_database_enforces_one_place(client):
    base = library(client)
    one = source(client, base, "One")
    a, b = section(client, base, "A"), section(client, base, "B")
    with session_scope(client.app.state.knowledge_service.sessions) as session:
        session.add(TopicSourceLink(id="tsl_1", topic_id=a, source_id=one, knowledge_base_id=base))
    sessions = client.app.state.knowledge_service.sessions
    with pytest.raises(Exception, match="UNIQUE"), session_scope(sessions) as session:
        session.add(TopicSourceLink(id="tsl_2", topic_id=b, source_id=one, knowledge_base_id=base))


def test_order_sets_exactly_the_current_children(client):
    base = library(client)
    part = section(client, base, "Part")
    s1, s2 = source(client, base, "Lecture 10"), source(client, base, "Lecture 2")
    move(client, base, [("source", s1), ("source", s2)], part)
    sub = section(client, base, "Sub", parent=part)
    wanted = [("source", s2), ("section", sub), ("source", s1)]

    def put(parent, items):
        return client.put(
            f"/api/knowledge-bases/{base}/contents/order",
            json={"parentId": parent, "children": [{"kind": k, "id": i} for k, i in items]},
        )

    saved = put(part, wanted)
    assert saved.status_code == 200, saved.text
    assert children(saved.json(), part) == wanted
    assert_contiguous(saved.json())
    for bad in (wanted[:2], wanted + [("source", "src_other")], wanted + [wanted[0]]):
        refused = put(part, bad)
        assert refused.status_code == 409
        assert "contents changed" in refused.json()["detail"]
    # Top level holds sections only.
    assert put(None, [("section", part), ("section", sub)]).status_code == 409
    assert put(None, [("source", s1)]).status_code == 409
    assert put(None, []).status_code == 409
    assert put(None, [("section", part)]).status_code == 200
    assert children(contents(client, base), part) == wanted


def test_delete_removes_the_subtree_and_keeps_every_source(client):
    base = library(client)
    other = library(client, "Other")
    keep = section(client, base, "Keep")
    part = section(client, base, "Part B")
    sub = section(client, base, "Sub", parent=part)
    a, b, c = (source(client, base, name) for name in "abc")
    move(client, base, [("source", a)], part)
    move(client, base, [("source", b)], sub)
    move(client, base, [("source", c)], keep)
    foreign = section(client, other, "Foreign")
    with session_scope(client.app.state.knowledge_service.sessions) as session:
        session.add(TopicSynthesis(topic_id=sub, markdown="# Sub", method="local"))
        session.add(TopicSynthesis(topic_id=keep, markdown="# Keep", method="local"))
    asked = client.post(
        f"/api/knowledge-bases/{base}/sessions", json={"focusChapterId": sub}
    ).json()
    kept_session = client.post(
        f"/api/knowledge-bases/{base}/sessions", json={"focusChapterId": keep}
    ).json()

    deleted = client.delete(f"/api/knowledge-bases/{base}/topics/{part}")
    assert deleted.status_code == 200, deleted.text
    data = deleted.json()
    assert [s["id"] for s in data["sections"]] == [keep]
    assert data["placements"] == [{"sourceId": c, "sectionId": keep, "position": 0}]
    assert {row["id"] for row in data["sources"]} == {a, b, c}
    assert_contiguous(data)
    with session_scope(client.app.state.knowledge_service.sessions) as session:
        assert session.get(TopicSynthesis, sub) is None
        assert session.get(TopicSynthesis, keep) is not None
        assert session.get(TopicNode, foreign) is not None
        assert session.scalar(select(func.count()).select_from(Source)) == 3
        assert session.get(KnowledgeSession, asked["id"]).focus_chapter_id is None
        assert session.get(KnowledgeSession, kept_session["id"]).focus_chapter_id == keep
    # Asking a session that still points at a vanished section does not break.
    with session_scope(client.app.state.knowledge_service.sessions) as session:
        session.get(KnowledgeSession, asked["id"]).focus_chapter_id = sub
    reply = client.post(f"/api/sessions/{asked['id']}/messages", json={"content": "anything?"})
    assert reply.status_code in {200, 201}, reply.text
    assert client.delete(f"/api/knowledge-bases/{base}/topics/{part}").status_code == 404
    assert client.delete(f"/api/knowledge-bases/{other}/topics/{keep}").status_code == 404


def test_trash_and_restore_keep_the_placement(client):
    base = library(client)
    first, second = source(client, base, "First"), source(client, base, "Second")
    part = section(client, base, "Part")
    move(client, base, [("source", first), ("source", second)], part)
    assert client.post(f"/api/sources/{first}/trash").status_code == 200
    data = contents(client, base)
    assert data["placements"] == [{"sourceId": second, "sectionId": part, "position": 1}]
    assert first not in {row["id"] for row in data["sources"]}
    # An index counts what the user sees: first is hidden, so index 0 is before second.
    third = source(client, base, "Third")
    data = move(client, base, [("source", third)], part, 0).json()
    assert children(data, part) == [("source", third), ("source", second)]
    assert client.post(f"/api/trash/source/{first}/restore").status_code == 200
    data = contents(client, base)
    assert {p["sourceId"] for p in data["placements"]} == {first, second, third}
    assert_contiguous(data)


def test_accepting_a_suggestion_moves_papers_and_only_unplaced_ones_are_offered(client):
    base = library(client)
    part = section(client, base, "Part")
    ids = [source(client, base, f"Paper {i}") for i in range(3)]
    move(client, base, [("source", ids[0])], part)
    created = client.post(
        f"/api/knowledge-bases/{base}/topics", json={"title": "New", "sourceIds": ids[:2]}
    )
    assert created.status_code == 201
    data = contents(client, base)
    placed = {p["sourceId"]: p["sectionId"] for p in data["placements"]}
    assert placed[ids[0]] == created.json()["id"] and placed[ids[1]] == created.json()["id"]
    assert ids[2] not in placed
    assert children(data, part) == []
    assert_contiguous(data)
    from gunther import library_topics

    with session_scope(client.app.state.knowledge_service.sessions) as session:
        unplaced = {p.source_id for p in library_topics._unfiled_papers(session, base)}
        assert not unplaced & set(ids[:2])


def test_a_new_section_goes_to_the_end_and_an_explicit_position_is_kept(client):
    base = library(client)
    one, two = section(client, base, "One"), section(client, base, "Two")
    three = section(client, base, "Three", position=0)
    data = contents(client, base)
    assert children(data, None) == [("section", three), ("section", one), ("section", two)]
    assert_contiguous(data)


def test_the_library_folder_mirrors_the_contents(client):
    base = library(client, "ML 2026")
    outline = source(client, base, "Outline")
    lecture = source(client, base, "Lecture 1")
    loose = source(client, base, "Loose blog")
    part = section(client, base, "Part A", description="Foundations")
    sub = section(client, base, "Lecture unit", parent=part)
    move(client, base, [("source", outline)], part)
    move(client, base, [("section", sub)], part, 1)
    move(client, base, [("source", lecture)], sub)
    client.app.state.library_folders.sync()
    folder = client.root / "Libraries" / "ML 2026"
    text = (folder / "Overview.md").read_text()
    assert text.index("## [Part A](<Sections/Part A.md>)") < text.index("Outline")
    assert text.index("Outline") < text.index("### [Lecture unit](<Sections/Lecture unit.md>)")
    assert text.index("### [Lecture unit]") < text.index("Lecture 1")
    assert text.index("Lecture 1") < text.index("## Not placed yet")
    assert text.index("## Not placed yet") < text.index("Loose blog")
    assert "Foundations" in text
    assert "Written by Gunther; edits here are not read back." in text
    assert (folder / "Sections" / "Part A.md").is_file()
    assert (folder / "Sections" / "Lecture unit.md").is_file()
    assert not (folder / "Topics").exists()
    assert loose
