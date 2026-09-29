"""Вписывание текста моделью (навык ``text_fit``).

Переполненный текст (``text.overflow`` / ``text.slide_clip``) не режется с
«…», а переписывается моделью короче — с сохранением смысла и абзацной
структуры. Модель здесь пишет только слова; всё остальное детерминировано
и проверяемо:

* бюджет символов считается из измерений аудита (во сколько раз текст
  больше рамки), а не угадывается моделью;
* ответ принимается, только если число абзацев совпало, длина в бюджете
  и **в тексте нет ни одного числа, которого не было в исходнике** (факты не
  выдумываются, ТЗ, Приложение 1, вопрос 4);
* запись идёт на уровне ``a:t`` — форматирование ранов сохраняется;
* решение «помогло или нет» принимает повторный аудит у вызывающего.

Промпт — отдельный версионированный файл ``prompts/text_fitting/text_fit.v1.yaml``.
"""

from __future__ import annotations

import asyncio
import math
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pydantic import BaseModel, Field

from deckdna.audit.issues import AuditIssue
from deckdna.errors import DeckDNAError
from deckdna.evaluation.measures import extract_numbers
from deckdna.providers.base import ModelGateway

PROMPT_NAME = "text_fit"
FIT_RULES = frozenset({"text.overflow", "text.slide_clip"})
# запас на неточность оценки метрик: просим чуть меньше, чем влезает
_BUDGET_MARGIN = 0.85
_MIN_BUDGET = 12
_MAX_CONCURRENT = 4
# _fit_ratio не умеет отличить «умеренное переполнение» от «фигура на
# порядок меньше текста» — обе ситуации дают одну и ту же нижнюю границу.
# Когда ratio упёрся в этот пол И исходный текст длиннее подписи/лейбла,
# честный бюджет был бы << _MIN_BUDGET: сжатие до 12 симв. не «впишет»
# текст, а сотрёт его смысл (см. ADR о фрагментации в badge-слотах).
_RATIO_FLOOR = 0.05
_UNFIXABLE_SOURCE_LEN = _MIN_BUDGET * 3

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_AXES_RE = (
    re.compile(
        r"(?:unbreakable word|widest line)\s*≈\s*([\d.]+)pt\s*>\s*usable width\s*([\d.]+)pt"
    ),
    re.compile(r"estimated text height\s*≈\s*([\d.]+)pt\s*>\s*usable height\s*([\d.]+)pt"),
)
# text.slide_clip: «text extent beyond slide edge x=…pt y=…pt»
_CLIP_RE = re.compile(r"beyond slide edge x=([\d.]+)pt y=([\d.]+)pt")
_EMU_PER_PT = 12700


class FitText(BaseModel):
    """Ответ модели: переписанные абзацы в исходном порядке."""

    paragraphs: list[str] = Field(min_length=1)


@dataclass
class FitReport:
    # issue_id -> краткое описание («Текст сокращён моделью: 412 → 190 симв.»)
    rewritten: dict[str, str] = field(default_factory=dict)
    # issue_id -> почему ответ модели не принят
    rejected: dict[str, str] = field(default_factory=dict)
    model_calls: int = 0


def can_rewrite(gateway: object) -> bool:
    """Есть ли у gateway настоящая модель для переписывания текста.

    Offline-mock синтезирует схемно-валидные, но бессмысленные строки —
    писать их в слайд нельзя. Mock участвует, только если ему явно задан
    fixture для этого промпта (тесты)."""
    if getattr(gateway, "provider_name", None) == "mock":
        return PROMPT_NAME in (getattr(gateway, "fixtures", None) or {})
    return gateway is not None


def _fit_ratio(issue: AuditIssue, shape=None) -> float | None:
    """Доля текста, которая помещается: usable / required по худшей оси.

    ``text.overflow`` несёт оба числа в evidence; у ``text.slide_clip`` есть
    только вылет за край слайда — вместимость считается как рамка /
    (рамка + вылет) по геометрии фигуры."""
    detail = " ".join(e.get("detail", "") for e in issue.evidence or [])
    ratios: list[float] = []
    clip = _CLIP_RE.search(detail)
    if clip is not None and shape is not None and shape.width and shape.height:
        for over, size in (
            (float(clip.group(1)), shape.width / _EMU_PER_PT),
            (float(clip.group(2)), shape.height / _EMU_PER_PT),
        ):
            if over > 0 and size > 0:
                ratios.append(size / (size + over))
        return max(_RATIO_FLOOR, min(min(ratios), 1.0)) if ratios else None
    for rx in _AXES_RE:
        for m in rx.finditer(detail):
            required, usable = float(m.group(1)), float(m.group(2))
            if required > 0 and usable > 0:
                ratios.append(usable / required)
    if not ratios and isinstance(issue.measured_value, int | float) and isinstance(
        issue.threshold, int | float
    ):
        if issue.measured_value > 0 and issue.threshold > 0:
            ratios.append(float(issue.threshold) / float(issue.measured_value))
    if not ratios:
        return None
    return max(_RATIO_FLOOR, min(min(ratios), 1.0))


def _iter_shapes(shapes):
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)


