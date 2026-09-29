"""Tests for the portable agent skill runtime (skill/run_skill.py).

Deliberately exercised against *synthetic, non-organizer* templates
(never vk_tech/vk_workspace/lct2026_submission) — the official
requirement is arbitrary-template adaptation, not benchmark-fixture
familiarity, and this is the one place in the repo whose whole job is to
prove the skill runs end to end on a template it has never seen.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import deckdna.generation.pipeline as pipeline_module  # noqa: E402
import deckdna.providers.factory as factory_module  # noqa: E402
from deckdna.contracts.variant_spec import Strategy  # noqa: E402
from deckdna.ingestion.content_parsers import parse_file  # noqa: E402
from deckdna.planning.evidence import build_evidence_graph  # noqa: E402
from deckdna.planning.story_director import LLM_PLANNER_VERSION  # noqa: E402
from deckdna.settings import Settings  # noqa: E402

import skill.run_skill as run_skill_module  # noqa: E402
from skill.run_skill import (  # noqa: E402
    SkillInputError,
    resolve_content_file,
    run_skill,
)

FIXTURES = _REPO_ROOT / "tests" / "fixtures"
UNSEEN_TEMPLATE = FIXTURES / "pptx" / "synthetic_unseen.pptx"
UNSEEN_TEMPLATE_SPARSE = FIXTURES / "pptx" / "synthetic_unseen_sparse.pptx"
CONTENT_MD = FIXTURES / "content" / "poc_article.md"

needs_render = pytest.mark.skipif(
    shutil.which("soffice") is None or not UNSEEN_TEMPLATE.exists(),
    reason="full run needs soffice + the synthetic unseen fixture",
)

API_KEY = "test-secret-not-real"  # noqa: S105 -- local stub only, never a real key

# Compact content that fits the unseen template's text slots: the
# positive-acceptance e2e below asserts exact "ok" + zero dropped_units
# on it. poc_article.md is deliberately denser and honestly drops a few
# units on these fixtures — it stays for the degradation tests.
FITTING_CONTENT_MD = """# Проектная презентация

Краткое вступление о продукте.

## Проблема

Основная боль пользователей описана в одном тезисе.

Второй поддерживающий тезис.

## Решение

Ключевая идея продукта коротко.

Преимущество номер два.

## План

Этап первого квартала.

