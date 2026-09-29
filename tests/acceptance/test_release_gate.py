"""Tests for scripts/release_gate.py — the pass/fail acceptance gate.

`deckdna benchmark` only prints a table and always exits 0; nothing else
in the repo turns the official constraints (3 variants, <=5min/deck, zero
blockers, native-editable PEI floor, VLM route actually engaged) into an
enforceable, CI-runnable gate. These tests prove `evaluate_variant`
actually catches each constraint violation (fast, synthetic records, no
render), plus one real end-to-end run against a synthetic *unseen*
template through the real pipeline.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "backend"))

import release_gate as rg  # noqa: E402
from deckdna.contracts.deck_plan import Brief  # noqa: E402
from deckdna.contracts.variant_spec import Strategy  # noqa: E402
from deckdna.ingestion.content_parsers import parse_file  # noqa: E402
from deckdna.planning.evidence import build_evidence_graph  # noqa: E402
from deckdna.planning.story_director import LLM_PLANNER_VERSION  # noqa: E402
from deckdna.providers.factory import build_gateway  # noqa: E402
from deckdna.settings import Settings  # noqa: E402
from release_gate import _classify_hard_gate, evaluate_variant, main, run_one  # noqa: E402

UNSEEN_TEMPLATE = _REPO_ROOT / "tests" / "fixtures" / "pptx" / "synthetic_unseen.pptx"
CONTENT = _REPO_ROOT / "tests" / "fixtures" / "content" / "poc_article.md"

needs_render = pytest.mark.skipif(
    shutil.which("soffice") is None or not UNSEEN_TEMPLATE.exists(),
    reason="live run needs soffice + the synthetic unseen fixture",
)


def _ok_record(tmp_path: Path, **overrides) -> dict:
    pptx = tmp_path / "deck.pptx"
    _write_fake_pptx(pptx)
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake")
    html = _write_fake_html(tmp_path)
    record = {
        "status": "generated",
        "duration_seconds": 5.0,
        "issues_by_severity": {},
        "hard_gate": {"new": [], "inherited": [], "new_by_rule": {}, "inherited_by_rule": {}},
        "editability": {"pei_level": 3, "raster_only_slides": 0},
        "slides_out": 1,
        "pptx_path": str(overrides.pop("pptx_path", pptx)),
        "pdf_path": str(overrides.pop("pdf_path", pdf)),
        "html_path": str(overrides.pop("html_path", html)),
        "contextual_audit_ran": True,
        "contextual_audit_complete": True,
        "planner": LLM_PLANNER_VERSION,
    }
    record.update(overrides)
    return record


def _write_fake_pptx(path: Path) -> None:
    """A minimal but genuinely valid OOXML-shaped zip -- enough for
    _check_pptx's real structural checks to pass."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("ppt/slides/slide1.xml", "<sld/>")


def _write_fake_html(tmp_path: Path) -> Path:
    """index.html + один slide-*.png — реальная структура render_html."""
    html_dir = tmp_path / "html"
    html_dir.mkdir(exist_ok=True)
    (html_dir / "index.html").write_text("<html><img src='slide-1.png'></html>")
    (html_dir / "slide-1.png").write_bytes(b"png")
    return html_dir


# --------------------------------------------------------------------------
# evaluate_variant — one constraint at a time, synthetic records, no render
# --------------------------------------------------------------------------


def test_evaluate_variant_generate_error_is_a_violation(tmp_path):
    record = {
        "status": "error",
        "error": {"code": "render_failed", "message": "soffice exploded"},
    }
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert violations == ["generate() failed: render_failed: soffice exploded"]


def test_evaluate_variant_over_budget(tmp_path):
    record = _ok_record(tmp_path, duration_seconds=999.0)
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert any("exceeds budget" in v for v in violations)


def test_evaluate_variant_blocker_issue_fails(tmp_path):
    record = _ok_record(tmp_path, issues_by_severity={"blocker": 2, "warning": 1})
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert any("blocker" in v for v in violations)