def _find(prs, slide_id: str | None, shape_id: str):
    for slide in prs.slides:
        if slide_id is not None and str(slide.slide_id) != str(slide_id):
            continue
        for shape in _iter_shapes(slide.shapes):
            if str(shape.shape_id) == str(shape_id) and shape.has_text_frame:
                title = ""
                if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
                    title = slide.shapes.title.text_frame.text.strip()
                return shape, title
    return None, ""


def _paragraphs(shape) -> list:
    return [p for p in shape.text_frame.paragraphs if p.text.strip()]


def _write(paragraphs: list, texts: list[str]) -> None:
    """Текст абзаца → в первый непустой a:t, остальные a:t абзаца пустеют."""
    for para, text in zip(paragraphs, texts, strict=True):
        runs = [t for t in para._p.iter(f"{{{A}}}t")]  # noqa: SLF001
        target = next((t for t in runs if (t.text or "").strip()), runs[0] if runs else None)
        if target is None:
            continue
        for t in runs:
            t.text = text if t is target else ""


def _bare(numbers: set[str]) -> set[str]:
    return {re.sub(r"[^\d.]", "", n) for n in numbers}


def validate(source: list[str], answer: FitText, budget: int) -> str | None:
    """Причина отказа или None, если ответ модели годится."""
    texts = [p.strip() for p in answer.paragraphs]
    if len(texts) != len(source):
        return f"модель вернула {len(texts)} абзацев вместо {len(source)}"
    if any(not t for t in texts):
        return "модель вернула пустой абзац"
    total = sum(len(t) for t in texts)
    if total > math.ceil(budget * 1.15):
        return f"текст всё ещё длинный: {total} симв. при бюджете {budget}"
    invented = _bare(extract_numbers(" ".join(texts))) - _bare(
        extract_numbers(" ".join(source))
    )
    if invented:
        return "модель добавила числа, которых нет в исходнике: " + ", ".join(sorted(invented))
    if total >= sum(len(s) for s in source):
        return "текст не стал короче"
    return None


async def fit_texts(
    deck_in: str | Path,
    issues: list[AuditIssue],
    gateway: ModelGateway,
    deck_out: str | Path,
    *,
    language: str = "ru",
    max_concurrent: int = _MAX_CONCURRENT,
) -> FitReport:
    """Переписать моделью текст фигур из ``issues`` → ``deck_out``.

    Фигура обрабатывается один раз, даже если на неё несколько проблем.
    Отказ провайдера по одной фигуре не роняет остальные — он попадает в
    ``rejected``. ``deck_out`` пишется всегда (копия, если нечего менять).
    """
    deck_in, deck_out = Path(deck_in), Path(deck_out)
    report = FitReport()
    prs = Presentation(str(deck_in))
    jobs: dict[tuple[str | None, str], tuple[AuditIssue, list, list[str], int, str]] = {}
    for issue in issues:
        if issue.rule_code not in FIT_RULES or not issue.shape_ids:
            continue
        key = (issue.slide_id, issue.shape_ids[0])
        if key in jobs:
            continue
        shape, title = _find(prs, issue.slide_id, issue.shape_ids[0])
        ratio = _fit_ratio(issue, shape)
        if shape is None or ratio is None:
            report.rejected[issue.id] = "фигура или измерение переполнения не найдены"
            continue
        paras = _paragraphs(shape)
        source = [p.text.strip() for p in paras]
        length = sum(len(s) for s in source)
        if not paras or length < _MIN_BUDGET:
            report.rejected[issue.id] = "в фигуре слишком мало текста для переписывания"
            continue
        if ratio <= _RATIO_FLOOR and length > _UNFIXABLE_SOURCE_LEN:
            report.rejected[issue.id] = (
                "фигура на порядок меньше текста — честное сокращение "
                "уничтожило бы смысл, оставляю как overflow"
            )
            continue
        budget = max(_MIN_BUDGET, int(length * ratio * _BUDGET_MARGIN))
        jobs[key] = (issue, paras, source, budget, title)

    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def ask(source: list[str], budget: int, title: str) -> FitText:
        async with semaphore:
            return await gateway.text_json(
                PROMPT_NAME,
                {
                    "paragraphs": source,
                    "max_chars": budget,
                    "language": language,
                    "slide_title": title,
                },
                FitText,
            )

    items = list(jobs.values())
    answers = await asyncio.gather(
        *(ask(src, budget, title) for _, _, src, budget, title in items),
        return_exceptions=True,
    )
    report.model_calls = len(items)
    for (issue, paras, source, budget, _), answer in zip(items, answers, strict=True):
        if isinstance(answer, DeckDNAError):
            report.rejected[issue.id] = f"модель недоступна: {answer.code}"
            continue
        if isinstance(answer, BaseException):
            report.rejected[issue.id] = f"ошибка модели: {type(answer).__name__}"
            continue
        reason = validate(source, answer, budget)
        if reason is not None:
            report.rejected[issue.id] = reason
            continue
        new = [p.strip() for p in answer.paragraphs]
        _write(paras, new)
        report.rewritten[issue.id] = (
            f"Текст сокращён моделью: {sum(len(s) for s in source)} → "
            f"{sum(len(t) for t in new)} симв."
        )
    if report.rewritten:
        prs.save(str(deck_out))
    elif deck_in != deck_out:
        shutil.copyfile(deck_in, deck_out)
    return report
