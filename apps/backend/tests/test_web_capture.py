import asyncio
import gzip
import hashlib
import io
import time
from pathlib import Path

import pytest
from fake_models import FakeProvider, assertion_payload, gateway
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from gunther import asset_service, web_capture
from gunther.config import Settings
from gunther.database import session_scope
from gunther.extraction import ModelExtractor
from gunther.main import create_app
from gunther.models import Asset, Source, WebSnapshot
from gunther.web_capture import (
    PinnedHttpFetcher,
    PublicWebUrlPolicy,
    ResolvedWebTarget,
    WebCaptureFetchError,
    WebCaptureTimeoutError,
    WebCaptureTooLargeError,
    WebFetchResponse,
    _acquire_capture_lock,
    _decode_transfer_body,
    _PinnedHttpsConnection,
    _release_capture_lock,
    fetch_public_page,
)

PUBLIC_IP = "93.184.216.34"


class FakeResolver:
    def __init__(self, addresses: dict[str, list[str]] | None = None) -> None:
        self.addresses = addresses or {}
        self.calls: list[tuple[str, int]] = []

    def resolve(self, host: str, port: int) -> list[str]:
        self.calls.append((host, port))
        return self.addresses.get(host, [PUBLIC_IP])


class ScriptedFetcher:
    def __init__(self, script: dict[str, WebFetchResponse | Exception]) -> None:
        self.script = script
        self.calls: list[ResolvedWebTarget] = []

    def fetch(
        self,
        target: ResolvedWebTarget,
        *,
        timeout_seconds: float,
    ) -> WebFetchResponse:
        assert timeout_seconds > 0
        self.calls.append(target)
        result = self.script[target.url]
        if isinstance(result, Exception):
            raise result
        return result


def html_response(
    body: bytes,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> WebFetchResponse:
    return WebFetchResponse(
        status=status,
        headers={"content-type": "text/html; charset=utf-8", **(headers or {})},
        body=body,
    )


def make_client(
    tmp_path: Path,
    fetcher: ScriptedFetcher,
    resolver: FakeResolver | None = None,
    *,
    processing_worker_enabled: bool = True,
) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
                assets_dir=tmp_path / "assets",
                recordings_dir=tmp_path / "recordings",
                deepseek_api_key=None,
                stt_provider="compatible",
                processing_worker_enabled=processing_worker_enabled,
            ),
            web_capture_fetcher=fetcher,
            web_capture_resolver=resolver or FakeResolver(),
        )
    )


