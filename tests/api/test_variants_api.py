"""OR-007 через API: variants[].strategy реально меняет выход.

POST /generations с faithful/balanced/visual — три побитово разных
deck-артефакта (раньше стратегия шла только в имя файла и все три
pptx были идентичны). Плюс честная поверхность: свой deck_plan_id
у каждого варианта.
"""

import hashlib
import shutil
from pathlib import Path

import pytest
from deckdna.api.app import app
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "pptx" / "vk_tech_template.pptx"
CONTENT = FIXTURES / "content" / "poc_article.md"
PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)

client = TestClient(app)

BRIEF = {
    "purpose": "Показать три варианта колоды",
    "audience": "эксперты VK Tech",
    "language": "ru",
    "target_slide_count": 12,
}

needs_render = pytest.mark.skipif(
    shutil.which("soffice") is None or not TEMPLATE.exists(),
    reason="generation pipeline needs the fixture + soffice",
)


@needs_render
def test_three_strategies_give_distinct_decks(wait_generation):
    project = client.post(
        "/api/v1/projects", json={"name": "variants", "target_slide_count": 12}
    ).json()
    tpl = client.post(
        f"/api/v1/projects/{project['id']}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    )
    assert tpl.status_code == 201, tpl.text
    pack = client.post(
        f"/api/v1/projects/{project['id']}/content-packs",
        files={"files": (CONTENT.name, CONTENT.read_bytes(), "text/markdown")},
    )
    assert pack.status_code == 202, pack.text

    accepted = client.post(
        f"/api/v1/projects/{project['id']}/generations",
        json={
            "template_id": tpl.json()["id"],
            "content_pack_id": pack.json()["content_pack"]["id"],
            "brief": BRIEF,
            "variants": [
                {"strategy": "faithful"},
                {"strategy": "balanced"},
                {"strategy": "visual"},
            ],
        },
    )
    assert accepted.status_code == 202, accepted.text

    run = wait_generation(client, accepted.json()["generation_id"])
    assert run["state"] == "completed"
    assert len(run["variants"]) == 3
    strategies = [v["strategy"] for v in run["variants"]]
    assert strategies == ["faithful", "balanced", "visual"]

    # разные планы → разные deck_plan_id; run-level deck_plan — первого варианта
    plan_ids = [v["deck_plan_id"] for v in run["variants"]]
    assert len(set(plan_ids)) == 3
    assert run["deck_plan_id"] == plan_ids[0]

    # главное: pptx-артефакты побитово разные — не одна колода × 3 имени
    deck_hashes = set()
    for variant in run["variants"]:
        blob = client.get(
            f"/api/v1/artifacts/{variant['deck_artifact_id']}/download"
        ).content
        deck_hashes.add(hashlib.sha256(blob).hexdigest())
    assert len(deck_hashes) == 3