def test_evaluate_variant_new_hard_gate_issue_fails(tmp_path):
    """Новый hard-gate finding (overflow/contrast/...) — violation."""
    record = _ok_record(
        tmp_path,
        hard_gate={
            "new": [
                {
                    "rule_code": "text.overflow",
                    "slide_index": 3,
                    "severity": "error",
                    "message": "m",
                }
            ],
            "inherited": [
                {
                    "rule_code": "accessibility.contrast",
                    "slide_index": 0,
                    "severity": "error",
                    "message": "i",
                    "inherited_via": "protected_slide",
                }
            ],
            "new_by_rule": {"text.overflow": 1},
            "inherited_by_rule": {"accessibility.contrast": 1},
        },
    )
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert any("hard-gate" in v and "text.overflow" in v for v in violations), violations


def _exemplar_compose(slide_part="ppt/slides/slide7.xml"):
    return {"slides": [{"index": 0, "exemplar": {"slide_part": slide_part}}]}


def _issue(rule, ids, measured, slide=0):
    return {
        "rule_code": rule,
        "slide_index": slide,
        "severity": "error",
        "deterministic": True,
        "shape_ids": ids,
        "measured_value": measured,
        "message": "m",
    }


def test_classify_same_shape_worsened_metric_is_new():
    """Тот же shape_id + та же rule, но метрика хуже → регрессия, не inherited."""
    baseline = {
        "ppt/slides/slide7.xml": [
            _issue("text.overflow", ["7"], 50.0),
            _issue("accessibility.contrast", ["8"], 3.0),
        ]
    }
    out = _classify_hard_gate(
        [
            # overflow: 80.0 > 50.0 — ухудшение (больше = хуже)
            _issue("text.overflow", ["7"], 80.0),
            # contrast: 1.5 < 3.0 — ухудшение (меньше = хуже)
            _issue("accessibility.contrast", ["8"], 1.5),
        ],
        _exemplar_compose(),
        baseline,
    )
    assert len(out["new"]) == 2
    assert out["inherited"] == []


def test_classify_same_shape_equal_or_better_metric_is_inherited():
    """Та же фигура + equal-or-better метрика → та же находка, inherited."""
    baseline = {
        "ppt/slides/slide7.xml": [
            _issue("text.overflow", ["7"], 50.0),
            _issue("accessibility.contrast", ["8"], 3.0),
        ]
    }
    out = _classify_hard_gate(
        [
            # overflow: 40.0 <= 50.0 — не хуже (ещё над порогом, но не регрессия)
            _issue("text.overflow", ["7"], 40.0),
            # contrast: 3.0 == 3.0 — та же находка
            _issue("accessibility.contrast", ["8"], 3.0),
        ],
        _exemplar_compose(),
        baseline,
    )
    assert out["new"] == []
    assert len(out["inherited"]) == 2


def test_classify_unprovable_attribution_is_new():
    """Иной набор фигур или отсутствие метрики — доказать нельзя → new."""
    baseline = {
        "ppt/slides/slide7.xml": [
            _issue("text.overflow", ["7"], 50.0),
        ]
    }
    out = _classify_hard_gate(
        [
            # другой shape — не та же находка
            _issue("text.overflow", ["9"], 40.0),
            # тот же shape, но метрика отсутствует — не доказуемо
            _issue("text.overflow", ["7"], None),
        ],
        _exemplar_compose(),
        baseline,
    )
    assert len(out["new"]) == 2


def test_classify_protected_slide_inherited_without_metric():
    """Protected/verbatim-слайд — байт-идентичная копия → inherited по построению."""
    compose = {"slides": [{"index": 0, "protected": True}]}
    out = _classify_hard_gate(
        [_issue("accessibility.contrast", ["8"], 0.5)],
        compose,
        {},
    )
    assert out["new"] == []
    assert out["inherited"][0]["inherited_via"] == "protected_slide"