def create_base(client: TestClient, title: str = "Genome research") -> str:
    response = client.post(
        "/api/knowledge-bases",
        json={
            "title": title,
            "question": "What evidence belongs here?",
            "description": "A durable home for captured genomic evidence.",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.fixture
def document_pdf() -> bytes:
    from test_asset_capture import pdf_bytes

    return pdf_bytes("A result in the original document.")


@pytest.mark.parametrize("content_type", ["application/pdf", "application/octet-stream"])
def test_direct_pdf_creates_a_snapshot_and_queues_the_file_reading(
    tmp_path: Path, document_pdf: bytes, content_type: str,
) -> None:
    url = "https://example.com/download"
    fetcher = ScriptedFetcher({
        url: WebFetchResponse(200, {"Content-Type": content_type}, document_pdf),
    })
    with make_client(tmp_path, fetcher, processing_worker_enabled=False) as client:
        response = client.post(
            "/api/captures/web", json={"url": url, "clientCaptureId": "pdf-capture_1234"},
        )
        assert response.status_code == 201, response.text
        captured = response.json()
        source = captured["importResult"]["source"]
        asset = captured["asset"]
        snapshot = captured["snapshot"]
        assert source["kind"] == "file"
        assert source["asset"]["id"] == snapshot["assetId"] == asset["id"]
        assert asset["mediaType"] == "application/pdf"
        assert asset["originalName"] == "download.pdf"
        assert client.get(asset["downloadUrl"]).content == document_pdf
        assert snapshot["originalUrl"] == snapshot["finalUrl"] == url
        assert snapshot["contentType"] == content_type
        expected_hash = hashlib.sha256(document_pdf).hexdigest()
        assert snapshot["contentHash"] == asset["contentHash"] == expected_hash
        assert "note" not in captured

        with session_scope(client.app.state.knowledge_service.sessions) as session:
            stored = session.scalars(select(WebSnapshot)).one()
            assert stored.source_id == source["id"]
            assert stored.client_capture_id == "pdf-capture_1234"
            assert stored.request_fingerprint

        assert client.app.state.processing_worker.run_once(ai=False)
        reading = client.get(f"/api/sources/{source['id']}/document").raise_for_status().json()
        [text] = [
            e for e in reading["elements"] if "A result in the original document." in e["content"]
        ]
        assert text["anchor"]["page"] == 1
        assert client.get(f"/api/sources/{source['id']}").json()["webSnapshot"] == snapshot


@pytest.mark.parametrize("metadata", [
    b'<meta name="citation_pdf_url" content="files/report.pdf">',
    b'<link rel="alternate" type="application/pdf" href="files/report.pdf">',
], ids=["citation", "alternate"])
def test_declared_pdf_is_resolved_from_the_page_and_fetched_through_redirects(
    tmp_path: Path, document_pdf: bytes, metadata: bytes,
) -> None:
    url = "https://example.com/articles/report"
    linked = "https://example.com/articles/files/report.pdf"
    final = "https://cdn.example.net/report.pdf"
    resolver = FakeResolver({"cdn.example.net": ["1.1.1.1"]})
    fetcher = ScriptedFetcher({
        url: html_response(b"<title>Research report</title>" + metadata + b"<p>Landing page.</p>"),
        linked: WebFetchResponse(302, {"Location": final}, b""),
        final: WebFetchResponse(200, {"Content-Type": "application/pdf"}, document_pdf),
    })
    with make_client(tmp_path, fetcher, resolver, processing_worker_enabled=False) as client:
        captured = client.post("/api/captures/web", json={"url": url}).raise_for_status().json()
        assert captured["importResult"]["source"]["title"] == "Research report"
        assert captured["importResult"]["source"]["kind"] == "file"
        assert client.get(captured["asset"]["downloadUrl"]).content == document_pdf
        assert captured["snapshot"]["originalUrl"] == url
        assert captured["snapshot"]["finalUrl"] == final
        assert "note" not in captured

    assert [target.url for target in fetcher.calls] == [url, linked, final]
    assert [target.address for target in fetcher.calls] == [PUBLIC_IP, PUBLIC_IP, "1.1.1.1"]
    assert all(target.allow_pdf for target in fetcher.calls)
    assert resolver.calls == [("example.com", 443), ("example.com", 443), ("cdn.example.net", 443)]


@pytest.mark.parametrize("linked_response", [
    WebFetchResponse(401, {}, b""),
    WebFetchResponse(403, {}, b""),
    WebFetchResponse(404, {}, b""),
    TimeoutError(),
    WebFetchResponse(200, {"Content-Type": "application/pdf"}, b"<p>Sign in.</p>"),
], ids=["unauthorized", "forbidden", "missing", "timeout", "non-pdf"])
def test_unavailable_linked_pdf_saves_the_page_with_a_note(
    tmp_path: Path, linked_response: WebFetchResponse | Exception,
) -> None:
    url = "https://example.com/report"
    linked = "https://example.com/report.pdf"
    body = (
        b'<meta name="citation_pdf_url" content="/report.pdf">'
        b'<link rel="alternate" type="application/pdf" href="/other.pdf">'
        b"<p>The public reading.</p>"
    )
    fetcher = ScriptedFetcher({url: html_response(body), linked: linked_response})
    with make_client(tmp_path, fetcher, processing_worker_enabled=False) as client:
        captured = client.post("/api/captures/web", json={"url": url}).raise_for_status().json()
        assert captured["importResult"]["source"]["kind"] == "link"
        assert client.get(captured["asset"]["downloadUrl"]).content == body
        assert captured["snapshot"]["originalUrl"] == captured["snapshot"]["finalUrl"] == url
        assert captured["note"] == "Saved the web page; the linked PDF needed separate access."

    assert [target.url for target in fetcher.calls] == [url, linked]


@pytest.mark.parametrize("status", [200, 206], ids=["short-body", "partial-status"])
def test_incomplete_pdf_fails_directly_and_falls_back_from_a_page(
    tmp_path: Path, document_pdf: bytes, status: int,
) -> None:
    url = "https://example.com/report"
    pdf_url = f"{url}.pdf"
    body = document_pdf[:len(document_pdf) // 2]
    expected = len(document_pdf) if status == 200 else len(body)
    page = b'<link rel="alternate" type="application/pdf" href="/report.pdf"><p>Public text.</p>'
    fetcher = ScriptedFetcher({
        url: html_response(page),
        pdf_url: WebFetchResponse(
            status, {"Content-Type": "application/pdf", "Content-Length": str(expected)}, body,
        ),
    })
    with make_client(tmp_path, fetcher, processing_worker_enabled=False) as client:
        rejected = client.post("/api/captures/web", json={"url": pdf_url})
        assert rejected.status_code == 502
        assert ("Content-Length" if status == 200 else "partial PDF") in rejected.json()["detail"]
        with session_scope(client.app.state.knowledge_service.sessions) as session:
            assert session.scalar(select(WebSnapshot)) is None
            assert session.scalar(select(Source)) is None
            assert session.scalar(select(Asset)) is None

        fallback = client.post("/api/captures/web", json={"url": url}).raise_for_status().json()
        assert fallback["importResult"]["source"]["kind"] == "link"
        assert fallback["snapshot"]["finalUrl"] == url
        assert client.get(fallback["asset"]["downloadUrl"]).content == page
        assert fallback["note"] == "Saved the web page; the linked PDF needed separate access."


def test_fallback_receipt_keeps_its_notice_on_replay_and_url_reuse(tmp_path: Path) -> None:
    url = "https://example.com/report"
    fetcher = ScriptedFetcher({
        url: html_response(b'<meta name="citation_pdf_url" content="/report.pdf"><p>Public.</p>'),
        f"{url}.pdf": WebFetchResponse(403, {}, b""),
    })
    with make_client(tmp_path, fetcher, processing_worker_enabled=False) as client:
        payload = {
            "url": url,
            "knowledgeBaseId": create_base(client),
            "clientCaptureId": "fallback-capture-1234",
        }
        first = client.post("/api/captures/web", json=payload).raise_for_status().json()
        assert first["note"] == "Saved the web page; the linked PDF needed separate access."

    unavailable = ScriptedFetcher({})
    with make_client(tmp_path, unavailable, processing_worker_enabled=False) as restarted:
        replay = restarted.post("/api/captures/web", json=payload).raise_for_status().json()
        assert replay["idempotentReplay"] is True
        assert replay["note"] == first["note"]
        reused_payload = {**payload, "clientCaptureId": "fallback-reuse-1234"}
        reused = restarted.post("/api/captures/web", json=reused_payload).raise_for_status().json()
        assert reused["idempotentReplay"] is False
        assert reused["note"] == first["note"]
        replay = restarted.post("/api/captures/web", json=reused_payload).raise_for_status().json()
        assert replay["idempotentReplay"] is True
        assert replay["note"] == first["note"]
    assert unavailable.calls == []


@pytest.mark.parametrize("redirect", [False, True])
def test_private_linked_pdf_is_refused_by_the_public_url_policy(
    tmp_path: Path, redirect: bool,
) -> None:
    url = "https://example.com/report"
    private = "http://127.0.0.1/report.pdf"
    link = "https://example.com/report.pdf" if redirect else private
    body = f'<meta name="citation_pdf_url" content="{link}"><p>Public.</p>'.encode()
    fetcher = ScriptedFetcher({
        url: html_response(body),
        link: WebFetchResponse(302, {"Location": private}, b""),
    })
    with make_client(tmp_path, fetcher, processing_worker_enabled=False) as client:
        captured = client.post("/api/captures/web", json={"url": url}).raise_for_status().json()
        assert captured["importResult"]["source"]["kind"] == "link"
        assert captured["note"]
    assert [target.url for target in fetcher.calls] == ([url, link] if redirect else [url])


def test_library_reuses_the_live_source_by_original_and_final_url(tmp_path: Path) -> None:
    url = "https://example.com/report"
    final = "https://example.com/article"
    alias = "https://example.com/redirect"
    redirect = WebFetchResponse(302, {"Location": final}, b"")
    fetcher = ScriptedFetcher({
        url: redirect, alias: redirect, final: html_response(b"<p>The reading.</p>"),
    })
    with make_client(tmp_path, fetcher, processing_worker_enabled=False) as client:
        library = create_base(client)
        other_library = create_base(client, "Other research")
        first = client.post(
            "/api/captures/web",
            json={"url": url, "knowledgeBaseId": library, "clientCaptureId": "original-receipt"},
        ).raise_for_status().json()
        assert first["importResult"]["duplicate"] is False
        source_id = first["importResult"]["source"]["id"]
        payloads = [
            {
                "url": repeated_url,
                "knowledgeBaseId": library,
                "title": "Another title",
                "clientCaptureId": f"reused-receipt-{index}",
            }
            for index, repeated_url in enumerate([f"{url}#reading", final, alias])
        ]
        for payload in payloads:
            capture = client.post("/api/captures/web", json=payload).raise_for_status().json()
            assert capture["importResult"]["duplicate"] is True
            assert capture["idempotentReplay"] is False
            assert capture["importResult"]["source"]["id"] == source_id
            assert capture["snapshot"] == first["snapshot"]
        sources = client.get(f"/api/knowledge-bases/{library}/sources").raise_for_status().json()
        assert [source["id"] for source in sources] == [source_id]
    assert [target.url for target in fetcher.calls] == [url, final, alias, final]

    unavailable = ScriptedFetcher({})
    with make_client(tmp_path, unavailable, processing_worker_enabled=False) as restarted:
        changed_requests = (
            {"url": "https://example.com/other"},
            {"knowledgeBaseId": other_library},
        )
        for payload in payloads:
            replay = restarted.post("/api/captures/web", json=payload).raise_for_status().json()
            assert replay["idempotentReplay"] is True
            assert replay["importResult"]["duplicate"] is True
            assert replay["importResult"]["source"]["id"] == source_id
            assert replay["snapshot"] == first["snapshot"]
            for change in changed_requests:
                conflict = restarted.post("/api/captures/web", json={**payload, **change})
                assert conflict.status_code == 409
    assert unavailable.calls == []


def test_capture_skips_a_source_moving_to_trash_during_receipt_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.com/report"
    fetcher = ScriptedFetcher({url: html_response(b"<p>The reading.</p>")})
    with make_client(tmp_path, fetcher, processing_worker_enabled=False) as client:
        payload = {"url": url, "knowledgeBaseId": create_base(client)}
        first = client.post("/api/captures/web", json=payload).raise_for_status().json()
        old_source_id = first["importResult"]["source"]["id"]
        engine = client.app.state.knowledge_service.sessions.kw["bind"]

        with engine.begin() as pending_trash:
            # Stage a real Trash request and hold its commit until capture attempts a write.
            with monkeypatch.context() as patch:
                patch.setattr(
                    client.app.state.trash_service, "sessions",
                    sessionmaker(bind=pending_trash, join_transaction_mode="rollback_only"),
                )
                client.post(f"/api/sources/{old_source_id}/trash").raise_for_status()
            assert client.get(f"/api/sources/{old_source_id}").json()["trashedAt"] is None

            def commit_trash(_connection, _cursor, statement, _params, _context, _many):
                if pending_trash.in_transaction() and (
                    statement == "BEGIN IMMEDIATE" or statement.startswith("INSERT INTO revisions")
                ):
                    pending_trash.commit()

            retry = {**payload, "clientCaptureId": "capture-during-trash"}
            event.listen(engine, "before_cursor_execute", commit_trash)
            try:
                captured = client.post("/api/captures/web", json=retry).raise_for_status().json()
            finally:
                event.remove(engine, "before_cursor_execute", commit_trash)
            assert not pending_trash.in_transaction()

        assert captured["importResult"]["duplicate"] is False
        new_source_id = captured["importResult"]["source"]["id"]
        assert new_source_id != old_source_id
        assert client.get(f"/api/sources/{old_source_id}").json()["trashedAt"]
        replay = client.post("/api/captures/web", json=retry).raise_for_status().json()
        assert replay["idempotentReplay"] is True
        assert replay["importResult"]["source"]["id"] == new_source_id
        assert [target.url for target in fetcher.calls] == [url, url]


def test_trashed_pdf_can_be_captured_again_and_dedupe_is_library_scoped(
    tmp_path: Path, document_pdf: bytes,
) -> None:
    url = "https://example.com/report.pdf"
    fetcher = ScriptedFetcher({
        url: WebFetchResponse(200, {"Content-Type": "application/pdf"}, document_pdf),
    })
    with make_client(tmp_path, fetcher, processing_worker_enabled=False) as client:
        library = create_base(client)
        other_library = create_base(client, "Other research")
        payload = {"url": url, "knowledgeBaseId": library}
        first = client.post("/api/captures/web", json=payload).raise_for_status().json()
        other = client.post(
            "/api/captures/web", json={**payload, "knowledgeBaseId": other_library},
        ).raise_for_status().json()

        source_id = first["importResult"]["source"]["id"]
        client.post(f"/api/sources/{source_id}/trash").raise_for_status()
        recaptured = client.post("/api/captures/web", json=payload).raise_for_status().json()
        assert recaptured["importResult"]["duplicate"] is False
        assert other["importResult"]["duplicate"] is False
        assert len({c["importResult"]["source"]["id"] for c in (first, other, recaptured)}) == 3
        assert first["asset"]["id"] == recaptured["asset"]["id"] == other["asset"]["id"]
        assert client.get(f"/api/sources/{source_id}").json()["trashedAt"]
        assert len(fetcher.calls) == 3


def test_oversize_pdf_leaves_no_capture(
    tmp_path: Path, document_pdf: bytes, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asset_service, "MAX_PDF_INPUT_BYTES", len(document_pdf) - 1)
    url = "https://example.com/report.pdf"
    fetcher = ScriptedFetcher({
        url: WebFetchResponse(200, {"Content-Type": "application/pdf"}, document_pdf),
    })
    with make_client(tmp_path, fetcher, processing_worker_enabled=False) as client:
        response = client.post("/api/captures/web", json={"url": url})
        assert response.status_code == 413
        assert "PDF response is too large" in response.json()["detail"]
        with session_scope(client.app.state.knowledge_service.sessions) as session:
            assert session.scalar(select(WebSnapshot)) is None
            assert session.scalar(select(Source)) is None
            assert session.scalar(select(Asset)) is None


def test_article_figures_are_retained_once_and_private_image_urls_are_not_fetched(tmp_path):
    from test_documents import png

    article = (
        b'<h1>Study</h1><p>Result [1].</p><figure><img src="/plot.png"/>'
        b"<figcaption>Figure 1. The observed result.</figcaption></figure>"
        b'<img src="/plot.png"/><img src="http://127.0.0.1/private.png"/>'
        b"<h2>References</h2><p>[1] Smith 2020.</p>"
    )
    fetcher = ScriptedFetcher(
        {
            "https://example.com/study": html_response(article),
            "https://example.com/plot.png": WebFetchResponse(
                200, {"content-type": "image/png"}, png()
            ),
        }
    )
    with make_client(tmp_path, fetcher) as client:
        captured = client.post(
            "/api/captures/web",
            json={
                "originalUrl": "https://example.com/study",
            },
        )
        assert captured.status_code == 201, captured.text
        source_id = captured.json()["importResult"]["source"]["id"]
        document = client.get(f"/api/sources/{source_id}/document").json()
        figures = [e for e in document["elements"] if e["kind"] == "figure"]
        image = figures[0]["payload"]["imageAssetId"]
        assert figures[1]["payload"]["imageAssetId"] == image
        assert not figures[2]["payload"].get("imageAssetId")
        assert client.get(f"/api/assets/{image}").content == png()
        assert document["references"][0]["raw"] == "[1] Smith 2020."
        assert document["manifest"]["missingImages"] == 1
    assert [target.url for target in fetcher.calls] == [
        "https://example.com/study",
        "https://example.com/plot.png",
    ]


def test_web_capture_preserves_bytes_body_hash_locator_provenance_and_inbox(
    tmp_path: Path,
) -> None:
    original = (
        b"<html><head><title>Genomics Guide</title></head><body>"
        b"<p>BRCA1 -> associated_with -> DNA repair</p>"
        b"<script>not evidence</script></body></html>"
    )
    fetcher = ScriptedFetcher({"https://example.com/guide": html_response(original)})
    with make_client(tmp_path, fetcher, processing_worker_enabled=False) as client:
        statement = "BRCA1 -> associated_with -> DNA repair"
        fake = FakeProvider(assertion_payload(
            ("BRCA1", "associated_with", "DNA repair", statement),
        ).model_dump_json())
        client.app.state.knowledge_service.extractor = ModelExtractor(gateway(fake))
        response = client.post(
            "/api/captures/web",
            json={
                "originalUrl": "https://example.com/guide",
                "notes": "Useful background for the genome course.",
            },
        )
        assert response.status_code == 201
        captured = response.json()
        source = captured["importResult"]["source"]
        asset = captured["asset"]
        snapshot = captured["snapshot"]
        expected_hash = hashlib.sha256(original).hexdigest()

        assert source["kind"] == "link"
        assert source["title"] == "Genomics Guide"
        assert source["asset"]["id"] == asset["id"]
        assert asset["contentHash"] == expected_hash
        assert snapshot == {
            "originalUrl": "https://example.com/guide",
            "finalUrl": "https://example.com/guide",
            "capturedAt": snapshot["capturedAt"],
            "status": 200,
            "contentType": "text/html; charset=utf-8",
            "contentHash": expected_hash,
            "assetId": asset["id"],
        }
        assert client.get(asset["downloadUrl"]).content == original
        assert captured["importResult"]["created"] == {"assertions": 0, "entities": 0}
        assert not fake.requests
        assert client.app.state.processing_worker.run_once(ai=True)

        detail = client.get(f"/api/sources/{source['id']}").json()
        assert detail["webSnapshot"] == snapshot
        assert statement in detail["content"]
        assert "not evidence" not in detail["content"]
        assert detail["assertions"][0]["evidence"][0]["locator"].startswith("line ")
        assert any(
            item["sourceId"] == source["id"] and item["state"] == "unfiled"
            for item in client.get("/api/inbox").json()
        )

    stored = [path for path in (tmp_path / "assets").rglob("*") if path.is_file()]
    assert len(stored) == 1
    assert stored[0].read_bytes() == original
    assert fetcher.calls[0].address == PUBLIC_IP
    assert fetcher.calls[0].host == "example.com"


def test_web_capture_can_be_filed_directly_and_url_alias_is_accepted(tmp_path: Path) -> None:
    body = b"Captured plain text evidence."
    fetcher = ScriptedFetcher(
        {
            "http://example.com/notes": WebFetchResponse(
                200,
                {"Content-Type": "text/plain; charset=utf-8"},
                body,
            )
        }
    )
    with make_client(tmp_path, fetcher) as client:
        knowledge_base_id = create_base(client)
        response = client.post(
            "/api/captures/web",
            json={
                "url": "http://example.com/notes",
                "title": "External notes",
                "knowledgeBaseId": knowledge_base_id,
            },
        )
        assert response.status_code == 201
        source_id = response.json()["importResult"]["source"]["id"]
        assert source_id in {
            source["id"]
            for source in client.get(f"/api/knowledge-bases/{knowledge_base_id}/sources").json()
        }
        assert all(item["sourceId"] != source_id for item in client.get("/api/inbox").json())


def test_client_capture_id_is_idempotent_without_refetching(tmp_path: Path) -> None:
    fetcher = ScriptedFetcher(
        {"https://example.com/once": html_response(b"<p>Exactly once snapshot</p>")}
    )
    first_source_id = ""
    with make_client(tmp_path, fetcher) as client:
        knowledge_base_id = create_base(client)
        other_base_id = create_base(client, "Other research")
        payload = {
            "url": " HTTPS://EXAMPLE.COM.:443/once#reader ",
            "title": " Exactly once ",
            "notes": " Retry context. ",
            "knowledgeBaseId": f" {knowledge_base_id} ",
            "clientCaptureId": "mobile-capture_1234",
        }
        normalized_payload = {
            **payload,
            "url": "https://example.com/once",
            "title": "Exactly once",
            "notes": "Retry context.",
            "knowledgeBaseId": knowledge_base_id,
        }
        first = client.post("/api/captures/web", json=payload)
        second = client.post("/api/captures/web", json=normalized_payload)
        assert first.status_code == second.status_code == 201
        assert len(fetcher.calls) == 1
        assert second.json()["idempotentReplay"] is True
        assert second.json()["importResult"]["duplicate"] is True
        assert (
            second.json()["importResult"]["source"]["id"]
            == first.json()["importResult"]["source"]["id"]
        )
        first_source_id = first.json()["importResult"]["source"]["id"]
        assert second.json()["asset"]["id"] == first.json()["asset"]["id"]

        conflicting_intents = [
            {**normalized_payload, "url": "https://example.com/different"},
            {**normalized_payload, "title": "Different title"},
            {**normalized_payload, "notes": "Different context."},
            {**normalized_payload, "knowledgeBaseId": other_base_id},
            {key: value for key, value in normalized_payload.items() if key != "title"},
            {key: value for key, value in normalized_payload.items() if key != "notes"},
            {key: value for key, value in normalized_payload.items() if key != "knowledgeBaseId"},
        ]
        for conflicting_payload in conflicting_intents:
            conflict = client.post("/api/captures/web", json=conflicting_payload)
            assert conflict.status_code == 409
        assert len(fetcher.calls) == 1

    restarted_fetcher = ScriptedFetcher({})
    with make_client(tmp_path, restarted_fetcher) as restarted:
        replay = restarted.post("/api/captures/web", json=normalized_payload)
        assert replay.status_code == 201
        assert replay.json()["idempotentReplay"] is True
        assert replay.json()["importResult"]["source"]["id"] == first_source_id
        restarted_conflict = restarted.post(
            "/api/captures/web",
            json={**normalized_payload, "notes": "Changed after restart."},
        )
        assert restarted_conflict.status_code == 409
        assert restarted_fetcher.calls == []


def test_older_web_snapshot_without_a_fingerprint_still_replays(tmp_path: Path) -> None:
    url = "https://example.com/legacy"
    fetcher = ScriptedFetcher({url: html_response(b"<p>Saved reading</p>")})
    payload = {"url": url, "clientCaptureId": "legacy_capture_1234"}
    with make_client(tmp_path, fetcher) as client:
        captured = client.post("/api/captures/web", json=payload).raise_for_status().json()
        with session_scope(client.app.state.knowledge_service.sessions) as session:
            snapshot = session.scalars(select(WebSnapshot).where(
                WebSnapshot.client_capture_id == payload["clientCaptureId"],
            )).one()
            snapshot.request_fingerprint = None

    restarted = ScriptedFetcher({})
    with make_client(tmp_path, restarted) as client:
        replay = client.post(
            "/api/captures/web", json={**payload, "notes": "Context changed"},
        ).raise_for_status().json()
        assert replay["idempotentReplay"] is True
        assert replay["snapshot"] == captured["snapshot"]
        assert replay["importResult"]["source"]["id"] == captured["importResult"]["source"]["id"]
        assert client.post(
            "/api/captures/web", json={**payload, "url": "https://example.com/other"},
        ).status_code == 409
        assert restarted.calls == []


def test_redirected_page_is_decoded_once_per_read_and_keeps_snapshot_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = "<title>Final report</title><p>Evidence – final.</p>".encode("windows-1252")
    decoded_bodies = []
    decode_text = web_capture._decode_text

    def decode(data, **kwargs):
        decoded_bodies.append(data)
        return decode_text(data, **kwargs)

    monkeypatch.setattr(web_capture, "_decode_text", decode)
    resolver = FakeResolver({"example.com": [PUBLIC_IP], "cdn.example.net": ["1.1.1.1"]})
    fetcher = ScriptedFetcher(
        {
            "https://example.com/start": html_response(
                b"redirect",
                status=302,
                headers={"Location": "https://cdn.example.net/final"},
            ),
            "https://cdn.example.net/final": html_response(
                original, headers={"content-type": "text/html"},
            ),
        }
    )
    with make_client(tmp_path, fetcher, resolver) as client:
        url = "https://example.com/start"
        reader = client.app.state.knowledge_service.page_reader
        text, title = reader(url)
        assert title == "Final report"
        assert "Evidence – final." in text
        assert decoded_bodies == [original]
        response = client.post(
            "/api/captures/web",
            json={"url": url},
        )
        assert response.status_code == 201
        captured = response.json()
        assert captured["snapshot"]["originalUrl"] == url
        assert captured["snapshot"]["finalUrl"] == "https://cdn.example.net/final"
        assert captured["importResult"]["source"]["title"] == title
        source_id = captured["importResult"]["source"]["id"]
        reading = client.get(f"/api/sources/{source_id}/document").json()
        assert "Evidence – final." in [element["content"] for element in reading["elements"]]
        assert client.get(captured["asset"]["downloadUrl"]).content == original
        assert decoded_bodies == [original, original]
        assert [target.address for target in fetcher.calls] == [PUBLIC_IP, "1.1.1.1"] * 2
        assert resolver.calls == [("example.com", 443), ("cdn.example.net", 443)] * 2


def test_redirect_count_is_bounded(tmp_path: Path) -> None:
    fetcher = ScriptedFetcher(
        {
            "https://example.com/loop": html_response(
                b"redirect",
                status=302,
                headers={"location": "/loop"},
            )
        }
    )
    with make_client(tmp_path, fetcher) as client:
        response = client.post(
            "/api/captures/web",
            json={"url": "https://example.com/loop"},
        )
        assert response.status_code == 422
        assert len(fetcher.calls) == web_capture.MAX_REDIRECTS + 1


def test_total_fetch_timeout_is_enforced(tmp_path: Path, monkeypatch) -> None:
    class SlowFetcher(ScriptedFetcher):
        def fetch(
            self,
            target: ResolvedWebTarget,
            *,
            timeout_seconds: float,
        ) -> WebFetchResponse:
            self.calls.append(target)
            time.sleep(0.05)
            return html_response(b"<p>Too late</p>")

    monkeypatch.setattr(web_capture, "TOTAL_TIMEOUT_SECONDS", 0.01)
    fetcher = SlowFetcher({})
    with make_client(tmp_path, fetcher) as client:
        response = client.post(
            "/api/captures/web",
            json={"url": "https://example.com/slow"},
        )
        assert response.status_code == 504
    with pytest.raises(WebCaptureTimeoutError):
        fetch_public_page(
            "https://example.com/slow", PublicWebUrlPolicy(FakeResolver()), fetcher, timeout=0.01,
        )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "https://user:secret@example.com/",
        "http://localhost/",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.8/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:8.8.8.8]/",
        "http://[64:ff9b::808:808]/",
        "http://[64:ff9b:1::808:808]/",
        "http://[2001:0000:4136:e378:8000:63bf:3fff:fdd2]/",
        "http://[2002:0808:0808::1]/",
        "https://example.com:8443/",
        "http://2130706433/",
    ],
)
def test_unsafe_url_targets_are_rejected_before_fetch(tmp_path: Path, url: str) -> None:
    fetcher = ScriptedFetcher({})
    with make_client(tmp_path, fetcher) as client:
        response = client.post("/api/captures/web", json={"url": url})
        assert response.status_code == 422
        assert fetcher.calls == []