Этап второго квартала.
"""


def _fitting_content(tmp_path: Path) -> Path:
    path = tmp_path / "fitting.md"
    path.write_text(FITTING_CONTENT_MD, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# resolve_content_file — no pipeline/render involved, fast and exhaustive
# --------------------------------------------------------------------------


def test_resolve_content_file_single_file_ok():
    assert resolve_content_file(CONTENT_MD) == CONTENT_MD


def test_resolve_content_file_unsupported_extension(tmp_path):
    bogus = tmp_path / "notes.rtf"
    bogus.write_text("hello")
    with pytest.raises(SkillInputError) as exc:
        resolve_content_file(bogus)
    assert "unsupported content format" in str(exc.value)
    assert exc.value.to_envelope()["error"]["code"] == "invalid_input"


def test_resolve_content_file_missing_path(tmp_path):
    with pytest.raises(SkillInputError, match="content path not found"):
        resolve_content_file(tmp_path / "does-not-exist.md")


def test_resolve_content_file_directory_single_candidate(tmp_path):
    (tmp_path / "readme.txt").write_text("hi")
    (tmp_path / "notes.rtf").write_text("ignored, unsupported extension")
    resolved = resolve_content_file(tmp_path)
    assert resolved == tmp_path / "readme.txt"


def test_resolve_content_file_directory_empty(tmp_path):
    (tmp_path / "notes.rtf").write_text("ignored")
    with pytest.raises(SkillInputError, match="no supported content file"):
        resolve_content_file(tmp_path)


def test_resolve_content_file_directory_ambiguous(tmp_path):
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    with pytest.raises(SkillInputError) as exc:
        resolve_content_file(tmp_path)
    assert "ambiguous" in str(exc.value)
    candidates = exc.value.to_envelope()["error"]["details"]["candidates"]
    assert len(candidates) == 2


# --------------------------------------------------------------------------
# Early-failure paths: honest, non-zero, summary.json always written
# --------------------------------------------------------------------------


def test_run_skill_missing_template_is_honest_early_failure(tmp_path):
    out_dir = tmp_path / "out"
    summary = run_skill(
        template=tmp_path / "missing.pptx",
        content=CONTENT_MD,
        out_dir=out_dir,
    )
    assert summary["status"] == "failed"
    assert summary["variants"] == []
    assert summary["error"]["code"] == "invalid_input"
    # written to disk even though nothing ran
    on_disk = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert on_disk == summary


def test_run_skill_ambiguous_content_directory_is_honest_early_failure(tmp_path):
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / "a.md").write_text("a")
    (content_dir / "b.md").write_text("b")
    summary = run_skill(
        template=UNSEEN_TEMPLATE, content=content_dir, out_dir=tmp_path / "out"
    )
    assert summary["status"] == "failed"
    assert summary["error"]["code"] == "invalid_input"
    assert summary["variants"] == []


def test_run_skill_llm_without_provider_config_fails_honestly(tmp_path, monkeypatch):
    """--llm with mock_provider off and no model configured must fail
    before touching the pipeline — and must not leak the configured
    canary key/base_url into summary.json on the way out."""
    canary_url = "https://canary.invalid:9999/v1"
    monkeypatch.setattr(
        factory_module,
        "settings",
        Settings(
            mock_provider=False,
            provider_base_url=canary_url,
            provider_api_key=API_KEY,
            model_text="",
        ),
    )
    out_dir = tmp_path / "out"
    summary = run_skill(
        template=UNSEEN_TEMPLATE,
        content=CONTENT_MD,
        out_dir=out_dir,
        use_llm=True,
    )
    assert summary["status"] == "failed"
    assert summary["error"]["code"] == "provider_capability_missing"
    assert summary["variants"] == []
    dump = json.dumps(summary) + (out_dir / "summary.json").read_text(encoding="utf-8")
    assert API_KEY not in dump
    assert "canary.invalid" not in dump


# --------------------------------------------------------------------------
# Destination reuse: stale artifacts must never masquerade as new output
# --------------------------------------------------------------------------


def test_run_skill_rejects_nonempty_out_dir(tmp_path):
    """Not idempotent, honestly: a used destination is rejected up front
    (except a lone summary.json — an earlier run that died early)."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "stale-deck.pptx").write_bytes(b"PK\x03\x04")
    summary = run_skill(
        template=UNSEEN_TEMPLATE, content=CONTENT_MD, out_dir=out_dir
    )
    assert summary["status"] == "failed"
    assert summary["error"]["code"] == "invalid_input"
    assert "not empty" in summary["error"]["message"]
    assert "stale-deck.pptx" in summary["error"]["details"]["leftover"]


def test_run_skill_rerun_after_early_failure_is_allowed(tmp_path):
    """A directory holding only the previous run's summary.json is safe
    to reuse — that run produced nothing that could go stale."""
    out_dir = tmp_path / "out"
    first = run_skill(
        template=tmp_path / "missing.pptx", content=CONTENT_MD, out_dir=out_dir
    )
    assert first["status"] == "failed"
    second = run_skill(
        template=tmp_path / "missing.pptx", content=CONTENT_MD, out_dir=out_dir
    )
    # rejected by the template check again — NOT by the nonempty-dir guard
    assert second["error"]["code"] == "invalid_input"
    assert "template not found" in second["error"]["message"]


def test_run_skill_unexpected_error_is_typed_envelope(tmp_path, monkeypatch):
    """A non-DeckDNAError from the pipeline becomes a typed
    internal_error — never a bare traceback, never str(exc) (exception
    text can carry credentials)."""

    def _boom(*args, **kwargs):
        raise RuntimeError("canary-secret-123 inside exception text")

    monkeypatch.setattr(run_skill_module, "generate", _boom)
    summary = run_skill(
        template=UNSEEN_TEMPLATE,
        content=CONTENT_MD,
        out_dir=tmp_path / "out",
        strategies=[Strategy.faithful],
    )
    assert summary["status"] == "failed"
    assert len(summary["variants"]) == 1
    variant = summary["variants"][0]
    assert variant["status"] == "failed"
    assert variant["error"]["code"] == "internal_error"
    assert variant["error"]["details"]["exception"] == "RuntimeError"
    dump = json.dumps(summary) + (
        tmp_path / "out" / "summary.json"
    ).read_text(encoding="utf-8")
    assert "canary-secret-123" not in dump  # exception TEXT never leaks


