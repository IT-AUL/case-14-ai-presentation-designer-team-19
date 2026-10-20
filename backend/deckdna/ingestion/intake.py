"""Единый приём файлов (ADR-034 §1): тип каждого файла по содержимому и
расширению — ``template`` | ``content`` | ``data`` | ``brandbook`` |
``unknown``.

Правила, по порядку:

* ``.potx`` — шаблон; ``.pptx`` — шаблон, если в имени нет «бренд»/«brand»
  (тогда пример брендбука); второй и следующие ``.pptx`` — старые колоды
  как контент (ADR-032);
* ``.csv``/``.xlsx`` — данные; ``.json`` — токены бренда, табличный JSON
  (данные) или пак контента;
* ``.pdf`` — брендбук, если текст похож на гайдлайн (палитра с HEX/RGB и
  слова «брендбук», «палитра»…), иначе контент;
* картинки — логотип брендбука, если в имени «logo»/«лого»/«brand»; иначе
  не принимаются;
* ``.md``/``.txt``/``.docx`` — контент.

Решение объясняется строкой ``reason``; пользователь может поправить тип
(``PATCH /projects/{id}/intake/files/{name}``)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

KINDS = ("template", "content", "data", "brandbook", "unknown")
_CONTENT = {".md", ".markdown", ".txt", ".docx", ".pdf", ".json", ".pptx", ".potx"}
_DATA = {".csv", ".xlsx", ".json", ".txt", ".md"}
_BRAND = {".json", ".pdf", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".pptx", ".potx"}
_IMAGES = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
_BRAND_NAME = re.compile(r"brand|бренд|guideline|гайдлайн|logo|лого|token", re.I)


def suffix(name: str) -> str:
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def allowed(name: str, kind: str) -> bool:
    """Можно ли принять файл как *kind* (для правки типа пользователем)."""
    ext = suffix(name)
    return {
        "template": ext in {".pptx", ".potx"},
        "content": ext in _CONTENT,
        "data": ext in _DATA,
        "brandbook": ext in _BRAND,
        "unknown": True,
    }.get(kind, False)


@dataclass
class Detected:
    kind: str
    reason: str


def _json_kind(data: bytes) -> Detected:
    from deckdna.template.brandbook import looks_like_tokens

    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Detected("unknown", "JSON не читается")
    if looks_like_tokens(raw):
        return Detected("brandbook", "токены цветов и шрифтов")
    if isinstance(raw, dict) and ("sections" in raw or "schema_version" in raw):
        return Detected("content", "пак контента")
    from deckdna.ingestion.tabular import json_table

    try:
        table = json_table(raw)
    except Exception:  # noqa: BLE001 — не таблица
        table = None
    if table:
        return Detected("data", "таблица в JSON")
    return Detected("content", "JSON как текст")


def detect(name: str, data: bytes, *, template_taken: bool = False) -> Detected:
    ext = suffix(name)
    if ext == ".potx":
        return Detected("template", "файл шаблона .potx")
    if ext == ".pptx":
        if _BRAND_NAME.search(name):
            return Detected("brandbook", "пример слайдов бренда (по имени)")
        if template_taken:
            return Detected("content", "вторая презентация — старая колода как контент")
        return Detected("template", "презентация — шаблон оформления")
    if ext in {".csv", ".xlsx"}:
        return Detected("data", "таблица")
    if ext == ".json":
        return _json_kind(data)
    if ext == ".pdf":
        from deckdna.template.brandbook import looks_like_brand_pdf

        if _BRAND_NAME.search(name) or looks_like_brand_pdf(data):
            return Detected("brandbook", "PDF-гайдлайн с палитрой")
        return Detected("content", "документ PDF")
    if ext in _IMAGES:
        if _BRAND_NAME.search(name):
            return Detected("brandbook", "логотип")
        return Detected(
            "unknown", "отдельные картинки пока не принимаются; логотип назовите logo.*",
        )
    if ext in {".md", ".markdown", ".txt", ".docx"}:
        return Detected("content", "текст")
    return Detected("unknown", f"формат {ext or 'без расширения'} не поддерживается")


__all__ = ["KINDS", "Detected", "allowed", "detect", "suffix"]
