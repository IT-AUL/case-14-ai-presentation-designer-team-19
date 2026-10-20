"""ADR-031 §8: состояние API переживает перезапуск процесса.

Создаём проект, шаблон, пак и генерацию, затем поднимаем НОВЫЙ ``_Store``
из снапшота на диске — всё доступно, байты артефактов на месте, секретов
provider-сессий в снапшоте нет (ADR-005).
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

from deckdna.api import app as app_module
from deckdna.api.app import STORE, _Store, app
from deckdna.api.persistence import load_store
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "pptx" / "vk_tech_template.pptx"
CONTENT = FIXTURES / "content" / "poc_article.md"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
A = "/api/v1"


def test_store_survives_restart(tmp_path, monkeypatch, wait_generation):
    monkeypatch.setattr(app_module.settings, "artifact_root", str(tmp_path))
    monkeypatch.setattr(app_module.settings, "state_snapshot", True)
    client = TestClient(app)

    pid = client.post(f"{A}/projects", json={"name": "переживёт перезапуск"}).json()["id"]
    tpl = client.post(
        f"{A}/projects/{pid}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    ).json()
    pack = client.post(
        f"{A}/projects/{pid}/content-packs",
        files={"files": (CONTENT.name, CONTENT.read_bytes(), "text/markdown")},
        data={"brief": json.dumps({"language": "ru"})},
    ).json()["content_pack"]
    monkeypatch.setattr(app_module.settings, "provider_allow_private_networks", True)
    session = client.post(
        f"{A}/provider-sessions",
        json={
            "label": "демо",
            "project_id": pid,
            "base_url": "http://127.0.0.1:9/v1",
            "api_token": "secret-token-never-on-disk",
            "models": {"text": "m", "vision": "v"},
        },
    )
    assert session.status_code == 201, session.text
    gen = client.post(
        f"{A}/projects/{pid}/generations",
        json={
            "template_id": tpl["id"],
            "content_pack_id": pack["id"],
            "brief": {"purpose": "демо", "audience": "жюри", "language": "ru",
                      "target_slide_count": 5},
            "variants": [{"strategy": "balanced"}],
        },
    ).json()
    run = wait_generation(client, gen["generation_id"])
    assert run["state"] == "completed", run
    # фоновый прогон закончился после последнего запроса — снапшот ловит
    # его через периодическую запись; здесь пишем явно, как сделал бы цикл
    app_module._persist()

    snapshot = tmp_path / "state" / "store.pkl"
    assert snapshot.exists()
    raw = snapshot.read_bytes()
    assert b"secret-token-never-on-disk" not in raw
    assert "provider_secrets" not in pickle.loads(raw)["fields"]  # noqa: S301

    fresh = _Store()
    assert load_store(fresh, snapshot)
    assert pid in fresh.projects
    assert tpl["id"] in fresh.templates
    assert pack["id"] in fresh.content_packs
    assert gen["generation_id"] in fresh.runs
    assert fresh.runs[gen["generation_id"]].detail.state.value == "completed"
    assert not fresh.provider_secrets and not fresh.provider_sessions
    assert session.json()["id"] in STORE.provider_sessions  # в памяти — да
    # байты артефактов (шаблон, колода) восстановлены из blobs/
    tpl_art = fresh.artifacts[tpl["artifact_id"]]
    assert tpl_art.data == STORE.artifacts[tpl["artifact_id"]].data
    deck_ids = list(fresh.runs[gen["generation_id"]].variant_artifacts.values())[0]
    assert fresh.artifacts[deck_ids["pptx"]].data[:2] == b"PK"


def test_corrupt_snapshot_starts_empty(tmp_path):
    path = tmp_path / "store.pkl"
    path.write_bytes(b"not a pickle")
    fresh = _Store()
    assert load_store(fresh, path) is False
    assert fresh.projects == {}
