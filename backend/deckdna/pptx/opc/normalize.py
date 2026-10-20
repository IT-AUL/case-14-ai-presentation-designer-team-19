"""Нормализация пакета шаблона: ``.potx`` → пакет с типом ``.pptx`` (ADR-031 §2).

``python-pptx`` открывает только основную часть с content type
``presentationml.presentation.main+xml`` и отказывает пакету шаблона
(``presentationml.template.main+xml``) с ``ValueError``. Autopsy, работающий
через собственную OPC-модель, такой пакет читал, а вёрстка падала на стадии
``composing.deck``. Структура частей ``.potx`` и ``.pptx`` одинакова, отличается
только тип основной части (и, реже, macro-варианты), поэтому достаточно
переписать ``[Content_Types].xml`` в копии байтов. Остальные части не
трогаются.
"""

from __future__ import annotations

import io
import zipfile

_CONTENT_TYPES = "[Content_Types].xml"
_PRESENTATION_MAIN = (
    b"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
)
# Варианты основной части, которые python-pptx не открывает, но которые
# структурно являются той же презентацией: шаблон, шаблон с макросами,
# презентация с макросами, слайд-шоу.
_FOREIGN_MAIN_TYPES = (
    b"application/vnd.openxmlformats-officedocument.presentationml.template.main+xml",
    b"application/vnd.ms-powerpoint.template.macroEnabled.main+xml",
    b"application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
    b"application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml",
    b"application/vnd.ms-powerpoint.slideshow.macroEnabled.main+xml",
)


def needs_normalization(data: bytes) -> bool:
    """True, если основная часть пакета имеет тип шаблона/слайд-шоу."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            types = zf.read(_CONTENT_TYPES)
    except (zipfile.BadZipFile, KeyError):
        return False
    return any(t in types for t in _FOREIGN_MAIN_TYPES)


def normalize_template_bytes(data: bytes) -> bytes:
    """Копия пакета с типом основной части ``presentation.main+xml``.

    Пакет, которому нормализация не нужна (обычный ``.pptx``), возвращается
    как есть — те же байты, без перепаковки. Битый ZIP не обрабатывается
    здесь: его отвергает ``OpcPackage.open`` с ``package_corrupt``."""
    if not needs_normalization(data):
        return data
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            payload = src.read(info.filename)
            if info.filename == _CONTENT_TYPES:
                for foreign in _FOREIGN_MAIN_TYPES:
                    payload = payload.replace(foreign, _PRESENTATION_MAIN)
            dst.writestr(info, payload)
    return out.getvalue()
