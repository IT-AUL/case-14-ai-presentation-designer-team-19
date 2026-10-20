"""ADR-031 §4: текстовая модель без зрения не роняет генерацию.

``OpenAICompatibleProvider`` с пустым ``DECKDNA_MODEL_VISION`` отвечает на
``vision_json`` ошибкой ``provider_capability_missing``. Раньше контекстный
аудит пробрасывал её, и не собиралась ни одна колода. Теперь стадия
пропускается с раскрытием: ``contextual_audit.ran=false`` с причиной и
``fallbacks[feature=audit.contextual]`` в паспорте.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from deckdna.api import app as app_module
from deckdna.api.app import app
from deckdna.errors import DeckDNAError
from deckdna.generation.pipeline import generate
from deckdna.providers.mock import MockProvider
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "pptx" / "vk_tech_template.pptx"
CONTENT = FIXTURES / "content" / "poc_article.md"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
BRIEF = {"purpose": "Представить решение", "audience": "жюри", "language": "ru",
         "target_slide_count": 6}

pytestmark = pytest.mark.skipif(shutil.which("soffice") is None, reason="needs soffice")


class TextOnlyProvider(MockProvider):
    """Как OpenAICompatibleProvider без vision-модели."""

    async def vision_json(self, prompt_name, images, payload, schema):  # noqa: ANN001
        self.calls.append({"method": "vision_json", "prompt": prompt_name})
        raise DeckDNAError(
            code="provider_capability_missing",
            message="provider has no vision model configured",
            stage="providers",
        )


def _assert_disclosed(passport: dict, contextual: dict) -> None:
    assert contextual["ran"] is False
    assert contextual["reason"].startswith("provider_capability_missing")
    fallbacks = [f for f in passport.get("fallbacks") or [] if f["feature"] == "audit.contextual"]
    assert fallbacks and fallbacks[0]["strategy"] == "skipped"


def test_text_only_model_still_builds_deck(tmp_path):
    gateway = TextOnlyProvider()
    report = generate(TEMPLATE, CONTENT, BRIEF, tmp_path, gateway=gateway)
    assert Path(report["artifacts"]["pptx"]).exists()
    assert Path(report["artifacts"]["pdf"]).exists()
    # зрение действительно запрашивалось — пропуск не «тихий выключатель»
    assert any(c["method"] == "vision_json" for c in gateway.calls)
    _assert_disclosed(report["quality_passport"], report["contextual_audit"])
    on_disk = json.loads(Path(report["artifacts"]["quality_passport"]).read_text("utf-8"))
    assert any(f["feature"] == "audit.contextual" for f in on_disk["fallbacks"])


def test_text_only_model_api_generation_completes(monkeypatch, wait_generation):
    gateway = TextOnlyProvider()
    monkeypatch.setattr(app_module, "build_gateway", lambda config=None: gateway)
    client = TestClient(app)
    pid = client.post("/api/v1/projects", json={"name": "no vision"}).json()["id"]
    tpl = client.post(
        f"/api/v1/projects/{pid}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    )
    pack = client.post(
        f"/api/v1/projects/{pid}/content-packs",
        files={"files": (CONTENT.name, CONTENT.read_bytes(), "text/markdown")},
    )
    gen = client.post(
        f"/api/v1/projects/{pid}/generations",
        json={
            "template_id": tpl.json()["id"],
            "content_pack_id": pack.json()["content_pack"]["id"],
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
            "use_llm": True,
        },
    )
    assert gen.status_code == 202, gen.text
    run = wait_generation(client, gen.json()["generation_id"])
    assert run["state"] == "completed", run
    variant = run["variants"][0]
    # контекстный аудит не выполнялся — честный None, а не 0 issues
    assert variant["contextual_issues"] is None
    assert any(c["method"] == "vision_json" for c in gateway.calls)
