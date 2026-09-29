"""OR-016: статическая проверка заявленных моделей на лицензию/размер.

ТЗ: open weights, Apache-2.0/MIT, ≤35B (image-модели ≤20B). Модели в
прогонах сегодня не вызываются — но идентификаторы уже задекларированы
в `configs/models.example.yaml` и `providers/vk_inference.py`; проверка
сверяет каждый такой идентификатор с ручным манифестом
`configs/model_licenses.yaml` (модель, лицензия, параметры, источник).

Проверка падает (violations), если:
- лицензия модели не входит в policy.allowed_licenses;
- size_b больше капа модальности (35B, image — 20B);
- модель задекларирована в коде/конфиге, но отсутствует в манифесте
  (незадекларированная).

Поля `unverified` (нет публичной карточки — напр. VK-hosted `qwen3.8-27b`
из ТЗ) — warnings, не violations: честно зафиксировано, без угадывания.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MANIFEST_PATH = Path("configs/model_licenses.yaml")
PROVIDERS_GLOB = "backend/deckdna/providers/*.py"
MODELS_PROFILE = Path("configs/models.example.yaml")

_DEFAULT_MODEL_RE = re.compile(r"\bDEFAULT_\w*MODEL\w*\s*=\s*[\"']([^\"']+)[\"']")
_ENV_DEFAULT_RE = re.compile(r"\$\{[^}:]+:-([^}]*)\}")

_UNVERIFIED = "unverified"


@dataclass
class ModelCheck:
    """Итог проверки одной модели манифеста."""

    model_id: str
    license: str
    size_b: Any
    modality: str
    status: str  # ok | warning | violation
    problems: list[str] = field(default_factory=list)


@dataclass
class LicenseReport:
    """Сводка проверки манифеста + покрытие деклараций кодом."""

    checks: list[ModelCheck] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    declared_missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations and not self.declared_missing


def _resolve_env_default(raw: str) -> str:
    """`${VAR:-default}` → default; пустое значение → ''."""
    m = _ENV_DEFAULT_RE.search(raw or "")
    return m.group(1).strip() if m else (raw or "").strip()


def collect_declared_models(root: Path | str = ".") -> dict[str, str]:
    """Идентификаторы моделей, задекларированные в коде/конфигах.

    Источники: `configs/models.example.yaml` (models.*.model_id,
    env-default часть `${VAR:-...}`) и `DEFAULT_*_MODEL`-константы в
    `backend/deckdna/providers/*.py`. Возвращает id → источник.
    """
    root = Path(root)
    declared: dict[str, str] = {}

    profile = root / MODELS_PROFILE
    if profile.exists():
        data = yaml.safe_load(profile.read_text()) or {}
        models = data.get("models") or {}
        if isinstance(models, dict):
            for role, spec in models.items():
                if not isinstance(spec, dict):
                    continue
                model_id = _resolve_env_default(str(spec.get("model_id") or ""))
                if model_id:
                    declared[model_id] = f"{MODELS_PROFILE} (models.{role})"

    for path in sorted(root.glob(PROVIDERS_GLOB)):
        for match in _DEFAULT_MODEL_RE.finditer(path.read_text()):
            declared.setdefault(match.group(1), f"{path} (constant)")

    return declared


def load_manifest(path: Path | str = MANIFEST_PATH) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"model license manifest missing: {path}")
    return yaml.safe_load(path.read_text()) or {}


def check(
    manifest_path: Path | str = MANIFEST_PATH,
    root: Path | str = ".",
) -> LicenseReport:
    """Проверить манифест и покрытие заявленных моделей."""
    manifest = load_manifest(manifest_path)
    policy = manifest.get("policy") or {}
    allowed = {str(lic).lower() for lic in policy.get("allowed_licenses") or []}
    cap_default = float(policy.get("max_size_b", 35))
    cap_image = float(policy.get("max_image_size_b", 20))
    entries = manifest.get("models") or []

    report = LicenseReport()
    for entry in entries:
        model_id = str(entry.get("model_id") or "")
        license_ = str(entry.get("license") or "").lower()
        size_b = entry.get("size_b")
        modality = str(entry.get("modality") or "text")
        problems: list[str] = []

        if license_ == _UNVERIFIED:
            report.warnings.append(f"{model_id}: license unverified")
        elif license_ not in allowed:
            problems.append(
                f"license '{license_}' not in allowed {sorted(allowed)}"
            )

        cap = cap_image if modality == "image" else cap_default
        if size_b in (None, _UNVERIFIED):
            report.warnings.append(f"{model_id}: size_b unverified")
        else:
            try:
                size = float(size_b)
            except (TypeError, ValueError):
                problems.append(f"size_b '{size_b}' is not a number")
            else:
                if size > cap:
                    problems.append(f"size_b {size} > {cap} ({modality} cap)")

        status = "violation" if problems else "ok"
        if problems:
            report.violations.extend(f"{model_id}: {p}" for p in problems)
        report.checks.append(
            ModelCheck(
                model_id=model_id,
                license=license_,
                size_b=size_b,
                modality=modality,
                status=status,
                problems=problems,
            )
        )

    in_manifest = {c.model_id for c in report.checks}
    for model_id, where in sorted(collect_declared_models(root).items()):
        if model_id not in in_manifest:
            report.declared_missing.append(f"{model_id} (declared in {where})")

    return report


def format_report(report: LicenseReport) -> str:
    """Человекочитаемая сводка для CLI/скрипта."""
    lines = ["model_licenses check"]
    for c in report.checks:
        lines.append(
            f"  {c.status:9} {c.model_id:28} lic={c.license:12} "
            f"size={c.size_b}B mod={c.modality}"
            + (f"  <-- {'; '.join(c.problems)}" if c.problems else "")
        )
    for w in report.warnings:
        lines.append(f"  warning   {w}")
    for m in report.declared_missing:
        lines.append(f"  VIOLATION {m} — not in manifest")
    verdict = "OK" if report.ok else "FAILED"
    lines.append(f"verdict: {verdict}")
    return "\n".join(lines)
