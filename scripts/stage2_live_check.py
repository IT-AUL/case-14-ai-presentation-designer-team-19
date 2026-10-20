"""Live checks for ADR-033/034 with a configured provider (explicit owner authorization).

--mode interpret: Qwen intent + interpretations, numeric rejection report.
--mode search: real SearXNG-backed /plans; model-only fallback does not pass.
--mode timings: understanding, questions, plan, previews, generation and rebuild over REST.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


async def interpretation(content: Path) -> dict:
    from deckdna.analysis.interpret import classify_intent, interpret_pack
    from deckdna.contracts.deck_plan import Brief
    from deckdna.evaluation.measures import usage_snapshot
    from deckdna.ingestion.content_parsers import parse_file
    from deckdna.providers.factory import build_gateway
    from deckdna.settings import settings

    if settings.mock_provider:
        raise RuntimeError("Set DECKDNA_MOCK_PROVIDER=false and configure VK Qwen 3.8 27B")
    gateway = build_gateway()
    try:
        pack = parse_file(content)
        # An intentionally ambiguous purpose exercises the model intent classifier.
        pack = pack.model_copy(update={"datasets": [
            d.model_copy(update={"context_text": None}) for d in pack.datasets
        ]})
        pack, intent = await classify_intent(pack, "Результаты работы компании", gateway)
        brief = Brief(purpose="Результаты работы компании", audience="руководство",
                      language="ru", target_slide_count=7)
        pack, report = await interpret_pack(pack, brief, gateway)
        # намерение на размытой цели честно может быть «none» — это не провал;
        # провал — нет интерпретаций или модель выдумала числа
        return {"ok": report.interpreted > 0 and report.rejected == 0,
                "mode": "interpret", "model": settings.model_text, "intent": intent,
                "report": report.to_dict(), "usage": usage_snapshot(gateway),
                "insights": [i.model_dump(mode="json") for i in pack.insights]}
    finally:
        await gateway.aclose()


def rest_probe(args) -> dict:
    with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=args.timeout) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        def wait(job_id):
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                job = request("GET", f"/jobs/{job_id}")
                if job["state"] in {"failed", "canceled"}:
                    raise RuntimeError(f"job {job_id}: {job.get('error') or job['state']}")
                if job["state"] == "completed":
                    return job
                time.sleep(0.2)
            raise TimeoutError(f"job {job_id} timed out")

        cap = request("GET", "/capabilities")
        if not cap["features"]["model_auto"]:
            raise RuntimeError(
                "Configure a real server provider; a mock timing is not a live check"
            )
        pid = request("POST", "/projects", json={"name": f"stage2-live-{args.mode}"})["id"]
        files = [("files", (args.template.name, args.template.read_bytes(),
                              "application/octet-stream"))]
        if args.mode == "search":
            files.append(("files", ("idea.md", b"# DeckDNA\n\nCorporate presentation assistant.",
                                      "text/markdown")))
        else:
            files.append(("files", (args.content.name, args.content.read_bytes(),
                                      "application/octet-stream")))
        intake = request("POST", f"/projects/{pid}/intake", files=files)
        if intake.get("job_id"):
            wait(intake["job_id"])
        brief = {"purpose": "Рассказать о сервисе презентаций" if args.mode == "search"
                 else "Итоги продаж", "audience": "руководство", "language": "ru",
                 "target_slide_count": 8, "content_fill": "expand" if args.mode == "search"
                 else "shrink"}
        understanding = None
        questions = []
        if args.mode == "timings":
            understanding = request(
                "GET", f"/content-packs/{intake['content_pack_id']}/understanding",
                params={"use_llm": True},
            )
            if not understanding.get("blocks"):
                raise RuntimeError("understanding returned no content blocks")
            questions = request("GET", f"/projects/{pid}/questions")["questions"]
            # Проверяем сохранение ответов; рекомендации-следствия не подтверждаем.
            answers = {
                q["id"]: next(o["value"] for o in q.get("options", []) if o.get("recommended"))
                for q in questions
                if q.get("kind") != "call_to_action"
                and any(o.get("recommended") for o in q.get("options", []))
            }
            answered = request("POST", f"/projects/{pid}/answers", json={"answers": answers})
            brief = {**brief, **{k: v for k, v in answered["brief"].items() if v}}
        planned = request("POST", f"/projects/{pid}/plans", json={
            "content_pack_id": intake["content_pack_id"], "template_id": intake["template_id"],
            "brief": brief, "strategies": ["balanced"], "use_llm": True, "respond_async": True,
        })
        wait(planned["job_id"])
        plan = request("GET", f"/plans/{planned['plan_id']}")
        if args.mode == "search":
            research = plan.get("research") or {}
            return {"ok": bool(research.get("sources")), "mode": "search", "project_id": pid,
                    "plan_id": plan["plan_id"], "research": research,
                    "note": "No sources means FAIL, even if model fallback built a plan"}
        started = time.monotonic()
        preview = request("POST", f"/plans/{plan['plan_id']}/style-previews", json={})
        wait(preview["job_id"])
        preview_s = time.monotonic() - started
        record = request("GET", f"/style-previews/{preview['preview_id']}")
        errors = [s["error"] for s in record["styles"] if s.get("error")]
        if errors:
            raise RuntimeError(f"style previews failed: {errors}")
        started = time.monotonic()
        generated = request("POST", f"/projects/{pid}/generations", json={
            "template_id": intake["template_id"], "content_pack_id": plan["content_pack_id"],
            "deck_plan": plan["plans"][0]["deck_plan"], "brief": brief, "use_llm": True,
            "preview_id": preview["preview_id"], "variants": [{"strategy": record["recommended"]}],
        })
        wait(generated["job_id"])
        generation_s = time.monotonic() - started
        vid = generated["variant_ids"][0]
        slides = request("GET", f"/variants/{vid}/slides", params={"limit": 200})["items"]
        slide = next((s for s in slides if s["index"] == 3), slides[len(slides) // 2])
        started = time.monotonic()
        regenerated = request("POST", f"/variants/{vid}/slides/{slide['id']}/regenerate",
                              json={"action": "other_layout"})
        rebuild_s = time.monotonic() - started
        exported = request("POST", f"/variants/{vid}/exports",
                           json={"formats": ["quality_passport"]})
        exported = request("GET", f"/exports/{exported['export_id']}")
        artifact = next(a for a in exported["artifacts"] if a["format"] == "quality_passport")
        # download_url already contains /api/v1; the client base URL does too.
        passport = request("GET", artifact["download_url"].removeprefix("/api/v1"))
        usage = (passport.get("metrics") or {}).get("usage") or {}
        result = {"ok": bool(usage.get("model_calls")), "mode": "timings", "project_id": pid,
                "dataset": args.content.name, "usage": usage,
                "model_profiles": passport.get("model_profiles"),
                "fallbacks": passport.get("fallbacks"),
                "generation_id": generated["generation_id"], "variant_id": vid,
                "preview_seconds_three_styles": round(preview_s, 3),
                "generation_seconds_one_style": round(generation_s, 3),
                "regenerate_seconds_one_slide": round(rebuild_s, 3),
                "preview_faster": preview_s < generation_s,
                "deck_revision": regenerated["deck_revision"]}
        result.update({
            "understanding_blocks": len(understanding["blocks"]),
            "understanding_datasets": len(understanding.get("datasets", [])),
            "questions": [q["id"] for q in questions],
            "plan_id": plan["plan_id"], "preview_id": preview["preview_id"],
        })
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("interpret", "search", "timings"), required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--template", type=Path,
                        default=ROOT / "tests/fixtures/pptx/vk_tech_template.pptx")
    parser.add_argument("--content", type=Path, default=ROOT / "examples/stage2/sales_monthly.csv")
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    try:
        result = (asyncio.run(interpretation(args.content)) if args.mode == "interpret"
                  else rest_probe(args))
    except Exception as exc:  # noqa: BLE001 — live failures must be recorded too
        result = {"ok": False, "mode": args.mode, "error": f"{type(exc).__name__}: {exc}"}
    result["seconds"] = round(time.monotonic() - started, 3)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