def test_ordinary_global_ipv6_target_is_allowed_without_dns() -> None:
    resolver = FakeResolver()
    target = PublicWebUrlPolicy(resolver).resolve(
        "HTTPS://[2606:4700:4700:0:0:0:0:1111]:443/dns#fragment"
    )

    assert target.url == "https://[2606:4700:4700::1111]/dns"
    assert target.address == "2606:4700:4700::1111"
    assert resolver.calls == []


def test_any_private_dns_answer_is_rejected_to_block_rebinding(tmp_path: Path) -> None:
    resolver = FakeResolver({"example.com": [PUBLIC_IP, "127.0.0.1"]})
    fetcher = ScriptedFetcher({})
    with make_client(tmp_path, fetcher, resolver) as client:
        response = client.post(
            "/api/captures/web",
            json={"url": "https://example.com/rebind"},
        )
        assert response.status_code == 422
        assert fetcher.calls == []


def test_redirect_to_private_target_is_rejected_before_second_fetch(tmp_path: Path) -> None:
    fetcher = ScriptedFetcher(
        {
            "https://example.com/start": html_response(
                b"redirect",
                status=302,
                headers={"location": "http://127.0.0.1/admin"},
            )
        }
    )
    with make_client(tmp_path, fetcher) as client:
        response = client.post(
            "/api/captures/web",
            json={"url": "https://example.com/start"},
        )
        assert response.status_code == 422
        assert len(fetcher.calls) == 1


