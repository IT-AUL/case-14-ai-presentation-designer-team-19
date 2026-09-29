"""OR-016: манифест моделей валиден и покрывает все заявленные id."""

from pathlib import Path

import yaml
from deckdna.evaluation.model_licenses import (
    check,
    collect_declared_models,
    load_manifest,
)

ROOT = Path(".").resolve()
MANIFEST = ROOT / "configs/model_licenses.yaml"


def test_manifest_exists_and_parses():
    data = load_manifest(MANIFEST)
    assert data["policy"]["allowed_licenses"] == ["apache-2.0", "mit"]
    assert len(data["models"]) >= 4


def test_all_declared_models_covered_and_valid():
    report = check(MANIFEST, ROOT)
    assert not report.declared_missing, report.declared_missing
    assert not report.violations, report.violations


def test_declared_model_scan_finds_config_and_provider_ids():
    declared = collect_declared_models(ROOT)
    for model_id in ("qwen2.5-32b-instruct", "qwen2.5-vl-32b-instruct", "bge-m3"):
        assert model_id in declared
    assert "qwen3.8-27b" in declared  # DEFAULT_VK_MODEL


def _write_manifest(tmp_path, models):
    path = tmp_path / "manifest.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "policy": {
                    "allowed_licenses": ["apache-2.0", "mit"],
                    "max_size_b": 35,
                    "max_image_size_b": 20,
                },
                "models": models,
            }
        )
    )
    return path


def test_bad_license_is_violation(tmp_path):
    path = _write_manifest(
        tmp_path,
        [{"model_id": "m1", "license": "openrail", "size_b": 10, "modality": "text"}],
    )
    report = check(path, tmp_path)
    assert any("license" in v for v in report.violations)


def test_oversize_is_violation(tmp_path):
    path = _write_manifest(
        tmp_path,
        [
            {"model_id": "big", "license": "mit", "size_b": 70, "modality": "text"},
            {"model_id": "img", "license": "mit", "size_b": 25, "modality": "image"},
        ],
    )
    report = check(path, tmp_path)
    assert len(report.violations) == 2  # 70>35 и 25>20 image-cap


def test_undeclared_code_model_is_violation(tmp_path):
    path = _write_manifest(tmp_path, [])
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "models.example.yaml").write_text(
        'models:\n  text:\n    model_id: ghost-70b\n'
    )
    report = check(path, tmp_path)
    assert any("ghost-70b" in m for m in report.declared_missing)
    assert not report.ok


def test_unverified_is_warning_not_violation(tmp_path):
    path = _write_manifest(
        tmp_path,
        [{"model_id": "vk-x", "license": "unverified", "size_b": "unverified", "modality": "text"}],
    )
    report = check(path, tmp_path)
    assert report.ok
    assert len(report.warnings) == 2
