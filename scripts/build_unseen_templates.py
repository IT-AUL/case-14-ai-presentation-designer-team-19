"""Build additional synthetic "unseen" template fixtures (OR-014/OR-032).

The single ``synthetic_unseen.pptx`` was the only generalization datapoint.
These three fixtures deliberately differ from it AND from the organizer
decks along separate axes:

- ``synthetic_unseen_43.pptx`` — 4:3 aspect (10x7.5in), light cream theme,
  Georgia serif, a REAL c:chart part and a real a:tbl (python-pptx
  natives) so chart/table capability matching is exercised on unseen
  structure;
- ``synthetic_unseen_sparse.pptx`` — 16:9 but only 4 slides: title, one
  content exemplar, one chart slide, one picture slide. A 12-slide plan
  is forced to reuse the same exemplars → exercises shared-part unshare
  (chart/media) and honest drops;
- ``synthetic_unseen_dense.pptx`` — 16:10 aspect (third distinct ratio),
  dark slate theme, overloaded exemplars: 9-bullet bodies, two tables, a
  dense KPI band — audit density/overflow rules fire by design.

Run: ``.venv/bin/python scripts/build_unseen_templates.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "pptx"


def _rgb(hexv: str) -> RGBColor:
    return RGBColor.from_string(hexv)


def _solid(shape, hexv: str) -> None:
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(hexv)


def _text(shape, lines: list[tuple[str, float, str, bool]], font: str) -> None:
    tf = shape.text_frame
    tf.word_wrap = True
    for i, (text, size, color, bold) in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = _rgb(color)
        run.font.name = font


def _box(slide, x, y, w, h, fill_hex=None):
    shape = slide.shapes.add_shape(
        1, Inches(x), Inches(y), Inches(w), Inches(h)
    )  # MSO_SHAPE.RECTANGLE
    if fill_hex:
        _solid(shape, fill_hex)
    else:
        shape.fill.background()
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _png(path: Path, w: int, h: int, bg: str, dot: str) -> Path:
    img = Image.new("RGB", (w, h), "#" + bg)
    d = ImageDraw.Draw(img)
    d.rectangle((w // 5, h // 5, 4 * w // 5, 4 * h // 5), fill="#" + dot)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


# ---------------------------------------------------------------- 4:3 cream
def build_43(out: Path) -> Path:
    """4:3, светлая «бумажная» тема, Georgia; реальные c:chart и a:tbl."""
    CANVAS, PANEL, ACCENT, INK, MUTED = "F6F1E4", "FFFFFF", "6B2D5C", "2B2622", "7A6E62"
    FONT = "Georgia"
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    # title
    s = prs.slides.add_slide(blank)
    _box(s, 0, 0, 10, 7.5, CANVAS)
    _box(s, 0.7, 2.6, 8.6, 0.06, ACCENT)
    t = s.shapes.add_textbox(Inches(0.7), Inches(1.6), Inches(8.6), Inches(1.0))
    _text(t, [("Печатный квартальный обзор", 34, INK, True)], FONT)
    sub = s.shapes.add_textbox(Inches(0.7), Inches(2.9), Inches(8.6), Inches(0.6))
    _text(sub, [("4:3 unseen-шаблон — светлая тема, serif-шрифт", 15, MUTED, False)], FONT)

    # content exemplar: шапка + две полосы текста
    s = prs.slides.add_slide(blank)
    _box(s, 0, 0, 10, 7.5, CANVAS)
    _box(s, 0, 0, 10, 0.9, ACCENT)
    h = s.shapes.add_textbox(Inches(0.5), Inches(0.15), Inches(9), Inches(0.6))
    _text(h, [("Раздел: операционные метрики", 20, "FFFFFF", True)], FONT)
    for i in range(2):
        y = 1.3 + i * 2.9
        _box(s, 0.5, y, 9.0, 2.6, PANEL)
        head = s.shapes.add_textbox(Inches(0.8), Inches(y + 0.15), Inches(8.4), Inches(0.5))
        _text(head, [("Подраздел", 14, ACCENT, True)], FONT)
        body = s.shapes.add_textbox(Inches(0.8), Inches(y + 0.7), Inches(8.4), Inches(1.7))
        _text(
            body,
            [("Тело подраздела содержит пояснения и факты, "
              "которые переносятся в слот при заливке.", 11, INK, False)],
            FONT,
        )

    # chart exemplar — реальный c:chart + embedded workbook
    s = prs.slides.add_slide(blank)
    _box(s, 0, 0, 10, 7.5, CANVAS)
    _box(s, 0, 0, 10, 0.9, ACCENT)
    h = s.shapes.add_textbox(Inches(0.5), Inches(0.15), Inches(9), Inches(0.6))
    _text(h, [("Годовая динамика", 20, "FFFFFF", True)], FONT)
    cd = CategoryChartData()
    cd.categories = ["Сегмент A", "Сегмент B", "Сегмент C", "Сегмент D"]
    cd.add_series("План", (10.0, 14.0, 12.5, 16.0))
    s.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(0.8), Inches(1.3), Inches(8.4), Inches(5.6), cd,
    )

    # table exemplar — реальная a:tbl
    s = prs.slides.add_slide(blank)
    _box(s, 0, 0, 10, 7.5, CANVAS)
    _box(s, 0, 0, 10, 0.9, ACCENT)
    h = s.shapes.add_textbox(Inches(0.5), Inches(0.15), Inches(9), Inches(0.6))
    _text(h, [("Сводная таблица", 20, "FFFFFF", True)], FONT)
    tbl_shape = s.shapes.add_table(4, 3, Inches(0.8), Inches(1.4), Inches(8.4), Inches(4.6))
    tbl = tbl_shape.table
    for r, row in enumerate(
        [("Метрика", "Значение", "Комментарий"),
         ("Покрытие", "84%", "по регионам"),
         ("Отток", "3.1%", "ниже плана"),
         ("NPS", "52", "стабилен")]
    ):
        for c, val in enumerate(row):
            cell = tbl.cell(r, c)
            cell.text = val
            cell.text_frame.paragraphs[0].runs[0].font.size = Pt(12)

    # picture slide
    s = prs.slides.add_slide(blank)
    _box(s, 0, 0, 10, 7.5, CANVAS)
    _box(s, 0, 0, 10, 0.9, ACCENT)
    h = s.shapes.add_textbox(Inches(0.5), Inches(0.15), Inches(9), Inches(0.6))
    _text(h, [("Иллюстрация", 20, "FFFFFF", True)], FONT)
    img = _png(Path(tempfile.gettempdir()) / "unseen43_art.png", 480, 320, PANEL, ACCENT)
    s.shapes.add_picture(str(img), Inches(1.6), Inches(1.6), width=Inches(6.8))
    cap = s.shapes.add_textbox(Inches(1.6), Inches(6.4), Inches(6.8), Inches(0.5))
    _text(cap, [("Подпись под иллюстрацией", 11, MUTED, False)], FONT)

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    return out


# ---------------------------------------------------------------- sparse 16:9
def build_sparse(out: Path) -> Path:
    """Всего 4 слайда — план вынужден переиспользовать exemplar'ы (unshare)."""
    CANVAS, PANEL, ACCENT, INK, MUTED = "0F1B2E", "1B2C42", "4CC9F0", "E8ECF1", "8FA3BD"
    FONT = "Calibri"
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    s = prs.slides.add_slide(blank)
    _box(s, 0, 0, 13.333, 7.5, CANVAS)
    t = s.shapes.add_textbox(Inches(0.8), Inches(3.0), Inches(11), Inches(1.0))
    _text(t, [("Минималистичный unseen", 36, INK, True)], FONT)
    _box(s, 0.8, 4.2, 2.0, 0.08, ACCENT)

    # единственный текстовый exemplar
    s = prs.slides.add_slide(blank)
    _box(s, 0, 0, 13.333, 7.5, CANVAS)
    h = s.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11), Inches(0.7))
    _text(h, [("Единственный контентный exemplar", 24, INK, True)], FONT)
    body = s.shapes.add_textbox(Inches(0.8), Inches(1.5), Inches(11), Inches(5.0))
    _text(
        body,
        [("Первый абзац с фактом.", 14, MUTED, False),
         ("Второй абзац с деталями.", 14, MUTED, False),
         ("Третий абзац с выводом.", 14, MUTED, False)],
        FONT,
    )

    # chart exemplar (чтобы unshare реально сработал на повторном использовании)
    s = prs.slides.add_slide(blank)
    _box(s, 0, 0, 13.333, 7.5, CANVAS)
    h = s.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11), Inches(0.7))
    _text(h, [("Единственный chart-exemplar", 24, INK, True)], FONT)
    cd = CategoryChartData()
    cd.categories = ["A", "B", "C"]
    cd.add_series("S", (5.0, 8.0, 6.0))
    s.shapes.add_chart(
        XL_CHART_TYPE.LINE, Inches(1.2), Inches(1.6), Inches(10.8), Inches(5.2), cd,
    )

    # picture exemplar
    s = prs.slides.add_slide(blank)
    _box(s, 0, 0, 13.333, 7.5, CANVAS)
    img = _png(Path(tempfile.gettempdir()) / "unseen_sparse.png", 800, 400, PANEL, ACCENT)
    s.shapes.add_picture(str(img), Inches(2.6), Inches(1.2), width=Inches(8))
    cap = s.shapes.add_textbox(Inches(2.6), Inches(6.4), Inches(8), Inches(0.5))
    _text(cap, [("Картиночный exemplar", 12, MUTED, False)], FONT)

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    return out


