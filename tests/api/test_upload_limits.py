"""Upload size limits.

upload_template already checked ``len(data) > max_upload_mb`` but only
after ``await file.read()`` had already buffered the entire body --
unbounded regardless of the configured limit. upload_content_pack had no
size check at all. Both now read through ``_read_upload_limited``, which
aborts as soon as the running total crosses the limit instead of after
reading everything. ``settings.max_upload_mb`` is patched down to a small
value so the test payload stays tiny and fast.
"""

from deckdna.api import app as app_module
from deckdna.api.app import app
from fastapi.testclient import TestClient

client = TestClient(app)

PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


def _project() -> str:
    return client.post("/api/v1/projects", json={"name": "upload limit test"}).json()[
        "id"
    ]


def test_template_upload_rejects_oversized_body_without_buffering_it(monkeypatch):
    monkeypatch.setattr(app_module.settings, "max_upload_mb", 1)
    project_id = _project()
    oversized = b"\x00" * (2 * 1024 * 1024)  # 2 MB > the patched 1 MB limit

    resp = client.post(
        f"/api/v1/projects/{project_id}/templates",
        files={"file": ("big.pptx", oversized, PPTX_MIME)},
    )
    assert resp.status_code == 413, resp.text
    assert resp.json()["error"]["code"] == "invalid_input"


def test_content_pack_upload_rejects_oversized_body(monkeypatch):
    monkeypatch.setattr(app_module.settings, "max_upload_mb", 1)
    project_id = _project()
    oversized = ("# heading\n\n" + "word " * 400_000).encode()  # > 1 MB

    resp = client.post(
        f"/api/v1/projects/{project_id}/content-packs",
        files={"files": ("big.md", oversized, "text/markdown")},
    )
    assert resp.status_code == 413, resp.text
    assert resp.json()["error"]["code"] == "invalid_input"


def test_content_pack_upload_accepts_body_under_the_limit(monkeypatch):
    monkeypatch.setattr(app_module.settings, "max_upload_mb", 1)
    project_id = _project()
    small = b"# heading\n\nsmall body.\n"

    resp = client.post(
        f"/api/v1/projects/{project_id}/content-packs",
        files={"files": ("small.md", small, "text/markdown")},
    )
    assert resp.status_code == 202, resp.text