def _fake_report(variant_dir: Path) -> dict:
    variant_dir.mkdir(parents=True, exist_ok=True)
    pptx = variant_dir / "deck.pptx"
    pptx.write_bytes(b"PK\x03\x04")
    pdf = variant_dir / "deck.pdf"
    pdf.write_bytes(b"%PDF-1")
    qp = variant_dir / "quality-passport.json"
    qp.write_text("{}", encoding="utf-8")
    return {
        "audit_issues": [{"severity": "warning"}],
        "artifacts": {
            "pptx": str(pptx),
            "pdf": str(pdf),
            "quality_passport": str(qp),
        },
        "duration_seconds": 1.0,
        "slides_out": 5,
        "planner": "deterministic",
        "contextual_audit": {"ran": False},
        "compose_report": {"dropped_units": {}, "warnings": []},
        "quality_passport": {
            "metrics": {
                "editability": {
                    "pei_level": 1,
                    "native_text_ratio": 1.0,
                    "raster_only_slides": 0,
                }
            }
        },
    }


def test_run_skill_malformed_generate_report_is_typed_and_continues(
    tmp_path, monkeypatch
):
    """Postprocessing errors (report access, stat/hash) must also become
    a typed internal_error — and must not abort the remaining variants."""

    def _fake_generate(template, content_file, brief, variant_dir, gateway=None, strategy=None):
        if strategy is Strategy.faithful:
            return {}  # malformed: every postprocessing key access raises
        return _fake_report(variant_dir)

    def _fake_render_html(pptx_path, html_dir):
        html_dir.mkdir(parents=True, exist_ok=True)
        index = html_dir / "index.html"
        index.write_text("<html></html>", encoding="utf-8")
        return index

    monkeypatch.setattr(run_skill_module, "generate", _fake_generate)
    monkeypatch.setattr(run_skill_module, "render_html", _fake_render_html)
    out_dir = tmp_path / "out"
    summary = run_skill(
        template=UNSEEN_TEMPLATE,
        content=CONTENT_MD,
        out_dir=out_dir,
        strategies=[Strategy.faithful, Strategy.balanced],
    )
    assert summary["status"] == "partial"
    broken, healthy = summary["variants"]
    assert broken["strategy"] == "faithful"
    assert broken["status"] == "failed"
    assert broken["error"]["code"] == "internal_error"
    assert broken["error"]["details"]["exception"] == "KeyError"
    assert healthy["strategy"] == "balanced"
    assert healthy["status"] == "ok"
    # summary still written per the always-written contract
    on_disk = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert on_disk == summary


def test_run_skill_artifact_ref_error_is_typed_envelope(tmp_path, monkeypatch):
    """A stat/hash failure inside _artifact_ref is typed internal_error
    too — the old code let it escape as a bare traceback with no summary."""

    def _fake_generate(template, content_file, brief, variant_dir, gateway=None, strategy=None):
        return _fake_report(variant_dir)

    def _boom_artifact_ref(path, out_dir):
        raise RuntimeError("canary-secret-456")

    monkeypatch.setattr(run_skill_module, "generate", _fake_generate)
    monkeypatch.setattr(run_skill_module, "_artifact_ref", _boom_artifact_ref)
    out_dir = tmp_path / "out"
    summary = run_skill(
        template=UNSEEN_TEMPLATE,
        content=CONTENT_MD,
        out_dir=out_dir,
        strategies=[Strategy.faithful],
    )
    assert summary["status"] == "failed"
    variant = summary["variants"][0]
    assert variant["error"]["code"] == "internal_error"
    assert variant["error"]["details"]["exception"] == "RuntimeError"
    dump = json.dumps(summary) + (out_dir / "summary.json").read_text(
        encoding="utf-8"
    )
    assert "canary-secret-456" not in dump


