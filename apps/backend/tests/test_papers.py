"""Papers at scale: passages, what each paper says about itself, copies, topics."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gunther.config import Settings
from gunther.content import parse_content
from gunther.database import session_scope
from gunther.main import create_app
from gunther.models import ContentBlock, Work
from gunther.papers import read_paper
from gunther.pdf_text import reflow


def make_pdf(lines: list[tuple[int, str]]) -> bytes:
    """A one-page PDF: (font size, text) lines from the top, in Helvetica."""

    operations, y = [], 760
    for size, text in lines:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        operations.append(f"BT /F1 {size} Tf 72 {y} Td ({safe}) Tj ET")
        y -= size + 6
    stream = "\n".join(operations).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def paper_pdf(title: str, doi: str, note: str = "") -> bytes:
    return make_pdf(
        [
            (20, title),
            (11, "Ada Lovelace, Grace Hopper"),
            (10, "Abstract"),
            (10, "We compare marker gene methods for labelling cell types in single-cell"),
            (10, "RNA sequencing data across twelve tissues and find that curated"),
            (10, "markers remain competitive with reference mapping."),
            (10, "1 Introduction"),
            (10, f"Cell type annotation is a central step in analysis. doi:{doi} {note}"),
        ]
    )


def client_for(path: Path, **overrides) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                database_url=f"sqlite+pysqlite:///{path / 'db.sqlite'}",
                assets_dir=path / "assets",
                recordings_dir=path / "recordings",
                processing_worker_enabled=False,
                ocr_provider="disabled",
                seed_demo=False,
                deepseek_api_key=None,
                stt_provider="compatible",
                **overrides,
            )
        )
    )


def library(client: TestClient, title: str = "Single-cell") -> str:
    response = client.post(
        "/api/knowledge-bases",
        json={"title": title, "question": "How are cells labelled?", "description": "Papers"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client: TestClient, base_id: str, file_name: str, data: bytes) -> str:
    response = client.post(
        "/api/captures/assets",
        params={
            "title": Path(file_name).stem,
            "fileName": file_name,
            "kind": "paper",
            "knowledgeBaseId": base_id,
            "deferProcessing": "true",
        },
        content=data,
        headers={"Content-Type": "application/pdf"},
    )
    assert response.status_code == 201, response.text
    return response.json()["importResult"]["source"]["id"]


def note(client: TestClient, base_id: str, title: str, content: str) -> str:
    response = client.post(
        "/api/sources",
        json={"title": title, "kind": "note", "content": content, "knowledgeBaseId": base_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["source"]["id"]


def drain(client: TestClient) -> None:
    worker = client.app.state.processing_worker
    while worker.run_once():
        pass


# Passages ----------------------------------------------------------------------


def test_reflow_joins_wrapped_lines_and_keeps_headings_and_words():
    page = "\n".join(
        [
            "Abstract",
            "This technical report presents the training methodology and evaluation re-",
            "sults of the open-",
            "source multilingual models released in mid-2023 for many languages here.",
            "They work well.",
            "# Sampled counts",
            "1 Introduction",
            "• first point in a list that is long enough to wrap onto the next line",
            "细胞类型注释通过标记基因为聚类分配身份，例如",
            "细胞的标记基因为聚类分配身份，这一段很长很长很长很长很长很长很长。",
        ]
    )
    lines = reflow(page, vocabulary={"results", "open", "source"}).split("\n")
    # Section headings become Markdown headings, numbered ones by depth.
    assert lines[0] == "## Abstract"
    assert lines[1].startswith(
        "This technical report presents the training methodology and "
        "evaluation results of the open-source multilingual"
    )
    assert "## 1 Introduction" in lines
    assert any(line.startswith("• first point") for line in lines)
    # Chinese wrapped across lines joins without a space.
    assert "例如细胞的标记基因" in lines[-1]
    assert reflow("3.2.1 Scaled Dot-Product Attention\n7. References") == (
        "#### 3.2.1 Scaled Dot-Product Attention\n## 7. References"
    )
    # A printed "#" must not turn into a Markdown heading.
    assert "\\# Sampled counts" in lines


def test_reference_lists_are_not_searched_and_number_fragments_not_embedded():
    body = (
        "## 2 Method\nWe train the encoder with a contrastive objective on pairs.\n"
        "0.2 0.3 0.4 0.5 0.6 81.2 79.4 88.0\n"
        "## References\n[1] A. Author. A paper about contrastive objectives. 2020.\n"
    )
    flags = {block.text[:12]: block.payload for block in parse_content(body)}
    assert parse_content("Genome quality needs controls.")[0].payload == {}
    assert flags["We train the"] == {}
    assert flags["0.2 0.3 0.4 "] == {"noEmbed": True}
    assert flags["[1] A. Autho"] == {"reference": True, "noEmbed": True}


def test_titles_by_font_join_small_caps_and_stop_at_the_authors():
    from gunther.pdf_text import _title_case

    assert _title_case("AN IMAGE IS WORTH 16 X16 W ORDS: TRANSFORMERS FOR IMAGE RECOGNITION") == (
        "An Image Is Worth 16 X16 Words: Transformers for Image Recognition"
    )
    assert _title_case("Attention Is All You Need") == "Attention Is All You Need"


def test_blocks_are_passages_of_whole_sentences_with_exact_offsets():
    sentence = "Marker genes label clusters of cells in a reproducible way. "
    body = "# Methods\n" + sentence * 20 + "\n"
    blocks = [block for block in parse_content(body) if block.kind != "heading"]
    assert 1 < len(blocks) < 5
    for block in blocks:
        assert body[block.anchor["charStart"] : block.anchor["charEnd"]] == block.text
        assert len(block.text) <= 600 and block.text.endswith(".")


def test_reading_a_paper_finds_abstract_identifiers_year_and_outline():
    texts = [
        "Provided proper attribution is provided, you may reproduce the figures.",
        "Attention Is All You Need",
        "Ashish Vaswani∗",
        "Google Brain",
        "Łukasz Kaiser∗",
        "Abstract",
        "The dominant sequence transduction models are based on recurrent networks.",
        "We propose the Transformer, based solely on attention mechanisms.",
        "∗Equal contribution.",
        "arXiv:1706.03762v7 [cs.CL] 2 Aug 2023",
        "1 Introduction",
        "Recurrent neural networks have been firmly established.",
        "3.2.1 Scaled Dot-Product Attention",
        "2021. Mr. TyDi: A multi-lingual benchmark for",
    ]
    blocks = [
        ContentBlock(id=f"b{i}", kind="paragraph", content=text, anchor_json='{"page": 1}')
        for i, text in enumerate(texts)
    ]
    from gunther.papers import PdfFacts

    facts = read_paper("1706.03762", blocks, PdfFacts(title="Attention Is All You Need"))
    assert facts.authors == "Ashish Vaswani, Łukasz Kaiser"
    assert facts.arxiv_id == "1706.03762" and facts.year == 2017
    assert facts.abstract_block_ids == ["b6", "b7"]
    assert facts.abstract.startswith("The dominant sequence")
    assert facts.outline == ["1 Introduction", "3.2.1 Scaled Dot-Product Attention"]
    assert facts.identifier == "arxiv:1706.03762"


def test_an_abstract_announced_inside_the_front_matter_is_found():
    blocks = [
        ContentBlock(
            id="front",
            kind="paragraph",
            content="U-Net Olaf Ronneberger, University of Freiburg, Germany Abstract. There is "
            "large consent that successful training of deep networks requires many samples.",
            anchor_json='{"page": 1}',
        ),
        ContentBlock(
            id="rest",
            kind="paragraph",
            content="In this paper, we present a network.",
            anchor_json='{"page": 1}',
        ),
    ]
    facts = read_paper("U-Net", blocks)
    assert facts.abstract.startswith("There is large consent")
    assert facts.abstract_block_ids == ["front", "rest"]


def test_a_note_without_an_abstract_is_summarised_by_its_opening():
    blocks = [
        ContentBlock(id="h", kind="heading", content="Lecture 3", anchor_json="{}"),
        ContentBlock(
            id="p",
            kind="paragraph",
            content="Doublets inflate cluster counts, so we remove them before annotation.",
            anchor_json="{}",
        ),
    ]
    facts = read_paper("Lecture 3", blocks)
    assert facts.title == "Lecture 3" and not facts.title_from_document
    assert facts.abstract_block_ids == ["p"] and facts.year is None


# Papers, copies and folders ------------------------------------------------------


def test_papers_are_read_retitled_and_copies_share_a_work(tmp_path):
    root = tmp_path / "Gunther"
    with client_for(tmp_path, library_root=root) as client:
        base = library(client)
        first = upload(
            client,
            base,
            "lovelace2021.pdf",
            paper_pdf("Marker Genes for Cell Type Annotation", "10.1234/cells.2021.001"),
        )
        # The same paper again, from a different file (another download of it).
        second = upload(
            client,
            base,
            "download (1).pdf",
            paper_pdf(
                "Marker Genes for Cell Type Annotation", "10.1234/cells.2021.001", "Preprint copy."
            ),
        )
        other = upload(
            client,
            base,
            "other.pdf",
            paper_pdf("Reference Mapping at Atlas Scale", "10.1234/atlas.2022.9"),
        )
        drain(client)

        paper = client.get(f"/api/sources/{first}/paper").json()
        assert paper["title"] == "Marker Genes for Cell Type Annotation"
        assert paper["authors"] == "Ada Lovelace, Grace Hopper"
        assert paper["doi"] == "10.1234/cells.2021.001"
        assert paper["abstract"].startswith("We compare marker gene methods")
        assert paper["outline"] == ["1 Introduction"]
        assert [copy["id"] for copy in paper["copies"]] == [second]
        assert client.get(f"/api/sources/{other}/paper").json()["copies"] == []
        # Named after its file, so it takes the paper's own title.
        assert client.get(f"/api/sources/{first}").json()["title"] == paper["title"]
        with session_scope(client.app.state.knowledge_service.sessions) as session:
            assert session.scalar(select(func.count()).select_from(Work)) == 2

        client.app.state.library_folders.sync()
        folder = root / "Libraries" / "Single-cell"
        summary = next(folder.glob("Sources/*Marker Genes*/summary.md")).read_text()
        assert summary.startswith("# Marker Genes for Cell Type Annotation")
        assert "## Abstract" in summary and "doi.org/10.1234/cells.2021.001" in summary
        overview = (folder / "Overview.md").read_text()
        assert "3 sources · 2 papers · 0 topics" in overview

        # Deleting both copies for good leaves no orphaned work behind.
        for source_id in (first, second):
            client.post(f"/api/sources/{source_id}/trash")
            assert client.delete(f"/api/trash/source/{source_id}").status_code == 200
        with session_scope(client.app.state.knowledge_service.sessions) as session:
            assert session.scalar(select(func.count()).select_from(Work)) == 1


# Topics --------------------------------------------------------------------------

CELLS = [
    "Marker genes label T cells and B cells in single-cell clustering of immune tissue.",
    "Single-cell clustering of immune tissue separates T cells by marker genes.",
    "Immune cell types are annotated from marker genes after single-cell clustering.",
    "Marker genes for B cells guide single-cell clustering annotation of tissue.",
    "Doublets distort single-cell clustering; marker genes still label immune cells.",
    "T cells and B cells share marker genes that confuse single-cell clustering.",
]
ROCKETS = [
    "Rocket engines burn liquid fuel and oxygen to reach orbit from the launch pad.",
    "Orbit insertion needs rocket engines with enough fuel after launch.",
    "Launch vehicles stage rocket engines so the fuel mass drops before orbit.",
    "Reusable rocket boosters land after launch and save fuel for the next orbit.",
    "Liquid oxygen and fuel feed rocket engines during launch toward orbit.",
    "Rocket fuel tanks and engines dominate launch mass on the way to orbit.",
]


def test_topics_are_suggested_accepted_asked_and_written_up(tmp_path):
    root = tmp_path / "Gunther"
    with client_for(tmp_path, library_root=root) as client:
        base = library(client, "Mixed reading")
        cells = [note(client, base, f"Cells {i}", text) for i, text in enumerate(CELLS)]
        rockets = [note(client, base, f"Rockets {i}", text) for i, text in enumerate(ROCKETS)]
        drain(client)

        result = client.get(f"/api/knowledge-bases/{base}/topic-suggestions").json()
        assert result["method"] == "keywords" and result["unfiled"] == 12
        groups = sorted(sorted(s["sourceIds"]) for s in result["suggestions"])
        assert groups == sorted([sorted(cells), sorted(rockets)])
        rocket_suggestion = next(
            s for s in result["suggestions"] if set(s["sourceIds"]) == set(rockets)
        )
        assert any("rocket" in term or "fuel" in term for term in rocket_suggestion["keywords"])

        created = client.post(
            f"/api/knowledge-bases/{base}/topics",
            json={"title": "Rockets", "sourceIds": rocket_suggestion["sourceIds"]},
        )
        assert created.status_code == 201, created.text
        topic = created.json()
        assert sorted(topic["sourceIds"]) == sorted(rockets)
        # Filed papers are no longer suggested.
        assert client.get(f"/api/knowledge-bases/{base}/topic-suggestions").json()["unfiled"] == 6

        # A source outside the library cannot be filed.
        stranger = note(client, library(client, "Elsewhere"), "Stranger", "Unrelated text here.")
        refused = client.post(
            f"/api/knowledge-bases/{base}/topics/{topic['id']}/sources",
            json={"sourceIds": [stranger]},
        )
        assert refused.status_code == 422

        # Asking the topic reads only its papers.
        session = client.post(
            f"/api/knowledge-bases/{base}/sessions", json={"focusChapterId": topic["id"]}
        ).json()
        answer = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"content": "marker genes rocket fuel"},
        ).json()["assistantMessage"]
        assert answer["citations"]
        assert {c["sourceId"] for c in answer["citations"]} <= set(rockets)

        assert (
            client.get(f"/api/knowledge-bases/{base}/topics/{topic['id']}/overview").status_code
            == 404
        )
        written = client.post(f"/api/knowledge-bases/{base}/topics/{topic['id']}/overview")
        assert written.status_code == 200, written.text
        overview = written.json()
        assert overview["method"] == "local" and overview["sourceCount"] == 6
        assert overview["markdown"].startswith("# Rockets\n")
        assert "[1]" in overview["markdown"]
        assert {c["sourceId"] for c in overview["citations"]} == set(rockets)
        assert all(c["blockId"] for c in overview["citations"])

        client.app.state.library_folders.sync()
        folder = root / "Libraries" / "Mixed reading"
        assert (folder / "Topics" / "Rockets.md").read_text() == overview["markdown"]
        listing = (folder / "Overview.md").read_text()
        assert "[Rockets](<Topics/Rockets.md>) — 6 sources" in listing
        assert "## Not in a topic" in listing


class TopicAxes:
    """One axis per theme, so papers and questions land predictably."""

    model_id = "test-topic-axes-v1"
    min_similarity = 0.5

    def encode(self, texts, *, query=False):
        return [
            [
                float("rocket" in t.lower() or "orbit" in t.lower()),
                float("cell" in t.lower() or "marker" in t.lower()),
                0.05,
            ]
            for t in texts
        ]


def test_broad_questions_in_a_large_library_also_cite_whole_papers(tmp_path):
    with client_for(tmp_path) as client:
        client.app.state.knowledge_service.index.embedder = TopicAxes()
        base = library(client, "Big")
        # More rocket papers than a question's passages, so some are only reached
        # through their abstracts.
        rockets = [
            note(client, base, f"Rockets {i}", f"{text} Report {i // 6 + 1}.")
            for i, text in enumerate(ROCKETS * 2)
        ]
        for i, text in enumerate(CELLS):
            note(client, base, f"Cells {i}", text)
        drain(client)

        result = client.get(f"/api/knowledge-bases/{base}/topic-suggestions").json()
        assert result["method"] == "semantic"
        assert {len(s["sourceIds"]) for s in result["suggestions"]} == {12, 6}

        session = client.post(f"/api/knowledge-bases/{base}/sessions", json={}).json()
        answer = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"content": "What do we know about getting to orbit?"},
        ).json()["assistantMessage"]
        overviews = [c for c in answer["citations"] if c["locator"] == "Abstract"]
        assert overviews and {c["sourceId"] for c in overviews} <= set(rockets)
        assert all(c["blockId"] for c in overviews)


def test_paper_is_unread_until_processed(tmp_path):
    with client_for(tmp_path) as client:
        base = library(client)
        source = upload(client, base, "wait.pdf", paper_pdf("Waiting For Its Turn Paper", "10.1/x"))
        assert client.get(f"/api/sources/{source}/paper").status_code == 404
        drain(client)
        body = client.get(f"/api/sources/{source}/paper").json()
        assert json.loads(json.dumps(body))["title"] == "Waiting For Its Turn Paper"
