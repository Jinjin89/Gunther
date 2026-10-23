"""Shared evidence eligibility, with preserved canonical furniture and provenance."""

import json

import pytest
from sqlalchemy import create_engine, literal, select, text

from gunther import digest, vector_index
from gunther.content import ParsedBlock
from gunther.conversation import LocalKnowledgeResponder
from gunther.database import Base, create_database_engine, create_session_factory, session_scope
from gunther.document_parser import docling_document
from gunther.document_structure import evidence_eligible
from gunther.extraction import LocalExtractor
from gunther.knowledge_index import embeddable
from gunther.migrations import run_migrations
from gunther.models import ContentBlock, DocumentElement, Source, SourcePaper
from gunther.outputs import Pool
from gunther.papers import _abstract, _gist
from gunther.service import KnowledgeService

FURNITURE = ["footnote", "page_header", "page_footer", "page_number", "sidebar", "margin_note"]
EXCLUDED = [*FURNITURE, "metadata", "figure_text", "figure_panel", "embedded_image", "reference"]
BODY = "Bodyquartz evidence describes the measured response in a complete source paragraph."


@pytest.mark.parametrize(
    "kind,payload,allowed",
    [
        *((kind, {}, False) for kind in EXCLUDED),
        ("paragraph", {"reference": True}, False),
        ("paragraph", {}, True),
        ("paragraph", {"noEmbed": True}, True),
        ("caption", {}, True),
        ("table", {}, True),
        ("equation", {}, True),
        ("list_item", {}, True),
        ("heading", {}, False),
    ],
)
def test_policy_agrees_for_parsed_json_and_sql_blocks(kind, payload, allowed):
    assert evidence_eligible(kind, payload) is allowed
    assert evidence_eligible(kind, json.dumps(payload)) is allowed
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(evidence_eligible(literal(kind), literal(json.dumps(payload))))
            )
            is allowed
        )
        assert (
            connection.scalar(
                select(evidence_eligible(literal("heading"), literal("{}"), include_headings=True))
            )
            is True
        )
    engine.dispose()


@pytest.mark.parametrize("label", FURNITURE)
def test_docling_keeps_furniture_label_text_and_provenance(label):
    original = "Original furniture text, including ambiguous information."
    provenance = [{"page_no": 2, "bbox": {"l": 2, "t": 8, "r": 9, "b": 3}}]
    document = docling_document(
        {
            "body": {"children": [{"$ref": "#/texts/0"}]},
            "furniture": {"children": [{"$ref": "#/texts/1"}]},
            "texts": [
                {"label": "text", "text": BODY},
                {"label": label, "text": original, "prov": provenance},
            ],
        }
    )
    element = document.elements[1]
    assert element.kind == label and element.text == original
    assert element.anchor["provenance"] == provenance
    assert element.payload["documentRef"] == "#/texts/1"
    assert element.payload["noEmbed"] is True
    assert document.chunks()[1].kind == label
    assert not evidence_eligible(element.kind, element.payload)


def block(identifier, content, kind="paragraph", payload=None):
    return ContentBlock(
        id=identifier, kind=kind, content=content, payload_json=json.dumps(payload or {})
    )


def test_abstract_skips_furniture_even_when_it_looks_like_a_boundary_or_abstract():
    blocks = [
        block("fake", "Abstract. " + BODY, "sidebar"),
        block("heading", "Abstract", "heading"),
        block("first", BODY),
        *(block(kind, "1 Introduction", kind) for kind in EXCLUDED),
        block("flagged", "Reference contaminant.", payload={"reference": True}),
        block("second", "A second complete sentence belongs to this same abstract."),
        block("end", "Scope and ownership", "heading"),
        block("later", "This belongs to the body after the abstract."),
    ]
    abstract, ids = _abstract(blocks)
    assert ids == ["first", "second"]
    assert abstract == BODY + " A second complete sentence belongs to this same abstract."


def test_gist_skips_furniture_and_references_but_keeps_short_body_after_opening():
    blocks = [
        *(block(kind, "Abstract. " + BODY, kind) for kind in EXCLUDED),
        block("flagged", BODY, payload={"reference": True}),
        block("body", BODY),
        block("short", "Short body evidence.", payload={"noEmbed": True}),
    ]
    assert _abstract(blocks) == ("", [])
    assert _gist(blocks) == (BODY + " Short body evidence.", ["body", "short"])


@pytest.fixture
def service(tmp_path):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'policy.sqlite'}")
    run_migrations(engine, Base.metadata)
    sessions = create_session_factory(engine)
    try:
        yield KnowledgeService(sessions, LocalExtractor(), LocalKnowledgeResponder())
    finally:
        engine.dispose()