def test_evaluate_variant_baseline_error_is_violation(tmp_path):
    """Baseline-аудит упал → атрибуция невозможна → честный gate FAIL."""
    record = _ok_record(
        tmp_path,
        hard_gate={
            "new": [],
            "inherited": [],
            "new_by_rule": {},
            "inherited_by_rule": {},
            "baseline_error": "RuntimeError: template unreadable",
        },
    )
    problems = evaluate_variant(record, llm=True, budget_seconds=300.0)
    assert any("baseline" in p.lower() for p in problems)


def test_evaluate_variant_inherited_only_hard_gate_passes(tmp_path):
    """Наследованные с verbatim/protected слайдов находки не роняют gate."""
    record = _ok_record(
        tmp_path,
        hard_gate={
            "new": [],
            "inherited": [
                {
                    "rule_code": "text.overflow",
                    "slide_index": 0,
                    "severity": "error",
                    "message": "i",
                    "inherited_via": "protected_slide",
                }
            ],
            "new_by_rule": {},
            "inherited_by_rule": {"text.overflow": 1},
        },
    )
    assert evaluate_variant(record, llm=False, budget_seconds=300.0) == []


def test_evaluate_variant_llm_planner_fallback_fails(tmp_path):
    """llm_enabled + детерминированный planner = LLM-маршрут не проверен."""
    record = _ok_record(tmp_path, planner="story-director/deterministic-0.1.0")
    violations = evaluate_variant(record, llm=True, budget_seconds=300.0)
    assert any("planner" in v and "fell back" in v for v in violations), violations
    # явно суженный scope (VLM-path only) — fallback допустим
    assert evaluate_variant(record, llm=True, budget_seconds=300.0, require_llm_planner=False) == []


def test_evaluate_variant_missing_html_fails(tmp_path):
    record = _ok_record(tmp_path, html_path="")
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert any("html" in v for v in violations), violations


def test_evaluate_variant_html_export_error_fails(tmp_path):
    record = _ok_record(tmp_path, html_path="", html_error="RuntimeError: pdftoppm gone")
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert any("html export failed" in v for v in violations), violations


def test_evaluate_variant_html_render_count_mismatch_fails(tmp_path):
    """Число slide-*.png != slides_out → stale/partial render — FAIL."""
    record = _ok_record(tmp_path, slides_out=3)  # в fake html один png
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert any("expected 3" in v for v in violations), violations


def test_evaluate_variant_html_missing_referenced_render_fails(tmp_path):
    """index ссылается на slide-2.png, которого нет на диске → FAIL."""
    record = _ok_record(tmp_path, slides_out=1)  # html/: index + slide-1.png
    html_dir = Path(record["html_path"])
    (html_dir / "index.html").write_text("<img src='slide-1.png'><img src='slide-2.png'>")
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert any("mismatch" in v and "slide-2.png" in v for v in violations), violations


def test_evaluate_variant_html_partial_index_references_fails(tmp_path):
    """2 png на диске, index ссылается только на одну → FAIL."""
    record = _ok_record(tmp_path, slides_out=2)
    html_dir = Path(record["html_path"])
    (html_dir / "slide-2.png").write_bytes(b"png")
    (html_dir / "index.html").write_text("<img src='slide-1.png'>")
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert any("unreferenced" in v for v in violations), violations


