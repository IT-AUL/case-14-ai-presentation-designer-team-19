"""Нативные композиции в рамке шаблона: рамка без контента, токены дизайна,
пять архетипов — текст влезает, фигуры в области, нет остатков шаблона.

Два разных шаблона (реальный и синтетический «невиданный») + колода,
собранная python-pptx прямо в тесте: ничего шаблонно-специфичного."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from deckdna.audit.basic import audit_deck
from deckdna.pptx.composing.native_compose import (
    ARCHETYPES,
    Box,
    choose_archetype,
    compose,
    slide_frame,
)
from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu, Pt

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pptx"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
GEOMETRY_RULES = {
    "text.overflow",
    "layout.out_of_bounds",
    "layout.unintended_overlap",
    "text.slide_clip",
}

ITEMS = {
    "cards": [
        {"heading": "Безопасность", "body": "Шифрование данных и аудит доступа по 152-ФЗ."},
        {"heading": "Масштабирование", "body": "Кластер растёт без простоя и ручной настройки."},
        {"heading": "Наблюдаемость", "body": "Метрики, логи и трейсы в одном интерфейсе."},
        {"heading": "Интеграции", "body": "Коннекторы к корпоративным системам и открытый API."},
    ],
    "process": [
        {"heading": "Сбор данных", "body": "Подключаем источники и проверяем выгрузки."},
        {"heading": "Обучение", "body": "Дообучаем модель на примерах заказчика."},
        {"heading": "Пилот", "body": "Запуск в одном подразделении, сбор метрик."},
        {"heading": "Тиражирование", "body": "Раскатка на всю компанию с поддержкой 24/7."},
    ],
    "kpi": [
        {"value": "99,95%", "heading": "Доступность", "body": "SLA облачной платформы"},
        {"value": "3×", "heading": "Быстрее", "body": "вывод сервисов в продакшен"},
        {"value": "40%", "heading": "Экономия", "body": "затрат на инфраструктуру за год"},
    ],
    "two_column": [
        {"heading": "Было", "body": "Ручная сборка слайдов занимала два дня, стиль расходился."},
        {"heading": "Стало", "body": "Колода собирается за минуты из шаблона компании."},
    ],
    "list": [
        {"heading": "Цель", "body": "сократить подготовку презентаций в 10 раз"},
        {"heading": "Аудитория", "body": "менеджеры продуктов и пресейл"},
        {"heading": "Ограничения", "body": "открытые модели до 35B параметров"},
        {"heading": "Результат", "body": "редактируемый PPTX, PDF и HTML"},
    ],
}
TITLE = "Новая композиция слайда"


# ─────────────────────────── helpers ───────────────────────────


def _parts(pptx_path: Path, slide_no: int) -> tuple[str, dict, int, int]:
    """(имя части слайда, layout/master/theme XML, ширина, высота)."""
    prs = Presentation(str(pptx_path))
    slide = prs.slides[slide_no - 1]
    layout = slide.slide_layout
    master = layout.slide_master
    theme = next(r.target_part for r in master.part.rels.values() if r.reltype.endswith("/theme"))
    extra = {
        "layout_xml": layout.part.blob,
        "master_xml": master.part.blob,
        "theme_xml": theme.blob,
    }
    return str(slide.part.partname).lstrip("/"), extra, prs.slide_width, prs.slide_height


def _read(pptx_path: Path, part: str) -> bytes:
    with zipfile.ZipFile(pptx_path) as zf:
        return zf.read(part)


def _single_slide_deck(src: Path, part: str, xml: bytes, out: Path) -> Path:
    """Копия колоды с заменённой частью слайда, в которой оставлен только
    этот слайд (аудит и рендер — одного слайда, индекс 0)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, xml if item.filename == part else zin.read(item.filename))
    prs = Presentation(io.BytesIO(buf.getvalue()))
    lst = prs.slides._sldIdLst
    for sld in list(lst):
        if prs.part.related_part(sld.rId).partname != "/" + part:
            lst.remove(sld)
    prs.save(str(out))
    return out


