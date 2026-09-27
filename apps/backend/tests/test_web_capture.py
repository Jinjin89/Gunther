import asyncio
import gzip
import hashlib
import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gunther import web_capture
from gunther.config import Settings
from gunther.main import create_app
from gunther.web_capture import (
    PinnedHttpFetcher,
    PublicWebUrlPolicy,
    ResolvedWebTarget,
    WebCaptureFetchError,
    WebCaptureTooLargeError,
    WebFetchResponse,
    _acquire_capture_lock,
    _decode_transfer_body,
    _PinnedHttpsConnection,
    _readable_page,
    _release_capture_lock,
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
) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
                assets_dir=tmp_path / "assets",
                recordings_dir=tmp_path / "recordings",
                seed_demo=False,
                deepseek_api_key=None,
                stt_provider="openai",
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


def test_web_capture_preserves_bytes_body_hash_locator_provenance_and_inbox(
    tmp_path: Path,
) -> None:
    original = (
        b"<html><head><title>Genomics Guide</title></head><body>"
        b"<p>BRCA1 -> associated_with -> DNA repair</p>"
        b"<script>not evidence</script></body></html>"
    )
    fetcher = ScriptedFetcher(
        {"https://example.com/guide": html_response(original)}
    )
    with make_client(tmp_path, fetcher) as client:
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

        detail = client.get(f"/api/sources/{source['id']}").json()
        assert detail["webSnapshot"] == snapshot
        assert "BRCA1 -> associated_with -> DNA repair" in detail["content"]
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
            for source in client.get(
                f"/api/knowledge-bases/{knowledge_base_id}/sources"
            ).json()
        }
        assert all(
            item["sourceId"] != source_id for item in client.get("/api/inbox").json()
        )


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
            {
                key: value
                for key, value in normalized_payload.items()
                if key != "knowledgeBaseId"
            },
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


def test_redirects_are_manually_validated_and_preserve_both_urls(tmp_path: Path) -> None:
    resolver = FakeResolver(
        {"example.com": [PUBLIC_IP], "cdn.example.net": ["1.1.1.1"]}
    )
    fetcher = ScriptedFetcher(
        {
            "https://example.com/start": html_response(
                b"redirect",
                status=302,
                headers={"Location": "https://cdn.example.net/final"},
            ),
            "https://cdn.example.net/final": html_response(b"<p>Final evidence</p>"),
        }
    )
    with make_client(tmp_path, fetcher, resolver) as client:
        response = client.post(
            "/api/captures/web",
            json={"url": "https://example.com/start"},
        )
        assert response.status_code == 201
        assert response.json()["snapshot"]["originalUrl"] == "https://example.com/start"
        assert response.json()["snapshot"]["finalUrl"] == "https://cdn.example.net/final"
        assert [target.address for target in fetcher.calls] == [PUBLIC_IP, "1.1.1.1"]
        assert resolver.calls == [("example.com", 443), ("cdn.example.net", 443)]


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
    ]
    for index, (url, result, expected_status) in enumerate(cases):
        case_root = tmp_path / str(index)
        case_root.mkdir()
        fetcher = ScriptedFetcher({url: result})
        with make_client(case_root, fetcher) as client:
            response = client.post("/api/captures/web", json={"url": url})
            assert response.status_code == expected_status
            asset_root = case_root / "assets"
            assert not asset_root.exists() or not any(asset_root.rglob("*"))


class StreamResponse:
    def __init__(self, data: bytes, headers: dict[str, str]) -> None:
        self.stream = io.BytesIO(data)
        self.headers = {key.casefold(): value for key, value in headers.items()}

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name.casefold())

    def read(self, size: int) -> bytes:
        return self.stream.read(size)


class StreamConnection:
    sock = None


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


def test_declared_html_charset_is_used_for_chinese_readable_text() -> None:
    html = "<title>生物信息学</title><p>基因组课程资料</p>".encode("gbk")
    readable, title = _readable_page(html, "text/html; charset=gbk")
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

    class Response:
        status = 200

        @staticmethod
        def getheaders() -> list[tuple[str, str]]:
            return [("Content-Type", "text/plain")]

        @staticmethod
        def getheader(_name: str) -> str | None:
            return None

        @staticmethod
        def read(_size: int) -> bytes:
            if captured.get("read"):
                return b""
            captured["read"] = True
            return b"pinned"

    class Connection:
        sock = None

        def __init__(self, host: str, port: int, timeout: float) -> None:
            captured["connection"] = (host, port, timeout)

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            captured["request"] = (method, path, headers)

        @staticmethod
        def getresponse() -> Response:
            return Response()

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
    )
    response = PinnedHttpFetcher().fetch(target, timeout_seconds=10)
    assert response.body == b"pinned"
    assert captured["connection"] == (PUBLIC_IP, 80, 5.0)
    assert captured["request"][2]["Host"] == "example.com"  # type: ignore[index]


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
