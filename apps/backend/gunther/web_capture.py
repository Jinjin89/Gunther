from __future__ import annotations

import asyncio
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from typing import Protocol
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from gunther.asset_service import (
    MAX_EXTRACTED_CHARACTERS,
    AssetService,
    _decode_text,
    _ReadableHtml,
)
from gunther.database import session_scope
from gunther.models import Asset, KnowledgeBaseRecord, Source, WebSnapshot
from gunther.schemas import (
    CreateSourceInput,
    ImportCountsOut,
    ImportResultOut,
    SourceSummaryOut,
    WebCaptureInput,
    WebCaptureOut,
    WebSnapshotOut,
)
from gunther.service import KnowledgeService

CONNECT_TIMEOUT_SECONDS = 5.0
TOTAL_TIMEOUT_SECONDS = 15.0
MAX_REDIRECTS = 5
MAX_WEB_URL_LENGTH = 4_096
MAX_WIRE_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
ALLOWED_CONTENT_TYPES = frozenset(
    {"text/html", "application/xhtml+xml", "text/plain"}
)
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RESERVED_HOST_SUFFIXES = (
    ".internal",
    ".invalid",
    ".local",
    ".localhost",
    ".onion",
    ".test",
    ".example",
    ".home.arpa",
)
_BLOCKED_IPV6_TRANSITION_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "::ffff:0:0/96",  # IPv4-mapped addresses can acquire IPv4 socket semantics.
        "64:ff9b::/96",  # Well-known NAT64 prefix.
        "64:ff9b:1::/48",  # Local-use NAT64 prefix.
        "2001::/32",  # Teredo embeds an IPv4 endpoint in an IPv6 address.
        "2002::/16",  # 6to4 embeds an IPv4 destination in an IPv6 address.
    )
)
_capture_locks_guard = Lock()
_capture_locks: dict[str, tuple[Lock, int]] = {}


class WebCaptureError(RuntimeError):
    pass


class WebCaptureValidationError(WebCaptureError):
    pass


class WebCaptureTooLargeError(WebCaptureValidationError):
    pass


class WebCaptureTimeoutError(WebCaptureError):
    pass


class WebCaptureFetchError(WebCaptureError):
    pass


class WebCaptureConflictError(WebCaptureError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedWebTarget:
    """One validated URL whose TCP destination can no longer be rebound."""

    url: str
    scheme: str
    host: str
    port: int
    address: str
    request_target: str
    host_header: str


@dataclass(frozen=True, slots=True)
class _NormalizedWebUrl:
    url: str
    scheme: str
    host: str
    port: int
    request_target: str
    host_header: str


@dataclass(frozen=True, slots=True)
class _WebCaptureIntent:
    normalized_url: str
    title: str | None
    notes: str
    knowledge_base_id: str | None
    fingerprint: str


@dataclass(frozen=True, slots=True)
class WebFetchResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def header(self, name: str) -> str | None:
        wanted = name.casefold()
        return next(
            (value for key, value in self.headers.items() if key.casefold() == wanted),
            None,
        )


class WebResolver(Protocol):
    def resolve(self, host: str, port: int) -> Sequence[str]: ...


class WebFetcher(Protocol):
    def fetch(
        self,
        target: ResolvedWebTarget,
        *,
        timeout_seconds: float,
    ) -> WebFetchResponse: ...


class SystemWebResolver:
    """Resolve once; the resulting IP is passed explicitly to the connection."""

    def resolve(self, host: str, port: int) -> Sequence[str]:
        addresses: list[str] = []
        for _family, _type, _proto, _canonical, socket_address in socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        ):
            address = str(socket_address[0])
            if address not in addresses:
                addresses.append(address)
        return addresses


def _public_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise WebCaptureValidationError("The URL resolved to an invalid address") from error
    blocked_transition_address = isinstance(address, ipaddress.IPv6Address) and any(
        address in network for network in _BLOCKED_IPV6_TRANSITION_NETWORKS
    )
    if (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or blocked_transition_address
    ):
        raise WebCaptureValidationError(
            "Web capture only connects to publicly routable addresses"
        )
    return str(address)