def _shapes(xml: bytes) -> list[tuple[int, str, Box | None, str]]:
    """(id, тег, рамка, текст) верхнего уровня spTree."""
    root = etree.fromstring(xml)
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    out = []
    for el in tree:
        tag = etree.QName(el).localname
        if tag in {"nvGrpSpPr", "grpSpPr", "extLst"}:
            continue
        c = el.find(f"./*/{{{P}}}cNvPr")
        xfrm = el.find(f".//{{{A}}}xfrm")
        box = None
        if xfrm is not None and xfrm.find(f"{{{A}}}off") is not None:
            off, ext = xfrm.find(f"{{{A}}}off"), xfrm.find(f"{{{A}}}ext")
            box = Box(int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")))
        text = "".join(t.text or "" for t in el.iter(f"{{{A}}}t")).strip()
        out.append((int(c.get("id")), tag, box, text))
    return out


def _all_ids(xml: bytes) -> list[int]:
    root = etree.fromstring(xml)
    return [int(v) for v in root.xpath("//p:cNvPr/@id", namespaces={"p": P})]


def _check(src: Path, slide_no: int, archetype: str, items: list[dict], tmp_path: Path):
    part, extra, sw, sh = _parts(src, slide_no)
    original = _read(src, part)
    frame, region, tokens = slide_frame(original, sw, sh, **extra)
    frame_ids = set(_all_ids(frame))
    xml = compose(frame, region, tokens, archetype, TITLE, items, sw, sh)
    out = _single_slide_deck(src, part, xml, tmp_path / f"{src.stem}_{archetype}.pptx")

    prs = Presentation(str(out))  # открывается
    assert len(prs.slides) == 1

    issues = [i for i in audit_deck(out) if i.slide_index == 0 and i.rule_code in GEOMETRY_RULES]
    assert not issues, [(i.rule_code, i.message) for i in issues]

    ids = _all_ids(xml)
    assert len(ids) == len(set(ids)), "id фигур должны быть уникальны"

    new = [s for s in _shapes(xml) if s[0] not in frame_ids]
    assert new, "композиция должна добавить фигуры"
    for sid, tag, box, _ in new:
        assert tag in {"sp", "cxnSp"}, "только нативные фигуры"
        assert box is not None and region.contains(box, tol=1), (sid, box, region)

    texts = [t for _, _, _, t in _shapes(xml)]
    assert any(TITLE in t for t in texts), "заголовок записан"
    for it in items:
        if it.get("heading"):
            assert any(it["heading"] in t for t in texts)

    # тексты удалённых фигур шаблона не остаются на слайде
    kept = {t for _, _, _, t in _shapes(frame)}
    removed = {t for _, _, _, t in _shapes(original) if t and t not in kept}
    content = " ".join(str(v) for it in items for v in it.values() if v) + " " + TITLE
    for t in removed:
        if t in content:  # совпадение с новым контентом — не остаток
            continue
        assert all(t not in x for x in texts), t
    return xml, region, tokens


# ─────────────────────────── real + unseen templates ───────────────────────────

TEMPLATES = [("vk_tech_template.pptx", 16), ("synthetic_unseen.pptx", 2)]


@pytest.mark.parametrize("archetype", ARCHETYPES)
@pytest.mark.parametrize(("name", "slide_no"), TEMPLATES)
def test_archetype_fits_region_and_passes_geometry_audit(name, slide_no, archetype, tmp_path):
    _check(FIXTURES / name, slide_no, archetype, ITEMS[archetype], tmp_path)


def test_side_composition_frame_keeps_title_column(tmp_path):
    """Контент эталона стоял справа от заголовка — область тоже справа."""
    src = FIXTURES / "vk_tech_template.pptx"
    part, extra, sw, sh = _parts(src, 25)
    _, region, tokens = slide_frame(_read(src, part), sw, sh, **extra)
    tb = Box(*tokens["title_box"])
    assert region.x >= tb.r
    _check(src, 25, "process", ITEMS["process"], tmp_path)


def test_tokens_come_from_the_slide():
    src = FIXTURES / "synthetic_unseen.pptx"
    part, extra, sw, sh = _parts(src, 2)
    _, _, t = slide_frame(_read(src, part), sw, sh, **extra)
    for key in ("title_font", "body_font", "text_color", "heading_color", "accent", "card_fill"):
        assert key in t
    assert 12 <= t["body_size_pt"] <= 18 and 14 <= t["heading_size_pt"] <= 24
    assert t["corner"] in {"rect", "roundRect"}
    # тёмный фон шаблона распознан — светлый текст, карточки не «белые»
    assert t["dark_background"] is True
    assert "srgb" in t["card_fill"]


def test_long_content_never_overflows(tmp_path):
    """Не влезает даже на 11pt — более ёмкий архетип / сокращение, но не
    переполнение."""
    long_body = " ".join(["очень длинное описание пункта с подробностями"] * 12)
    items = [{"heading": f"Пункт номер {i}", "body": long_body} for i in range(6)]
    _check(FIXTURES / "vk_tech_template.pptx", 16, "cards", items, tmp_path)


def test_five_cards_and_six_steps(tmp_path):
    src = FIXTURES / "synthetic_unseen.pptx"
    five = ITEMS["cards"] + [{"heading": "Поддержка", "body": "Команда инженеров 24/7."}]
    _check(src, 2, "cards", five, tmp_path)
    steps = [{"heading": f"Этап {i}", "body": "Короткое описание шага."} for i in range(1, 7)]
    _check(src, 3, "process", steps, tmp_path)


# ─────────────────────────── deck built in the test ───────────────────────────


def _png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (40, 40), "red").save(out, format="PNG")
    return out.getvalue()


