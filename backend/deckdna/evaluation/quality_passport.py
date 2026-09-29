"""Quality Passport assembly v0 — honest metrics only (docs/ARCHITECTURE.md).

``assemble_quality_passport`` склеивает уже существующие честные измерения
в один QualityPassport (contracts/quality_passport.py):

Реально посчитанные поля:
- ``metrics.validity.opens_cleanly`` — из PEI: пакет открылся как OPC zip;
- ``metrics.editability.pei_level`` — ``pptx/validation/pei.assess_pptx``;
- ``metrics.editability.raster_only_slides`` — кол-во issues
  ``editability.raster_only`` из ``audit_deck``;
- ``metrics.editability.native_text_ratio`` — доля текстовых фигур среди
  контентных фигур по фактам PEI (text_shapes / (text+vector+pictures));
- ``metrics.readability.overflow_count`` — кол-во issues ``text.overflow``;
- ``metrics.content_support.supported_claims`` / ``unsupported_claims`` —
  ГРУБЫЙ прокси: runs_replaced / runs_skipped из отчёта компиляции
  (заменённые тексты vs. юниты, не поместившиеся в ран-слоты) —
  плюс ``compose_report.dropped_units``: юниты table/chart/image/...,
  которые план просил, но эта стадия компиляции пока не умеет
  компоновать. Оба — «контент запрошен, в вывод не попал», поэтому
  честно складываются в unsupported_claims;
  после пользовательского repair оба поля становятся ``None``, так как
  статистика compose не пересчитывается для новой ревизии;
- ``fallbacks`` — по одной записи на каждый kind из dropped_units
  (strategy="drop", disclosure — тот же текст, что в warnings отчёта):
  видимая деградация с обоснованием внутри паспорта. Плюс одна
  агрегированная запись на остаточные ``integrity.placeholder_text``
  issues (strategy="retain"): слоты, оставшиеся со стоковым текстом
  шаблона — прямой сигнал деградации content support;
  в ``unsupported_claims`` они НЕ складываются: там знаменатель —
  контентные юниты плана, а тут — фигуры, и пересечение с
  runs_skipped без связки slot↔unit непроверяемо (двойной счёт);
- ``issues_summary`` — разбивка audit-issues по severity + unresolved;
- ``metrics.timings`` / ``exports`` — если переданы/файл существует;
- ``provenance.skill_version`` — ``version`` из skill/manifest.yaml
  (package-relative path, как api/app.py:_manifest_dict);
- ``provenance.config_hashes`` — sha256 реально использованных в прогоне
  конфигов: generation.default.yaml и audit.default.yaml (те же
  DEFAULT_*_PATH, что грузят load_generation_config/default_audit_config).

Правдивые provenance-поля (pipeline передаёт их собранными):
- ``prompt_versions`` — registry-имя → версия только для промптов,
  чья стадия реально сходила в модель И была принята (storyline при
  принятом LLM-плане, contextual_slide_audit при отработавшем VLM-аудите,
  exemplar_rerank при принятом rerank'е);
- ``model_profiles`` — model_id только успешно завершённых вызовов,
  чья стадия принята (provider.used_model_ids × принятые стадии),
  provider — sanitized host[:port]; license/size_b из
  configs/model_licenses.yaml, модель вне manifest-а — честные None;
- на детерминированном прогоне оба поля честно пусты — выдумывать
  промпты/модели нельзя.

Реально замеряется дополнительно:
- ``validity.round_trip_ok`` — повторное открытие выходного .pptx через
  python-pptx + совпадение числа слайдов с compose_report;
- ``validity.ooxml_errors`` — пакетный sanity (не XSD-валидация):
  все xml/rels части парсятся, sldIdLst ↔ slide parts, dangling rels,
  Content_Types покрытие; детали не попадают в паспорт — в контракте
  только счётчик;
- ``readability.contrast_failures`` — счётчик accessibility.contrast.

Считается в deckdna.evaluation.measures (contract D3):
- ``style_fidelity`` — доли слайдов без нарушений соответствия шаблону
  (палитра / шрифты / происхождение макета / якоря);
- ``content_support.numbers_verified/failed`` — legacy-имена счётчиков
  глобального numeric presence в тексте источника; это не проверка
  утверждения, сущности или арифметики. Ограничение явно раскрывается
  в ``fallbacks`` паспорта;
- ``readability.avg_occupancy`` — средняя заполненность слайда;
- ``timings.per_stage`` — время по этапам конвейера;
- ``usage`` — вызовы/токены модели за прогон (нули без модели).

Оставлено None (считать не из чего):
- ``editability.preserved_unsupported_objects`` — компиляция v0 не
  отслеживает неподдержанные объекты отдельно.

"""