def _normalize_web_url(raw_url: str) -> _NormalizedWebUrl:
    """Canonicalize the request URL without doing DNS or making a network request."""

    value = raw_url.strip()
    if (
        not value
        or len(value) > MAX_WEB_URL_LENGTH
        or any(ord(character) < 32 for character in value)
    ):
        raise WebCaptureValidationError("The web URL is invalid")
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.casefold()
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise WebCaptureValidationError("The web URL is invalid") from error
    if scheme not in {"http", "https"}:
        raise WebCaptureValidationError("Only http and https URLs can be captured")
    if parsed.username is not None or parsed.password is not None:
        raise WebCaptureValidationError("URLs containing user information are not allowed")
    if not host:
        raise WebCaptureValidationError("The web URL must contain a host")
    if "%" in host:
        raise WebCaptureValidationError("Scoped or encoded host addresses are not allowed")

    normalized_host = host.rstrip(".").casefold()
    try:
        literal_address = ipaddress.ip_address(normalized_host)
    except ValueError:
        try:
            normalized_host = normalized_host.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise WebCaptureValidationError("The web URL host is invalid") from error
        labels = normalized_host.split(".")
        if (
            len(labels) < 2
            or len(normalized_host) > 253
            or all(label.isdecimal() for label in labels)
            or any(
                not label
                or len(label) > 63
                or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
                for label in labels
            )
        ):
            raise WebCaptureValidationError("The web URL host is invalid") from None
        if (
            normalized_host in {"home.arpa", "localhost"}
            or normalized_host.endswith(_RESERVED_HOST_SUFFIXES)
        ):
            raise WebCaptureValidationError("Reserved host names cannot be captured") from None
    else:
        normalized_host = str(literal_address)

    expected_port = 443 if scheme == "https" else 80
    if port is not None and port != expected_port:
        raise WebCaptureValidationError("Only the standard HTTP and HTTPS ports are allowed")

    encoded_path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    encoded_query = quote(parsed.query, safe="=&?/:@!$'()*+,;%-._~")
    display_host = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    canonical_url = urlunsplit((scheme, display_host, encoded_path, encoded_query, ""))
    return _NormalizedWebUrl(
        url=canonical_url,
        scheme=scheme,
        host=normalized_host,
        port=expected_port,
        request_target=f"{encoded_path}?{encoded_query}" if encoded_query else encoded_path,
        host_header=display_host,
    )