def test_run_one_html_cleanup_error_is_structured(tmp_path, monkeypatch):
    """Ошибка cleanup gate-owned html dir → html_error, не traceback."""
    variant_dir = tmp_path / "variant"
    html_dir = variant_dir / "html"
    html_dir.mkdir(parents=True)
    (html_dir / "slide-9.png").write_bytes(b"stale")

    pptx = tmp_path / "deck.pptx"
    _write_fake_pptx(pptx)
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake")

    def fake_generate(t, c, b, out, gateway=None, strategy=None):
        return {
            "artifacts": {"pptx": str(pptx), "pdf": str(pdf)},
            "audit_issues": [],
            "compose_report": {"slides": []},
            "duration_seconds": 1.0,
            "slides_out": 1,
            "planner": LLM_PLANNER_VERSION,
            "contextual_audit": {"ran": True, "complete": True},
            "quality_passport": {
                "metrics": {"editability": {"pei_level": 3, "raster_only_slides": 0}}
            },
        }

    monkeypatch.setattr(rg, "generate", fake_generate)

    def denied(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(rg.shutil, "rmtree", denied)
    brief = Brief(purpose="p", audience="a", language="ru", target_slide_count=12)
    record = run_one(Path("t.pptx"), Path("c.md"), brief, Strategy.balanced, variant_dir, None)
    assert record["status"] == "generated"
    assert "PermissionError" in record["html_error"]
    assert record["html_path"] == ""
    violations = evaluate_variant(record, llm=True, budget_seconds=300.0)
    assert any("html export failed" in v for v in violations), violations


def test_run_one_duration_covers_html_export(tmp_path, monkeypatch):
    """duration_seconds — wall-clock run_one (generate+export),
    generate_seconds — только pipeline: бюджет считается по полному."""
    variant_dir = tmp_path / "variant"
    pptx = tmp_path / "deck.pptx"
    _write_fake_pptx(pptx)
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake")

    def fake_generate(t, c, b, out, gateway=None, strategy=None):
        return {
            "artifacts": {"pptx": str(pptx), "pdf": str(pdf)},
            "audit_issues": [],
            "compose_report": {"slides": []},
            "duration_seconds": 1.0,
            "slides_out": 1,
            "planner": LLM_PLANNER_VERSION,
            "contextual_audit": {"ran": True, "complete": True},
            "quality_passport": {
                "metrics": {"editability": {"pei_level": 3, "raster_only_slides": 0}}
            },
        }

    def fake_render(pptx_path, out):
        time.sleep(0.1)  # измеримое время экспорта
        out.mkdir(exist_ok=True)
        (out / "index.html").write_text("<img src='slide-1.png'>")
        (out / "slide-1.png").write_bytes(b"png")
        return out / "index.html"

    monkeypatch.setattr(rg, "generate", fake_generate)
    monkeypatch.setattr(rg, "render_html", fake_render)
    brief = Brief(purpose="p", audience="a", language="ru", target_slide_count=12)
    record = run_one(Path("t.pptx"), Path("c.md"), brief, Strategy.balanced, variant_dir, None)
    assert record["generate_seconds"] == 1.0
    # wall-clock покрывает экспорт: >= времени, проведённого в render
    assert record["duration_seconds"] >= 0.09


def test_run_one_cleans_stale_html_dir(tmp_path, monkeypatch):
    """Stale slide-*.png прошлого прогона в gate-owned html dir
    вычищается до render_html — счёт не фальсифицируется (idempotent)."""
    variant_dir = tmp_path / "variant"
    html_dir = variant_dir / "html"
    html_dir.mkdir(parents=True)
    (html_dir / "index.html").write_text("stale")
    (html_dir / "slide-99.png").write_bytes(b"stale")

    pptx = tmp_path / "deck.pptx"
    _write_fake_pptx(pptx)
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake")

    def fake_generate(t, c, b, out, gateway=None, strategy=None):
        return {
            "artifacts": {"pptx": str(pptx), "pdf": str(pdf)},
            "audit_issues": [],
            "compose_report": {"slides": []},
            "duration_seconds": 1.0,
            "slides_out": 1,
            "planner": LLM_PLANNER_VERSION,
            "contextual_audit": {"ran": True, "complete": True},
            "quality_passport": {
                "metrics": {"editability": {"pei_level": 3, "raster_only_slides": 0}}
            },
        }

    def fake_render(pptx_path, out):
        out.mkdir(exist_ok=True)
        (out / "index.html").write_text("<img src='slide-1.png'>")
        (out / "slide-1.png").write_bytes(b"png")
        return out / "index.html"

    monkeypatch.setattr(rg, "generate", fake_generate)
    monkeypatch.setattr(rg, "render_html", fake_render)
    brief = Brief(purpose="p", audience="a", language="ru", target_slide_count=12)
    record = run_one(Path("t.pptx"), Path("c.md"), brief, Strategy.balanced, variant_dir, None)
    assert record["html_path"]
    assert not (html_dir / "slide-99.png").exists()
    # ровно один свежий рендер — evaluate чист
    assert evaluate_variant(record, llm=True, budget_seconds=300.0) == []


def test_evaluate_variant_html_without_renders_fails(tmp_path):
    html_dir = tmp_path / "html_broken"
    html_dir.mkdir()
    (html_dir / "index.html").write_text("<html/>")  # без slide-*.png
    record = _ok_record(tmp_path, html_path=html_dir)
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert any("slide-*.png" in v for v in violations), violations


def test_evaluate_variant_unexpected_error_is_a_violation(tmp_path):
    """Не-DeckDNAError исключение — структурированный error-record."""
    record = {
        "status": "error",
        "error": {"code": "unexpected_error", "message": "ValueError: boom"},
    }
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert violations == ["generate() failed: unexpected_error: ValueError: boom"]


@pytest.mark.parametrize(
    ("editability", "expected_substring"),
    [
        ({"pei_level": None, "raster_only_slides": 0}, "not computed"),
        ({"pei_level": 1, "raster_only_slides": 0}, "< required"),
        # pei.py's own rubric: L2 does NOT guarantee "no raster-only slide"
        # (only L3 does) -- must still be rejected, not waved through.
        ({"pei_level": 2, "raster_only_slides": 0}, "< required"),
        ({"pei_level": 3, "raster_only_slides": 2}, "whole-slide-raster"),
    ],
)
def test_evaluate_variant_editability_floor(tmp_path, editability, expected_substring):
    record = _ok_record(tmp_path, editability=editability)
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert any(expected_substring in v for v in violations), violations


def test_evaluate_variant_llm_requested_but_contextual_audit_never_ran(tmp_path):
    record = _ok_record(tmp_path, contextual_audit_ran=False)
    violations = evaluate_variant(record, llm=True, budget_seconds=300.0)
    assert any("contextual" in v for v in violations)
    # same record is a clean pass when --llm wasn't requested
    assert evaluate_variant(record, llm=False, budget_seconds=300.0) == []


def test_evaluate_variant_llm_rejects_partial_contextual_coverage(tmp_path):
    record = _ok_record(tmp_path, contextual_audit_complete=False)
    violations = evaluate_variant(record, llm=True, budget_seconds=300.0)
    assert any("coverage is incomplete" in violation for violation in violations)
    record.pop("contextual_audit_complete")
    violations = evaluate_variant(record, llm=True, budget_seconds=300.0)
    assert any("coverage is incomplete" in violation for violation in violations)


def test_evaluate_variant_corrupt_pptx_bytes_caught(tmp_path):
    pptx = tmp_path / "corrupt.pptx"
    pptx.write_bytes(b"not actually a zip")
    record = _ok_record(tmp_path, pptx_path=pptx)
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert any("zip" in v for v in violations)


def test_evaluate_variant_missing_pdf_magic_caught(tmp_path):
    pdf = tmp_path / "corrupt.pdf"
    pdf.write_bytes(b"not a pdf")
    record = _ok_record(tmp_path, pdf_path=pdf)
    violations = evaluate_variant(record, llm=False, budget_seconds=300.0)
    assert any("magic bytes" in v for v in violations)


def test_evaluate_variant_clean_record_passes(tmp_path):
    record = _ok_record(tmp_path)
    assert evaluate_variant(record, llm=True, budget_seconds=300.0) == []


# --------------------------------------------------------------------------
# Real end-to-end run through the actual pipeline (synthetic unseen template)
# --------------------------------------------------------------------------


@needs_render
def test_gate_end_to_end_pass_on_unseen_template(tmp_path):
    out_dir = tmp_path / "gate"
    exit_code = main(
        [
            "--templates",
            str(UNSEEN_TEMPLATE),
            "--content",
            str(CONTENT),
            "--strategies",
            "faithful",
            "--no-llm",
            "--out-dir",
            str(out_dir),
        ]
    )
    assert exit_code == 0
    report = json.loads((out_dir / "gate-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["passed"] == 1
    assert report["failed"] == 0
    assert report["rows"][0]["violations"] == []


@needs_render
def test_gate_end_to_end_nonzero_exit_on_impossible_budget(tmp_path):
    out_dir = tmp_path / "gate"
    exit_code = main(
        [
            "--templates",
            str(UNSEEN_TEMPLATE),
            "--content",
            str(CONTENT),
            "--strategies",
            "faithful",
            "--no-llm",
            "--budget",
            "0.001",
            "--out-dir",
            str(out_dir),
        ]
    )
    assert exit_code == 1
    report = json.loads((out_dir / "gate-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert any("exceeds budget" in v for v in report["rows"][0]["violations"])


def test_gate_unexpected_exception_still_writes_report(tmp_path, monkeypatch):
    """Не-DeckDNAError в generate(): структурированный fail + отчёт, не traceback."""
    import release_gate

    def _boom(*a, **kw):
        raise ValueError("soffice segfaulted internally")

    monkeypatch.setattr(release_gate, "generate", _boom)
    out_dir = tmp_path / "gate"
    exit_code = main(
        [
            "--templates",
            str(UNSEEN_TEMPLATE),
            "--content",
            str(CONTENT),
            "--strategies",
            "faithful",
            "--no-llm",
            "--out-dir",
            str(out_dir),
        ]
    )
    assert exit_code == 1
    report = json.loads((out_dir / "gate-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    row = report["rows"][0]
    assert row["status"] == "error"
    assert row["error"]["code"] == "unexpected_error"
    assert "ValueError" in row["error"]["message"]
    assert any("unexpected_error" in v for v in row["violations"])


def _schema_calls(server, schema_name: str) -> list[dict]:
    return [
        r["json"]
        for r in server.requests
        if r["json"].get("response_format", {}).get("json_schema", {}).get("name") == schema_name
    ]


@needs_render
def test_gate_llm_route_over_real_http_stub(tmp_path, monkeypatch, local_openai):
    """Full-scope `--llm` gate против реального TCP stub'а.

    env-конфиг (DECKDNA_MOCK_PROVIDER=false + provider endpoint) →
    `build_gateway(Settings())` → OpenAICompatibleProvider → HTTP до
    local_openai. PASS возможен только если: planner == LLM-планировщик
    (text_json/SlideOutlineBatch по проводу, батчами слайдов) И
    contextual-аудит реально пошёл (vision_json/SlideChecksResult с PNG
    data-url на каждый слайд).

    Пин на v1 — тест конкретно проверяет SlideOutlineBatch (storyline
    v2's схема) по проводу; v3 (дефолт с 27.09, ADR-016) шлёт
    SlideStructureBatch/SlideContent/LayoutFitAnswer вместо неё.
    """
    import deckdna.generation.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v1")
    # Ответы stub'а: схема-валидный SlideOutlineBatch, заземлённый на
    # реальный узел evidence-графа этого контента, покрывающий ВЕСЬ
    # диапазон индексов колоды разом (stub отдаёт один и тот же ответ на
    # каждый storyline-вызов независимо от того, какой батч индексов
    # реально запросили — plan_deck_llm шлёт несколько вызовов, по батчу
    # слайдов на вызов), и SlideChecksResult с десятью вердиктами —
    # доказательство, что полный VLM-ответ доезжает обратно в пайплайн.
    brief = Brief(
        purpose="Release gate: official 3-variant LLM/VLM route",
        audience="эксперты и жюри",
        language="ru",
        target_slide_count=12,
    )
    pack = parse_file(CONTENT)
    graph = build_evidence_graph(pack)
    claim_id = next(n.id for n in graph.nodes if n.type.value == "claim")
    local_openai.responses["SlideOutlineBatch"] = {
        "slides": [
            {
                "index": i,
                "purpose": "overview",
                "title_intent": f"LLM-вывод {i}",
                "key_message": "Заключение по данным.",
                "evidence_ids": [claim_id],
            }
            for i in range(brief.target_slide_count)
        ]
    }
    local_openai.responses["SlideChecksResult"] = {
        "verdicts": [
            {
                "check": str(check),
                "verdict": "pass",
                "rationale": "stub",
                "confidence": 0.9,
            }
            for check in range(1, 11)
        ]
    }
    monkeypatch.setenv("DECKDNA_MOCK_PROVIDER", "false")
    monkeypatch.setenv("DECKDNA_PROVIDER_BASE_URL", local_openai.base_url)
    monkeypatch.setenv("DECKDNA_PROVIDER_API_KEY", "test-secret")
    monkeypatch.setenv("DECKDNA_MODEL_TEXT", "stub-text")
    monkeypatch.setenv("DECKDNA_MODEL_VISION", "stub-vision")
    # env → Settings() → реальный factory-путь (валидация + провайдер)
    monkeypatch.setattr(rg, "build_gateway", lambda: build_gateway(Settings()))

    out_dir = tmp_path / "gate"
    rc = main(
        [
            "--templates",
            str(UNSEEN_TEMPLATE),
            "--content",
            str(CONTENT),
            "--strategies",
            "balanced",
            "--slides",
            "12",
            "--out-dir",
            str(out_dir),
        ]
    )
    assert rc == 0
    report = json.loads((out_dir / "gate-report.json").read_text())
    row = report["rows"][0]
    assert row["status"] == "generated"
    assert row["planner"] == LLM_PLANNER_VERSION
    assert row["contextual_audit_ran"] is True
    assert row["violations"] == []
    assert Path(row["pptx_path"]).exists()
    # Провод: LLM-планировщик и VLM-аудит реально ушли по HTTP (батчами —
    # >=1 storyline-вызов, не обязательно один на всю колоду).
    assert _schema_calls(local_openai, "SlideOutlineBatch")
    vision = _schema_calls(local_openai, "SlideChecksResult")
    assert vision  # contextual-аудит на слайдах
    for body in vision:
        content = body["messages"][0]["content"]
        images = [c for c in content if c["type"] == "image_url"]
        assert images and all(
            i["image_url"]["url"].startswith("data:image/png;base64,") for i in images
        )
    assert report["gate_scope"].startswith("full LLM/VLM route")


@needs_render
def test_gate_mockprovider_planner_fallback_fails_full_scope(tmp_path):
    """Default MockProvider + `--llm` full-scope → честный FAIL.

    MockProvider принципиально откатывает LLM-планировщик →
    planner != LLM_PLANNER_VERSION → violation; gate не подделывает
    PASS на mock-маршруте.
    """
    out_dir = tmp_path / "gate"
    rc = main(
        [
            "--templates",
            str(UNSEEN_TEMPLATE),
            "--content",
            str(CONTENT),
            "--strategies",
            "balanced",
            "--slides",
            "12",
            "--out-dir",
            str(out_dir),
        ]
    )
    assert rc == 1
    report = json.loads((out_dir / "gate-report.json").read_text())
    row = report["rows"][0]
    assert row["planner"] != LLM_PLANNER_VERSION
    assert any("planner" in v for v in row["violations"])
    assert report["status"] == "fail"


@needs_render
def test_gate_same_basename_templates_disambiguated(tmp_path):
    """Два шаблона с одинаковым basename в разных каталогах — без
    перезаписи вывода и коллизии baseline-ключей."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    tpl_a = dir_a / "shared.pptx"
    tpl_b = dir_b / "shared.pptx"
    shutil.copy(UNSEEN_TEMPLATE, tpl_a)
    shutil.copy(UNSEEN_TEMPLATE, tpl_b)

    out_dir = tmp_path / "gate"
    main(
        [
            "--templates",
            str(tpl_a),
            str(tpl_b),
            "--content",
            str(CONTENT),
            "--strategies",
            "balanced",
            "--slides",
            "12",
            "--no-llm",
            "--out-dir",
            str(out_dir),
        ]
    )
    report = json.loads((out_dir / "gate-report.json").read_text())
    assert report["total"] == 2
    assert len(report["template_baseline_hard_gate_counts"]) == 2
    paths = {row["template_path"] for row in report["rows"]}
    assert paths == {str(tpl_a.resolve()), str(tpl_b.resolve())}
    # два различных выходных каталога — ничего не перезаписано
    variant_dirs = sorted(p.name for p in out_dir.iterdir() if p.is_dir())
    assert len(variant_dirs) == 2
    assert all(v.startswith("shared") for v in variant_dirs)


def test_gate_duplicate_template_path_rejected(tmp_path):
    """Идентичный resolved-путь дважды — честный exit 2, не перезапись."""
    rc = main(
        [
            "--templates",
            str(UNSEEN_TEMPLATE),
            str(UNSEEN_TEMPLATE),
            "--content",
            str(CONTENT),
            "--out-dir",
            str(tmp_path / "gate"),
        ]
    )
    assert rc == 2


def test_gate_missing_template_is_argparse_level_failure_not_a_traceback(tmp_path):
    proc = subprocess.run(  # noqa: S603 -- fixed argv, test-only
        [
            sys.executable,
            str(_SCRIPTS / "release_gate.py"),
            "--templates",
            str(tmp_path / "nope.pptx"),
            "--no-llm",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert "not found" in proc.stderr


# --------------------------------------------------------------------------
# dropped_units — плановый контент не может быть молча потерян
# --------------------------------------------------------------------------


def test_evaluate_variant_dropped_text_unplaced_is_violation(tmp_path):
    record = _ok_record(tmp_path, dropped_units={"text_unplaced": 2})
    violations = evaluate_variant(record, llm=True, budget_seconds=300.0)
    assert any("dropped planned content" in v for v in violations), violations
    assert any("text_unplaced x2" in v for v in violations), violations


def test_evaluate_variant_dropped_all_kinds_disclosed(tmp_path):
    record = _ok_record(tmp_path, dropped_units={"text_unplaced": 1, "chart_unplaced": 3})
    violations = evaluate_variant(record, llm=True, budget_seconds=300.0)
    assert any("chart_unplaced x3" in v for v in violations), violations
    assert any("text_unplaced x1" in v for v in violations), violations


def test_evaluate_variant_empty_or_zero_dropped_units_passes(tmp_path):
    for dropped in ({}, {"text_unplaced": 0}):
        record = _ok_record(tmp_path, dropped_units=dropped)
        violations = evaluate_variant(record, llm=True, budget_seconds=300.0)
        assert violations == [], violations


def test_run_one_propagates_dropped_units(tmp_path, monkeypatch):
    """dropped_units из compose_report попадают в record и в нарушения."""
    variant_dir = tmp_path / "variant"
    pptx = tmp_path / "deck.pptx"
    _write_fake_pptx(pptx)
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake")

    def fake_generate(t, c, b, out, gateway=None, strategy=None):
        return {
            "artifacts": {"pptx": str(pptx), "pdf": str(pdf)},
            "audit_issues": [],
            "compose_report": {
                "slides": [],
                "dropped_units": {"text_unplaced": 2, "chart_unplaced": 0},
            },
            "duration_seconds": 1.0,
            "slides_out": 1,
            "planner": LLM_PLANNER_VERSION,
            "contextual_audit": {"ran": True, "complete": True},
            "quality_passport": {
                "metrics": {"editability": {"pei_level": 3, "raster_only_slides": 0}}
            },
        }

    monkeypatch.setattr(rg, "generate", fake_generate)
    html_dir = tmp_path / "html"
    index = html_dir / "index.html"
    monkeypatch.setattr(rg, "render_html", lambda *a, **k: index)

    brief = Brief(purpose="t", audience="a", language="ru", target_slide_count=12)
    record = run_one(Path("t.pptx"), Path("c.md"), brief, Strategy.balanced, variant_dir, None)
    assert record["dropped_units"] == {"text_unplaced": 2, "chart_unplaced": 0}
    violations = evaluate_variant(record, llm=True, budget_seconds=300.0)
    assert any("text_unplaced x2" in v for v in violations), violations
