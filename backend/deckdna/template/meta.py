"""Служебные слайды шаблона по структуре — без модели и без списков слов.

Шаблоны с маркетплейсов (Slidesgo, Envato и др.) несут десятки слайдов,
которые не предназначены для контента: инструкции, титры автора, палитру,
каталоги иконок и иллюстраций. При анализе шаблона моделью их отмечает
``exemplar_describe`` (``is_template_meta``); этот модуль — запасной
детерминированный признак для пути без модели, по форме слайда:

* каталог — десятки (≥ ``_CATALOG_MIN_GROUPS``) групп-иконок при почти
  полном отсутствии текста;
* палитра — ≥3 hex-кодов цвета в тексте слайда;
* инструкции и титры — ссылки (http/www) в демо-тексте шаблона.
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
_SET_MIN_GROUPS = 25
_SET_MAX_TEXT = 200
_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")
_URL_RE = re.compile(r"https?://|www\.", re.I)


def is_meta_xml(xml: bytes) -> bool:
    root = etree.fromstring(xml)
    text = " ".join(t.text or "" for t in root.iter(f"{{{A}}}t"))
    if len(_HEX_RE.findall(text)) >= 3 or _URL_RE.search(text):
        return True
    # каталог иконок/иллюстраций: десятки групп-картинок почти без текста
    # (сложная схема тоже состоит из многих фигур, но несёт текст)
    groups = sum(1 for _ in root.iter(f"{{{P}}}grpSp"))
    return (
        groups >= _CATALOG_MIN_GROUPS and len(text.strip()) < _CATALOG_MAX_TEXT
    ) or (groups >= _SET_MIN_GROUPS and len(text.strip()) < _SET_MAX_TEXT)


def is_meta_slide(pkg: OpcPackage, part: str) -> bool:
    xml = pkg.parts.get(part)
    return xml is not None and is_meta_xml(xml)


def meta_slides(pkg: OpcPackage, parts: list[str]) -> set[str]:
    return {p for p in parts if is_meta_slide(pkg, p)}