from __future__ import annotations

import hashlib
import posixpath
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from lxml import etree
from pptx import Presentation

from deckdna.audit.basic import average_occupancy
from deckdna.audit.config import DEFAULT_AUDIT_CONFIG_PATH
from deckdna.contracts.deck_plan import Brief
from deckdna.contracts.quality_passport import (
    ContentSupport,
    Editability,
    Export,
    Fallback,
    Format,
    Inputs,
    IssuesSummary,
    Metrics,
    ModelProfile,
    PerModelItem,
    PerStageItem,
    Provenance,
    QualityPassport,
    Readability,
    StyleFidelity,
    Timings,
    Usage,
    Validity,
)
from deckdna.evaluation import measures
from deckdna.planning.config import DEFAULT_CONFIG_PATH as _GENERATION_CONFIG_PATH
from deckdna.pptx.validation.pei import assess_pptx

if TYPE_CHECKING:
    from deckdna.audit.issues import AuditIssue

SCHEMA_VERSION = "1.0"
PIPELINE_VERSION = "deckdna-poc/0.1.0"

_RULE_RASTER_ONLY = "editability.raster_only"
_RULE_TEXT_OVERFLOW = "text.overflow"
_RULE_PLACEHOLDER_TEXT = "integrity.placeholder_text"
_RULE_CONTRAST = "accessibility.contrast"

_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_OFFICE_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


# backend/deckdna/evaluation/quality_passport.py → <repo>/skill/manifest.yaml
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKILL_MANIFEST_PATH = _REPO_ROOT / "skill" / "manifest.yaml"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _skill_version() -> str | None:
    """version из skill/manifest.yaml; None, если манифест не читается."""
    try:
        data = yaml.safe_load(
            _SKILL_MANIFEST_PATH.read_text(encoding="utf-8")
        ) or {}
    except OSError:
        return None
    version = data.get("version")
    return str(version) if version is not None else None


def _config_hashes() -> dict[str, str]:
    """sha256 содержимого конфигов, реально использованных прогоном.

    generation.default.yaml грузится ``load_generation_config`` (как
    ``protected_slide_indices`` в pipeline), audit.default.yaml —
    ``default_audit_config`` внутри ``audit_deck``: хэшируются именно
    те файлы, что читал лоадер, по их DEFAULT_*_PATH.
    """
    hashes: dict[str, str] = {}
    for path in (_GENERATION_CONFIG_PATH, DEFAULT_AUDIT_CONFIG_PATH):
        try:
            key = str(path.relative_to(_REPO_ROOT))
        except ValueError:
            key = path.name
        try:
            hashes[key] = _sha256(path)
        except OSError:
            continue
    return hashes