def test_run_skill_dropped_units_are_disclosed_and_degrade(tmp_path, monkeypatch):
    """Planned content the composer dropped must surface in the summary
    and make the variant degraded (overall partial, nonzero exit) — a
    valid deck that omits supplied content is not a clean success."""

    def _fake_generate(template, content_file, brief, variant_dir, gateway=None, strategy=None):
        report = _fake_report(variant_dir)
        if strategy is Strategy.faithful:
            report["compose_report"] = {
                "dropped_units": {"text_unplaced": 42, "image": 3},
                "warnings": ["45 unit(s) dropped"],
            }
        return report

    def _fake_render_html(pptx_path, html_dir):
        html_dir.mkdir(parents=True, exist_ok=True)
        index = html_dir / "index.html"
        index.write_text("<html></html>", encoding="utf-8")
        return index

    monkeypatch.setattr(run_skill_module, "generate", _fake_generate)
    monkeypatch.setattr(run_skill_module, "render_html", _fake_render_html)
    out_dir = tmp_path / "out"
    summary = run_skill(
        template=UNSEEN_TEMPLATE,
        content=CONTENT_MD,
        out_dir=out_dir,
        strategies=[Strategy.faithful, Strategy.balanced],
    )
    assert summary["status"] == "partial"
    dropped, healthy = summary["variants"]
    assert dropped["status"] == "degraded"
    assert dropped["dropped_units"] == {"text_unplaced": 42, "image": 3}
    assert dropped["compose_warnings"] == ["45 unit(s) dropped"]
    # artifacts are still real and disclosed — drop is honest, not hidden
    assert dropped["artifacts"]["pptx"] is not None
    assert dropped["artifacts"]["html"] is not None
    assert healthy["status"] == "ok"
    assert healthy["dropped_units"] == {}
    on_disk = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert on_disk == summary


def test_skill_resolves_bundled_deckdna_checkout():
    """_ensure_deckdna_importable must pin THIS repo's backend/, not an
    installed package from another checkout."""
    import deckdna

    backend = (_REPO_ROOT / "backend").resolve()
    assert Path(deckdna.__file__).resolve().is_relative_to(backend)


# --------------------------------------------------------------------------
# Real pipeline, unseen template — the actual proof this thing works
# --------------------------------------------------------------------------


@needs_render
def test_run_skill_single_strategy_end_to_end_unseen_template(tmp_path):
    out_dir = tmp_path / "out"
    summary = run_skill(
        template=UNSEEN_TEMPLATE,
        content=CONTENT_MD,
        out_dir=out_dir,
        target_slide_count=10,
        strategies=[Strategy.faithful],
    )
    # this template honestly drops a few planned text units — degraded
    # is a completed variant, failed would fail the whole run outright
    assert summary["status"] in ("ok", "partial"), summary
    assert len(summary["variants"]) == 1
    variant = summary["variants"][0]
    assert variant["status"] in ("ok", "degraded")
    assert variant["slides_out"] > 0

    pptx = out_dir / variant["artifacts"]["pptx"]["path"]
    pdf = out_dir / variant["artifacts"]["pdf"]["path"]
    html_index = out_dir / variant["artifacts"]["html"]["path"]
    passport = out_dir / variant["artifacts"]["quality_passport"]["path"]

    # checksums in the report actually match the bytes on disk
    for key, path in (("pptx", pptx), ("pdf", pdf), ("quality_passport", passport)):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == variant["artifacts"][key]["sha256"]
        assert path.stat().st_size == variant["artifacts"][key]["bytes"]

    # pptx is a real, valid OOXML zip — not a truncated/corrupt file
    with zipfile.ZipFile(pptx) as zf:
        assert zf.testzip() is None
        assert "[Content_Types].xml" in zf.namelist()
        assert any(n.startswith("ppt/slides/slide") for n in zf.namelist())

    # pdf has the real %PDF- magic bytes
    assert pdf.read_bytes()[:5] == b"%PDF-"

    # html is real, openable, references real slide assets
    assert html_index.exists()
    html_text = html_index.read_text(encoding="utf-8")
    assert "<html" in html_text.lower()
    assert any(html_index.parent.glob("slide-*.png"))

    # nothing in summary.json embeds raw artifact bytes
    raw = (out_dir / "summary.json").read_text(encoding="utf-8")
    assert "PK\x03\x04" not in raw  # zip magic would show up if pptx bytes leaked


