"""Служебные слайды шаблона по структуре — без модели и без списков слов.

Шаблоны с маркетплейсов (Slidesgo, Envato и др.) несут десятки слайдов,
которые не предназначены для контента: инструкции, титры автора, палитру,
каталоги иконок и иллюстраций. При анализе шаблона моделью их отмечает
``exemplar_describe`` (``is_template_meta``); этот модуль — запасной
детерминированный признак для пути без модели, по форме слайда:

* каталог — десятки (≥ ``_CATALOG_MIN_GROUPS``) групп- или картинок-иконок
  при почти полном отсутствии текста. Картинки считаются наравне с группами
  (ADR-031 §6): каталоги иконок шаблона питча LCT собраны из сотен ``p:pic``
  без единой группы, детектор их не видел, и вариант ``visual`` строил
  слайд процесса на странице «ИКОНКИ». Инфографику из картинок отделяет
  число подписей: у неё подпись почти у каждой фигуры, у каталога — одна
  на несколько значков.

* палитра — минимум четыре залитых swatch shapes с разными точными
  HEX-подписями; CSS-код или две цветовые метки этим признаком не являются.

Инструкции и титры по форме не отличить от слайда с кодом или
ссылками (VK Tech: пример CSS-кода со ссылкой — законный дизайн под
технический контент), поэтому их отмечает только модель.
"""

from __future__ import annotations

import re

from lxml import etree

from deckdna.pptx.opc.package import OpcPackage

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_CATALOG_MIN_GROUPS = 40
_CATALOG_MAX_TEXT = 400
# набор значков/карт с подписью-заголовком: чуть меньше групп, почти без текста
_SET_MIN_GROUPS = 20
_SET_MAX_TEXT = 200
# сколько значков приходится на одну подпись, не меньше
_CATALOG_ITEMS_PER_RUN = 3
_SET_ITEMS_PER_RUN = 2


def is_meta_xml(xml: bytes) -> bool:
    root = etree.fromstring(xml)
    swatches = set()
    for shape in root.iter(f"{{{P}}}sp"):
        if shape.find(f"{{{P}}}spPr/{{{A}}}solidFill") is None:
            continue
        label = "".join(t.text or "" for t in shape.iter(f"{{{A}}}t")).strip()
        match = re.fullmatch(r"(?:HEX\s*)?#\s*([0-9a-f]{6})", label, re.IGNORECASE)
        if match:
            swatches.add(match.group(1).upper())
    if len(swatches) >= 4:
        return True
    runs = [t.text for t in root.iter(f"{{{A}}}t") if (t.text or "").strip()]
    text = " ".join(runs)
    # A heading alone is ambiguous (credit risk, marketing attribution,
    # software licensing). Require an explicit template-use/author notice.
    heading = re.compile(r"^(?:credits|attribution|template licen[cs]e|"
                         r"лицензия шаблона|авторы шаблона|атрибуция)\s*[:.!]?$", re.I)
    notice = re.compile(
        r"(?:presentation\s+template\s+is\s+free|"
        r"(?:keep|retain)\s+this\s+slide.{0,80}(?:credit|attribut)|"
        r"(?:credit|attribut).{0,60}(?:authors?|creators?).{0,40}template|"
        r"(?:укажите|сохраните).{0,60}автор\w*\s+шаблона)", re.I,
    )
    if any(heading.fullmatch(run.strip()) for run in runs) and notice.search(text):
        return True
    # каталог иконок/иллюстраций: десятки групп или картинок почти без
    # текста (сложная схема тоже состоит из многих фигур, но несёт текст)
    items = sum(1 for _ in root.iter(f"{{{P}}}grpSp")) + sum(
        1 for _ in root.iter(f"{{{P}}}pic")
    )
    # инфографика из картинок (столбики-картинки с подписями «10», сетка
    # карточек с иконками, схема структуры компании с логотипами) несёт
    # подпись почти у каждой фигуры; у каталога подписей в разы меньше
    chars = len(text.strip())
    catalog = (
        items >= _CATALOG_MIN_GROUPS and chars < _CATALOG_MAX_TEXT
        and len(runs) * _CATALOG_ITEMS_PER_RUN <= items
    )
    icon_set = (
        items >= _SET_MIN_GROUPS and chars < _SET_MAX_TEXT
        and len(runs) * _SET_ITEMS_PER_RUN <= items
    )
    return catalog or icon_set


def is_meta_slide(pkg: OpcPackage, part: str) -> bool:
    xml = pkg.parts.get(part)
    return xml is not None and is_meta_xml(xml)


def meta_slides(pkg: OpcPackage, parts: list[str]) -> set[str]:
    return {p for p in parts if is_meta_slide(pkg, p)}