def _ooxml_structural_errors(zf: zipfile.ZipFile) -> list[str]:
    """Пакетный sanity OOXML: список строк-проблем (пусто — чисто).

    НЕ XSD-валидация (схемного валидатора в проекте нет). Проверяется:
    - каждая *.xml/*.rels часть парсится lxml;
    - sldIdLst ↔ slide parts: каждый rId резолвится в существующий
      slide part, счётчики совпадают;
    - dangling rels: internal Target любого .rels существует в пакете;
    - [Content_Types].xml покрывает каждую часть (Default по ext или
      Override по имени).
    """
    errors: list[str] = []
    names = set(zf.namelist())

    parsed: dict[str, Any] = {}
    for n in sorted(names):
        if n.endswith((".xml", ".rels")):
            try:
                parsed[n] = etree.fromstring(zf.read(n))
            except etree.XMLSyntaxError:
                errors.append(f"{n}: XML не парсится")

    pres = parsed.get("ppt/presentation.xml")
    if pres is not None:
        pres_rels = parsed.get("ppt/_rels/presentation.xml.rels")
        rel_map = (
            {r.get("Id"): r.get("Target") for r in pres_rels}
            if pres_rels is not None
            else {}
        )
        sld_ids = pres.findall(f".//{{{_P_NS}}}sldIdLst/{{{_P_NS}}}sldId")
        slide_parts = {
            n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)
        }
        for el in sld_ids:
            rid = el.get(f"{{{_OFFICE_REL_NS}}}id")
            tgt = rel_map.get(rid)
            if tgt is None:
                errors.append(f"sldIdLst: {rid} без relationship")
            else:
                full = posixpath.normpath(posixpath.join("ppt", tgt))
                if full not in names:
                    errors.append(f"sldIdLst: {rid} → {tgt} — части нет в пакете")
                elif full not in slide_parts:
                    errors.append(f"sldIdLst: {rid} → {tgt} — не slide part")
        if len(sld_ids) != len(slide_parts):
            errors.append(
                f"sldIdLst={len(sld_ids)} vs slide parts={len(slide_parts)}"
            )

    for n in sorted(names):
        if not n.endswith(".rels") or n not in parsed:
            continue
        base = posixpath.dirname(posixpath.dirname(n))
        for r in parsed[n]:
            if r.get("TargetMode") == "External":
                continue
            tgt = r.get("Target", "")
            full = (
                tgt.lstrip("/")
                if tgt.startswith("/")
                else posixpath.normpath(posixpath.join(base, tgt))
            )
            if full not in names:
                errors.append(f"{n}: dangling rel → {tgt}")

    ct = parsed.get("[Content_Types].xml")
    if ct is not None:
        defaults = {
            (el.get("Extension") or "").lower()
            for el in ct.findall(f"{{{_CT_NS}}}Default")
        }
        overrides = {
            (el.get("PartName") or "").lstrip("/")
            for el in ct.findall(f"{{{_CT_NS}}}Override")
        }
        for n in sorted(names):
            if (
                n.endswith(("/", ".rels"))
                or n == "[Content_Types].xml"
            ):
                continue
            ext = n.rsplit(".", 1)[-1].lower()
            if n not in overrides and ext not in defaults:
                errors.append(f"{n}: нет content-type декларации")
    return errors


def _validity_metrics(
    out_path: Path, expected_slides: int | None
) -> tuple[bool, list[str]]:
    """Round-trip + структурный sanity выходной колоды.

    round_trip_ok — файл заново открывается python-pptx и число слайдов
    совпадает с ожидаемым (None expected → достаточно открытия).
    Возвращает (round_trip_ok, список проблем): детали в паспорт не
    попадают — в контракте Validity только счётчик, список доступен
    вызывающему коду для логов/диагностики.
    """
    errors: list[str] = []
    try:
        prs = Presentation(out_path)
        round_trip_ok = (
            expected_slides is None or len(prs.slides) == expected_slides
        )
        if not round_trip_ok:
            errors.append(
                f"round-trip: слайдов {len(prs.slides)}, ожидалось {expected_slides}"
            )
    except Exception:  # noqa: BLE001 — сам факт exception и есть результат проверки
        round_trip_ok = False
        errors.append("round-trip: python-pptx не открывает пакет")
    try:
        with zipfile.ZipFile(out_path) as zf:
            errors += _ooxml_structural_errors(zf)
    except (zipfile.BadZipFile, OSError):
        errors.append("выходной файл не является OPC zip-пакетом")
    return round_trip_ok, errors