def _capture_intent(payload: WebCaptureInput) -> _WebCaptureIntent:
    normalized_url = _normalize_web_url(payload.requested_url).url
    title = payload.title.strip() if payload.title and payload.title.strip() else None
    notes = payload.notes.strip()
    knowledge_base_id = (
        payload.knowledge_base_id.strip()
        if payload.knowledge_base_id and payload.knowledge_base_id.strip()
        else None
    )
    canonical = json.dumps(
        {
            "knowledgeBaseId": knowledge_base_id,
            "notes": notes,
            "title": title,
            "url": normalized_url,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _WebCaptureIntent(
        normalized_url=normalized_url,
        title=title,
        notes=notes,
        knowledge_base_id=knowledge_base_id,
        fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


class PublicWebUrlPolicy:
    """Validate the URL and every resolved address before a network connection."""

    def __init__(self, resolver: WebResolver) -> None:
        self.resolver = resolver

    def resolve(self, raw_url: str) -> ResolvedWebTarget:
        normalized = _normalize_web_url(raw_url)
        try:
            literal_address = ipaddress.ip_address(normalized.host)
        except ValueError:
            literal_address = None

        if literal_address is not None:
            addresses = [_public_address(str(literal_address))]
        else:
            try:
                addresses = list(self.resolver.resolve(normalized.host, normalized.port))
            except WebCaptureError:
                raise
            except OSError as error:
                raise WebCaptureFetchError("The web host could not be resolved") from error
            if not addresses:
                raise WebCaptureFetchError("The web host did not resolve to an address")
            addresses = [_public_address(address) for address in addresses]
        return ResolvedWebTarget(
            url=normalized.url,
            scheme=normalized.scheme,
            host=normalized.host,
            port=normalized.port,
            address=addresses[0],
            request_target=normalized.request_target,
            host_header=normalized.host_header,
        )


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        target: ResolvedWebTarget,
        *,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(target.host, target.port, timeout=timeout, context=context)
        self._pinned_address = target.address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _decode_transfer_body(
    response: http.client.HTTPResponse,
    connection: http.client.HTTPConnection,
    *,
    deadline: float,
) -> bytes:
    declared = response.getheader("Content-Length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError as error:
            raise WebCaptureFetchError("The response Content-Length is invalid") from error
        if declared_size < 0:
            raise WebCaptureFetchError("The response Content-Length is invalid")
        if declared_size > MAX_WIRE_BYTES:
            raise WebCaptureTooLargeError("The compressed web response is too large")

    encoding = (response.getheader("Content-Encoding") or "identity").strip().casefold()
    if encoding in {"", "identity"}:
        decompressor = None
    elif encoding == "gzip":
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        decompressor = zlib.decompressobj(zlib.MAX_WBITS)
    else:
        raise WebCaptureValidationError("The response uses an unsupported content encoding")

    wire_size = 0
    decoded = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WebCaptureTimeoutError("The web response timed out")
        if connection.sock is not None:
            connection.sock.settimeout(max(0.001, remaining))
        chunk = response.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        wire_size += len(chunk)
        if wire_size > MAX_WIRE_BYTES:
            raise WebCaptureTooLargeError("The compressed web response is too large")
        if decompressor is None:
            decoded.extend(chunk)
        else:
            budget = MAX_RESPONSE_BYTES - len(decoded)
            decoded.extend(decompressor.decompress(chunk, budget + 1))
            if decompressor.unconsumed_tail:
                raise WebCaptureTooLargeError("The expanded web response is too large")
        if len(decoded) > MAX_RESPONSE_BYTES:
            raise WebCaptureTooLargeError("The expanded web response is too large")

    if decompressor is not None:
        budget = MAX_RESPONSE_BYTES - len(decoded)
        decoded.extend(decompressor.flush(budget + 1))
        if len(decoded) > MAX_RESPONSE_BYTES:
            raise WebCaptureTooLargeError("The expanded web response is too large")
        if not decompressor.eof or decompressor.unused_data:
            raise WebCaptureFetchError("The compressed web response is malformed")
    return bytes(decoded)


class PinnedHttpFetcher:
    """Connect to the policy-approved IP while keeping Host and TLS SNI intact."""

    def __init__(self, ssl_context: ssl.SSLContext | None = None) -> None:
        self.ssl_context = ssl_context or ssl.create_default_context()

    def fetch(
        self,
        target: ResolvedWebTarget,
        *,
        timeout_seconds: float,
    ) -> WebFetchResponse:
        deadline = time.monotonic() + timeout_seconds
        connect_timeout = min(CONNECT_TIMEOUT_SECONDS, timeout_seconds)
        connection: http.client.HTTPConnection
        if target.scheme == "https":
            connection = _PinnedHttpsConnection(
                target,
                timeout=connect_timeout,
                context=self.ssl_context,
            )
        else:
            connection = http.client.HTTPConnection(
                target.address,
                target.port,
                timeout=connect_timeout,
            )
        try:
            connection.request(
                "GET",
                target.request_target,
                headers={
                    "Host": target.host_header,
                    "User-Agent": "Gunther-WebCapture/1.0",
                    "Accept": "text/html, application/xhtml+xml, text/plain;q=0.9",
                    "Accept-Encoding": "gzip, deflate",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            headers: dict[str, str] = {}
            for name, value in response.getheaders():
                key = name.casefold()
                headers[key] = f"{headers[key]}, {value}" if key in headers else value
            body = _decode_transfer_body(response, connection, deadline=deadline)
            return WebFetchResponse(status=response.status, headers=headers, body=body)
        except WebCaptureError:
            raise
        except TimeoutError as error:
            raise WebCaptureTimeoutError("The web response timed out") from error
        except (http.client.HTTPException, OSError, ssl.SSLError, zlib.error) as error:
            raise WebCaptureFetchError("The web page could not be fetched") from error
        finally:
            connection.close()


class _HtmlTitle(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "title":
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title" and self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)

    def title(self) -> str:
        return " ".join(" ".join(self.parts).split())


def _decode_response(data: bytes, content_type: str) -> str:
    match = re.search(r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\s\"']+)", content_type, re.I)
    if match and len(match.group(1)) <= 40:
        try:
            decoded = data.decode(match.group(1), errors="replace")
        except LookupError:
            pass
        else:
            if "\x00" not in decoded:
                return decoded
    return _decode_text(data)


def _readable_page(data: bytes, content_type: str) -> tuple[str, str | None]:
    media_type = content_type.split(";", 1)[0].strip().casefold()
    decoded = _decode_response(data, content_type)
    if media_type == "text/plain":
        return decoded.strip(), None
    title_parser = _HtmlTitle()
    title_parser.feed(decoded)
    readable_parser = _ReadableHtml()
    readable_parser.feed(decoded)
    return readable_parser.text().strip(), title_parser.title() or None


def _redirect_url(
    target: ResolvedWebTarget, response: WebFetchResponse, redirect_count: int
) -> str | None:
    """Where a redirect response points, or None when the response is not a redirect."""

    if response.status not in REDIRECT_STATUSES:
        return None
    if redirect_count == MAX_REDIRECTS:
        raise WebCaptureValidationError("The web page redirected too many times")
    location = (response.header("location") or "").strip()
    if not location:
        raise WebCaptureFetchError("The redirect response has no Location header")
    redirected_url = urljoin(target.url, location)
    if len(redirected_url) > MAX_WEB_URL_LENGTH:
        raise WebCaptureValidationError("The redirect URL is too long")
    return redirected_url


def _page_content_type(response: WebFetchResponse) -> str:
    """The content type of a page that may be read, after the status and size checks."""

    if not 200 <= response.status < 300:
        raise WebCaptureFetchError(f"The web page returned HTTP status {response.status}")
    content_type = (response.header("content-type") or "").strip()
    if len(content_type) > 160:
        raise WebCaptureValidationError("The response Content-Type is too long")
    if any(ord(character) < 32 for character in content_type):
        raise WebCaptureValidationError("The response Content-Type is invalid")
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type not in ALLOWED_CONTENT_TYPES:
        raise WebCaptureValidationError("Only HTML, XHTML, and plain-text pages can be captured")
    if not response.body:
        raise WebCaptureValidationError("The web response is empty")
    if len(response.body) > MAX_RESPONSE_BYTES:
        raise WebCaptureTooLargeError("The expanded web response is too large")
    return content_type


def fetch_public_page(
    url: str,
    policy: PublicWebUrlPolicy,
    fetcher: WebFetcher,
    *,
    timeout: float = TOTAL_TIMEOUT_SECONDS,
) -> tuple[str, str | None]:
    """The readable text and title of one public page, for reading a source further.

    The same rules as a capture: the public-address policy for the URL and every
    redirect, the redirect and size limits, and only HTML or plain text.
    """

    deadline = time.monotonic() + timeout
    target = policy.resolve(url)
    for redirect_count in range(MAX_REDIRECTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WebCaptureTimeoutError("The web page timed out")
        try:
            response = fetcher.fetch(target, timeout_seconds=remaining)
        except WebCaptureError:
            raise
        except TimeoutError as error:
            raise WebCaptureTimeoutError("The web page timed out") from error
        except Exception as error:
            raise WebCaptureFetchError("The web page could not be fetched") from error
        redirected = _redirect_url(target, response, redirect_count)
        if redirected is None:
            return _readable_page(response.body, _page_content_type(response))
        target = policy.resolve(redirected)
    raise WebCaptureValidationError("The web page redirected too many times")


def _asset_file_name(final_url: str, media_type: str) -> str:
    candidate = Path(unquote(urlsplit(final_url).path)).name.strip() or "snapshot"
    desired_suffix = ".txt" if media_type == "text/plain" else ".html"
    if not Path(candidate).suffix:
        candidate += desired_suffix
    return candidate


def _timestamp(value: datetime) -> str:
    return f"{value.isoformat(timespec='milliseconds')}Z"


async def _acquire_capture_lock(capture_id: str) -> Lock:
    with _capture_locks_guard:
        lock, users = _capture_locks.get(capture_id, (Lock(), 0))
        _capture_locks[capture_id] = (lock, users + 1)
    try:
        while not lock.acquire(blocking=False):
            await asyncio.sleep(0.01)
        return lock
    except BaseException:
        _drop_capture_lock_user(capture_id, lock)
        raise


def _drop_capture_lock_user(capture_id: str, lock: Lock) -> None:
    with _capture_locks_guard:
        active = _capture_locks.get(capture_id)
        if active is None:
            return
        active_lock, users = active
        if active_lock is not lock:
            raise RuntimeError("Web capture lock identity changed unexpectedly")
        if users == 1:
            del _capture_locks[capture_id]
        else:
            _capture_locks[capture_id] = (active_lock, users - 1)


def _release_capture_lock(capture_id: str, lock: Lock) -> None:
    lock.release()
    _drop_capture_lock_user(capture_id, lock)


class WebCaptureService:
    def __init__(
        self,
        assets: AssetService,
        knowledge: KnowledgeService,
        resolver: WebResolver,
        fetcher: WebFetcher,
    ) -> None:
        self.assets = assets
        self.knowledge = knowledge
        self.policy = PublicWebUrlPolicy(resolver)
        self.fetcher = fetcher

    @staticmethod
    def _snapshot_out(snapshot: WebSnapshot) -> WebSnapshotOut:
        return WebSnapshotOut(
            original_url=snapshot.original_url,
            final_url=snapshot.final_url,
            captured_at=_timestamp(snapshot.captured_at),
            status=snapshot.status,
            content_type=snapshot.content_type,
            content_hash=snapshot.content_hash,
            asset_id=snapshot.asset_id,
        )

    def _find_snapshot(self, client_capture_id: str) -> WebSnapshot | None:
        with session_scope(self.knowledge.sessions) as session:
            return session.scalar(
                select(WebSnapshot).where(
                    WebSnapshot.client_capture_id == client_capture_id
                )
            )

    def _replay(
        self,
        snapshot: WebSnapshot,
        intent: _WebCaptureIntent,
    ) -> WebCaptureOut:
        if snapshot.request_fingerprint is None:
            # v8 rows predate persisted request intent. Retain their URL-only
            # replay behavior instead of fabricating title/notes/filing intent
            # from Source data that may have changed after the capture.
            same_request = (
                _normalize_web_url(snapshot.original_url).url == intent.normalized_url
            )
        else:
            same_request = snapshot.request_fingerprint == intent.fingerprint
        if not same_request:
            raise WebCaptureConflictError(
                "clientCaptureId is already associated with a different web capture request"
            )
        with session_scope(self.knowledge.sessions) as session:
            asset = session.get(Asset, snapshot.asset_id)
            source = session.get(Source, snapshot.source_id)
            if asset is None or source is None:
                raise WebCaptureFetchError("The stored web capture is incomplete")
        detail = self.knowledge.get_source(snapshot.source_id)
        summary = SourceSummaryOut.model_validate(detail.model_dump())
        return WebCaptureOut(
            asset=self.assets._out(asset),
            import_result=ImportResultOut(
                source=summary,
                created=ImportCountsOut(entities=0, assertions=0),
                extraction_mode=self.knowledge.extractor.mode,
                duplicate=True,
            ),
            snapshot=self._snapshot_out(snapshot),
            idempotent_replay=True,
        )

    async def _resolve_with_deadline(
        self,
        url: str,
        deadline: float,
    ) -> ResolvedWebTarget:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WebCaptureTimeoutError("The web capture timed out")
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.policy.resolve, url),
                timeout=remaining,
            )
        except TimeoutError as error:
            raise WebCaptureTimeoutError("The web capture timed out") from error

    async def _fetch(self, original_url: str) -> tuple[ResolvedWebTarget, WebFetchResponse]:
        deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS
        target = await self._resolve_with_deadline(original_url, deadline)
        for redirect_count in range(MAX_REDIRECTS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WebCaptureTimeoutError("The web capture timed out")
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.fetcher.fetch,
                        target,
                        timeout_seconds=remaining,
                    ),
                    timeout=remaining,
                )
            except WebCaptureError:
                raise
            except TimeoutError as error:
                raise WebCaptureTimeoutError("The web capture timed out") from error
            except Exception as error:
                raise WebCaptureFetchError("The web page could not be fetched") from error
            redirected_url = _redirect_url(target, response, redirect_count)
            if redirected_url is None:
                return target, response
            target = await self._resolve_with_deadline(redirected_url, deadline)
        raise WebCaptureValidationError("The web page redirected too many times")

    @staticmethod
    def _source_content(
        snapshot_id: str,
        original_url: str,
        final_url: str,
        captured_at: datetime,
        response: WebFetchResponse,
        content_type: str,
        readable: str,
        notes: str,
        content_hash: str,
    ) -> str:
        metadata = (
            "# Web snapshot\n\n"
            f"Original URL: {original_url}\n"
            f"Final URL: {final_url}\n"
            f"Captured at: {_timestamp(captured_at)}\n"
            f"HTTP status: {response.status}\n"
            f"Content type: {content_type}\n"
            f"SHA-256: {content_hash}\n"
            f"Snapshot ID: {snapshot_id}"
        )
        sections = [metadata]
        if notes.strip():
            sections.append(f"## Your context\n\n{notes.strip()}")
        if readable.strip():
            available = max(0, MAX_EXTRACTED_CHARACTERS - len(metadata) - len(notes) - 64)
            sections.append(f"## Captured content\n\n{readable.strip()[:available]}")
        return "\n\n".join(sections)[:1_000_000]

    async def _capture_unlocked(self, payload: WebCaptureInput) -> WebCaptureOut:
        intent = _capture_intent(payload)
        requested_url = intent.normalized_url
        if payload.client_capture_id:
            existing = self._find_snapshot(payload.client_capture_id)
            if existing:
                return self._replay(existing, intent)

        if intent.knowledge_base_id:
            with session_scope(self.knowledge.sessions) as session:
                library = session.get(KnowledgeBaseRecord, intent.knowledge_base_id)
                if library is None or library.trashed_at is not None:
                    raise LookupError(
                        f"Knowledge Base {intent.knowledge_base_id} was not found"
                    )

        target, response = await self._fetch(requested_url)
        content_type = _page_content_type(response)
        media_type = content_type.split(";", 1)[0].strip().casefold()

        readable, page_title = await asyncio.to_thread(
            _readable_page,
            response.body,
            content_type,
        )
        captured_at = datetime.now(UTC).replace(tzinfo=None)
        content_hash = hashlib.sha256(response.body).hexdigest()
        snapshot_id = f"wbs_{uuid4().hex}"
        title = (intent.title or page_title or target.host).strip()[:160]
        asset = await self.assets.preserve_bytes(
            response.body,
            file_name=_asset_file_name(target.url, media_type),
            media_type=content_type,
        )
        source_content = self._source_content(
            snapshot_id,
            requested_url,
            target.url,
            captured_at,
            response,
            content_type,
            readable,
            intent.notes,
            content_hash,
        )
        snapshots: list[WebSnapshot] = []

        def link_snapshot(_session, source: Source) -> None:
            source.asset_id = asset.id
            snapshot = WebSnapshot(
                id=snapshot_id,
                source_id=source.id,
                asset_id=asset.id,
                client_capture_id=payload.client_capture_id,
                request_fingerprint=intent.fingerprint,
                original_url=requested_url,
                final_url=target.url,
                captured_at=captured_at,
                status=response.status,
                content_type=content_type,
                content_hash=content_hash,
            )
            _session.add(snapshot)
            snapshots.append(snapshot)

        try:
            imported = await asyncio.to_thread(
                self.knowledge.import_source,
                CreateSourceInput(
                    title=title,
                    kind="link",
                    content=source_content,
                    knowledge_base_id=intent.knowledge_base_id,
                ),
                initialize_source=link_snapshot,
            )
        except IntegrityError:
            if payload.client_capture_id:
                existing = self._find_snapshot(payload.client_capture_id)
                if existing:
                    return self._replay(existing, intent)
            raise
        if not snapshots:
            raise RuntimeError("The captured Source was not linked to its web snapshot")
        snapshot = snapshots[0]

        detail = self.knowledge.get_source(imported.source.id)
        summary = SourceSummaryOut.model_validate(detail.model_dump())
        return WebCaptureOut(
            asset=self.assets._out(asset),
            import_result=ImportResultOut(
                source=summary,
                created=imported.created,
                extraction_mode=imported.extraction_mode,
                duplicate=imported.duplicate,
            ),
            snapshot=self._snapshot_out(snapshot),
            idempotent_replay=False,
        )

    async def capture(self, payload: WebCaptureInput) -> WebCaptureOut:
        if not payload.client_capture_id:
            return await self._capture_unlocked(payload)
        lock = await _acquire_capture_lock(payload.client_capture_id)
        try:
            return await self._capture_unlocked(payload)
        finally:
            _release_capture_lock(payload.client_capture_id, lock)