@needs_render
def test_run_skill_all_three_strategies_are_really_different(tmp_path):
    """OR-007: faithful/balanced/visual must produce genuinely distinct
    decks, not the same output under three labels."""
    out_dir = tmp_path / "out"
    summary = run_skill(
        template=UNSEEN_TEMPLATE_SPARSE,
        content=CONTENT_MD,
        out_dir=out_dir,
        target_slide_count=10,
    )
    # the sparse template honestly drops planned text units on the
    # denser strategies — those variants are degraded, not failed, and
    # the drop is disclosed; a failed variant would fail this outright
    assert summary["status"] in ("ok", "partial"), summary
    assert {v["strategy"] for v in summary["variants"]} == {"faithful", "balanced", "visual"}
    for variant in summary["variants"]:
        assert variant["status"] in ("ok", "degraded")
        assert isinstance(variant["dropped_units"], dict)
        assert variant["artifacts"]["html"] is not None

    checksums = {v["strategy"]: v["artifacts"]["pptx"]["sha256"] for v in summary["variants"]}
    assert len(set(checksums.values())) == 3, checksums  # three distinct pptx files


@needs_render
def test_run_skill_complete_deck_positive_acceptance(tmp_path):
    """Strict positive bar: content that fits must produce three clean
    "ok" variants — no drops, no degradation, real artifacts. If the
    runtime could never produce a complete deck this fails outright."""
    summary = run_skill(
        template=UNSEEN_TEMPLATE,
        content=_fitting_content(tmp_path),
        out_dir=tmp_path / "out",
        target_slide_count=10,
    )
    assert summary["status"] == "ok", summary
    assert len(summary["variants"]) == 3
    for variant in summary["variants"]:
        assert variant["status"] == "ok", variant
        assert variant["dropped_units"] == {}, variant
        assert variant["slides_out"] > 0
        for artifact in ("pptx", "pdf", "html", "quality_passport"):
            assert variant["artifacts"][artifact] is not None, artifact
        editability = variant["editability"]
        assert editability["native_text_ratio"] is not None
        assert editability["native_text_ratio"] > 0


def test_run_skill_ambiguous_content_produces_no_giant_json(tmp_path):
    """Cheap regression guard (no render needed): summary.json stays small
    even on failure — nothing bulky ever gets embedded in it."""
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / "a.md").write_text("a")
    (content_dir / "b.md").write_text("b")
    out_dir = tmp_path / "out"
    run_skill(template=UNSEEN_TEMPLATE, content=content_dir, out_dir=out_dir)
    assert (out_dir / "summary.json").stat().st_size < 4096


# --------------------------------------------------------------------------
# Portability: runs correctly invoked as a subprocess from another cwd
# --------------------------------------------------------------------------


@needs_render
def test_run_skill_subprocess_from_another_cwd(tmp_path):
    """The acceptance bar is explicit: must work run from a cwd other than
    the repo root, with no PYTHONPATH set by the caller."""
    other_cwd = tmp_path / "somewhere-else"
    other_cwd.mkdir()
    out_dir = tmp_path / "out"

    proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell, test-only
        [
            sys.executable,
            str(_REPO_ROOT / "skill" / "run_skill.py"),
            str(UNSEEN_TEMPLATE),
            str(CONTENT_MD),
            str(out_dir),
            "--slides",
            "10",
            "--strategies",
            "faithful",
        ],
        cwd=other_cwd,
        capture_output=True,
        text=True,
        timeout=180,
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] in ("ok", "partial")
    # nonzero exit is the documented honest signal for a degraded run
    assert proc.returncode == (0 if payload["status"] == "ok" else 1)
    assert (out_dir / "summary.json").exists()


