"""Owner-run diagnostic must report fallback and missing web sources honestly."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _script():
    spec = importlib.util.spec_from_file_location(
        "stage2_probe", ROOT / "scripts/stage2_live_check.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("mode", "calls", "expected"), [
    ("timings", 2, True), ("timings", 0, False), ("search", 0, False),
])
def test_rest_probe_requires_evidence_and_one_api_prefix(
    monkeypatch, tmp_path, mode, calls, expected,
):
    script = _script()
    paths = []

    def handler(request):
        p = request.url.path.removeprefix("/api/v1")
        paths.append(request.url.path)
        if p == "/capabilities":
            body = {"features": {"model_auto": True}}
        elif p == "/projects":
            body = {"id": "p"}
        elif p == "/projects/p/intake":
            body = {"template_id": "t", "content_pack_id": "c"}
        elif p == "/content-packs/c/understanding":
            assert request.url.params.get("use_llm") == "true"
            body = {"blocks": [{"id": "b"}], "datasets": [{"id": "d"}]}
        elif p == "/projects/p/questions":
            body = {"questions": [{"id": "audience", "kind": "audience", "options": [
                {"value": "руководство", "recommended": True},
            ]}]}
        elif p == "/projects/p/answers":
            import json

            assert json.loads(request.content)["answers"] == {"audience": "руководство"}
            body = {"brief": {"audience": "руководство"}}
        elif p == "/projects/p/plans":
            body = {"plan_id": "plan", "job_id": "job-plan"}
        elif p.startswith("/jobs/"):
            body = {"state": "completed"}
        elif p == "/plans/plan":
            body = {"plan_id": "plan", "content_pack_id": "c",
                    "plans": [{"deck_plan": {"id": "deck"}}], "research": {"sources": []}}
        elif p == "/plans/plan/style-previews":
            body = {"preview_id": "preview", "job_id": "job-preview"}
        elif p == "/style-previews/preview":
            body = {"recommended": "balanced", "styles": [{"error": None}]}
        elif p == "/projects/p/generations":
            body = {"generation_id": "run", "job_id": "job-gen", "variant_ids": ["v"]}
        elif p == "/variants/v/slides":
            body = {"items": [{"index": 3, "id": "s"}]}
        elif p == "/variants/v/slides/s/regenerate":
            body = {"deck_revision": 2}
        elif p == "/variants/v/exports":
            body = {"export_id": "e"}
        elif p == "/exports/e":
            body = {"artifacts": [{"format": "quality_passport",
                                    "download_url": "/api/v1/artifacts/pass/download"}]}
        elif p == "/artifacts/pass/download":
            body = {"metrics": {"usage": {"model_calls": calls}}, "model_profiles": [],
                    "fallbacks": []}
        else:
            raise AssertionError(f"unexpected endpoint: {request.url}")
        return httpx.Response(200, json=body)

    real_client = httpx.Client
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs,
    ))
    template = tmp_path / "template.pptx"
    content = tmp_path / "data.csv"
    template.write_bytes(b"PK mock template, no renderer in this diagnostic test")
    content.write_text("a,b\nA,1\n")
    result = script.rest_probe(SimpleNamespace(
        api_url="http://backend.test/api/v1", timeout=5, mode=mode,
        template=template, content=content,
    ))
    assert result["ok"] is expected
    assert paths and all(p.startswith("/api/v1/") for p in paths)
    assert not any("/api/v1/api/v1" in p for p in paths)
    if mode == "timings":
        assert result["usage"]["model_calls"] == calls and result["deck_revision"] == 2
        assert result["understanding_blocks"] == result["understanding_datasets"] == 1
        assert result["questions"] == ["audience"]
        assert paths.index("/api/v1/content-packs/c/understanding") < paths.index(
            "/api/v1/projects/p/questions"
        ) < paths.index("/api/v1/projects/p/plans")
