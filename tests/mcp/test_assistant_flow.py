"""ADR-034 §9: the assistant chain through the real MCP and REST layers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from deckdna.api import app as api
from deckdna.mcp.client import DeckDNAClient
from deckdna.mcp.server import build_server
from deckdna.providers.mock import MockProvider
from mcp import Client

ROOT = Path(__file__).resolve().parents[2]


def _data(result):
    assert not result.is_error, result.content
    if result.structured_content is not None:
        data = result.structured_content
        return data.get("result", data)
    return json.loads(result.content[0].text)


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(api, "STORE", api._Store())
    monkeypatch.setattr(api.settings, "state_snapshot", False)
    monkeypatch.setattr(api, "build_gateway", lambda *args, **kwargs: MockProvider())


async def test_mcp_understand_questions_plan_preview_generate_and_regenerate(isolated):
    rest = DeckDNAClient()
    server = build_server(rest)
    try:
        async with Client(server) as client:
            async def call(name, body):
                return _data(await client.call_tool(name, body))

            tpl = await call("analyze_template", {
                "file_path": str(ROOT / "tests/fixtures/pptx/vk_tech_template.pptx"),
            })
            pid = tpl["project_id"]
            pack = await call("upload_content", {
                "project_id": pid, "file_path": str(ROOT / "examples/stage2/sales_monthly.csv"),
            })
            cp = pack["content_pack_id"]
            understood = await call("understand_content", {"content_pack_id": cp})
            assert understood["datasets"] and understood["blocks"]
            questions = await call("get_questions", {"project_id": pid})
            assert questions["questions"]
            answers = {
                q["id"]: next(o["value"] for o in q["options"] if o.get("recommended"))
                for q in questions["questions"]
            }
            answered = await call("answer_questions", {"project_id": pid, "answers": answers})
            brief = answered["brief"]
            insights = understood["datasets"][0]["insights"]
            insight_id = insights[0]["id"]
            confirmed = await call("confirm_insights", {"content_pack_id": cp,
                                                        "insight_ids": [insight_id]})
            assert confirmed["insights"][0]["confirmed"]
            bad = await client.call_tool("confirm_insights", {
                "content_pack_id": cp, "insight_ids": [insight_id, "missing"], "confirmed": False,
            })
            assert bad.is_error
            current = next(i for i in api.STORE.content_packs[cp].insights if i.id == insight_id)
            assert current.confirmed
            plan = await call("plan_deck", {
                "project_id": pid, "content_pack_id": cp, "template_id": tpl["template_id"],
                "purpose": brief["purpose"], "audience": brief["audience"],
                "target_slide_count": brief["target_slide_count"], "content_fill": "shrink",
                "key_message": brief.get("key_message"),
                "call_to_action": brief.get("call_to_action"),
            })
            preview = await call("style_previews", {"plan_id": plan["plan_id"]})
            for _ in range(600):
                job = await call("get_job", {"job_id": preview["job_id"]})
                if job["state"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.1)
            assert job["state"] == "completed", job
            assert len(job["style_preview"]["styles"]) == 3
            strategy = job["style_preview"]["recommended"]
            generated = await call("generate_variants", {
                "project_id": pid, "content_pack_id": plan["content_pack_id"],
                "template_id": tpl["template_id"], "purpose": brief["purpose"],
                "content_fill": "shrink", "strategies": [strategy],
                "plan_id": plan["plan_id"], "preview_id": preview["preview_id"],
                "key_message": brief.get("key_message"),
                "call_to_action": brief.get("call_to_action"),
            })
            for _ in range(600):
                job = await call("get_job", {"job_id": generated["job_id"]})
                if job["state"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.1)
            assert job["state"] == "completed", job
            variant = job["generation"]["variants"][0]
            run = api.STORE.runs[generated["generation_id"]]
            assert run.request.preview_id == preview["preview_id"]
            slide = next(s for s in plan["plans"][0]["slides"] if s["purpose"] == "data")
            rebuilt = await call("regenerate_slide", {
                "variant_id": variant["variant_id"], "slide_id": slide["slide_id"],
                "action": "other_chart",
            })
            assert rebuilt["deck_revision"] == 2 and rebuilt["preview_artifact_id"]
    finally:
        await rest.aclose()
