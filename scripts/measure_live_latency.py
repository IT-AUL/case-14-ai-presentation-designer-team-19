"""Замер времени генерации на реальном провайдере из .env (ADR-031 §9).

ТЗ: ≤5 минут на колоду; на финале обязателен VK Inference (Qwen 3.8 27B).
Агенты живые прогоны не запускают — команду запускает владелец, когда
доступ к VK Inference выдан:

    DECKDNA_MOCK_PROVIDER=false uv run python scripts/measure_live_latency.py

Параметры провайдера берутся из ``.env`` (``DECKDNA_PROVIDER_BASE_URL``,
``DECKDNA_PROVIDER_API_KEY``, ``DECKDNA_MODEL_TEXT``, ``DECKDNA_MODEL_VISION``,
``DECKDNA_PROVIDER_EXTRA_BODY`` — например отключение рассуждения Qwen).
Прогон: три организаторских шаблона × три варианта (faithful/balanced/
visual) на канонической фикстуре контента. Для каждой колоды печатается
``stage_timings`` и вердикт по бюджету; в конце — сводка и путь к JSON с
полными цифрами (``artifacts/latency/<время>/summary.json``).

Опции: ``--templates`` / ``--strategies`` / ``--content`` / ``--slides`` /
``--purpose`` / ``--audience`` / ``--key-message`` /
``--budget-s`` (по умолчанию 300 с на колоду).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from deckdna.generation.pipeline import generate  # noqa: E402
from deckdna.providers.factory import build_gateway  # noqa: E402
from deckdna.settings import settings  # noqa: E402

TEMPLATES = ["vk_tech_template.pptx", "vk_education.pptx", "vk_workspace.pptx"]
STRATEGIES = ["faithful", "balanced", "visual"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--templates", nargs="+", default=TEMPLATES)
    parser.add_argument("--strategies", nargs="+", default=STRATEGIES)
    parser.add_argument(
        "--content", default=str(ROOT / "tests/fixtures/content/deckdna_pitch_rich.md")
    )
    parser.add_argument("--slides", type=int, default=10)
    parser.add_argument("--purpose", default="", help="Реальная цель автора, если задана")
    parser.add_argument("--audience", default="", help="Аудитория, явно заданная автором")
    parser.add_argument("--key-message", default=None)
    parser.add_argument("--budget-s", type=float, default=300.0)
    args = parser.parse_args()

    if settings.mock_provider:
        print(
            "DECKDNA_MOCK_PROVIDER=true: замер на моках бессмысленен. Запустите с "
            "DECKDNA_MOCK_PROVIDER=false и настроенным провайдером в .env.",
            file=sys.stderr,
        )
        return 2
    print(f"provider: {settings.provider_base_url}  text: {settings.model_text}  "
          f"vision: {settings.model_vision or '— (контекстный аудит будет пропущен)'}")

    out_root = ROOT / "artifacts" / "latency" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows = []
    for template in args.templates:
        template_path = ROOT / "tests/fixtures/pptx" / template
        for strategy in args.strategies:
            out_dir = out_root / f"{Path(template).stem}-{strategy}"
            started = time.monotonic()
            error = None
            report: dict = {}
            try:
                report = generate(
                    template_path,
                    args.content,
                    {"purpose": args.purpose, "audience": args.audience,
                     "key_message": args.key_message,
                     "language": "ru", "target_slide_count": args.slides},
                    out_dir,
                    gateway=build_gateway(),
                    strategy=strategy,
                )
            except Exception as exc:  # noqa: BLE001 — замер фиксирует и отказы
                error = f"{type(exc).__name__}: {exc}"
            elapsed = time.monotonic() - started
            verdict = "FAIL" if error else ("OK" if elapsed <= args.budget_s else "OVER")
            rows.append({
                "template": template, "strategy": strategy, "seconds": round(elapsed, 1),
                "verdict": verdict, "error": error,
                "stage_timings": report.get("stage_timings"),
                "planner": report.get("planner"),
                "contextual_audit": report.get("contextual_audit"),
                "usage": report.get("usage"),
            })
            print(f"\n== {template} / {strategy}: {elapsed:.1f} s — {verdict}")
            if error:
                print(f"   ошибка: {error}")
            for item in report.get("stage_timings") or []:
                print(f"   {item.get('stage', '?'):<14} {item.get('seconds', 0):>7.1f} s")

    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    worst = max((r["seconds"] for r in rows), default=0.0)
    failed = [r for r in rows if r["verdict"] != "OK"]
    print(f"\nколод: {len(rows)}, худшее время: {worst:.1f} s, бюджет {args.budget_s:.0f} s "
          f"на колоду, не уложились/упали: {len(failed)}")
    print(f"подробно: {out_root / 'summary.json'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
