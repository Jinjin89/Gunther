import hashlib

from fastapi.testclient import TestClient
from httpx import Response


def append_recording_chunk(
    client: TestClient, recording_id: str, sequence: int, data: bytes
) -> Response:
    return client.put(
        f"/api/recordings/{recording_id}/chunks",
        params={"sequence": sequence},
        content=data,
        headers={"X-Chunk-SHA256": hashlib.sha256(data).hexdigest()},
    )


def completed_recording(
    client: TestClient, data: bytes, *, title: str, content_type: str
) -> Response:
    started = client.post(
        "/api/recordings/sessions",
        params={"title": title},
        headers={"Content-Type": content_type},
    ).raise_for_status()
    recording_id = started.json()["id"]
    append_recording_chunk(client, recording_id, 0, data).raise_for_status()
    return client.post(f"/api/recordings/{recording_id}/complete").raise_for_status()