# ---------------------------------------------------------------- dense 16:9
def build_dense(out: Path) -> Path:
    """Перегруженные exemplar'ы: длинные буллеты, две таблицы, KPI-лента."""
    CANVAS, PANEL, ACCENT, INK, MUTED = "232B33", "2E3842", "7BD389", "E9EDE9", "96A39E"
    FONT = "Courier New"
    prs = Presentation()
    prs.slide_width = Inches(12.8)
    prs.slide_height = Inches(8.0)
    blank = prs.slide_layouts[6]

    s = prs.slides.add_slide(blank)
    _box(s, 0, 0, 12.8, 8.0, CANVAS)
    t = s.shapes.add_textbox(Inches(0.7), Inches(3.0), Inches(12), Inches(1.0))
    _text(t, [("Плотный техно-отчёт", 34, ACCENT, True)], FONT)

    # exemplar: 9 буллетов + боковая панель
    s = prs.slides.add_slide(blank)
    _box(s, 0, 0, 12.8, 8.0, CANVAS)
    _box(s, 0, 0, 12.8, 0.8, PANEL)
    h = s.shapes.add_textbox(Inches(0.5), Inches(0.1), Inches(12), Inches(0.6))
    _text(h, [("Статус портфеля — детально", 22, INK, True)], FONT)
    body = s.shapes.add_textbox(Inches(0.5), Inches(1.0), Inches(8.0), Inches(6.0))
    tf = body.text_frame
    tf.word_wrap = True
    for i, txt in enumerate(
        [
            "Стрим A: миграция завершена на 78%, критический путь — биллинг",
            "Стрим B: 41 из 52 сервисов переведены на новую шину событий",
            "Стрим C: техдолг закрыт на 60%, остаток — легаси-отчётность",
            "Инциденты: 2 SEV-3 за период, оба закрыты без эскалации",
            "Найм: 9 офферов принято, 4 в процессе, воронка стабильна",
            "Бюджет: расход 87% от плана, отклонение в пределах нормы",
            "Риски: поставщик CDN подтвердил миграцию до конца квартала",
            "Комплаенс: аудит завершён, замечаний уровня blocker нет",
            "Итог: портфель в графике, фокус следующего периода — биллинг",
        ]
    ):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        r = p.add_run()
        r.text = "— " + txt
        r.font.size = Pt(12)
        r.font.color.rgb = _rgb(INK)
        r.font.name = FONT
    side = s.shapes.add_textbox(Inches(9.0), Inches(1.0), Inches(3.8), Inches(6.0))
    _text(side, [("Боковая колонка с примечаниями и оговорками по цифрам.",
                  11, MUTED, False)], FONT)

    # exemplar с двумя таблицами
    s = prs.slides.add_slide(blank)
    _box(s, 0, 0, 12.8, 8.0, CANVAS)
    h = s.shapes.add_textbox(Inches(0.5), Inches(0.2), Inches(12), Inches(0.6))
    _text(h, [("Две таблицы на одном слайде", 22, INK, True)], FONT)
    for i in range(2):
        ts = s.shapes.add_table(
            3, 4, Inches(0.5 + i * 6.2), Inches(1.2), Inches(5.9), Inches(3.4)
        )
        tb = ts.table
        for r in range(3):
            for c in range(4):
                tb.cell(r, c).text = f"T{i+1} r{r} c{c}"
    foot = s.shapes.add_textbox(Inches(0.5), Inches(5.0), Inches(12), Inches(1.8))
    _text(foot, [("Сноска: значения округлены до целых.", 11, MUTED, False)], FONT)

    # KPI-лента (другой паттерн, чем у synthetic_unseen)
    s = prs.slides.add_slide(blank)
    _box(s, 0, 0, 12.8, 8.0, CANVAS)
    h = s.shapes.add_textbox(Inches(0.5), Inches(0.2), Inches(12), Inches(0.6))
    _text(h, [("KPI-лента", 22, INK, True)], FONT)
    for i in range(5):
        x = 0.4 + i * 2.5
        _box(s, x, 1.5, 2.3, 4.5, PANEL)
        n = s.shapes.add_textbox(Inches(x + 0.15), Inches(1.8), Inches(2.0), Inches(0.8))
        _text(n, [(f"{i * 17 + 23}%", 30, ACCENT, True)], FONT)
        c = s.shapes.add_textbox(Inches(x + 0.15), Inches(2.8), Inches(2.0), Inches(2.8))
        _text(c, [("подпись показателя в две строки с пояснением",
                   10, MUTED, False)], FONT)

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    return out


def main() -> int:
    builders = {
        "synthetic_unseen_43.pptx": build_43,
        "synthetic_unseen_sparse.pptx": build_sparse,
        "synthetic_unseen_dense.pptx": build_dense,
    }
    for name, fn in builders.items():
        print(f"built {fn(FIXTURES / name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
