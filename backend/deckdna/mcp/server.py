"""MCP-сервер навыка DeckDNA (ADR-032 §1).

Ответ на вопрос «как встроить в VK AI Space»: любой MCP-совместимый агент
(конструктор агентов VK AI Space, IDE-агенты) подключает
DeckDNA как набор инструментов — без своего UI и без знания REST API.

Инструменты — тонкий слой над REST API (``DeckDNAClient``): каждый вызов —
это один-два запроса к уже существующим эндпоинтам, вход и выход —
контрактные модели API. Ошибки — ``ToolError`` с кодом ``DeckDNAError``
(``invalid_input: …``, ``not_found: …``), агент видит причину и может
исправить вызов.

Типичная цепочка агента: ``analyze_template`` → ``upload_content`` (одна
фраза, текст или файл) → ``plan_deck`` (оценка достаточности, план с
происхождением фактов) → ``research_slide`` (цифры для графика) →
``generate_variants`` (асинхронно, job id) → ``get_job`` до завершения →
``audit_deck`` / ``repair_issues`` → ``export_deck``. Ресурсы: Design DNA
шаблона, Quality Passport варианта, скачивание PPTX/PDF.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError

from deckdna.mcp.client import DeckDNAClient, DeckDNAToolError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MANIFEST = _REPO_ROOT / "skill" / "manifest.yaml"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

Strategy = Literal["faithful", "balanced", "visual"]
ContentFill = Literal["expand", "shrink", "ask"]

INSTRUCTIONS = (
    "DeckDNA turns any corporate PPTX/POTX template plus content (one sentence, text "
    "or files) into three editable presentation variants with a quality audit. "
    "Order: analyze_template -> upload_content -> understand_content -> get_questions "
    "-> answer_questions (ask the user) -> confirm_insights (user approval only) "
    "-> plan_deck -> style_previews (optional research_slide) "
    "-> generate_variants -> get_job until state is completed -> export_deck. "
    "Files: pass a local path (file_path) or base64 bytes (file_base64 + filename)."
)


def skill_version() -> str:
    try:
        return str(yaml.safe_load(_MANIFEST.read_text(encoding="utf-8")).get("version", "0"))
    except (OSError, yaml.YAMLError, AttributeError):
        return "0"


def _file_bytes(
    file_path: str | None, file_base64: str | None, filename: str | None, default_name: str
) -> tuple[str, bytes]:
    if file_path:
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise ToolError(f"invalid_input: file not found: {file_path}")
        return path.name, path.read_bytes()
    if file_base64:
        try:
            return filename or default_name, base64.b64decode(file_base64, validate=True)
        except ValueError as exc:
            raise ToolError("invalid_input: file_base64 is not valid base64") from exc
    raise ToolError("invalid_input: pass file_path or file_base64")


def _brief(
    purpose: str | None, audience: str | None, language: str | None,
    target_slide_count: int | None, content_fill: ContentFill | None, tone: str | None,
) -> dict[str, Any]:
    return {k: v for k, v in {
        "purpose": purpose, "audience": audience, "language": language,
        "target_slide_count": target_slide_count, "content_fill": content_fill, "tone": tone,
    }.items() if v is not None}


def _plan_summary(deck_plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "slide_id": s["id"],
            "index": s["index"],
            "purpose": s["purpose"],
            "title": s["title_intent"],
            "desired_visual": s.get("desired_visual"),
            "units": [
                {"kind": u["kind"], "text": u.get("text"),
                 "origin": u.get("origin") or "user"}
                for u in s.get("content_units", []) if u.get("kind") != "title"
            ],
        }
        for s in deck_plan.get("slides", [])
    ]


def build_server(client: DeckDNAClient | None = None, api_url: str | None = None) -> MCPServer:
    """MCP-сервер поверх API (в процессе или по ``api_url``)."""
    api = client or DeckDNAClient(api_url)
    server = MCPServer(
        name="deckdna",
        title="DeckDNA — цифровой дизайнер презентаций",
        instructions=INSTRUCTIONS,
        version=skill_version(),
    )

    async def call(coro):
        try:
            return await coro
        except DeckDNAToolError as exc:
            raise ToolError(f"{exc.code}: {exc.message}") from exc

    async def resolved_brief(project_id: str, overrides: dict[str, Any]) -> dict[str, Any]:
        from deckdna.assistant.brief import BRIEF_FIELDS

        # Both reads are pure: no understanding/inference or generated work.
        session = await call(api.get(f"/projects/{project_id}/session"))
        policy = await call(api.get(f"/projects/{project_id}/questions"))
        values = {**policy["suggested_brief"], **session["brief"], **overrides}
        if "target_slide_count" in overrides:
            values.pop("requested_slide_count", None)
        return {k: v for k, v in values.items() if k in BRIEF_FIELDS}

    @server.tool()
    async def analyze_template(
        file_path: str | None = None,
        file_base64: str | None = None,
        filename: str | None = None,
        project_id: str | None = None,
        project_name: str = "MCP",
    ) -> dict[str, Any]:
        """Upload a PPTX/POTX template and build its Design DNA (layouts, palette,
        fonts, slide roles). Creates a project unless project_id is given."""
        name, data = _file_bytes(file_path, file_base64, filename, "template.pptx")
        if project_id is None:
            project = await call(api.post("/projects", {"name": project_name}))
            project_id = project["id"]
        template = await call(api.request(
            "POST", f"/projects/{project_id}/templates",
            files={"file": (name, data, _PPTX)},
        ))
        analysis = await call(api.post(f"/templates/{template['id']}/analyze", {}))
        dna = await call(api.get(f"/templates/{template['id']}/design-dna"))
        roles: dict[str, int] = {}
        for role in dna.get("slide_roles") or dna.get("roles") or []:
            key = role.get("role") if isinstance(role, dict) else str(role)
            roles[key] = roles.get(key, 0) + 1
        return {
            "project_id": project_id,
            "template_id": template["id"],
            "analysis_id": analysis.get("analysis_id"),
            "package_type": template.get("package_type"),
            "exemplar_slides": len(dna.get("exemplars") or []),
            "layouts": len(dna.get("layouts") or []),
            "slide_roles": roles,
            "design_dna_uri": f"deckdna://templates/{template['id']}/design-dna",
        }

    @server.tool()
    async def upload_content(
        project_id: str,
        text: str | None = None,
        file_path: str | None = None,
        file_base64: str | None = None,
        filename: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Upload presentation content: a one-sentence brief or any text (`text`), or a
        file (.md/.txt/.docx/.pdf/.csv/.xlsx/.json/.pptx — an old deck to re-layout)."""
        if text is not None:
            name, data = "content.md", text.encode("utf-8")
        else:
            name, data = _file_bytes(file_path, file_base64, filename, "content.md")
        brief = {"language": language} if language else {}
        accepted = await call(api.request(
            "POST", f"/projects/{project_id}/content-packs",
            files=[("files", (name, data, "application/octet-stream"))],
            data={"brief": json.dumps(brief)} if brief else None,
        ))
        pack = accepted["content_pack"]
        return {
            "content_pack_id": pack["id"],
            "language": pack.get("language"),
            "title_hint": pack.get("title_hint"),
            "sections": len(pack.get("sections") or []),
            "tables": len(pack.get("tables") or []),
            "charts": len(pack.get("charts") or []),
            "diagrams": len(pack.get("diagrams") or []),
            "images": len(pack.get("assets") or []),
        }

    @server.tool()
    async def understand_content(
        content_pack_id: str, use_llm: bool | None = None,
        provider_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Read semantic blocks, capacity and data insights. Only explicit model mode
        adds interpretations; implications remain unconfirmed until the user agrees."""
        return await call(api.get(
            f"/content-packs/{content_pack_id}/understanding", use_llm=use_llm,
            provider_session_id=provider_session_id,
        ))

    @server.tool()
    async def get_questions(project_id: str) -> dict[str, Any]:
        """Questions with reasons and recommended choices. Ask the user before
        answer_questions; an empty list means that the question step can be skipped."""
        return await call(api.get(f"/projects/{project_id}/questions"))

    @server.tool()
    async def answer_questions(
        project_id: str, answers: dict[str, Any], expected_context_revision: int | None = None,
    ) -> dict[str, Any]:
        """Save the user's choices; returns the complete brief for plan_deck.
        Answers map question IDs to option values or custom text."""
        body = {"answers": answers}
        if expected_context_revision is not None:
            body["expected_context_revision"] = expected_context_revision
        return await call(api.post(f"/projects/{project_id}/answers", body))

    @server.tool()
    async def confirm_insights(
        content_pack_id: str, insight_ids: list[str], confirmed: bool = True,
    ) -> dict[str, Any]:
        """Confirm ONLY the insights explicitly accepted by the user (or unconfirm
        them). Never approve an implication/recommendation without asking the user."""
        # Validate the whole batch before applying it, so a typo cannot leave a
        # partially confirmed batch. Every mutation still uses the REST endpoint.
        pack = await call(api.get(f"/content-packs/{content_pack_id}"))
        known = {i["id"] for i in pack.get("insights", [])}
        missing = set(insight_ids) - known
        if missing:
            raise ToolError(f"not_found: insights not in content pack: {sorted(missing)}")
        return {"insights": [
            await call(api.request(
                "PATCH", f"/content-packs/{content_pack_id}/insights/{insight_id}",
                json_body={"confirmed": confirmed},
            )) for insight_id in dict.fromkeys(insight_ids)
        ]}

    @server.tool()
    async def style_previews(
        plan_id: str, slide_ids: list[str] | None = None,
        template_id: str | None = None, respond_async: bool = True,
    ) -> dict[str, Any]:
        """Render key slides in all three styles. With respond_async=true, poll
        get_job and then this tool's preview_id via get_job's style_preview result."""
        return await call(api.post(f"/plans/{plan_id}/style-previews", {
            "slide_ids": slide_ids, "template_id": template_id,
            "respond_async": respond_async,
        }))

    @server.tool()
    async def regenerate_slide(
        variant_id: str, slide_id: str,
        action: str = "other_layout", chart_type: str | None = None,
    ) -> dict[str, Any]:
        """Rebuild one slide (other_layout, shorter, other_chart, style:<strategy>)
        with audit and preview. Other slide XML bytes are preserved."""
        return await call(api.post(f"/variants/{variant_id}/slides/{slide_id}/regenerate", {
            "action": action, "chart_type": chart_type,
        }))

    @server.tool()
    async def plan_deck(
        project_id: str,
        content_pack_id: str,
        purpose: str | None = None,
        audience: str | None = None,
        language: str | None = None,
        target_slide_count: int | None = None,
        content_fill: ContentFill | None = None,
        template_id: str | None = None,
        strategies: list[Strategy] | None = None,
        tone: str | None = None,
        key_message: str | None = None,
        call_to_action: str | None = None,
        slide_count_policy: Literal["preferred", "exact", "maximum"] | None = None,
    ) -> dict[str, Any]:
        """Estimate whether the content fills the requested slide count and build the
        deck plan (slides, key messages, units with origin user|web|model). With
        content_fill=ask and too little content, returns the estimate and no plans."""
        body: dict[str, Any] = {
            "content_pack_id": content_pack_id,
            "brief": await resolved_brief(project_id, _brief(
                purpose, audience, language, target_slide_count, content_fill, tone)),
            "strategies": strategies or ["balanced"],
        }
        body["brief"].update({k: v for k, v in {
            "key_message": key_message, "call_to_action": call_to_action,
            "slide_count_policy": slide_count_policy,
        }.items() if v is not None})
        if template_id:
            body["template_id"] = template_id
        plan_set = await call(api.post(f"/projects/{project_id}/plans", body))
        fill = plan_set.get("content_fill") or {}
        return {
            "plan_id": plan_set["plan_id"],
            "content_pack_id": plan_set["content_pack_id"],
            "sufficiency": {"before": fill.get("before"), "after": fill.get("after"),
                            "resolved": fill.get("resolved"),
                            "needs_decision": fill.get("needs_decision", False)},
            "plans": [
                {"strategy": p["strategy"], "slides": _plan_summary(p["deck_plan"])}
                for p in plan_set.get("plans", [])
            ],
        }

    @server.tool()
    async def research_slide(
        plan_id: str,
        slide_id: str,
        focus: Literal["facts", "numbers"] = "facts",
        strategy: Strategy | None = None,
    ) -> dict[str, Any]:
        """Find facts (or figures for a chart: focus=numbers) for one plan slide on the
        web, each backed by a verbatim quote. Homogeneous figures become a native chart,
        mixed ones a table."""
        body: dict[str, Any] = {"focus": focus}
        if strategy:
            body["strategy"] = strategy
        result = await call(api.post(f"/plans/{plan_id}/slides/{slide_id}/research", body))
        slide = result["slide"]
        return {
            "added_units": result["added_units"],
            "content_pack_id": result["plan_set"]["content_pack_id"],
            "research": result["research"],
            "slide": _plan_summary({"slides": [slide]})[0],
        }

    @server.tool()
    async def generate_variants(
        project_id: str,
        template_id: str,
        content_pack_id: str,
        purpose: str | None = None,
        audience: str | None = None,
        language: str | None = None,
        target_slide_count: int | None = None,
        content_fill: ContentFill | None = None,
        strategies: list[Strategy] | None = None,
        plan_id: str | None = None,
        preview_id: str | None = None,
        tone: str | None = None,
        key_message: str | None = None,
        call_to_action: str | None = None,
        slide_count_policy: Literal["preferred", "exact", "maximum"] | None = None,
    ) -> dict[str, Any]:
        """Start building the variants (faithful/balanced/visual by default) in the
        template. Asynchronous: returns job_id — poll get_job until completed."""
        body: dict[str, Any] = {
            "template_id": template_id,
            "content_pack_id": content_pack_id,
            "brief": await resolved_brief(project_id, _brief(
                purpose, audience, language, target_slide_count, content_fill, tone)),
            "variants": [{"strategy": s} for s in (strategies or ["faithful", "balanced",
                                                                   "visual"])],
        }
        body["brief"].update({k: v for k, v in {
            "key_message": key_message, "call_to_action": call_to_action,
            "slide_count_policy": slide_count_policy,
        }.items() if v is not None})
        if preview_id:
            body["preview_id"] = preview_id
        if plan_id and preview_id:
            planned = await call(api.get(f"/plans/{plan_id}"))
            preview = await call(api.get(f"/style-previews/{preview_id}"))
            if preview["plan_id"] != plan_id:
                raise ToolError("invalid_input: preview belongs to another plan")
            entry = next((e for e in planned["plans"]
                          if e["deck_plan"]["id"] == preview.get("deck_plan_id")),
                         planned["plans"][0])
            body["deck_plan"] = entry["deck_plan"]
        elif plan_id:
            body["deck_plan_id"] = plan_id
        accepted = await call(api.post(f"/projects/{project_id}/generations", body))
        return {
            "generation_id": accepted["generation_id"],
            "job_id": accepted["job_id"],
            "variant_ids": accepted["variant_ids"],
        }

    @server.tool()
    async def get_job(job_id: str, after: int = 0) -> dict[str, Any]:
        """Job state, stage and progress, journal events after `after`; for a finished
        generation — variants with metrics and resource URIs."""
        job = await call(api.get(f"/jobs/{job_id}"))
        events = await call(api.get(f"/jobs/{job_id}/events", after=after))
        out: dict[str, Any] = {
            "job_id": job["id"], "kind": job["kind"], "state": job["state"],
            "stage": job.get("stage"), "progress": job.get("progress"),
            "error": job.get("error"), "result_ids": job.get("result_ids"),
            "events": events.get("items", events) if isinstance(events, dict) else events,
        }
        preview_id = (job.get("result_ids") or {}).get("preview_id")
        if preview_id and job["state"] == "completed":
            out["style_preview"] = await call(api.get(f"/style-previews/{preview_id}"))
        run_id = (job.get("result_ids") or {}).get("generation_id")
        if run_id:
            run = await call(api.get(f"/generations/{run_id}"))
            out["generation"] = {
                "generation_id": run["id"],
                "state": run["state"],
                "content_fill": run.get("content_fill"),
                "variants": [
                    {
                        "variant_id": v["id"], "strategy": v["strategy"],
                        "status": v["status"], "stage": v.get("stage"),
                        "metrics": v.get("metrics"),
                        "deck_uri": (
                            f"deckdna://artifacts/{v['deck_artifact_id']}"
                            if v.get("deck_artifact_id") else None
                        ),
                        "quality_passport_uri": f"deckdna://variants/{v['id']}/quality-passport",
                    }
                    for v in run.get("variants", [])
                ],
            }
        return out

    @server.tool()
    async def audit_deck(variant_id: str, max_issues: int = 30) -> dict[str, Any]:
        """Quality audit of a variant: deterministic checks (overflow, collisions, font
        floor, contrast, filler slides, …) with repairable flags and fix previews."""
        accepted = await call(api.post(f"/variants/{variant_id}/audits", {}))
        audit = await call(api.get(f"/audits/{accepted['audit_id']}"))
        issues = await call(api.get(f"/audits/{accepted['audit_id']}/issues", limit=500))
        items = issues.get("items", [])
        return {
            "audit_id": accepted["audit_id"],
            "deck_revision": audit.get("deck_revision"),
            "issue_count": audit.get("issue_count"),
            "summary_by_severity": audit.get("summary_by_severity"),
            "contextual_status": audit.get("contextual_status"),
            "issues": [
                {"issue_id": i["id"], "rule_code": i["rule_code"], "severity": i["severity"],
                 "slide_index": i.get("slide_index"), "message": i.get("message"),
                 "repairable": i.get("repairable")}
                for i in items[:max_issues]
            ],
        }

    @server.tool()
    async def repair_issues(audit_id: str, issue_ids: list[str] | None = None) -> dict[str, Any]:
        """Apply typed repairs to the selected issues (default: every repairable one) and
        re-audit; returns the new deck revision and per-issue outcomes."""
        if not issue_ids:
            issues = await call(api.get(f"/audits/{audit_id}/issues", limit=500,
                                        repairable="true"))
            issue_ids = [i["id"] for i in issues.get("items", []) if i.get("repairable")]
        if not issue_ids:
            return {"applied": 0, "note": "no repairable issues"}
        accepted = await call(api.post(f"/audits/{audit_id}/repairs",
                                       {"selected_issue_ids": issue_ids}))
        job = await call(api.get(f"/jobs/{accepted['job_id']}"))
        return {"job_id": accepted["job_id"], "deck_revision": accepted.get("deck_revision"),
                "result": job.get("result")}

    @server.tool()
    async def export_deck(
        variant_id: str,
        formats: list[Literal["pptx", "pdf", "html", "quality_passport"]] | None = None,
    ) -> dict[str, Any]:
        """Export the variant's current revision; returns artifact resource URIs
        (deckdna://artifacts/{id}) with sha256 and size."""
        accepted = await call(api.post(f"/variants/{variant_id}/exports",
                                       {"formats": formats or ["pptx", "pdf",
                                                               "quality_passport"]}))
        record = await call(api.get(f"/exports/{accepted['export_id']}"))
        return {
            "export_id": record["id"],
            "deck_revision": record["deck_revision"],
            "artifacts": [
                {"format": a["format"], "uri": f"deckdna://artifacts/{a['artifact_id']}",
                 "mime_type": a.get("mime_type"), "sha256": a["sha256"],
                 "size_bytes": a["size_bytes"]}
                for a in record.get("artifacts", [])
            ],
        }

    @server.resource(
        "deckdna://templates/{template_id}/design-dna",
        mime_type="application/json",
        description="Design DNA of an analyzed template",
    )
    async def design_dna(template_id: str) -> dict[str, Any]:
        try:
            return await api.get(f"/templates/{template_id}/design-dna")
        except DeckDNAToolError as exc:
            raise ResourceNotFoundError(f"{exc.code}: {exc.message}") from exc

    @server.resource(
        "deckdna://variants/{variant_id}/quality-passport",
        mime_type="application/json",
        description="Quality Passport of a variant's current revision",
    )
    async def quality_passport(variant_id: str) -> str:
        try:
            accepted = await api.post(f"/variants/{variant_id}/exports",
                                      {"formats": ["quality_passport"]})
            record = await api.get(f"/exports/{accepted['export_id']}")
            art = next(a for a in record["artifacts"] if a["format"] == "quality_passport")
            data = await api.get(f"/artifacts/{art['artifact_id']}/download")
        except (DeckDNAToolError, StopIteration, KeyError) as exc:
            raise ResourceNotFoundError(f"quality passport not available: {exc}") from exc
        return data.decode("utf-8") if isinstance(data, bytes) else json.dumps(
            data, ensure_ascii=False
        )

    @server.resource(
        "deckdna://artifacts/{artifact_id}",
        description="Download an artifact (PPTX, PDF, HTML zip, Quality Passport)",
    )
    async def artifact(artifact_id: str) -> bytes:
        try:
            data = await api.get(f"/artifacts/{artifact_id}/download")
        except DeckDNAToolError as exc:
            raise ResourceNotFoundError(f"{exc.code}: {exc.message}") from exc
        if isinstance(data, bytes):
            return data
        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    return server


TOOL_NAMES = (
    "analyze_template", "upload_content", "plan_deck", "research_slide",
    "generate_variants", "get_job", "audit_deck", "repair_issues", "export_deck",
    "understand_content", "get_questions", "answer_questions", "confirm_insights",
    "style_previews", "regenerate_slide",
)

__all__ = ["INSTRUCTIONS", "TOOL_NAMES", "build_server", "skill_version"]
