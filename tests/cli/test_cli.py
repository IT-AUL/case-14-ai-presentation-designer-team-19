"""CLI commands: inspect-template and export.

inspect-template needs no external tools; export tests are gated on
soffice/pdftoppm presence (skipped on machines without LibreOffice).
"""

import json
import shutil
import zipfile
from pathlib import Path

import pytest
from deckdna.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

SUBMISSION = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "pptx"
    / "lct2026_submission.pptx"
)
FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "pptx" / "vk_tech_template.pptx"
)

HAS_SOFFICE = shutil.which("soffice") is not None
HAS_PDFTOPPM = shutil.which("pdftoppm") is not None
requires_render = pytest.mark.skipif(
    not (HAS_SOFFICE and HAS_PDFTOPPM), reason="soffice/pdftoppm not installed"
)


@pytest.fixture(scope="module")
def template() -> Path:
    if not FIXTURE.exists():
        pytest.skip("vk_tech_template fixture not present")
    return FIXTURE


def test_inspect_template_prints_forensics_json(template):
    result = runner.invoke(app, ["inspect-template", str(template)])
    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["slides"] == 54
    assert report["masters"] >= 1
    assert report["layouts"] >= 1
    assert report["layout_usage"]
    assert report["declared_fonts"]
    assert report["dominant_layout"]


def test_inspect_template_out_writes_file(template, tmp_path):
    out = tmp_path / "report" / "forensics.json"
    result = runner.invoke(app, ["inspect-template", str(template), "--out", str(out)])
    assert result.exit_code == 0, result.stdout
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk["slides"] == 54
    assert json.loads(result.stdout) == on_disk


def test_inspect_template_corrupt_package(tmp_path):
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"not a zip")
    result = runner.invoke(app, ["inspect-template", str(bad)])
    assert result.exit_code == 1
    envelope = json.loads(result.stdout)
    assert envelope["error"]["code"] == "package_corrupt"


def test_inspect_template_missing_file(tmp_path):
    result = runner.invoke(app, ["inspect-template", str(tmp_path / "nope.pptx")])
    assert result.exit_code != 0  # typer argument validation