def test_run_skill_subprocess_invalid_template_nonzero_exit(tmp_path):
    """No render needed: a bad template path must exit non-zero and print
    a typed error, not a bare traceback."""
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(_REPO_ROOT / "skill" / "run_skill.py"),
            str(tmp_path / "nope.pptx"),
            str(CONTENT_MD),
            str(tmp_path / "out"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["error"]["code"] == "invalid_input"


# --------------------------------------------------------------------------
# --llm: default mock provider (deterministic, offline) + a real local
# OpenAI-compatible HTTP stub (no external key, no network)
# --------------------------------------------------------------------------


@needs_render
def test_run_skill_llm_flag_with_default_mock_provider(tmp_path):
    """`--llm` with the default DECKDNA_MOCK_PROVIDER=true must exercise
    the LLM/VLM code path fully offline and still complete the run."""
    summary = run_skill(
        template=UNSEEN_TEMPLATE,
        content=_fitting_content(tmp_path),
        out_dir=tmp_path / "out",
        target_slide_count=10,
        use_llm=True,
        strategies=[Strategy.faithful],
    )
    assert summary["status"] == "ok", summary
    assert summary["llm_enabled"] is True
    variant = summary["variants"][0]
    assert variant["status"] == "ok"
    assert variant["dropped_units"] == {}
    assert variant["contextual_audit"]["ran"] is True


@needs_render
def test_run_skill_llm_over_real_local_http_no_secret_leak(tmp_path, monkeypatch, local_openai):
    """Real TCP round trip to a local OpenAI-compatible stub (conftest's
    `local_openai`, same fixture tests/providers/test_llm_http_e2e.py
    uses) — proves the --llm wiring for real, and that the fake API key
    never appears anywhere in the skill's own JSON output.

    Пин на v1 — тест проверяет storyline-специфичную схему
    (SlideOutlineBatch) over real HTTP; v3 (дефолт с 27.09, ADR-016)
    использует deck_structure/content_writer/layout_fit."""
    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v1")
    content_file = _fitting_content(tmp_path)
    pack = parse_file(content_file)
    graph = build_evidence_graph(pack)
    claim_id = next(n.id for n in graph.nodes if n.type.value == "claim")
    # Stub отдаёт ОДИН ответ на все storyline-вызовы независимо от того,
    # какой батч индексов реально запросили (plan_deck_llm шлёт несколько
    # storyline-вызовов, по батчу слайдов на вызов) — поэтому список
    # покрывает весь диапазон индексов колоды разом.
    local_openai.responses["SlideOutlineBatch"] = {
        "slides": [
            {
                "index": i,
                "purpose": "overview",
                "title_intent": f"LLM-вывод {i}",
                "key_message": "Заключение по данным.",
                "evidence_ids": [claim_id],
            }
            for i in range(10)
        ]
    }
    local_openai.responses["SlideChecksResult"] = {
        "verdicts": [
            {"check": "5", "verdict": "fail", "rationale": "stub", "confidence": 0.9}
        ]
    }
    monkeypatch.setattr(
        factory_module,
        "settings",
        Settings(
            mock_provider=False,
            provider_base_url=local_openai.base_url,
            provider_api_key=API_KEY,
            model_text="stub-text-model",
            model_vision="stub-vision-model",
        ),
    )

    summary = run_skill(
        template=UNSEEN_TEMPLATE,
        content=content_file,
        out_dir=tmp_path / "out",
        target_slide_count=10,
        use_llm=True,
        strategies=[Strategy.faithful],
    )

    assert local_openai.requests, "gateway never actually called the local server"
    assert all(r["authorization"] == f"Bearer {API_KEY}" for r in local_openai.requests)

    # strict positive bar over real TCP: the stub's plan (built from
    # THIS brief's fitting content) must drive a complete clean deck —
    # LLM planner accepted, VLM contextual audit ran, zero drops
    assert summary["status"] == "ok", summary
    assert len(summary["variants"]) == 1
    variant = summary["variants"][0]
    assert variant["status"] == "ok"
    assert variant["dropped_units"] == {}
    assert variant["planner"] == LLM_PLANNER_VERSION
    assert variant["contextual_audit"]["ran"] is True
    for artifact in ("pptx", "pdf", "html", "quality_passport"):
        assert variant["artifacts"][artifact] is not None, artifact

    # real editability, beyond zip validity: native text objects exist
    editability = variant["editability"]
    assert editability["pei_level"] is not None
    assert editability["native_text_ratio"] is not None
    assert editability["native_text_ratio"] > 0

    dump = json.dumps(summary, ensure_ascii=False)
    assert API_KEY not in dump
    assert local_openai.base_url not in dump
    on_disk = (tmp_path / "out" / "summary.json").read_text(encoding="utf-8")
    assert API_KEY not in on_disk
    assert local_openai.base_url not in on_disk
