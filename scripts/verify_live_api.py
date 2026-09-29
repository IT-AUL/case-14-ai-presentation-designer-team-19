#!/usr/bin/env python3
"""Isolated real-provider API verification; credentials remain in this process.

Run with ``uv run python scripts/verify_live_api.py --help``. No HTTP server,
Docker service, fixture, or external checkout is modified. Output is local.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import logging
import sys
import time
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from deckdna.api import app as api  # noqa: E402
from deckdna.errors import DeckDNAError  # noqa: E402
from deckdna.providers.factory import build_gateway  # noqa: E402
from deckdna.settings import Settings  # noqa: E402

Color = Literal["red", "blue", "green", "yellow", "white", "black", "unknown"]


class ColorGrid(BaseModel):
    top_left: Color
    top_right: Color
    bottom_left: Color
    bottom_right: Color


async def semantic_vision_probe(config: Settings) -> dict:
    """Answers require image pixels; no expected colors occur in the prompt."""
    image = Image.new("RGB", (384, 384), "white")
    draw = ImageDraw.Draw(image)
    expected = dict(zip(ColorGrid.model_fields, ("yellow", "blue", "red", "green"), strict=True))
    boxes = [(0, 0, 191, 191), (192, 0, 383, 191), (0, 192, 191, 383), (192, 192, 383, 383)]
    for box, color in zip(boxes, expected.values(), strict=True):
        draw.rectangle(box, fill=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    gateway = build_gateway(config)
    result = await gateway._json_completion(
        config.model_vision,
        [{"role": "user", "content": [
            {"type": "text", "text": "Identify each quadrant's color in this image. "
             "If image pixels are unavailable return unknown. Answer JSON only."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
        ColorGrid,
        role="probe_vision_semantic",
    )
    return {"passed": result.model_dump() == expected, "expected": expected,
            "actual": result.model_dump(), "usage": gateway.usage_report()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--content", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--slides", type=int, default=15)
    parser.add_argument("--variants", nargs="+", default=["balanced"],
                        choices=["faithful", "balanced", "visual"])
    parser.add_argument("--probe-only", action="store_true")
    args = parser.parse_args()
    config = Settings(_env_file=args.env_file)
    if config.mock_provider:
        raise RuntimeError("Real provider required: DECKDNA_MOCK_PROVIDER=false")
    if not args.probe_only and (args.template is None or args.content is None):
        parser.error("--template and --content are required for generation")
    args.output.mkdir(parents=True, exist_ok=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Reproduce the UI scenario: analysis has no session/server provider;
    # subsequent generation receives actual credentials through a lease.
    api.settings = config.model_copy(update={"mock_provider": True})
    summary = {"models": {"text": config.model_text, "vision": config.model_vision},
               "chat_options": config.provider_chat_options.model_dump(exclude_none=True)}

    def save(name: str, data: object) -> None:
        (args.output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    with TestClient(api.app) as client:
        def request(method: str, path: str, **kwargs):
            response = client.request(method, "/api/v1" + path, **kwargs)
            if response.is_error:
                # No request/response body: session validation may echo secrets.
                raise RuntimeError(f"API {method} {path}: HTTP {response.status_code}")
            return response.json()

        project = request("POST", "/projects", json={"name": "live API verification",
                          "target_slide_count": args.slides})
        project_path = f"/projects/{project['id']}"
        session = request("POST", "/provider-sessions", json={
            "label": "live verification", "base_url": config.provider_base_url,
            "api_token": config.provider_api_key, "project_id": project["id"],
            "models": {"text": config.model_text, "vision": config.model_vision or None},
            "capabilities": {"structured_output": True, "image_input": bool(config.model_vision)},
        })
        try:
            summary["capability_probes"] = request("POST", f"/provider-sessions/{session['id']}/test")
            summary["semantic_vision"] = asyncio.run(semantic_vision_probe(config))
            save("verification.json", summary)
            if not summary["semantic_vision"]["passed"]:
                raise RuntimeError("Vision semantic probe failed")
            if args.probe_only:
                print(json.dumps(summary, ensure_ascii=False))
                return 0
            template = request("POST", project_path + "/templates", files={
                "file": (args.template.name, args.template.read_bytes(),
                         "application/vnd.openxmlformats-officedocument.presentationml.presentation")})
            template_path = f"/templates/{template['id']}"
            request("POST", template_path + "/analyze", json={})
            dna = request("GET", template_path + "/design-dna")
            summary["descriptions_before_generation"] = sum(bool(e.get("content_description")) for e in dna["exemplars"])
            pack = request("POST", project_path + "/content-packs", files={
                "files": (args.content.name, args.content.read_bytes(), "text/markdown")})
            started = time.monotonic()
            accepted = request("POST", project_path + "/generations", json={
                "template_id": template["id"], "content_pack_id": pack["content_pack"]["id"],
                "provider_session_id": session["id"], "variants": [{"strategy": s} for s in args.variants],
                "brief": {"purpose": "Представить научную базу и план приложения",
                          "audience": "эксперты и пользователи", "language": "ru",
                          "target_slide_count": args.slides},
            })
            run_path = f"/generations/{accepted['generation_id']}"
            while True:
                run = request("GET", run_path)
                if run["state"] in {"completed", "failed", "canceled"}:
                    break
                if time.monotonic() - started > 1200:
                    request("POST", run_path + "/cancel")
                    raise RuntimeError("Generation exceeded verification timeout")
                time.sleep(1)
            summary["generation_seconds"] = round(time.monotonic() - started, 3)
            save("generation.json", run)
            summary["state"] = run["state"]
            dna = request("GET", template_path + "/design-dna")
            summary["descriptions_after_generation"] = sum(bool(e.get("content_description")) for e in dna["exemplars"])
            save("design-dna.json", dna)
            summary["variants"] = []
            for variant in run["variants"]:
                if variant["status"] != "completed":
                    continue
                out = args.output / variant["strategy"]
                out.mkdir(exist_ok=True)
                exported = request("POST", f"/variants/{variant['id']}/exports", json={
                    "formats": ["pptx", "pdf", "html", "quality_passport"]})
                record = request("GET", f"/exports/{exported['export_id']}")
                artifacts = []
                for artifact in record["artifacts"]:
                    response = client.get(artifact["download_url"])
                    if response.status_code != 200 or hashlib.sha256(response.content).hexdigest() != artifact["sha256"]:
                        raise RuntimeError("Artifact download/checksum failed")
                    ext = "json" if artifact["format"] == "quality_passport" else artifact["format"]
                    name = "quality-passport" if ext == "json" else "deck"
                    (out / f"{name}.{ext}").write_bytes(response.content)
                    artifacts.append({"format": artifact["format"], "bytes": len(response.content)})
                report = api.STORE.runs[run["id"]].variant_reports[variant["id"]]
                (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                summary["variants"].append({"strategy": variant["strategy"], "artifacts": artifacts,
                                            "usage": report["usage"], "metrics": variant["metrics"]})
            save("verification.json", summary)
            print(json.dumps(summary, ensure_ascii=False))
            return 0 if run["state"] == "completed" else 1
        finally:
            client.delete(f"/api/v1/provider-sessions/{session['id']}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeckDNAError as exc:
        print(f"verification failed: {exc.code} HTTP {exc.http_status}", file=sys.stderr)
        raise SystemExit(1) from None