def test_export_rejects_unknown_format(template, tmp_path):
    result = runner.invoke(
        app, ["export", str(template), "--format", "gif", "--out", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "invalid_input"


def test_export_reports_render_error_without_tools(template, tmp_path, monkeypatch):
    """Without soffice on PATH the command must emit the typed envelope."""
    if HAS_SOFFICE:
        monkeypatch.setattr(shutil, "which", lambda tool: None)
    result = runner.invoke(
        app, ["export", str(template), "--format", "pdf", "--out", str(tmp_path / "x.pdf")]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "render_failed"


@requires_render
def test_export_pdf_real(template, tmp_path):
    out = tmp_path / "deck.pdf"
    result = runner.invoke(
        app, ["export", str(template), "--format", "pdf", "--out", str(out)]
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["format"] == "pdf"
    assert out.exists()
    assert zipfile.is_zipfile(template)
    assert out.read_bytes()[:5] == b"%PDF-"


@requires_render
def test_export_png_real(template, tmp_path):
    out_dir = tmp_path / "slides"
    result = runner.invoke(
        app, ["export", str(template), "--format", "png", "--out", str(out_dir)]
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["format"] == "png"
    assert report["slides"], "expected rendered slide PNGs"
    first = Path(report["slides"][0])
    assert first.exists() and first.suffix == ".png"


@requires_render
def test_export_html_real(template, tmp_path):
    # тяжёлая organizer-фикстура (54 слайда, реальные картинки): html — директория
    # с index.html + slide-*.png, без giant base64 blob (libxml2 limit fix)
    out_dir = tmp_path / "deck-html"
    result = runner.invoke(
        app, ["export", str(template), "--format", "html", "--out", str(out_dir)]
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["format"] == "html"
    index = Path(report["index"])
    assert index.exists() and index.name == "index.html"
    slides = sorted(out_dir.glob("slide-*.png"))
    assert len(slides) == 54  # вся фикстура отрендерилась
    html = index.read_text(encoding="utf-8")
    assert "data:image" not in html  # ассеты — отдельные файлы, не base64
    assert f'src="{slides[0].name}"' in html
    # реальный текст презентации присутствует как captions
    assert "Спасибо за внимание" in html or "Дополнительная информация" in html


def test_stub_commands_still_present():
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert "not implemented" in result.stdout


# --- generate ---

CONTENT_MD = (
    Path(__file__).resolve().parents[1] / "fixtures" / "content" / "poc_article.md"
)


@requires_render
def test_generate_end_to_end(template, tmp_path):
    out_dir = tmp_path / "gen"
    result = runner.invoke(
        app,
        [
            "generate",
            str(template),
            str(CONTENT_MD),
            str(out_dir),
            "--purpose",
            "Показать сквозной пайплайн",
            "--audience",
            "эксперты VK Tech",
            "--slides",
            "10",
        ],
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["slides"] == 10
    for key in ("pptx", "pdf", "quality_passport"):
        assert Path(report["artifacts"][key]).exists()
    assert report["editability"]["pei_level"] is not None
    assert report["issues"]["total"] == sum(report["issues"]["by_severity"].values())


def test_generate_bad_slide_count(template, tmp_path):
    # Brief.target_slide_count is ge=3/le=40 (deck_plan.py) -- 3 is a
    # deliberately supported short-deck size now ("any input, any
    # template", 29.09), not below-minimum. 2 is genuinely out of bounds.
    result = runner.invoke(
        app, ["generate", str(template), str(CONTENT_MD), str(tmp_path), "--slides", "2"]
    )
    assert result.exit_code == 1
    envelope = json.loads(result.stdout)
    assert envelope["error"]["code"] == "invalid_input"
    assert envelope["error"]["stage"] == "planning"


# --- audit ---

AUDIT_GOLDEN = {
    # vk_tech_template.pptx is byte-identical to vk_tech.pptx (md5-verified),
    # so the golden counts from tests/audit/test_golden_organizer_templates.py
    # apply verbatim.
    "vk_tech_template.pptx": {
        "text.overflow": 66,
        "image.aspect_ratio": 75,
        "integrity.duplicate_slide": 8,
        "layout.edge_margin": 6,
        "layout.out_of_bounds": 7,
        "integrity.placeholder_text": 271,
        "layout.unintended_overlap": 7,
        "text.font_floor": 67,
        "accessibility.contrast": 128,
        "density.occupancy": 11,
        "template.font_scale": 2,  # Q1: наблюдаемая шкала; было 418
        "template.color_palette": 40,
    },
    "vk_workspace.pptx": {
        "text.overflow": 11,
        "integrity.duplicate_slide": 8,
        "layout.edge_margin": 2,
        "layout.out_of_bounds": 2,
        "integrity.placeholder_text": 137,
        "layout.unintended_overlap": 3,
        # Alpha-compositing (ad87cf9, 28.09) now finds contrast hidden
        # behind semi-transparent fills -- was 21 before that rule got
        # more accurate.
        "accessibility.contrast": 29,
        "density.table_size": 1,
        "density.occupancy": 5,
        "template.font_scale": 1,  # Q1; было 53
        "template.color_palette": 49,
        "text.slide_clip": 1,
    },
    "lct2026_submission.pptx": {
        "text.overflow": 11,
        "image.aspect_ratio": 5,
        "integrity.empty_slide": 1,
        "layout.edge_margin": 3,
        "layout.out_of_bounds": 3,
        "layout.unintended_overlap": 4,
        "accessibility.contrast": 6,
        "density.occupancy": 1,
        "template.color_palette": 1,
        "chart.metadata": 2,
        "template.anchor_position": 3,
    },
}


def _fixture(name: str) -> Path:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "pptx" / name
    if not path.exists():
        pytest.skip(f"fixture missing: {path}")
    return path


@pytest.mark.parametrize("fixture,expected", AUDIT_GOLDEN.items(), ids=list(AUDIT_GOLDEN))
def test_audit_matches_golden_counts(fixture, expected):
    result = runner.invoke(app, ["audit", str(_fixture(fixture))])
    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["summary"]["by_rule_code"] == expected
    assert report["issue_count"] == len(report["issues"]) == sum(expected.values())
    assert report["summary"]["by_severity"]  # every issue lands in a severity bucket
    issue = report["issues"][0]
    assert issue["rule_code"] in expected
    assert issue["schema_version"] == "1.0"
    assert issue["deterministic"] is True


def test_audit_out_writes_file(tmp_path):
    out = tmp_path / "audit.json"
    result = runner.invoke(
        app, ["audit", str(_fixture("vk_tech_template.pptx")), "--out", str(out)]
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout) == json.loads(out.read_text(encoding="utf-8"))


def test_audit_corrupt_package(tmp_path):
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"not a zip")
    result = runner.invoke(app, ["audit", str(bad)])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "package_corrupt"


def test_audit_missing_file(tmp_path):
    result = runner.invoke(app, ["audit", str(tmp_path / "nope.pptx")])
    assert result.exit_code != 0


# --- ingest ---

CONTENT_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "content"

VALID_PACK_JSON = {
    "schema_version": "1.0",
    "id": "pack-demo",
    "language": "ru",
    "sections": [
        {
            "id": "s1",
            "heading": "Проблема",
            "level": 1,
            "blocks": [
                {
                    "kind": "paragraph",
                    "text": "Ручная сборка колод занимает часы.",
                    "source_ref": {"artifact_id": "src.docx"},
                }
            ],
        }
    ],
    "tables": [],
    "assets": [],
    "warnings": [],
}


def test_ingest_markdown_fixture():
    md = CONTENT_FIXTURES / "sample_brief.md"
    if not md.exists():
        pytest.skip("sample_brief.md fixture missing")
    result = runner.invoke(app, ["ingest", str(md)])
    assert result.exit_code == 0, result.stdout
    pack = json.loads(result.stdout)
    assert pack["schema_version"] == "1.0"
    assert pack["language"] == "ru"
    assert pack["title_hint"] == "Квартальный отчёт"
    headings = [s["heading"] for s in pack["sections"]]
    assert "Метрики" in headings and "Выводы" in headings
    list_blocks = [
        b for s in pack["sections"] for b in s["blocks"] if b["kind"] == "list"
    ]
    assert list_blocks and len(list_blocks[0]["items"]) == 3
    # canonical serializer: optional fields are absent, not null
    for section in pack["sections"]:
        assert "level" not in section or isinstance(section["level"], int)


def test_ingest_json_content_pack(tmp_path):
    src = tmp_path / "pack.json"
    src.write_text(json.dumps(VALID_PACK_JSON), encoding="utf-8")
    result = runner.invoke(app, ["ingest", str(src)])
    assert result.exit_code == 0, result.stdout
    pack = json.loads(result.stdout)
    assert pack["id"] == "pack-demo"
    assert pack["sections"][0]["heading"] == "Проблема"


def test_ingest_txt(tmp_path):
    src = tmp_path / "note.txt"
    src.write_text("строка раз\nстрока два", encoding="utf-8")
    result = runner.invoke(app, ["ingest", str(src)])
    assert result.exit_code == 0, result.stdout
    pack = json.loads(result.stdout)
    assert len(pack["sections"]) == 1
    assert pack["sections"][0]["blocks"][0]["kind"] == "paragraph"
    assert "строка раз" in pack["sections"][0]["blocks"][0]["text"]


def test_ingest_out_writes_file(tmp_path):
    md = CONTENT_FIXTURES / "sample_brief.md"
    if not md.exists():
        pytest.skip("sample_brief.md fixture missing")
    out = tmp_path / "pack.json"
    result = runner.invoke(app, ["ingest", str(md), "--out", str(out)])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout) == json.loads(out.read_text(encoding="utf-8"))


def test_ingest_unsupported_extension(tmp_path):
    src = tmp_path / "data.xlsx"
    src.write_bytes(b"pk")
    result = runner.invoke(app, ["ingest", str(src)])
    assert result.exit_code == 1
    envelope = json.loads(result.stdout)
    assert envelope["error"]["code"] == "invalid_input"


def test_ingest_broken_json(tmp_path):
    src = tmp_path / "bad.json"
    src.write_text("{nope", encoding="utf-8")
    result = runner.invoke(app, ["ingest", str(src)])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "invalid_input"


def test_ingest_missing_file(tmp_path):
    result = runner.invoke(app, ["ingest", str(tmp_path / "nope.md")])
    assert result.exit_code != 0


# --- benchmark ---

PPTX_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pptx"
ORGANIZER_NAMES = ("vk_tech.pptx", "vk_workspace.pptx", "vk_education.pptx")
UNSEEN_NAME = "synthetic_unseen.pptx"
CONTENT_MD = CONTENT_FIXTURES / "sample_brief.md"

requires_organizer_fixtures = pytest.mark.skipif(
    not all((PPTX_FIXTURES / name).exists() for name in ORGANIZER_NAMES)
    or not CONTENT_MD.exists(),
    reason="organizer pptx/content fixtures missing",
)


@requires_render
@requires_organizer_fixtures
def test_benchmark_all_templates_within_budget(tmp_path):
    report_path = tmp_path / "report.json"
    result = runner.invoke(
        app,
        [
            "benchmark",
            str(CONTENT_MD),
            "--fixtures-dir",
            str(PPTX_FIXTURES),
            "--work-dir",
            str(tmp_path / "work"),
            "--out",
            str(report_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    for name in (*ORGANIZER_NAMES, UNSEEN_NAME):
        assert name in result.stdout
    assert "budget=300s" in result.stdout

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["budget_seconds"] == 300.0
    # unseen fixture joins the run when present in fixtures-dir
    assert [row["template"] for row in report["templates"]] == [*ORGANIZER_NAMES, UNSEEN_NAME]
    for row in report["templates"]:
        assert row["status"] == "ok", row
        assert row["verdict"] == (
            "ok" if row["duration_seconds"] <= report["budget_seconds"] else "over_budget"
        )
        assert row["duration_seconds"] < report["budget_seconds"]
        assert row["verdict"] == "ok"
        assert isinstance(row["slides_out"], int)
        # Sanity on the field's shape (a dict of known severities with
        # non-negative counts) -- NOT "must contain 'error' or be empty".
        # That stronger assertion broke live: capacity-aware exemplar
        # selection (unit_counts= on select_exemplar_slides, using the
        # real fillable-slot count instead of the long_runs proxy) fixed
        # a genuine integrity.empty_slide error on synthetic_unseen.pptx
        # -- a deck that's down to warnings only is a real improvement,
        # not a shape the test should refuse to accept.
        assert isinstance(row["issues_by_severity"], dict)
        assert set(row["issues_by_severity"]) <= {"blocker", "error", "warning", "info"}
        assert all(n > 0 for n in row["issues_by_severity"].values())
    assert report["summary"] == {"ok": 4}


@requires_render
@requires_organizer_fixtures
def test_benchmark_verdict_over_budget(tmp_path):
    result = runner.invoke(
        app,
        [
            "benchmark",
            str(CONTENT_MD),
            "--fixtures-dir",
            str(PPTX_FIXTURES),
            "--work-dir",
            str(tmp_path / "work"),
            "--out",
            str(tmp_path / "report.json"),
            "--budget",
            "0",
        ],
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    for row in report["templates"]:
        if row["status"] == "ok":
            assert row["verdict"] == "over_budget"


@requires_render
@requires_organizer_fixtures
def test_benchmark_missing_template_gives_partial_report(tmp_path):
    # только один шаблон в fixtures-dir: остальные два — error-строки, прогон не падает
    only_dir = tmp_path / "fixtures"
    only_dir.mkdir()
    (only_dir / "vk_tech.pptx").write_bytes((PPTX_FIXTURES / "vk_tech.pptx").read_bytes())
    result = runner.invoke(
        app,
        [
            "benchmark",
            str(CONTENT_MD),
            "--fixtures-dir",
            str(only_dir),
            "--work-dir",
            str(tmp_path / "work"),
            "--out",
            str(tmp_path / "report.json"),
            "--budget",
            "0",
        ],
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    by_name = {row["template"]: row for row in report["templates"]}
    assert by_name["vk_workspace.pptx"]["status"] == "error"
    assert by_name["vk_education.pptx"]["status"] == "error"
    assert by_name["vk_workspace.pptx"]["error"]["code"] == "invalid_input"


# --- repair ---


def test_repair_default_out_suffix(template, tmp_path):
    """deckdna repair: issues до/после + счётчики applied/skipped/failed."""
    import shutil as _sh

    src = tmp_path / "deck.pptx"
    _sh.copy(template, src)
    result = runner.invoke(app, ["repair", str(src)])
    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    repaired = src.with_name("deck-repaired.pptx")
    assert report["out"] == str(repaired)
    assert repaired.exists()
    assert report["issues"]["before"] > 0
    # применённые действия реально уменьшают число issues
    assert report["issues"]["after"] < report["issues"]["before"]
    actions = report["actions"]
    assert actions["applied"] > 0
    assert actions["total"] == actions["applied"] + actions["skipped"] + actions[
        "failed"
    ] + actions["not_implemented"]
    assert actions["by_type"]


def test_repair_explicit_out(template, tmp_path):
    src = tmp_path / "deck.pptx"
    src.write_bytes(template.read_bytes())
    out = tmp_path / "fixed" / "out.pptx"
    result = runner.invoke(app, ["repair", str(src), "--out", str(out)])
    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["out"] == str(out) and out.exists()
    # --out не создаёт лишний -repaired рядом
    assert not src.with_name("deck-repaired.pptx").exists()


def test_repair_corrupt_package(tmp_path):
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"not a zip")
    result = runner.invoke(app, ["repair", str(bad)])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "package_corrupt"


def test_repair_protected_slides_flag(tmp_path):
    """--protected-slides: действия на защищённых слайдах не применяются,
    сами слайды остаются нетронутыми (OR-031 через CLI)."""
    from pptx import Presentation

    src = tmp_path / "deck.pptx"
    src.write_bytes(SUBMISSION.read_bytes())
    out = tmp_path / "fixed.pptx"
    result = runner.invoke(
        app, ["repair", str(src), "--protected-slides", "6,7,8,9,10", "-o", str(out)]
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["protected_indices"] == [6, 7, 8, 9, 10]
    skipped = report["actions"]["skipped"]
    assert skipped > 0, "ожидались действия, отклонённые гардом"

    before = Presentation(str(src))
    fixed = Presentation(str(out))
    for i in [6, 7, 8, 9, 10]:
        assert fixed.slides[i].element.xml == before.slides[i].element.xml


def test_repair_protected_slides_config_fallback(tmp_path):
    """Без флага — protected_slide_indices из generation config
    (в дефолтном configs/generation.default.yaml пуст → все применяются)."""
    src = tmp_path / "deck.pptx"
    src.write_bytes(SUBMISSION.read_bytes())
    result = runner.invoke(app, ["repair", str(src), "-o", str(tmp_path / "o.pptx")])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["protected_indices"] == []


def test_repair_protected_slides_bad_value(tmp_path):
    src = tmp_path / "deck.pptx"
    src.write_bytes(SUBMISSION.read_bytes())
    result = runner.invoke(
        app, ["repair", str(src), "--protected-slides", "a,b"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "invalid_input"