def test_large_body_invalid_type_and_fetch_failure_are_safe(tmp_path: Path) -> None:
    cases = [
        (
            "https://example.com/large",
            WebFetchResponse(
                200,
                {"content-type": "text/plain"},
                b"x" * (web_capture.MAX_RESPONSE_BYTES + 1),
            ),
            413,
        ),
        (
            "https://example.com/image",
            WebFetchResponse(200, {"content-type": "image/png"}, b"not really png"),
            422,
        ),
        (
            "https://example.com/down",
            WebCaptureFetchError("connection failed"),
            502,
        ),
        ("https://example.com/blocked", WebFetchResponse(403, {}, b""), 502),
    ]
    for index, (url, result, expected_status) in enumerate(cases):
        case_root = tmp_path / str(index)
        case_root.mkdir()
        fetcher = ScriptedFetcher({url: result})
        with make_client(case_root, fetcher) as client:
            response = client.post("/api/captures/web", json={"url": url})
            assert response.status_code == expected_status
            assert response.json()["detail"]
            asset_root = case_root / "assets"
            assert not asset_root.exists() or not any(asset_root.rglob("*"))


class StreamResponse:
    status = 200

    def __init__(self, data: bytes, headers: dict[str, str]) -> None:
        self.stream = io.BytesIO(data)
        self.headers = {key.casefold(): value for key, value in headers.items()}

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name.casefold())

    def read(self, size: int) -> bytes:
        return self.stream.read(size)


