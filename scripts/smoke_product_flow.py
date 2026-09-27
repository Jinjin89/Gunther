"""Run Gunther's capture -> Inbox -> Library flow against a disposable server.

This is intentionally write-protected and loopback-only. It creates synthetic
records, so run it only against a temporary workspace with ``--allow-write``.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import secrets
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen


class SmokeFailure(RuntimeError):
    pass


def _loopback_url(value: str) -> str:
    normalized = value.rstrip("/") + "/"
    parsed = urlsplit(normalized)
    if parsed.scheme != "http" or not parsed.hostname:
        raise argparse.ArgumentTypeError("smoke server must use loopback HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("smoke server URL contains unsafe parts")
    if parsed.hostname != "localhost":
        try:
            if not ipaddress.ip_address(parsed.hostname).is_loopback:
                raise argparse.ArgumentTypeError("smoke server must be loopback-only")
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                "smoke server must be loopback-only"
            ) from error
    return normalized


class Client:
    def __init__(self, base_url: str, token: str = "") -> None:
        self.base_url = base_url
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = urljoin(self.base_url, path.lstrip("/"))
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["X-Gunther-Token"] = self.token
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise SmokeFailure(f"oversized response from {method} {path}")
                return None if not raw else json.loads(raw)
        except HTTPError as error:
            detail = error.read(32_768).decode(errors="replace")
            raise SmokeFailure(
                f"{method} {path} returned {error.code}: {detail}"
            ) from error
        except (URLError, TimeoutError) as error:
            raise SmokeFailure(f"{method} {path} could not connect: {error}") from error


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def run(client: Client) -> dict[str, Any]:
    marker = f"smoke-{secrets.token_hex(6)}"
    health = client.request("GET", "health")
    _expect(health.get("status") == "ok", "health did not report ok")
    bootstrap = client.request("GET", "workspace/bootstrap")
    _expect(bootstrap.get("workspaceId", "").startswith("wsp_"), "workspace id missing")

    note = client.request(
        "POST",
        "notes",
        {
            "title": f"Bioinformatics capture {marker}",
            "content": (
                "Needleman-Wunsch performs global sequence alignment. "
                f"Synthetic verification {marker}."
            ),
            "clientCaptureId": f"capture_{marker.replace('-', '_')}",
        },
    )
    _expect(note.get("status") == "inbox", "new note did not enter Notebook Inbox")

    knowledge_base = client.request(
        "POST",
        "knowledge-bases",
        {
            "title": f"Bioinformatics {marker}",
            "eyebrow": "Course",
            "subtitle": "Algorithms, papers, and lectures",
            "question": "How do computational methods explain biological data?",
            "description": "Synthetic end-to-end product verification.",
            "color": "green",
        },
    )
    base_id = knowledge_base["id"]

    inbox_before = client.request("GET", "inbox")
    _expect(
        any(item.get("noteId") == note["id"] for item in inbox_before),
        "captured note was not visible in unified Inbox",
    )
    filed = client.request(
        "POST",
        f"notes/{quote(note['id'], safe='')}/file",
        {"knowledgeBaseId": base_id},
    )
    source = filed["importResult"]["source"]
    _expect(filed["note"]["status"] == "filed", "note was not marked filed")

    inbox_after = client.request("GET", "inbox")
    _expect(
        not any(item.get("noteId") == note["id"] for item in inbox_after),
        "filed note remained in the attention Inbox",
    )
    filed_notes = client.request("GET", "notes?status=filed")
    _expect(
        any(item.get("id") == note["id"] for item in filed_notes),
        "filed note was not preserved in Notebook history",
    )
    sources = client.request(
        "GET", f"knowledge-bases/{quote(base_id, safe='')}/sources"
    )
    _expect(
        any(item.get("id") == source["id"] for item in sources),
        "promoted source was not attached to the knowledge base",
    )

    session = client.request(
        "POST",
        f"knowledge-bases/{quote(base_id, safe='')}/sessions",
        {"selectedSourceIds": [source["id"]]},
    )
    _expect(
        source["id"] in session.get("selectedSourceIds", []),
        "knowledge session did not retain its evidence scope",
    )
    query = urlencode({"q": marker, "limit": 20})
    search = client.request("GET", f"search?{query}")
    _expect(search, "workspace search did not find the synthetic capture")

    bases = client.request("GET", "knowledge-bases")
    final_base = next(item for item in bases if item["id"] == base_id)
    _expect(final_base["sourceCount"] >= 1, "library source count was not updated")
    return {
        "status": "passed",
        "workspaceId": bootstrap["workspaceId"],
        "knowledgeBaseId": base_id,
        "noteId": note["id"],
        "sourceId": source["id"],
        "sessionId": session["id"],
        "checks": 12,
        "extractionMode": health["extractionMode"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", type=_loopback_url, required=True)
    parser.add_argument("--token", default="")
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="confirm the target is a disposable workspace",
    )
    args = parser.parse_args()
    if not args.allow_write:
        parser.error("--allow-write is required for this synthetic flow")
    try:
        result = run(Client(args.base_url, args.token))
    except SmokeFailure as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