@pytest.mark.parametrize("headed", [False, True])
def test_consumers_share_policy_and_keep_canonical_elements(service, monkeypatch, headed):
    source_id = "source-policy"
    with session_scope(service.sessions) as session:
        saved_source = Source(
            id=source_id,
            title="Synthetic source",
            kind="paper",
            content=BODY,
            content_hash="synthetic-policy-source",
        )
        session.add(saved_source)
        session.flush()
        elements = [
            ParsedBlock("Scope and ownership", "heading"),
            ParsedBlock(BODY),
            *(ParsedBlock("Furniturezircon excluded source text. " * 3, k) for k in EXCLUDED),
            ParsedBlock("Furniturezircon legacy reference text.", payload={"reference": True}),
            ParsedBlock("Numericquartz 12 34 56", payload={"noEmbed": True}),
            ParsedBlock("Captionquartz describes a preserved visual in this source.", "caption"),
        ]
        if headed:
            for element in elements[1:]:
                element.headings = ["Scope and ownership"]
                element.parent = 0
        from gunther.documents import ParsedDocument

        document = ParsedDocument(elements)
        revision = service.index.publish(
            session, saved_source, document.chunks(), document=document
        )
        blocks = list(
            session.scalars(
                select(ContentBlock)
                .where(ContentBlock.revision_id == revision.id)
                .order_by(ContentBlock.ordinal)
            )
        )
        canonical = list(
            session.scalars(
                select(DocumentElement)
                .where(DocumentElement.revision_id == revision.id)
                .order_by(DocumentElement.ordinal)
            )
        )
        assert [(e.kind, e.content) for e in canonical] == [(e.kind, e.text) for e in elements]
        assert service.index.retrieve(session, [source_id], "Furniturezircon") == []
        assert (
            service.index.retrieve(session, [source_id], "Bodyquartz")[0].block.id == blocks[1].id
        )
        assert service.index.retrieve(session, [source_id], "Numericquartz")

        vector_blocks = list(
            session.scalars(
                select(ContentBlock).where(ContentBlock.revision_id == revision.id, *embeddable())
            )
        )
        assert [b.kind for b in vector_blocks] == ["paragraph", "caption"]
        request = digest.prepare(session, saved_source, revision.id)
        assert [p.block_id for p in request.passages] == [b.id for b in (blocks[1], *blocks[-2:])]

        pool = Pool(50)
        service._studio_reading(session, [source_id], pool)
        assert pool.items and all("Furniturezircon" not in e.text for e in pool.items)
        assert "Bodyquartz" in " ".join(e.text for e in pool.items)
        expanded = service._section_blocks(session, blocks[1])
        assert blocks[1] in expanded
        assert all("Furniturezircon" not in b.content for b in expanded)
        assert service._section_blocks(session, blocks[2]) == []
        assert "Furniturezircon" not in service._ask_reader(session, False)(pool.items[0]).text
        extra = service._source_reader(session)(pool.items[0])
        assert extra is None or "Furniturezircon" not in extra.text

        # Simulate legacy FTS/vector entries created before the policy existed.
        session.execute(
            text(
                "INSERT INTO knowledge_fts(block_id, source_id, revision_id, tokens) "
                "VALUES (:block, :source, :revision, 'furniturezircon')"
            ),
            {"block": blocks[2].id, "source": source_id, "revision": revision.id},
        )

        class FakeEmbedder:
            model_id = "synthetic-policy-test"

            def encode(self, texts, *, query=False):
                return [[1.0, 0.0] for _ in texts]

        service.index.embedder = FakeEmbedder()
        monkeypatch.setattr(vector_index, "nearest", lambda *a, **kw: [(blocks[2].id, 1.0)])
        monkeypatch.setattr(vector_index, "nearest_among", lambda *a, **kw: [(blocks[2].id, 1.0)])
        assert service.index.retrieve(session, [source_id], "Furniturezircon") == []
        assert (
            service.index.retrieve(
                session, [source_id], "Furniturezircon", block_ids=[blocks[2].id]
            )
            == []
        )

        paper = SourcePaper(
            source_id=source_id,
            revision_id=revision.id,
            title="Source",
            abstract=blocks[2].content,
            abstract_block_ids_json=json.dumps([blocks[2].id]),
        )
        session.add(paper)
        session.flush()
        monkeypatch.setattr(vector_index, "nearest_papers", lambda *a, **kw: [(source_id, 1.0)])
        assert service.index.nearest_papers(session, [source_id], "Furniturezircon", limit=3) == []