class StreamConnection:
    sock = None


@pytest.mark.parametrize("compressed", [False, True])
def test_transfer_rejects_short_fixed_length_bodies(compressed: bool) -> None:
    body = b"%PDF-1.4\n"
    wire = gzip.compress(body) if compressed else body
    headers = {"Content-Type": "application/pdf", "Content-Length": str(len(wire) + 1)}
    if compressed:
        headers["Content-Encoding"] = "gzip"
    with pytest.raises(WebCaptureFetchError, match="Content-Length"):
        _decode_transfer_body(
            StreamResponse(wire, headers),  # type: ignore[arg-type]
            StreamConnection(),  # type: ignore[arg-type]
            deadline=time.monotonic() + 1,
            allow_pdf=True,
        )


def test_compressed_and_expanded_response_limits_are_independent(monkeypatch) -> None:
    monkeypatch.setattr(web_capture, "MAX_WIRE_BYTES", 32)
    monkeypatch.setattr(web_capture, "MAX_RESPONSE_BYTES", 64)
    with pytest.raises(WebCaptureTooLargeError, match="compressed"):
        _decode_transfer_body(
            StreamResponse(b"", {"Content-Length": "33"}),  # type: ignore[arg-type]
            StreamConnection(),  # type: ignore[arg-type]
            deadline=time.monotonic() + 1,
        )

    compressed = gzip.compress(b"x" * 65)
    assert len(compressed) <= 32
    with pytest.raises(WebCaptureTooLargeError, match="expanded"):
        _decode_transfer_body(
            StreamResponse(compressed, {"Content-Encoding": "gzip"}),  # type: ignore[arg-type]
            StreamConnection(),  # type: ignore[arg-type]
            deadline=time.monotonic() + 1,
        )