def _built_deck(path: Path) -> Path:
    """Колода python-pptx: плейсхолдер заголовка, карточки, картинка, группа,
    мелкий логотип в углу — рамка должна оставить только заголовок и логотип."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Title Only
    slide.shapes.title.text = "Старый заголовок шаблона"
    sw, sh = 12192000, 6858000
    for i in range(3):
        x = int(sw * (0.05 + i * 0.31))
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, x, int(sh * 0.3), int(sw * 0.28), int(sh * 0.5)
        )
        card.fill.solid()
        card.fill.fore_color.rgb = RGBColor(0xEE, 0xF2, 0xF7)
        tb = slide.shapes.add_textbox(x + 100000, int(sh * 0.35), int(sw * 0.25), 400000)
        run = tb.text_frame.paragraphs[0].add_run()
        run.text = f"Текст карточки шаблона {i}"
        run.font.size = Pt(13)
    slide.shapes.add_picture(io.BytesIO(_png()), int(sw * 0.6), int(sh * 0.25), 1200000, 1200000)
    grp = slide.shapes.add_group_shape()
    grp.shapes.add_shape(MSO_SHAPE.OVAL, int(sw * 0.4), int(sh * 0.82), 300000, 300000)
    slide.shapes.add_picture(io.BytesIO(_png()), int(sw * 0.94), int(sh * 0.92), 200000, 200000)
    prs.save(str(path))
    return path


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_python_pptx_built_deck(archetype, tmp_path):
    src = _built_deck(tmp_path / "built.pptx")
    xml, _, tokens = _check(src, 1, archetype, ITEMS[archetype], tmp_path)
    tags = [tag for _, tag, _, _ in _shapes(xml)]
    assert "grpSp" not in tags  # группа контента удалена
    assert tags.count("pic") == 1  # логотип в углу остался, картинка контента — нет
    assert tokens["corner"] == "roundRect"
    assert tokens["card_fill"] == {"srgb": "EEF2F7"}


def test_frame_without_title_gets_one(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 500000, 2000000, 3000000, 2000000)
    path = tmp_path / "blank.pptx"
    prs.save(str(path))
    _check(path, 1, "list", ITEMS["list"], tmp_path)


# ─────────────────────────── archetype choice ───────────────────────────


def test_choose_archetype():
    assert choose_archetype(ITEMS["kpi"]) == "kpi"
    assert choose_archetype(ITEMS["cards"]) == "cards"
    assert choose_archetype(ITEMS["two_column"]) == "two_column"
    assert choose_archetype(ITEMS["cards"], hint="process") == "process"
    steps = [{"heading": f"Шаг {i}", "body": "x"} for i in range(1, 4)]
    assert choose_archetype(steps) == "process"
    assert choose_archetype([{"heading": None, "body": "x"}] * 3) == "list"
    assert choose_archetype([{"heading": "h", "body": "b"}] * 8) == "list"