def assemble_quality_passport(
    out_path: str | Path,
    compose_report: dict[str, Any],
    audit_issues: list[AuditIssue],
    *,
    content_pack_id: str = "unavailable",
    brief: Brief | dict[str, Any] | None = None,
    run_id: str | None = None,
    variant_id: str | None = None,
    duration_seconds: float | None = None,
    prompt_versions: dict[str, str] | None = None,
    model_profiles: list[ModelProfile] | None = None,
    stage_timings: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    source_text: str | None = None,
    auto_fixes: dict[str, int] | None = None,
    repair_fixes: dict[str, int] | None = None,
) -> QualityPassport:
    """Собирает QualityPassport из отчёта компиляции, audit-issues и PEI.

    Параметры:
    - out_path — сгенерированная колода (проверяется PEI и хэшируется в exports);
    - compose_report — JSON-отчёт ``pptx.composing.minimal.generate_deck``;
    - audit_issues — результат ``audit.basic.audit_deck`` на том же файле;
    - content_pack_id — id пака контента ("unavailable", если неизвестен);
    - brief — Brief (модель или dict) для честного brief_hash; без него хэш
      считается от deck_plan_id отчёта и помечается как derived;
    - run_id — по умолчанию audit_run_id из issues, иначе uuid;
    - duration_seconds — замеренное время прогона, если кто-то его мерил;
    - prompt_versions — prompt_name -> version для промптов, реально
      ушедших в gateway на этом прогоне (пусто в детерминированном режиме);
    - model_profiles — модели, реально отработавшие на этом прогоне
      (пусто без gateway; лицензия/size обогащаются из manifest-а,
      неизвестная модель остаётся с None-полями — честно);
    - stage_timings — [{stage, seconds, cached}] по этапам конвейера;
    - usage — {total_tokens, model_calls, per_model}: расход модели за
      прогон; None → нули (детерминированный путь, а не «неизвестно»);
    - source_text — текст ContentPack для сверки чисел колоды с
      источником (None → ``numbers_*`` остаются None: сверять не с чем);
    - auto_fixes — {rule_code: сколько проблем исправлено автоматически
      до ревизии 1}: попадает в ``fallbacks`` (strategy="auto_fix") и
      ``issues_summary.fixed``;
    - repair_fixes — то же для исправлений, выбранных пользователем ПОСЛЕ
      генерации (POST /audits/{id}/repairs): strategy="user_repair".
    """
    out_path = Path(out_path)
    pei = assess_pptx(out_path)

    totals = compose_report.get("totals", {})
    runs_replaced = totals.get("runs_replaced", 0)
    runs_skipped = totals.get("runs_skipped", 0)
    dropped_units: dict[str, int] = compose_report.get("dropped_units", {}) or {}
    dropped_total = sum(
        count for kind, count in dropped_units.items() if kind != "text_truncated"
    )

    text_shapes = sum(s.text_shapes for s in pei.slides)
    vector_shapes = sum(s.vector_shapes for s in pei.slides)
    pictures = sum(s.pictures for s in pei.slides)
    native_text_ratio = (
        round(text_shapes / (text_shapes + vector_shapes + pictures), 4)
        if text_shapes + vector_shapes + pictures
        else None
    )

    raster_only = sum(1 for i in audit_issues if i.rule_code == _RULE_RASTER_ONLY)
    overflow = sum(1 for i in audit_issues if i.rule_code == _RULE_TEXT_OVERFLOW)
    contrast_failures = sum(
        1 for i in audit_issues if i.rule_code == _RULE_CONTRAST
    )
    expected_slides = len(compose_report.get("slides") or [])
    round_trip_ok, ooxml_errors = _validity_metrics(
        out_path, expected_slides or None
    )
    placeholder_left = sum(
        1 for i in audit_issues if i.rule_code == _RULE_PLACEHOLDER_TEXT
    )
    by_severity = {"blocker": 0, "error": 0, "warning": 0, "info": 0}
    for issue in audit_issues:
        if issue.severity in by_severity:
            by_severity[issue.severity] += 1

    if isinstance(brief, dict):
        brief = Brief.model_validate(brief)
    brief_hash = (
        hashlib.sha256(brief.model_dump_json().encode()).hexdigest()
        if brief is not None
        else hashlib.sha256(str(compose_report.get("deck_plan_id", "")).encode()).hexdigest()
    )

    run_id = (
        run_id
        or next((i.audit_run_id for i in audit_issues if i.audit_run_id), None)
        or hashlib.sha256(out_path.read_bytes()).hexdigest()[:12]
    )

    # битый пакет не должен ронять паспорт: он и есть результат проверки
    # validity — метрики, которым нужен открытый файл, честно None
    try:
        slide_count: int | None = len(Presentation(out_path).slides)
    except Exception:  # noqa: BLE001
        slide_count = None
    fidelity = (
        measures.style_fidelity(audit_issues, slide_count) if slide_count else None
    )
    numbers = (
        measures.check_numbers(measures.deck_text(out_path), source_text)
        if source_text is not None and slide_count is not None
        else None
    )
    try:
        avg_occupancy = average_occupancy(out_path)
    except Exception:  # noqa: BLE001 — метрика факультативна, не валит паспорт
        avg_occupancy = None
    usage = usage or {"total_tokens": 0, "model_calls": 0, "per_model": []}
    fixed_total = sum((auto_fixes or {}).values()) + sum(
        (repair_fixes or {}).values()
    )

    template_path = Path(compose_report["template"])
    exports = [
        Export(
            format=Format.pptx,
            artifact_id=out_path.name,
            sha256=_sha256(out_path),
            size_bytes=out_path.stat().st_size,
        )
    ]

    return QualityPassport(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        variant_id=variant_id,
        generated_at=datetime.now(timezone.utc),  # noqa: UP017 — datetime.UTC нет на py310
        inputs=Inputs(
            template_sha256=_sha256(template_path),
            template_name=template_path.name,
            content_pack_id=content_pack_id,
            brief_hash=brief_hash,
        ),
        metrics=Metrics(
            validity=Validity(
                opens_cleanly=pei.openable,
                ooxml_errors=len(ooxml_errors),
                round_trip_ok=round_trip_ok,
            ),
            editability=Editability(
                pei_level=pei.level,
                native_text_ratio=native_text_ratio,
                raster_only_slides=raster_only,
            ),
            style_fidelity=StyleFidelity(**fidelity) if fidelity else None,
            content_support=ContentSupport(
                supported_claims=runs_replaced if not repair_fixes else None,
                unsupported_claims=(
                    ((runs_skipped + dropped_total) or None)
                    if not repair_fixes
                    else None
                ),
                numbers_verified=numbers.verified if numbers else None,
                numbers_failed=numbers.failed if numbers else None,
            ),
            readability=Readability(
                overflow_count=overflow,
                contrast_failures=contrast_failures,
                avg_occupancy=avg_occupancy,
            ),
            timings=Timings(
                total_seconds=duration_seconds,
                per_stage=[PerStageItem(**item) for item in stage_timings]
                if stage_timings
                else None,
            ),
            usage=Usage(
                total_tokens=usage["total_tokens"],
                model_calls=usage["model_calls"],
                per_model=[PerModelItem(**m) for m in usage.get("per_model", [])],
            ),
        ),
        issues_summary=IssuesSummary(
            **by_severity,
            fixed=fixed_total or None,
            unresolved=len(audit_issues),
        ),
        fallbacks=[
            *(
                [
                    Fallback(
                        feature="metrics.content_support.numbers_verified",
                        strategy="numeric_presence_only",
                        disclosure=(
                            "numbers_verified/numbers_failed count only whether a "
                            "numeric value appears anywhere in the source text; "
                            "they do not verify the associated claim, entity, unit "
                            "or calculation"
                        ),
                    )
                ]
                if numbers is not None
                else []
            ),
            *[
                Fallback(
                    feature=f"composing.{kind}_units",
                    strategy="truncate" if kind == "text_truncated" else "drop",
                    disclosure=(
                        f"{count} text unit(s) shortened to fit editable boxes"
                        if kind == "text_truncated"
                        else f"{count} {kind} unit(s) dropped — "
                        + (
                            "slides had fewer eligible text slots than "
                            "planned text units"
                            if kind == "text_unplaced"
                            else f"{kind} content not yet composable"
                        )
                    ),
                )
                for kind, count in sorted(dropped_units.items())
                if count
            ],
            *(
                [
                    Fallback(
                        feature="audit.integrity.placeholder_text",
                        strategy="retain",
                        disclosure=(
                            f"{placeholder_left} shape(s) still carry "
                            "template stock text — content slot(s) "
                            "left unfilled"
                        ),
                    )
                ]
                if placeholder_left
                else []
            ),
            *[
                Fallback(
                    feature=f"repair.{rule}",
                    strategy="auto_fix",
                    disclosure=(
                        f"{count} проблем(ы) «{rule}» исправлены автоматически "
                        "до показа: "
                        + (
                            "текст переписан моделью короче (навык text_fit, "
                            "без новых чисел)"
                            if rule == "text.overflow"
                            else "безопасное детерминированное исправление"
                        )
                        + ", результат подтверждён повторным аудитом"
                    ),
                )
                for rule, count in sorted((auto_fixes or {}).items())
                if count
            ],
            *[
                Fallback(
                    feature=f"repair.{rule}",
                    strategy="user_repair",
                    disclosure=(
                        f"{count} проблем(ы) «{rule}» исправлены по выбору "
                        "пользователя после генерации; исправление "
                        "подтверждено повторным аудитом"
                    ),
                )
                for rule, count in sorted((repair_fixes or {}).items())
                if count
            ],
            *(
                [
                    Fallback(
                        feature="metrics.content_support",
                        strategy="not_recomputed",
                        disclosure=(
                            "content_support.supported_claims/unsupported_claims "
                            "не публикуются для этой ревизии: repair не "
                            "перезапускает compose и не пересчитывает "
                            "runs_replaced/runs_skipped/dropped_units"
                        ),
                    )
                ]
                if repair_fixes
                else []
            ),
        ] or None,
        provenance=Provenance(
            pipeline_version=PIPELINE_VERSION,
            skill_version=_skill_version(),
            # Только фактически использованные промпты/модели прогона
            # (пусто в детерминированном режиме — MODELS.md).
            prompt_versions=prompt_versions or {},
            model_profiles=model_profiles or [],
            config_hashes=_config_hashes(),
        ),
        exports=exports,
    )