@pytest.mark.parametrize(("content_type", "compressed"), [
    ("application/pdf", False),
    ("application/octet-stream", False),
    ("application/octet-stream", True),
])
def test_pdf_transfer_uses_pdf_limit_for_mime_and_split_magic_bytes(
    content_type: str, compressed: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"%PDF-1.4\n" + b"x" * 120
    monkeypatch.setattr(web_capture, "MAX_WIRE_BYTES", 16)
    monkeypatch.setattr(web_capture, "MAX_RESPONSE_BYTES", 32)
    monkeypatch.setattr(web_capture, "READ_CHUNK_BYTES", 3)
    monkeypatch.setattr(asset_service, "MAX_PDF_INPUT_BYTES", len(body))
    wire = gzip.compress(body) if compressed else body
    headers = {"Content-Type": content_type, "Content-Length": str(len(wire))}
    if compressed:
        headers["Content-Encoding"] = "gzip"
    assert _decode_transfer_body(
        StreamResponse(wire, headers),  # type: ignore[arg-type]
        StreamConnection(),  # type: ignore[arg-type]
        deadline=time.monotonic() + 1,
        allow_pdf=True,
    ) == body

    with pytest.raises(WebCaptureTooLargeError):
        _decode_transfer_body(
            StreamResponse(wire, headers),  # type: ignore[arg-type]
            StreamConnection(),  # type: ignore[arg-type]
            deadline=time.monotonic() + 1,
        )

    monkeypatch.setattr(asset_service, "MAX_PDF_INPUT_BYTES", len(body) - 1)
    with pytest.raises(WebCaptureTooLargeError):
        _decode_transfer_body(
            StreamResponse(wire, headers),  # type: ignore[arg-type]
            StreamConnection(),  # type: ignore[arg-type]
            deadline=time.monotonic() + 1,
            allow_pdf=True,
        )


def test_declared_html_charset_is_used_for_chinese_readable_text() -> None:
    html = "<title>生物信息学</title><p>基因组课程资料</p>".encode("gbk")
    url = "https://example.com/notes"
    fetcher = ScriptedFetcher({
        url: html_response(html, headers={"content-type": "text/html; charset=gbk"}),
    })
    readable, title = fetch_public_page(url, PublicWebUrlPolicy(FakeResolver()), fetcher)
    assert title == "生物信息学"
    assert "基因组课程资料" in readable


def test_cancelled_idempotency_waiter_does_not_leak_the_capture_lock() -> None:
    async def exercise() -> None:
        capture_id = "cancel-safe-capture"
        owner = await _acquire_capture_lock(capture_id)
        waiter = asyncio.create_task(_acquire_capture_lock(capture_id))
        await asyncio.sleep(0.02)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        _release_capture_lock(capture_id, owner)

        next_owner = await asyncio.wait_for(
            _acquire_capture_lock(capture_id),
            timeout=0.1,
        )
        _release_capture_lock(capture_id, next_owner)

    asyncio.run(exercise())


def test_production_http_fetcher_uses_pinned_ip_and_original_host(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response(StreamResponse):
        def getheaders(self) -> list[tuple[str, str]]:
            return list(self.headers.items())

    class Connection:
        sock = None

        def __init__(self, host: str, port: int, timeout: float) -> None:
            captured["connection"] = (host, port, timeout)

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            captured["request"] = (method, path, headers)

        @staticmethod
        def getresponse() -> Response:
            return Response(b"pinned", {"Content-Type": "text/plain"})

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(web_capture.http.client, "HTTPConnection", Connection)
    target = ResolvedWebTarget(
        url="http://example.com/page",
        scheme="http",
        host="example.com",
        port=80,
        address=PUBLIC_IP,
        request_target="/page",
        host_header="example.com",
        allow_pdf=True,
    )
    fetcher = PinnedHttpFetcher()
    response = fetcher.fetch(target, timeout_seconds=10)
    assert response.body == b"pinned"
    assert captured["connection"] == (PUBLIC_IP, 80, 5.0)
    assert captured["request"][:2] == ("GET", "/page")  # type: ignore[index]
    assert captured["request"][2]["Host"] == "example.com"  # type: ignore[index]
    assert "application/pdf" in captured["request"][2]["Accept"]  # type: ignore[index]
    assert fetch_public_page(target.url, PublicWebUrlPolicy(FakeResolver()), fetcher) == (
        "pinned", None,
    )
    assert "application/pdf" not in captured["request"][2]["Accept"]  # type: ignore[index]


def test_https_connection_pins_tcp_ip_but_uses_hostname_for_tls(monkeypatch) -> None:
    captured: dict[str, object] = {}
    raw_socket = object()
    wrapped_socket = object()

    class Context:
        def wrap_socket(self, sock: object, *, server_hostname: str) -> object:
            captured["tls"] = (sock, server_hostname)
            return wrapped_socket

    def create_connection(address, timeout, source_address):
        captured["tcp"] = (address, timeout, source_address)
        return raw_socket

    monkeypatch.setattr(web_capture.socket, "create_connection", create_connection)
    target = ResolvedWebTarget(
        url="https://example.com/",
        scheme="https",
        host="example.com",
        port=443,
        address=PUBLIC_IP,
        request_target="/",
        host_header="example.com",
    )
    connection = _PinnedHttpsConnection(
        target,
        timeout=2,
        context=Context(),  # type: ignore[arg-type]
    )
    connection.connect()
    assert captured["tcp"][0] == (PUBLIC_IP, 443)  # type: ignore[index]
    assert captured["tls"] == (raw_socket, "example.com")
    assert connection.sock is wrapped_socket
