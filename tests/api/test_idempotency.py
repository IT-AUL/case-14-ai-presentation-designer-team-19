"""docs/contracts/API.md §12 idempotency replay.

The middleware used to read STORE.idempotency before call_next with no
reservation of the key, so two genuinely concurrent requests sharing an
Idempotency-Key could both see "no record yet" and both execute the
mutation. Separately, only responses whose content-type starts with
"application/json" got cached -- a bodyless 204 No Content (every
successful DELETE in this API) never did, so a retried DELETE with the
same key re-ran the handler and hit 404 (already deleted) instead of
replaying the original 204.
"""

import asyncio

from deckdna.api.app import STORE, app
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

client = TestClient(app)


def test_delete_replays_original_204_not_404():
    project = client.post(
        "/api/v1/projects", json={"name": "idem delete", "target_slide_count": 10}
    ).json()
    key = {"Idempotency-Key": "del-idem-1"}

    first = client.delete(f"/api/v1/projects/{project['id']}", headers=key)
    assert first.status_code == 204, first.text

    second = client.delete(f"/api/v1/projects/{project['id']}", headers=key)
    assert second.status_code == 204, second.text
    assert second.content == b""

    # A DELETE for a real, still-existing project under a DIFFERENT key
    # is unaffected -- still a genuine 404 once actually gone, not
    # accidentally cached as anything.
    assert (
        client.delete(
            f"/api/v1/projects/{project['id']}",
            headers={"Idempotency-Key": "del-idem-2"},
        ).status_code
        == 404
    )


def test_concurrent_duplicate_requests_create_exactly_one_project():
    before = set(STORE.projects)
    key = {"Idempotency-Key": "concurrent-create-1"}
    body = {"name": "race test", "target_slide_count": 10}

    async def _run() -> tuple[int, int, dict, dict]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r1, r2 = await asyncio.gather(
                ac.post("/api/v1/projects", json=body, headers=key),
                ac.post("/api/v1/projects", json=body, headers=key),
            )
        return r1.status_code, r2.status_code, r1.json(), r2.json()

    status1, status2, json1, json2 = asyncio.run(_run())
    assert status1 == status2 == 201
    assert json1 == json2  # identical replayed body, same project id
    created = set(STORE.projects) - before
    assert len(created) == 1
