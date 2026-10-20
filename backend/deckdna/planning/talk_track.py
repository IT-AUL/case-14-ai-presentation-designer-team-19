"""Заметки докладчика: текст выступления на каждый слайд (ADR-032 §3).

``SlidePlan.speaker_note`` раньше нёс только служебные пометки планировщика,
а при клонировании слайда заметки отбрасывались — в PPTX их не было. Теперь
для каждого контентного слайда пишется talk track (2–5 предложений) и
кладётся в заметки слайда (``notesSlide``):

* **с моделью** — промпт ``prompts/speaker_notes/talk_track.v1.yaml``,
  батчами по слайдам. Только факты слайда: число, которого нет в текстах
  слайда, — отказ (та же проверка, что у ``content_writer.validate``);
  отказ или сбой модели — конспект;
* **без модели** — детерминированный конспект: ключевая мысль и пункты
  слайда связными предложениями.

К тексту выступления добавляются заметки автора исходной презентации
(импорт ``.pptx``) и раскрытие «дополнено моделью, требует проверки» для
слайдов с фактами ``origin=model`` (ADR-030 §4).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from deckdna.contracts.deck_plan import DeckPlan, Kind, Origin, Purpose, SlidePlan
from deckdna.evaluation.measures import extract_numbers
from deckdna.providers.base import ModelGateway

logger = logging.getLogger(__name__)

PROMPT_NAME = "talk_track"
_BATCH = 8
_MAX_CONCURRENT = 4
_MIN_CHARS, _MAX_CHARS = 40, 700
_MIN_SENTENCES, _MAX_SENTENCES = 2, 5
_SKIP = {Purpose.title, Purpose.thank_you, Purpose.section_divider}
_SENT_RE = re.compile(r"(?<=[.!?…])\s+")
MODEL_DISCLOSURE_RU = "Часть фактов дополнена моделью и требует проверки перед показом."
MODEL_DISCLOSURE_EN = "Some facts were added by the model and need checking before the talk."


class TalkTrackItem(BaseModel):
    index: int
    text: str = Field(min_length=1)


class TalkTrackBatch(BaseModel):
    slides: list[TalkTrackItem]


@dataclass
class TalkTrackReport:
    notes: dict[str, str] = field(default_factory=dict)  # slide id -> текст заметок
    model_written: int = 0
    extractive: int = 0
    rejected: int = 0

    def to_dict(self) -> dict:
        return {
            "slides_with_notes": len(self.notes),
            "model_written": self.model_written,
            "extractive": self.extractive,
            "rejected": self.rejected,
        }


def _facts(slide: SlidePlan) -> list[str]:
    return [
        u.text.strip() for u in slide.content_units
        if u.text and u.text.strip() and u.kind not in (Kind.title, Kind.subtitle)
    ]


_DANGLING_PREP_RE = re.compile(
    r"\s+(?:по|в|во|на|с|со|к|ко|от|для|из|о|об|за|при|до|под|над|про|через|и|а|но|или)"
    r"(?=[.!?…;:,]+(?:\s|$)|\s*$)",
    re.IGNORECASE,
)
_TECH_PREFIX_RE = re.compile(
    r"(?:^|(?<=[.!?…]\s))(?:Контекст|Цель|Тема|Задача|Стиль|Вариант|Context|Goal|Purpose|Style)\s*:\s*"
    r"(?:presentation|project|product|feature|initiative|report|deck|slides|slide|"
    r"balanced|faithful|visual|custom|default|"
    r"презентация|проект|продукт|инициатива|отчёт|отчет|доклад|слайды|колода|"
    r"сбалансированный|строгий|визуальный)"
    r"(?=\s*(?:[.!?…;:,]+(?:\s|$)|$))\s*[.!?…;:,]*\s*",
    re.IGNORECASE,
)


def _sentence(text: str) -> str:
    text = _TECH_PREFIX_RE.sub("", text).strip()
    text = _DANGLING_PREP_RE.sub("", text).strip().rstrip(";:,")
    if not text:
        return ""
    return text if text.endswith((".", "!", "?", "…")) else text + "."


def extractive_track(slide: SlidePlan) -> str:
    """Конспект без модели: ключевая мысль и до трёх пунктов слайда."""
    from deckdna.planning.story_director import clean_purpose_text

    parts: list[str] = []
    key = clean_purpose_text(slide.key_message) or ""
    title_clean = clean_purpose_text(slide.title_intent) or ""
    if key and key.casefold() != title_clean.casefold():
        sent = _sentence(key)
        if sent:
            parts.append(sent)
    for fact in _facts(slide):
        if len(parts) >= _MAX_SENTENCES - 1:
            break
        cleaned_fact = clean_purpose_text(fact)
        if not cleaned_fact:
            continue
        if cleaned_fact.casefold() not in key.casefold():
            sent = _sentence(cleaned_fact)
            if sent:
                parts.append(sent)
    if not parts and title_clean:
        sent = _sentence(title_clean)
        if sent:
            parts.append(sent)
    return " ".join(p for p in parts if p.strip(". "))


def validate_track(text: str, slide: SlidePlan) -> str | None:
    """Причина отказа или None."""
    text = text.strip()
    if not _MIN_CHARS <= len(text) <= _MAX_CHARS:
        return f"длина {len(text)} вне {_MIN_CHARS}–{_MAX_CHARS}"
    sentences = [s for s in _SENT_RE.split(text) if s.strip()]
    if not _MIN_SENTENCES <= len(sentences) <= _MAX_SENTENCES:
        return f"{len(sentences)} предложений вне {_MIN_SENTENCES}–{_MAX_SENTENCES}"
    pool = " ".join([slide.title_intent or "", slide.key_message or "", *_facts(slide)])
    invented = extract_numbers(text) - extract_numbers(pool)
    if invented:
        return "числа не со слайда: " + ", ".join(sorted(invented))
    return None


def can_write(gateway: object) -> bool:
    """Как у ``content_writer.can_rewrite``: мок участвует только с фикстурой."""
    if getattr(gateway, "provider_name", None) == "mock":
        return PROMPT_NAME in (getattr(gateway, "fixtures", None) or {})
    return gateway is not None


def _disclosures(slide: SlidePlan, language: str) -> list[str]:
    if any(u.origin == Origin.model for u in slide.content_units):
        return [MODEL_DISCLOSURE_RU if language.startswith("ru") else MODEL_DISCLOSURE_EN]
    return []


async def _model_batch(
    gateway: ModelGateway, slides: list[SlidePlan], language: str, audience: str,
    sem: asyncio.Semaphore,
) -> dict[int, str]:
    from deckdna.planning.story_director import clean_purpose_text

    payload = {
        "language": language,
        "audience": audience,
        "slides": [
            {
                "index": s.index,
                "title": clean_purpose_text(s.title_intent) or "",
                "key_message": clean_purpose_text(s.key_message) or "",
                "facts": _facts(s),
            }
            for s in slides
        ],
    }
    async with sem:
        try:
            out = await gateway.text_json(PROMPT_NAME, payload, TalkTrackBatch)
        except Exception as exc:  # noqa: BLE001 — сбой модели = конспект
            logger.warning("talk_track batch failed (%s); extractive notes", exc)
            return {}
    if not isinstance(out, TalkTrackBatch):
        return {}
    return {item.index: _sentence(item.text.strip()) for item in out.slides}


async def write_talk_tracks(plan: DeckPlan, gateway: ModelGateway | None) -> TalkTrackReport:
    """Заметки для всех контентных слайдов плана."""
    report = TalkTrackReport()
    language = plan.language or "ru"
    slides = [s for s in plan.slides if s.purpose not in _SKIP and _facts(s)]
    answers: dict[int, str] = {}
    if slides and gateway is not None and can_write(gateway):
        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        batches = [slides[i : i + _BATCH] for i in range(0, len(slides), _BATCH)]
        for part in await asyncio.gather(*(
            _model_batch(gateway, b, language, plan.brief.audience or "", sem) for b in batches
        )):
            answers.update(part)
    for slide in slides:
        track = answers.get(slide.index)
        reason = validate_track(track, slide) if track else "нет ответа"
        if track and reason is None:
            report.model_written += 1
        else:
            if track:
                report.rejected += 1
                logger.info("talk_track slide %d rejected: %s", slide.index, reason)
            track = extractive_track(slide)
            report.extractive += 1
        source_note = _author_note(slide)
        report.notes[slide.id] = "\n\n".join(
            part for part in (track, source_note, *_disclosures(slide, language)) if part
        )
    return report


def _author_note(slide: SlidePlan) -> str | None:
    """Заметки автора исходной презентации (импорт ``.pptx``), без служебных
    пометок планировщика («Раздел: …», «Сводка слайдов секций: …», «Данные: …»)."""
    from deckdna.planning.story_director import clean_purpose_text

    note = (slide.speaker_note or "").strip()
    for marker in (
        "Раздел:",
        "Сводка слайдов секций:",
        "Данные:",
        "Таблица:",
        "Выводы посчитаны по данным",
    ):
        idx = note.find(marker)
        if idx != -1:
            note = note[:idx].strip()
    note = _TECH_PREFIX_RE.sub("", note).strip()
    note = _DANGLING_PREP_RE.sub("", note).strip()
    if not clean_purpose_text(note):
        return None
    return _sentence(note) if note else None


def write_notes_to_pptx(pptx_path, compose_report: dict, notes: dict[str, str]) -> int:
    """Заметки в ``notesSlide`` слайдов готовой колоды. Слайды отчёта вёрстки
    идут в порядке колоды; продолжения источников и защищённые слайды
    заметок не получают. Возвращает число слайдов с заметками."""
    from pptx import Presentation

    if not notes:
        return 0
    prs = Presentation(str(pptx_path))
    written = 0
    for slide, info in zip(prs.slides, compose_report.get("slides", []), strict=False):
        text = notes.get(info.get("id"))
        if not text or info.get("bibliography_page", 1) > 1:
            continue
        frame = slide.notes_slide.notes_text_frame
        if frame is None:
            continue
        frame.text = text
        written += 1
    if written:
        prs.save(str(pptx_path))
    return written


__all__ = [
    "PROMPT_NAME",
    "TalkTrackBatch",
    "TalkTrackReport",
    "can_write",
    "extractive_track",
    "validate_track",
    "write_notes_to_pptx",
    "write_talk_tracks",
]
