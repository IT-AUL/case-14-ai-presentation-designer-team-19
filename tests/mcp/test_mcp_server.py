"""ADR-032 §1: навык DeckDNA как MCP-сервер.

In-process MCP-клиент (официальный SDK, без сети) вызывает цепочку агента:
шаблон → контент одной фразой → план → генерация (job) → get_job до
завершения → паспорт качества ресурсом → экспорт. Модели нет (мок не
включает модель сам), всё детерминированно.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
import yaml
from deckdna.mcp.server import TOOL_NAMES, build_server
from mcp import Client

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "tests/fixtures/pptx/vk_tech_template.pptx"

pytestmark = pytest.mark.skipif(shutil.which("soffice") is None, reason="needs soffice")


def _data(result) -> dict:
    assert not result.is_error, result.content
    if result.structured_content is not None:
        data = result.structured_content
        return data.get("result", data) if isinstance(data, dict) else data
    return json.loads(result.content[0].text)


async def _chain() -> dict:
    server = build_server()
    async with Client(server) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert set(TOOL_NAMES) <= tools

        tpl = _data(await client.call_tool("analyze_template", {"file_path": str(TEMPLATE)}))
        pid = tpl["project_id"]
        content = _data(await client.call_tool("upload_content", {
            "project_id": pid,
            "text": "Сервис собирает презентацию в шаблоне компании за пять минут: "
                    "аудит шаблона, план, вёрстка трёх вариантов и проверка качества.",
        }))
        plan = _data(await client.call_tool("plan_deck", {
            "project_id": pid,
            "content_pack_id": content["content_pack_id"],
            "purpose": "Представить сервис",
            "target_slide_count": 5,
            "content_fill": "shrink",
        }))
        assert plan["plans"] and plan["plans"][0]["slides"]
        assert plan["sufficiency"]["before"]["requested"] == 5
        origins = {u["origin"] for s in plan["plans"][0]["slides"] for u in s["units"]}
        assert "user" in origins

        gen = _data(await client.call_tool("generate_variants", {
            "project_id": pid,
            "template_id": tpl["template_id"],
            "content_pack_id": plan["content_pack_id"],
            "purpose": "Представить сервис",
            "target_slide_count": 5,
            "content_fill": "shrink",
            "strategies": ["balanced"],
            "plan_id": plan["plan_id"],
        }))
        job = {}
        for _ in range(600):
            job = _data(await client.call_tool("get_job", {"job_id": gen["job_id"]}))
            if job["state"] in ("completed", "failed", "canceled"):
                break
            await asyncio.sleep(0.2)
        assert job["state"] == "completed", job
        variant = job["generation"]["variants"][0]
        assert variant["status"] == "completed"

        passport = await client.read_resource(variant["quality_passport_uri"])
        passport_json = json.loads(passport.contents[0].text)
        assert passport_json["metrics"]["validity"]["opens_cleanly"] is True

        dna = await client.read_resource(tpl["design_dna_uri"])
        assert json.loads(dna.contents[0].text)

        exported = _data(await client.call_tool("export_deck", {
            "variant_id": variant["variant_id"], "formats": ["pptx"],
        }))
        pptx_uri = exported["artifacts"][0]["uri"]
        blob = await client.read_resource(pptx_uri)
        import base64

        assert base64.b64decode(blob.contents[0].blob)[:2] == b"PK"

        audit = _data(await client.call_tool("audit_deck", {"variant_id": variant["variant_id"]}))
        assert audit["audit_id"] and "issues" in audit

        # типизированная ошибка API доходит до агента с кодом
        bad = await client.call_tool("get_job", {"job_id": "job_missing"})
        assert bad.is_error and "not_found" in bad.content[0].text
        return {"tpl": tpl, "job": job}


def test_mcp_chain_one_sentence_to_passport():
    asyncio.run(_chain())


def test_manifest_lists_the_mcp_tools():
    manifest = yaml.safe_load((ROOT / "skill/manifest.yaml").read_text(encoding="utf-8"))
    mcp = manifest["mcp"]
    assert {t["name"] for t in mcp["tools"]} == set(TOOL_NAMES)
    assert set(mcp["transports"]) == {"stdio", "http"}
