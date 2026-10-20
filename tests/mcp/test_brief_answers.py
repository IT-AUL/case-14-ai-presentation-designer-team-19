"""Canonical preferences and typed validation survive the actual MCP adapter."""

import json

from deckdna.api import app as api
from deckdna.mcp.client import DeckDNAClient
from deckdna.mcp.server import build_server
from mcp import Client


async def test_mcp_canonical_answers_and_conflicts(monkeypatch):
    monkeypatch.setattr(api, "STORE", api._Store())
    monkeypatch.setattr(api.settings, "state_snapshot", False)
    project = await api.create_project(api.ProjectCreate(name="MCP brief"))
    rest = DeckDNAClient()
    try:
        async with Client(build_server(rest)) as client:
            result = await client.call_tool("answer_questions", {
                "project_id": project.id, "answers": {
                    "purpose": "Review", "language": "en", "target_slide_count": 6,
                    "content_fill": "shrink", "audience": "team",
                },
            })
            assert not result.is_error
            body = result.structured_content or json.loads(result.content[0].text)
            body = body.get("result", body)
            assert body["brief"]["target_slide_count"] == 6
            assert body["brief"]["content_fill"] == "shrink"
            assert body["brief"]["language"] == "en"
            uploaded = await client.call_tool("upload_content", {
                "project_id": project.id, "text": "A designer helps the team prepare presentations",
            })
            assert not uploaded.is_error
            cp = uploaded.structured_content or json.loads(uploaded.content[0].text)
            cp = cp.get("result", cp)["content_pack_id"]
            planned = await client.call_tool("plan_deck", {
                "project_id": project.id, "content_pack_id": cp,
            })
            assert not planned.is_error, planned.content
            plan = planned.structured_content or json.loads(planned.content[0].text)
            plan = plan.get("result", plan)
            assert plan["sufficiency"]["before"]["requested"] == 6
            saved = api.STORE.project_briefs[project.id]
            assert saved["purpose"] == "Review" and saved["audience"] == "team"
            assert saved["content_fill"] == "shrink" and saved["language"] == "en"
            bad = await client.call_tool("answer_questions", {
                "project_id": project.id, "answers": {"length": 7, "target_slide_count": 6},
            })
            assert bad.is_error
            assert api.STORE.project_briefs[project.id]["target_slide_count"] == 6
            stale = await client.call_tool("answer_questions", {
                "project_id": project.id, "answers": {"target_slide_count": 7},
                "expected_context_revision": 1,
            })
            assert stale.is_error
            assert api.STORE.project_briefs[project.id]["target_slide_count"] == 6
            capped = await client.call_tool("plan_deck", {
                "project_id": project.id, "content_pack_id": cp,
                "target_slide_count": 4, "slide_count_policy": "maximum",
            })
            assert not capped.is_error, capped.content
            b = api.STORE.project_briefs[project.id]
            assert b["slide_count_policy"] == "maximum"
            assert b["requested_slide_count"] == b["target_slide_count"] == 4
    finally:
        await rest.aclose()
