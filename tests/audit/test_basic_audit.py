"""Базовые детерминированные правила Render Arena (backend/deckdna/audit).

Каждое правило проверяется отдельно на заведомо плохой и заведомо хорошей
фикстуре, собранной python-pptx на лету.
"""

import json
from pathlib import Path

import pytest
from deckdna.audit.basic import (
    RULE_ANCHOR_POSITION,
    RULE_ASPECT_RATIO,
    RULE_BULLET_COUNT,
    RULE_BULLET_LENGTH,
    RULE_CHART_METADATA,
    RULE_CHART_SERIES,
    RULE_COLOR_PALETTE,
    RULE_CONTRAST,
    RULE_DUPLICATE_SLIDE,
    RULE_EDGE_MARGIN,
    RULE_EMPTY_SLIDE,
    RULE_FONT_FAMILY,
    RULE_FONT_FLOOR,
    RULE_FONT_SCALE,
    RULE_LAYOUT_ORIGIN,
    RULE_OCCUPANCY,
    RULE_OUT_OF_BOUNDS,
    RULE_PACKAGE,
    RULE_PLACEHOLDER_TEXT,
    RULE_RASTER_ONLY,
    RULE_SLIDE_CLIP,
    RULE_TABLE_SIZE,
    RULE_TEXT_OVERFLOW,
    RULE_UNINTENDED_OVERLAP,
    _Ctx,
    audit_deck,
    check_layout_origin,
)
from deckdna.audit.config import load_audit_config
from deckdna.repair.planner import plan_repairs_with_report
from jsonschema import validate
from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE
from pptx.util import Inches, Pt

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "audit-issue.schema.json"


def _png(tmp_path, w=64, h=64):
    p = tmp_path / "img.png"
    Image.new("RGB", (w, h), "navy").save(p)
    return p


def _blank_slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _issues_by_rule(issues, rule_code):
    return [i for i in issues if i.rule_code == rule_code]


def test_schema_validates_all_issues(tmp_path):
    """Каждый выданный issue соответствует audit-issue.schema.json."""
    prs = Presentation()
    slide = _blank_slide(prs)
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(0.3))
    box.text_frame.word_wrap = True
    box.text_frame.auto_size = MSO_AUTO_SIZE.NONE
    box.text_frame.text = "очень длинный текст " * 30
    slide.shapes.add_picture(
        str(_png(tmp_path)), 0, 0, width=prs.slide_width, height=prs.slide_height // 3
    )
    path = tmp_path / "deck.pptx"
    prs.save(path)

    schema = json.loads(SCHEMA_PATH.read_text())
    issues = audit_deck(path)
    assert issues, "фикстура должна дать хотя бы один issue"
    for issue in issues:
        validate(instance=issue.to_dict(), schema=schema)
        assert issue.deterministic is True
        assert issue.status == "open"
        if issue.bbox is not None:
            # bbox для UI overlay — нормализованные координаты [0,1], не EMU
            for key in ("x", "y", "w", "h"):
                assert 0.0 <= issue.bbox[key] <= 1.0, (key, issue.bbox)


class TestTextOverflow:
    def _deck(self, tmp_path, text, height):
        prs = Presentation()
        slide = _blank_slide(prs)
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), height)
        tf = box.text_frame
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.NONE  # иначе фигура растёт под текст
        tf.text = text
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad(self, tmp_path):
        """Длинный текст в узкой низкой рамке без autofit → text_overflow."""
        path = self._deck(tmp_path, "длинный текст который не помещается " * 20, Inches(0.3))
        issues = _issues_by_rule(audit_deck(path), RULE_TEXT_OVERFLOW)
        assert issues
        issue = issues[0]
        assert issue.severity == "error"
        assert issue.slide_index == 0
        assert issue.shape_ids
        assert issue.measured_value > issue.threshold

    def test_good(self, tmp_path):
        """Короткий текст в просторной рамке → без issue."""
        path = self._deck(tmp_path, "короткий текст", Inches(2))
        assert _issues_by_rule(audit_deck(path), RULE_TEXT_OVERFLOW) == []

    def test_unbreakable_word(self, tmp_path):
        """Одно слово без пробелов шире рамки не может перенестись и
        вылезает горизонтально → text_overflow даже при достаточной высоте."""
        path = self._deck(tmp_path, "ОченьДлинноеСловоБезПробелов" * 3, Inches(3))
        issues = _issues_by_rule(audit_deck(path), RULE_TEXT_OVERFLOW)
        assert issues
        assert "unbreakable" in issues[0].evidence[0]["detail"]

    def test_word_wrap_packing(self, tmp_path):
        """Жадный перенос по словам: три широких слова занимают три строки,
        а не ceil(суммарная_ширина / ширина_рамки)."""
        path = self._deck(
            tmp_path,
            "Длинноеслово Длинноеслово Длинноеслово",  # каждое ~ширина рамки
            Inches(0.9),  # хватает ровно на ~2 строки 18pt, не на 3
        )
        issues = _issues_by_rule(audit_deck(path), RULE_TEXT_OVERFLOW)
        assert issues
        assert "estimated text height" in issues[0].evidence[0]["detail"]


class TestGroupedTextOverlap:
    def test_overlapping_text_children_are_reported(self, tmp_path):
        """Collisions inside a donor group must not bypass overlap audit."""
        prs = Presentation()
        slide = _blank_slide(prs)
        group = slide.shapes.add_group_shape()
        first = group.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
        first.text_frame.text = "Первый текст"
        second = group.shapes.add_textbox(Inches(2.5), Inches(1), Inches(3), Inches(1))
        second.text_frame.text = "Второй текст"
        path = tmp_path / "group-overlap.pptx"
        prs.save(path)

        issues = _issues_by_rule(audit_deck(path), RULE_UNINTENDED_OVERLAP)
        assert issues
        assert {str(first.shape_id), str(second.shape_id)} <= set(issues[0].shape_ids)


class TestEmptySlide:
    def test_bad(self, tmp_path):
        """Слайд без единой фигуры с текстом или картинкой → empty_slide."""
        prs = Presentation()
        slide = _blank_slide(prs)
        slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(1), Inches(1), Inches(2), Inches(2)
        )  # декоративная фигура без текста не спасает от empty_slide
        path = tmp_path / "deck.pptx"
        prs.save(path)
        issues = _issues_by_rule(audit_deck(path), RULE_EMPTY_SLIDE)
        assert issues
        assert issues[0].severity == "error"  # frozen severity: integrity.empty_slide
        assert issues[0].slide_index == 0

    def test_good(self, tmp_path):
        """Слайд с текстовой фигурой → без issue."""
        prs = Presentation()
        slide = _blank_slide(prs)
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
        box.text_frame.text = "контент есть"
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_EMPTY_SLIDE) == []


class TestAspectRatio:
    def _deck(self, tmp_path, pic_w, pic_h, img=(64, 64)):
        prs = Presentation()
        slide = _blank_slide(prs)
        slide.shapes.add_picture(
            str(_png(tmp_path, *img)), Inches(1), Inches(1), width=pic_w, height=pic_h
        )
        box = slide.shapes.add_textbox(Inches(1), Inches(4), Inches(3), Inches(0.5))
        box.text_frame.text = "подпись"
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad(self, tmp_path):
        """Квадратная картинка растянута 4:1 → aspect_ratio."""
        path = self._deck(tmp_path, Inches(4), Inches(1))
        issues = _issues_by_rule(audit_deck(path), RULE_ASPECT_RATIO)
        assert issues
        assert issues[0].shape_ids
        assert issues[0].measured_value == pytest.approx(4.0, abs=0.01)

    def test_good(self, tmp_path):
        """Картинка в исходных пропорциях → без issue."""
        path = self._deck(tmp_path, Inches(2), Inches(2))
        assert _issues_by_rule(audit_deck(path), RULE_ASPECT_RATIO) == []


class TestPlaceholderPicture:
    """Картинка, вставленная через picture placeholder (insert_picture),
    имеет shape_type PLACEHOLDER, а не PICTURE — аудит должен её видеть."""

    def _deck(self, tmp_path):
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[8])  # Picture with Caption
        ph = next(
            p
            for p in slide.placeholders
            if p.placeholder_format.type == PP_PLACEHOLDER.PICTURE
        )
        return prs, slide, ph

    def test_placeholder_picture_counts_as_content(self, tmp_path):
        """Слайд только с placeholder-картинкой — не empty_slide."""
        prs, slide, ph = self._deck(tmp_path)
        pic = ph.insert_picture(str(_png(tmp_path)))
        assert pic.shape_type != MSO_SHAPE_TYPE.PICTURE  # именно placeholder
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_EMPTY_SLIDE) == []

    def test_placeholder_picture_aspect_and_raster(self, tmp_path):
        """Placeholder-картинка на весь слайд без текста с нарушенными
        пропорциями → aspect_ratio и raster_only срабатывают."""
        prs, slide, ph = self._deck(tmp_path)
        pic = ph.insert_picture(str(_png(tmp_path)))  # квадратная картинка
        pic.left = pic.top = 0
        pic.width, pic.height = prs.slide_width, prs.slide_height  # растянута
        # insert_picture кропит под пропорции слота — обнуляем crop,
        # иначе источник формально совпадает с новым аспектом фрейма
        pic.crop_left = pic.crop_right = pic.crop_top = pic.crop_bottom = 0
        path = tmp_path / "deck.pptx"
        prs.save(path)
        issues = audit_deck(path)
        assert _issues_by_rule(issues, RULE_ASPECT_RATIO)
        assert _issues_by_rule(issues, RULE_RASTER_ONLY)


class TestRasterOnly:
    def test_bad(self, tmp_path):
        """Одна картинка на весь слайд без текста → raster_only (blocker)."""
        prs = Presentation()
        slide = _blank_slide(prs)
        slide.shapes.add_picture(
            str(_png(tmp_path, 320, 240)),
            0,
            0,
            width=prs.slide_width,
            height=prs.slide_height,
        )
        path = tmp_path / "deck.pptx"
        prs.save(path)
        issues = _issues_by_rule(audit_deck(path), RULE_RASTER_ONLY)
        assert issues
        issue = issues[0]
        assert issue.severity == "blocker"
        assert issue.measured_value >= issue.threshold

    def test_good(self, tmp_path):
        """Большая картинка + текст → не raster-only."""
        prs = Presentation()
        slide = _blank_slide(prs)
        slide.shapes.add_picture(
            str(_png(tmp_path, 320, 240)),
            0,
            0,
            width=prs.slide_width,
            height=prs.slide_height,
        )
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
        box.text_frame.text = "заголовок поверх фото"
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_RASTER_ONLY) == []


def test_audit_deck_finds_seeded_defects(tmp_path):
    """Смешанный плохой дек: все четыре правила срабатывают на своих слайдах."""
    prs = Presentation()

    s_overflow = _blank_slide(prs)
    box = s_overflow.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(0.3))
    box.text_frame.word_wrap = True
    box.text_frame.auto_size = MSO_AUTO_SIZE.NONE
    box.text_frame.text = "переполнение " * 30

    _blank_slide(prs)  # empty

    s_pic = _blank_slide(prs)
    s_pic.shapes.add_picture(
        str(_png(tmp_path)), Inches(1), Inches(1), width=Inches(4), height=Inches(1)
    )
    ok_box = s_pic.shapes.add_textbox(Inches(1), Inches(4), Inches(3), Inches(0.5))
    ok_box.text_frame.text = "есть текст"

    s_raster = _blank_slide(prs)
    s_raster.shapes.add_picture(
        str(_png(tmp_path)), 0, 0, width=prs.slide_width, height=prs.slide_height
    )

    path = tmp_path / "mixed.pptx"
    prs.save(path)
    issues = audit_deck(path)
    found = {i.rule_code for i in issues}
    assert {RULE_TEXT_OVERFLOW, RULE_EMPTY_SLIDE, RULE_ASPECT_RATIO, RULE_RASTER_ONLY} <= found
    # индексы слайдов ведут к правильным слайдам
    assert all(i.slide_index is not None for i in issues)


class TestOutOfBounds:
    def _deck(self, tmp_path, left, top, width, height):
        prs = Presentation()
        slide = _blank_slide(prs)
        box = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
        box.text_frame.text = "рамка"
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_right_edge(self, tmp_path):
        """Фигура вылезает за правый край слайда → out_of_bounds (error)."""
        prs = Presentation()
        slide = _blank_slide(prs)
        slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            int(prs.slide_width * 0.8),
            Inches(1),
            int(prs.slide_width * 0.5),  # right edge = 130% слайда
            Inches(1),
        )
        path = tmp_path / "deck.pptx"
        prs.save(path)
        issues = _issues_by_rule(audit_deck(path), RULE_OUT_OF_BOUNDS)
        assert len(issues) == 1
        assert issues[0].severity == "error"  # frozen: layout.out_of_bounds
        assert issues[0].shape_ids
        assert issues[0].measured_value > issues[0].threshold

    def test_bad_negative_left(self, tmp_path):
        """Отрицательный left дальше допуска → out_of_bounds."""
        path = self._deck(tmp_path, -Inches(1), Inches(1), Inches(2), Inches(1))
        issues = _issues_by_rule(audit_deck(path), RULE_OUT_OF_BOUNDS)
        assert issues

    def test_good_inside(self, tmp_path):
        """Фигура целиком внутри → без issue."""
        path = self._deck(tmp_path, Inches(1), Inches(1), Inches(3), Inches(2))
        assert _issues_by_rule(audit_deck(path), RULE_OUT_OF_BOUNDS) == []

    @pytest.mark.parametrize("top,expected", [(5.44, True), (1.0, False)])
    def test_wrapped_table_rows_expand_past_slide(self, tmp_path, top, expected):
        """Declared table frame fits, but wrapped headers can push rows below it."""
        prs = Presentation()
        prs.slide_width = Inches(13.333)
        slide = _blank_slide(prs)
        frame = slide.shapes.add_table(
            4, 5, Inches(9.08), Inches(top), Inches(3.76), Inches(1.62)
        )
        values = [
            ["Район", "До пилота, дней", "После пилота, дней", "Потери до, %", "Потери после, %"],
            ["Север", "12", "4", "19", "15"],
            ["Центр", "10", "3", "17", "14"],
            ["Юг", "11", "5", "18", "16"],
        ]
        for i, row in enumerate(values):
            for j, text in enumerate(row):
                frame.table.cell(i, j).text = text
                for paragraph in frame.table.cell(i, j).text_frame.paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(18)
        path = tmp_path / f"table-{top}.pptx"
        prs.save(path)
        issues = _issues_by_rule(audit_deck(path), RULE_OUT_OF_BOUNDS)
        table_issues = [i for i in issues if str(frame.shape_id) in i.shape_ids]
        assert bool(table_issues) is expected
        if expected:
            assert "estimated rendered bottom" in table_issues[0].evidence[0]["detail"]
            assert table_issues[0].repairable is False
            plan = plan_repairs_with_report(table_issues)
            assert plan.actions == []
            assert table_issues[0].id in plan.unresolved

    def test_within_tolerance_not_flagged(self, tmp_path):
        """Микро-вылет в пределах допуска (~0.2% слайда) не считается."""
        prs = Presentation()
        slide = _blank_slide(prs)
        slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            int(prs.slide_width * 0.998),
            Inches(1),
            int(prs.slide_width * 0.004),  # right = 100.2% < tolerance
            Inches(1),
        )
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_OUT_OF_BOUNDS) == []

    def test_group_child_out_of_bounds(self, tmp_path):
        """Дитя grpSp, уходящее за край после chOff/chExt→slide конверсии,
        ловится — группа сама при этом в границах."""
        prs = Presentation()
        slide = _blank_slide(prs)
        group = slide.shapes.add_group_shape()
        child = group.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(1), Inches(1), Inches(1), Inches(1)
        )
        child.text_frame.text = "рамка"
        # масштаб child-space: группа off=(2",1") ext=(2",2"),
        # chOff=(2",1") chExt=(4",4") → child at (22",1") попадает в
        # slide (12",1"): правый край 13" > 10"-слайда, сама группа 4"
        xfrm = group.element.grpSpPr.xfrm
        xfrm.off_x, xfrm.off_y = Inches(2), Inches(1)
        xfrm.ext_cx, xfrm.ext_cy = Inches(2), Inches(2)
        xfrm.chOff_x, xfrm.chOff_y = Inches(2), Inches(1)
        xfrm.chExt_cx, xfrm.chExt_cy = Inches(4), Inches(4)
        child.left, child.top = Inches(22), Inches(1)
        child.width, child.height = Inches(2), Inches(2)
        path = tmp_path / "deck.pptx"
        prs.save(path)
        issues = _issues_by_rule(audit_deck(path), RULE_OUT_OF_BOUNDS)
        assert len(issues) == 1
        assert str(child.shape_id) in issues[0].shape_ids
        assert issues[0].bbox["x"] > 1.0  # 12" на 10" слайде

    def test_group_child_in_bounds_not_flagged(self, tmp_path):
        """Дитя grpSp, остающееся в границах после конверсии — без issue."""
        prs = Presentation()
        slide = _blank_slide(prs)
        group = slide.shapes.add_group_shape()
        child = group.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(1), Inches(1), Inches(1), Inches(1)
        )
        child.text_frame.text = "рамка"
        xfrm = group.element.grpSpPr.xfrm
        xfrm.off_x, xfrm.off_y = Inches(2), Inches(1)
        xfrm.ext_cx, xfrm.ext_cy = Inches(2), Inches(2)
        xfrm.chOff_x, xfrm.chOff_y = Inches(2), Inches(1)
        xfrm.chExt_cx, xfrm.chExt_cy = Inches(4), Inches(4)
        child.left, child.top = Inches(2), Inches(1)  # → slide (2",1"), 1×1"
        child.width, child.height = Inches(2), Inches(2)  # → 1" в slide-space
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_OUT_OF_BOUNDS) == []


class TestDuplicateSlide:
    def _two_slide_deck(self, tmp_path, text_a, text_b):
        prs = Presentation()
        for text in (text_a, text_b):
            slide = _blank_slide(prs)
            box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
            box.text_frame.text = text
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_identical(self, tmp_path):
        """Второй слайд с тем же текстом → duplicate на втором (warning)."""
        path = self._two_slide_deck(
            tmp_path,
            "Квартальный отчёт по продажам за первый квартал",
            "Квартальный отчёт по продажам за первый квартал",
        )
        issues = _issues_by_rule(audit_deck(path), RULE_DUPLICATE_SLIDE)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == "warning"  # frozen: integrity.duplicate_slide
        assert issue.slide_index == 1  # флагуется повтор, не оригинал
        assert issue.measured_value >= issue.threshold
        assert "слайду[0]" in issue.message  # оригинал — index-space slide_index

    def test_bad_near_duplicate(self, tmp_path):
        """Второй слайд — текст первого плюс одно слово → jaccard 11/12 ≥ 0.9."""
        path = self._two_slide_deck(
            tmp_path,
            "Проблема ручной сборки колод занимает часы работы всей команды проекта",
            "Проблема ручной сборки колод занимает часы работы всей команды проекта сейчас",
        )
        issues = _issues_by_rule(audit_deck(path), RULE_DUPLICATE_SLIDE)
        assert len(issues) == 1
        assert issues[0].measured_value >= 0.9

    def test_good_different(self, tmp_path):
        """Разный текст → без issue."""
        path = self._two_slide_deck(
            tmp_path,
            "Проблема: ручная сборка колод занимает часы",
            "Решение: автоматический компилятор презентаций",
        )
        assert _issues_by_rule(audit_deck(path), RULE_DUPLICATE_SLIDE) == []

    def test_empty_pair_not_duplicates(self, tmp_path):
        """Два пустых слайда — зона empty_slide, не duplicate."""
        prs = Presentation()
        _blank_slide(prs)
        _blank_slide(prs)
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_DUPLICATE_SLIDE) == []

    def test_shared_title_not_duplicate(self, tmp_path):
        """Общий заголовок+футер при разном теле — не дубликат."""
        path = self._two_slide_deck(
            tmp_path,
            "Отчёт Продукт Продажи выросли на сорок процентов год к году",
            "Отчёт Продукт Убытки космических программ сократились вдвое",
        )
        assert _issues_by_rule(audit_deck(path), RULE_DUPLICATE_SLIDE) == []


class TestPlaceholderText:
    def _deck(self, tmp_path, text):
        prs = Presentation()
        slide = _blank_slide(prs)
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
        box.text_frame.text = text
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_stock_captions(self, tmp_path):
        """Целые фреймы-заглушки: «Заголовок», «Текст», «Подзаголовок»,
        «Заголовок в две строчки», «Основной текст» → error."""
        prs = Presentation()
        slide = _blank_slide(prs)
        for i, t in enumerate(
            ["Заголовок", "Текст", "Подзаголовок",
             "Заголовок в две или в одну строчку", "Основной текст"]
        ):
            box = slide.shapes.add_textbox(
                Inches(1), Inches(0.4 + i), Inches(4), Inches(0.4)
            )
            box.text_frame.text = t
        path = tmp_path / "deck.pptx"
        prs.save(path)
        issues = _issues_by_rule(audit_deck(path), RULE_PLACEHOLDER_TEXT)
        assert len(issues) == 5
        assert all(i.severity == "error" for i in issues)

    def test_bad_english_stock_label_product(self, tmp_path):
        """The bare donor label ``product`` is a placeholder, not content."""
        issues = _issues_by_rule(
            audit_deck(self._deck(tmp_path, "product")), RULE_PLACEHOLDER_TEXT
        )
        assert len(issues) == 1
        assert issues[0].severity == "error"

    def test_bad_strong_markers_mid_text(self, tmp_path):
        """lorem/TODO как маркер внутри любого текста → error."""
        for marker in (
            "Lorem ipsum dolor sit amet",
            "Секция про метрики TODO доделать",
        ):
            issues = _issues_by_rule(
                audit_deck(self._deck(tmp_path, marker)), RULE_PLACEHOLDER_TEXT
            )
            assert len(issues) == 1, marker
            assert "strong marker" in issues[0].evidence[0]["detail"]

    def test_good_legit_short_titles(self, tmp_path):
        """Легитимные короткие заголовки не флагаются: слово «текст»
        внутри фразы и «заголовок» с настоящим продолжением."""
        for text in (
            "Выводы",
            "Риски",
            "Заголовок годового отчёта",   # не стоковый вариант — нет «строчк»
            "Текст письма регулятору",
            "Основной драйвер роста",
            "Пункт 4.2 договора",
        ):
            issues = _issues_by_rule(
                audit_deck(self._deck(tmp_path, text)), RULE_PLACEHOLDER_TEXT
            )
            assert issues == [], text


class TestUnintendedOverlap:
    def _deck(self, tmp_path, boxes, pictures=()):
        """boxes/pictures: (x, y, w, h[, text]) в долях сторон слайда."""
        prs = Presentation()
        slide = _blank_slide(prs)
        sw, sh = prs.slide_width, prs.slide_height
        for x, y, w, h, t in boxes:
            box = slide.shapes.add_textbox(
                int(sw * x), int(sh * y), int(sw * w), int(sh * h)
            )
            box.text_frame.text = t
        for x, y, w, h in pictures:
            slide.shapes.add_picture(
                str(_png(tmp_path)), int(sw * x), int(sh * y), int(sw * w), int(sh * h)
            )
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_partial_text_overlap(self, tmp_path):
        """Два текстовых блока частично пересекаются (~56% меньшего)
        → unintended_overlap (error)."""
        path = self._deck(
            tmp_path,
            [
                (0.10, 0.10, 0.40, 0.20, "Первый блок текста"),
                (0.20, 0.15, 0.40, 0.20, "Второй блок текста"),
            ],
        )
        issues = _issues_by_rule(audit_deck(path), RULE_UNINTENDED_OVERLAP)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == "error"
        assert len(issue.shape_ids) == 2
        assert 0.25 <= issue.measured_value < 0.90

    def test_good_containment_is_intentional(self, tmp_path):
        """Маленький текстовый блок ВНУТРИ большого текстового фрейма —
        вложенность (cover 100%), не коллизия → не флагается."""
        path = self._deck(
            tmp_path,
            [
                (0.05, 0.05, 0.50, 0.40, "Карточка с рамкой"),
                (0.20, 0.15, 0.10, 0.10, "Иконка-текст"),
            ],
        )
        issues = _issues_by_rule(audit_deck(path), RULE_UNINTENDED_OVERLAP)
        assert issues == []

    def test_good_text_over_picture_not_flagged(self, tmp_path):
        """Текст частично поверх картинки — стандартный приём дизайна
        (заголовок на фото), консервативно пропускаем."""
        path = self._deck(
            tmp_path,
            [(0.02, 0.06, 0.40, 0.30, "Заголовок слайда")],
            pictures=[(0.20, 0.15, 0.50, 0.60)],
        )
        issues = _issues_by_rule(audit_deck(path), RULE_UNINTENDED_OVERLAP)
        assert issues == []

    def test_bad_overflowing_text_over_picture_is_flagged(self, tmp_path):
        """Живой репро (сгенерированная колода): текст, который не
        помещается в СВОИХ границах (уже сам по себе text.overflow),
        частично перекрывает картинку рядом — это overflow физически
        наезжающий на соседа, не приём дизайна «подпись на фото», и
        теперь флагуется. Та же геометрия, что у
        test_good_text_over_picture_not_flagged (кроме объёма текста) —
        сравнение показывает, что разница именно в overflow, не в
        позиции/размере."""
        prs = Presentation()
        slide = _blank_slide(prs)
        sw, sh = prs.slide_width, prs.slide_height
        box = slide.shapes.add_textbox(
            int(sw * 0.02), int(sh * 0.06), int(sw * 0.40), int(sh * 0.30)
        )
        tf = box.text_frame
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.NONE  # иначе фигура растёт под текст
        tf.text = "длинный текст который не помещается " * 20
        slide.shapes.add_picture(
            str(_png(tmp_path)),
            int(sw * 0.20), int(sh * 0.15), int(sw * 0.50), int(sh * 0.60),
        )
        path = tmp_path / "deck.pptx"
        prs.save(path)

        assert _issues_by_rule(audit_deck(path), RULE_TEXT_OVERFLOW)  # precondition
        issues = _issues_by_rule(audit_deck(path), RULE_UNINTENDED_OVERLAP)
        assert len(issues) == 1
        assert issues[0].severity == "error"

    def test_good_fitting_text_over_large_picture_still_not_flagged(self, tmp_path):
        """Контрольная группа: тот же большой picture-фон, но короткий
        текст, который реально умещается — остаётся незафлагованным
        (не регрессия от нового overflow-гейта)."""
        path = self._deck(
            tmp_path,
            [(0.02, 0.06, 0.40, 0.30, "Кратко")],
            pictures=[(0.20, 0.15, 0.50, 0.60)],
        )
        issues = _issues_by_rule(audit_deck(path), RULE_UNINTENDED_OVERLAP)
        assert issues == []

    def test_good_two_pictures_overlapping_still_ignored(self, tmp_path):
        """Две картинки друг на друге — коллаж/декор, не текст, не
        трогаем (не расширяем гейт на pic-pic пары)."""
        path = self._deck(
            tmp_path,
            [],
            pictures=[(0.10, 0.10, 0.40, 0.40), (0.25, 0.25, 0.40, 0.40)],
        )
        issues = _issues_by_rule(audit_deck(path), RULE_UNINTENDED_OVERLAP)
        assert issues == []

    def test_good_fitting_text_over_small_icon_not_flagged(self, tmp_path):
        """Регрессия на реальном organizer-шаблоне: короткий родной текст
        карточки, частично перекрывающий маленькую decorative-иконку
        (~11% слайда, не hero) — легитимная композиция (карточки
        01-04, соединённые декоративной линией, продетой специально
        сквозь них в vk_tech_template.pptx, слайд 27), не дефект.
        Гейтить по размеру картинки пробовали и откатили именно
        из-за этого случая — тест фиксирует правильное поведение."""
        path = self._deck(
            tmp_path,
            [(0.02, 0.06, 0.40, 0.30, "Короткая подпись")],
            pictures=[(0.20, 0.15, 0.30, 0.35)],  # ~10.5% слайда — не hero
        )
        issues = _issues_by_rule(audit_deck(path), RULE_UNINTENDED_OVERLAP)
        assert issues == []

    def test_good_overflowing_text_over_hero_picture_still_flagged(self, tmp_path):
        """Контрольная группа: overflow не прощается даже над крупной
        hero-картинкой — переполненный текст остаётся дефектом
        независимо от размера картинки под ним."""
        prs = Presentation()
        slide = _blank_slide(prs)
        sw, sh = prs.slide_width, prs.slide_height
        box = slide.shapes.add_textbox(
            int(sw * 0.02), int(sh * 0.06), int(sw * 0.40), int(sh * 0.30)
        )
        tf = box.text_frame
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.NONE
        tf.text = "длинный текст который не помещается " * 20
        slide.shapes.add_picture(  # 30% слайда — заведомо hero-размер
            str(_png(tmp_path)),
            int(sw * 0.20), int(sh * 0.15), int(sw * 0.50), int(sh * 0.60),
        )
        path = tmp_path / "deck.pptx"
        prs.save(path)

        issues = _issues_by_rule(audit_deck(path), RULE_UNINTENDED_OVERLAP)
        assert len(issues) == 1
        assert issues[0].severity == "error"

    def test_good_edge_touch_below_threshold(self, tmp_path):
        """Касание краями (<25% площади меньшей) → не флагается."""
        path = self._deck(
            tmp_path,
            [
                (0.10, 0.10, 0.40, 0.20, "Блок один"),
                (0.45, 0.25, 0.40, 0.20, "Блок два"),  # inter 0.05*0.05
            ],
        )
        issues = _issues_by_rule(audit_deck(path), RULE_UNINTENDED_OVERLAP)
        assert issues == []

    def test_good_empty_frame_and_tiny_accent_ignored(self, tmp_path):
        """Пустой фрейм и крошечный акцент (<1% слайда) не участвуют."""
        path = self._deck(
            tmp_path,
            [
                (0.10, 0.10, 0.40, 0.20, "Контентный текст"),
                (0.20, 0.15, 0.40, 0.20, ""),            # пустой — пропуск
                (0.30, 0.20, 0.02, 0.02, "•"),         # акцент <1% — пропуск
            ],
        )
        issues = _issues_by_rule(audit_deck(path), RULE_UNINTENDED_OVERLAP)
        assert issues == []


class TestEdgeMargin:
    def _deck(self, tmp_path, x, y, w, h, text="контент", kind="text"):
        """kind: text | shape | picture — фигура в долях сторон слайда."""
        prs = Presentation()
        slide = _blank_slide(prs)
        sw, sh = prs.slide_width, prs.slide_height
        if kind == "picture":
            slide.shapes.add_picture(
                str(_png(tmp_path)), int(sw * x), int(sh * y), int(sw * w), int(sh * h)
            )
        else:
            add = slide.shapes.add_textbox if kind == "text" else slide.shapes.add_shape
            if kind == "shape":
                box = add(MSO_SHAPE.RECTANGLE, int(sw * x), int(sh * y), int(sw * w), int(sh * h))
            else:
                box = add(int(sw * x), int(sh * y), int(sw * w), int(sh * h))
            box.text_frame.text = text
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_flush_right(self, tmp_path):
        """Текстовый бокс вплотную к правому краю (gap 0%) → edge_margin."""
        path = self._deck(tmp_path, 0.85, 0.20, 0.15, 0.10)
        issues = _issues_by_rule(audit_deck(path), RULE_EDGE_MARGIN)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == "warning"  # frozen: layout.edge_margin
        assert "right" in issue.message
        assert issue.measured_value < issue.threshold
        assert issue.shape_ids

    def test_bad_flush_left_nearly(self, tmp_path):
        """Зазор 1% от левого края — «почти касается» → edge_margin."""
        path = self._deck(tmp_path, 0.01, 0.20, 0.30, 0.10)
        issues = _issues_by_rule(audit_deck(path), RULE_EDGE_MARGIN)
        assert len(issues) == 1
        assert "left" in issues[0].message

    def test_bad_two_sides_two_issues(self, tmp_path):
        """Бокс прижатый к левому И нижнему краям → два отдельных issue."""
        path = self._deck(tmp_path, 0.0, 0.85, 0.30, 0.15)
        issues = _issues_by_rule(audit_deck(path), RULE_EDGE_MARGIN)
        sides = {i.message.split("к ")[1].split(" ")[0] for i in issues}
        assert sides == {"left", "bottom"}

    def test_good_margined(self, tmp_path):
        """Бокс с отступом 10% от всех краёв → без issue."""
        path = self._deck(tmp_path, 0.10, 0.10, 0.30, 0.20)
        assert _issues_by_rule(audit_deck(path), RULE_EDGE_MARGIN) == []

    def test_good_full_bleed_text_panel(self, tmp_path):
        """Текстовая плашка ≥80% площади слайда — фоновая, не флагается."""
        path = self._deck(tmp_path, 0.0, 0.0, 1.0, 0.85)
        assert _issues_by_rule(audit_deck(path), RULE_EDGE_MARGIN) == []

    def test_good_picture_at_edge_not_flagged(self, tmp_path):
        """Картинка в край — легитимный bleed, правило только про текст."""
        path = self._deck(tmp_path, 0.60, 0.0, 0.40, 0.30, kind="picture")
        assert _issues_by_rule(audit_deck(path), RULE_EDGE_MARGIN) == []

    def test_good_placeholder_geometry_is_template(self, tmp_path):
        """Placeholder, подвинутый в край — авторская геометрия layout,
        не нарушение: правило его пропускает."""
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[0])
        title = slide.shapes.title
        title.text_frame.text = "заголовок у края"
        title.left, title.top = 0, 0
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_EDGE_MARGIN) == []

    def test_bad_full_width_band_top(self, tmp_path):
        """Лента на всю ширину прижата к верху: горизонталь — bleed
        (не проверяется), вертикальная сторона флагается честно."""
        path = self._deck(tmp_path, 0.0, 0.0, 0.99, 0.10)
        issues = _issues_by_rule(audit_deck(path), RULE_EDGE_MARGIN)
        assert len(issues) == 1
        assert "top" in issues[0].message


class TestBulletCount:
    def _deck(self, tmp_path, bullets, tag="buChar"):
        prs = Presentation()
        slide = _blank_slide(prs)
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(3))
        tf = box.text_frame
        tf.word_wrap = True
        for i in range(bullets):
            par = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            par.text = f"Пункт {i}"
            if tag:
                ppr = par._p.get_or_add_pPr()
                bu = ppr.makeelement(
                    "{http://schemas.openxmlformats.org/drawingml/2006/main}" + tag,
                    {} if tag != "buChar" else {"char": "•"},
                )
                ppr.append(bu)
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_seven_bullets(self, tmp_path):
        """7 буллет-параграфов в одном txBody → bullet_count (warning)."""
        path = self._deck(tmp_path, 7)
        issues = _issues_by_rule(audit_deck(path), RULE_BULLET_COUNT)
        assert len(issues) == 1
        assert issues[0].severity == "warning"  # frozen: density.bullet_count
        assert issues[0].measured_value == 7
        assert issues[0].threshold == 6

    def test_bad_numbered_list(self, tmp_path):
        """buAutoNum тоже буллет (нумерованный список)."""
        path = self._deck(tmp_path, 8, tag="buAutoNum")
        assert _issues_by_rule(audit_deck(path), RULE_BULLET_COUNT)

    def test_good_six_bullets(self, tmp_path):
        """Ровно 6 буллетов — на пороге, без issue."""
        path = self._deck(tmp_path, 6)
        assert _issues_by_rule(audit_deck(path), RULE_BULLET_COUNT) == []

    def test_good_plain_paragraphs(self, tmp_path):
        """>6 обычных абзацев без bu* — не буллеты, без issue."""
        path = self._deck(tmp_path, 9, tag=None)
        assert _issues_by_rule(audit_deck(path), RULE_BULLET_COUNT) == []

    def test_good_bullets_split_across_bodies(self, tmp_path):
        """8 буллетов по 4 в двух разных txBody — нет перегруза одного тела."""
        prs = Presentation()
        slide = _blank_slide(prs)
        for x in (1, 5):
            box = slide.shapes.add_textbox(Inches(x), Inches(1), Inches(3), Inches(2))
            tf = box.text_frame
            for i in range(4):
                par = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                par.text = f"Пункт {i}"
                ppr = par._p.get_or_add_pPr()
                bu = ppr.makeelement(
                    "{http://schemas.openxmlformats.org/drawingml/2006/main}buChar",
                    {"char": "•"},
                )
                ppr.append(bu)
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_BULLET_COUNT) == []


class TestFontFloor:
    """text.font_floor — видимый текст мельче порога читаемости (8pt)."""

    def _deck(self, tmp_path, size_pt, w_in=4.0):
        prs = Presentation()
        slide = _blank_slide(prs)
        box = slide.shapes.add_textbox(
            Inches(1), Inches(1), Inches(w_in), Inches(1)
        )
        tf = box.text_frame
        tf.text = "Обычный контентный текст достаточной длины"
        tf.paragraphs[0].runs[0].font.size = Pt(size_pt)
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_six_pt(self, tmp_path):
        """6pt контентный текст → font_floor (error)."""
        issues = _issues_by_rule(audit_deck(self._deck(tmp_path, 6)), RULE_FONT_FLOOR)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].measured_value == 6
        assert issues[0].threshold == 8.0

    def test_good_twelve_pt(self, tmp_path):
        assert _issues_by_rule(audit_deck(self._deck(tmp_path, 12)), RULE_FONT_FLOOR) == []

    def test_good_narrow_micro_label_skipped(self, tmp_path):
        """6pt в узком (<0.5") микро-лейбле — декоративная типографика, skip."""
        path = self._deck(tmp_path, 6, w_in=0.4)
        assert _issues_by_rule(audit_deck(path), RULE_FONT_FLOOR) == []

    def test_floor_from_yaml_changes_behavior(self, tmp_path):
        """font_floor_pt из yaml реально меняет поведение audit_deck."""
        cfg_path = tmp_path / "audit.yaml"
        cfg_path.write_text("font_floor_pt: 5.0\n", encoding="utf-8")
        cfg = load_audit_config(cfg_path)
        path = self._deck(tmp_path, 6)
        assert _issues_by_rule(audit_deck(path, config=cfg), RULE_FONT_FLOOR) == []


class TestContrast:
    """accessibility.contrast — WCAG-отношение текст/подложка < 4.5 (error)."""

    def _deck(self, tmp_path, text_rgb=None, bg_rgb=None, picture=None):
        """Один текстовый бокс; опционально — цветная прямоугольная
        подложка под ним или картинка."""
        prs = Presentation()
        slide = _blank_slide(prs)
        if picture is not None:
            slide.shapes.add_picture(
                str(picture), 0, 0, width=prs.slide_width, height=prs.slide_height
            )
        if bg_rgb is not None:
            rect = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height
            )
            rect.fill.solid()
            rect.fill.fore_color.rgb = bg_rgb
            rect.line.fill.background()
        box = slide.shapes.add_textbox(Inches(3), Inches(3), Inches(4), Inches(1))
        tf = box.text_frame
        tf.text = "Контентный текст для проверки контраста"
        run = tf.paragraphs[0].runs[0]
        run.font.size = Pt(14)
        if text_rgb is not None:
            run.font.color.rgb = text_rgb
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_dark_on_dark(self, tmp_path):
        """Почти чёрный текст на тёмной заливке → contrast (error)."""
        path = self._deck(
            tmp_path, text_rgb=RGBColor(0x20, 0x20, 0x20), bg_rgb=RGBColor(0x25, 0x25, 0x25)
        )
        issues = _issues_by_rule(audit_deck(path), RULE_CONTRAST)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].measured_value < 4.5
        assert issues[0].threshold == 4.5

    def test_good_black_on_white(self, tmp_path):
        """Текст theme dk1 на белом фоне слайда — без issue."""
        issues = _issues_by_rule(audit_deck(self._deck(tmp_path)), RULE_CONTRAST)
        assert issues == []

    def test_good_light_on_dark(self, tmp_path):
        """Белый текст на тёмной плашке-подложке — без issue."""
        path = self._deck(
            tmp_path, text_rgb=RGBColor(0xFF, 0xFF, 0xFF), bg_rgb=RGBColor(0x1A, 0x1A, 0x1A)
        )
        assert _issues_by_rule(audit_deck(path), RULE_CONTRAST) == []

    def test_skip_text_over_picture(self, tmp_path):
        """Текст поверх картинки: фон не сводится к цвету — честный skip."""
        path = self._deck(
            tmp_path, text_rgb=RGBColor(0x20, 0x20, 0x20), picture=_png(tmp_path)
        )
        assert _issues_by_rule(audit_deck(path), RULE_CONTRAST) == []

    def test_ratio_from_yaml_changes_behavior(self, tmp_path):
        """Серый текст на белом: <4.5 флагает; порог 3.0 из yaml — нет."""
        path = self._deck(tmp_path, text_rgb=RGBColor(0x80, 0x80, 0x80))
        assert _issues_by_rule(audit_deck(path), RULE_CONTRAST)
        cfg_path = tmp_path / "audit.yaml"
        cfg_path.write_text("contrast_min_ratio: 3.0\n", encoding="utf-8")
        cfg = load_audit_config(cfg_path)
        assert _issues_by_rule(audit_deck(path, config=cfg), RULE_CONTRAST) == []


class TestBulletLength:
    """density.bullet_length — буллет-параграф длиннее max_words_per_bullet
    (warning). Маркер буллета тот же, что у bullet_count."""

    def _deck(self, tmp_path, words: int, tag: str | None = "buChar", w_in=4.0):
        prs = Presentation()
        slide = _blank_slide(prs)
        box = slide.shapes.add_textbox(
            Inches(1), Inches(1), Inches(w_in), Inches(2)
        )
        par = box.text_frame.paragraphs[0]
        par.text = " ".join(f"слово{i}" for i in range(words))
        if tag:
            ppr = par._p.get_or_add_pPr()
            bu = ppr.makeelement(
                "{http://schemas.openxmlformats.org/drawingml/2006/main}" + tag,
                {"char": "•"} if tag == "buChar" else {},
            )
            ppr.append(bu)
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_long_bullet(self, tmp_path):
        """20 слов в буллете → bullet_length (warning), measured=20."""
        issues = _issues_by_rule(
            audit_deck(self._deck(tmp_path, 20)), RULE_BULLET_LENGTH
        )
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].measured_value == 20
        assert issues[0].threshold == 15

    def test_good_fifteen_words(self, tmp_path):
        """Ровно 15 слов — на пороге, без issue."""
        assert _issues_by_rule(
            audit_deck(self._deck(tmp_path, 15)), RULE_BULLET_LENGTH
        ) == []

    def test_good_long_plain_paragraph(self, tmp_path):
        """25 слов в параграфе БЕЗ буллет-маркера — не буллет, без issue."""
        assert _issues_by_rule(
            audit_deck(self._deck(tmp_path, 25, tag=None)), RULE_BULLET_LENGTH
        ) == []

    def test_good_numbered_long(self, tmp_path):
        """buAutoNum с 20 словами тоже ловится."""
        assert _issues_by_rule(
            audit_deck(self._deck(tmp_path, 20, tag="buAutoNum")),
            RULE_BULLET_LENGTH,
        )

    def test_limit_from_yaml_changes_behavior(self, tmp_path):
        """max_words_per_bullet из yaml реально меняет поведение."""
        path = self._deck(tmp_path, 8)
        assert _issues_by_rule(audit_deck(path), RULE_BULLET_LENGTH) == []
        cfg_path = tmp_path / "audit.yaml"
        cfg_path.write_text("max_words_per_bullet: 5\n", encoding="utf-8")
        cfg = load_audit_config(cfg_path)
        assert _issues_by_rule(audit_deck(path, config=cfg), RULE_BULLET_LENGTH)


class TestTableSize:
    """density.table_size — таблица >7 строк или >5 колонок (warning)."""

    def _deck(self, tmp_path, rows: int, cols: int):
        prs = Presentation()
        slide = _blank_slide(prs)
        frame = slide.shapes.add_table(
            rows, cols, Inches(1), Inches(1), Inches(6), Inches(3)
        )
        frame.table.cell(0, 0).text = "a"
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_many_rows(self, tmp_path):
        """11 строк → table_size, measured '11x2'."""
        issues = _issues_by_rule(
            audit_deck(self._deck(tmp_path, 11, 2)), RULE_TABLE_SIZE
        )
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].measured_value == "11x2"

    def test_bad_many_cols(self, tmp_path):
        """6 колонок → table_size (по оси колонок)."""
        assert _issues_by_rule(
            audit_deck(self._deck(tmp_path, 3, 6)), RULE_TABLE_SIZE
        )

    def test_good_boundary(self, tmp_path):
        """Ровно 7x5 — на пороге, без issue."""
        assert _issues_by_rule(
            audit_deck(self._deck(tmp_path, 7, 5)), RULE_TABLE_SIZE
        ) == []

    def test_limit_from_yaml_changes_behavior(self, tmp_path):
        """max_table_rows из yaml реально меняет поведение."""
        path = self._deck(tmp_path, 6, 2)
        assert _issues_by_rule(audit_deck(path), RULE_TABLE_SIZE) == []
        cfg_path = tmp_path / "audit.yaml"
        cfg_path.write_text("max_table_rows: 5\n", encoding="utf-8")
        cfg = load_audit_config(cfg_path)
        assert _issues_by_rule(audit_deck(path, config=cfg), RULE_TABLE_SIZE)


class TestFontFamily:
    """template.font_family — >2 distinct latin typefaces на слайде (warning)."""

    def _deck(self, tmp_path, fonts: list[str]):
        prs = Presentation()
        slide = _blank_slide(prs)
        for idx, name in enumerate(fonts):
            box = slide.shapes.add_textbox(
                Inches(1), Inches(1 + idx * 0.5), Inches(4), Inches(0.4)
            )
            run = box.text_frame.paragraphs[0].add_run()
            run.text = f"текст {name}"
            run.font.name = name
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_three_families(self, tmp_path):
        """3 семейства → font_family, measured=3, список в evidence."""
        issues = _issues_by_rule(
            audit_deck(self._deck(tmp_path, ["Arial", "Georgia", "Courier New"])),
            RULE_FONT_FAMILY,
        )
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].measured_value == 3
        assert "Courier New" in issues[0].evidence[0]["detail"]

    def test_good_two_families(self, tmp_path):
        """2 семейства — на пороге, без issue."""
        assert _issues_by_rule(
            audit_deck(self._deck(tmp_path, ["Arial", "Georgia"])),
            RULE_FONT_FAMILY,
        ) == []

    def test_good_implicit_only(self, tmp_path):
        """Ран без явного a:latin — не считаем (наследство недоказуемо)."""
        prs = Presentation()
        slide = _blank_slide(prs)
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
        box.text_frame.text = "просто текст"
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_FONT_FAMILY) == []

    def test_limit_from_yaml_changes_behavior(self, tmp_path):
        """max_font_families из yaml реально меняет поведение."""
        path = self._deck(tmp_path, ["Arial", "Georgia", "Courier New"])
        cfg_path = tmp_path / "audit.yaml"
        cfg_path.write_text("max_font_families: 4\n", encoding="utf-8")
        cfg = load_audit_config(cfg_path)
        assert _issues_by_rule(audit_deck(path, config=cfg), RULE_FONT_FAMILY) == []


class TestChartSeries:
    """density.chart_series — диаграмма >5 серий c:ser (warning)."""

    def _deck(self, tmp_path, n_series: int):
        chart_data = CategoryChartData()
        chart_data.categories = ["a", "b", "c"]
        for k in range(n_series):
            chart_data.add_series(f"s{k}", (k + 1, k + 2, k + 3))
        prs = Presentation()
        slide = _blank_slide(prs)
        slide.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED,
            Inches(1),
            Inches(1),
            Inches(6),
            Inches(4),
            chart_data,
        )
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_six_series(self, tmp_path):
        """6 серий → chart_series, measured=6."""
        issues = _issues_by_rule(
            audit_deck(self._deck(tmp_path, 6)), RULE_CHART_SERIES
        )
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].measured_value == 6

    def test_good_five_series(self, tmp_path):
        """Ровно 5 серий — на пороге, без issue."""
        assert _issues_by_rule(
            audit_deck(self._deck(tmp_path, 5)), RULE_CHART_SERIES
        ) == []

    def test_limit_from_yaml_changes_behavior(self, tmp_path):
        """max_chart_series из yaml реально меняет поведение."""
        path = self._deck(tmp_path, 6)
        cfg_path = tmp_path / "audit.yaml"
        cfg_path.write_text("max_chart_series: 7\n", encoding="utf-8")
        cfg = load_audit_config(cfg_path)
        assert _issues_by_rule(audit_deck(path, config=cfg), RULE_CHART_SERIES) == []


class TestOccupancy:
    """density.occupancy — заполненность слайда вне 25–75% (warning)."""

    def _deck_with_textboxes(self, tmp_path, boxes):
        """boxes — список (x, y, w, h) в дюймах, в каждом текстовый бокс."""
        prs = Presentation()
        slide = _blank_slide(prs)
        for x, y, w, h in boxes:
            box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
            box.text_frame.text = "контентный текст"
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_underfilled(self, tmp_path):
        """Мелкий текстбокс → occupancy < min, measured — доля."""
        issues = _issues_by_rule(
            audit_deck(self._deck_with_textboxes(tmp_path, [(1, 1, 1, 0.5)])),
            RULE_OCCUPANCY,
        )
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].measured_value < 0.25
        assert "меньше минимума" in issues[0].message

    def test_bad_overfilled(self, tmp_path):
        """Большие контентные боксы >75% площади → occupancy > max."""
        boxes = [(0.5, 0.5, 9, 3), (0.5, 3.5, 9, 3.5)]
        issues = _issues_by_rule(
            audit_deck(self._deck_with_textboxes(tmp_path, boxes)),
            RULE_OCCUPANCY,
        )
        assert len(issues) == 1
        assert issues[0].measured_value > 0.75
        assert "больше максимума" in issues[0].message

    def test_good_full_bleed_picture(self, tmp_path):
        """Полноэкранный визал — не «пустой» и не «перегруз»: honest skip
        фона/hero ≥80% из контентного числителя + occ_total покрыт."""
        prs = Presentation()
        slide = _blank_slide(prs)
        slide.shapes.add_picture(
            str(_png(tmp_path)), 0, 0, prs.slide_width, prs.slide_height
        )
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_OCCUPANCY) == []

    def test_good_medium(self, tmp_path):
        """Бокс в зоне 25–75% — без issue."""
        issues = _issues_by_rule(
            audit_deck(self._deck_with_textboxes(tmp_path, [(1, 1, 8, 4)])),
            RULE_OCCUPANCY,
        )
        assert issues == []

    def test_limit_from_yaml_changes_behavior(self, tmp_path):
        """occupancy_min из yaml реально меняет поведение."""
        path = self._deck_with_textboxes(tmp_path, [(1, 1, 4, 2)])
        cfg_path = tmp_path / "audit.yaml"
        cfg_path.write_text("occupancy_min: 0.05\n", encoding="utf-8")
        cfg = load_audit_config(cfg_path)
        assert _issues_by_rule(audit_deck(path, config=cfg), RULE_OCCUPANCY) == []


class TestFontScale:
    """template.font_scale: явный кегль вне шкалы шаблона.

    Шаблон по умолчанию декларирует шкалу {12,18,20,24,28,32,44}pt
    (master txStyles + presentation defaultTextStyle lvl1).
    """

    def _deck_with_size(self, tmp_path, pt: float | None):
        prs = Presentation()
        slide = _blank_slide(prs)
        box = slide.shapes.add_textbox(
            Inches(1), Inches(1), Inches(6), Inches(2)
        )
        run = box.text_frame.paragraphs[0].add_run()
        run.text = "Текст для проверки шкалы"
        if pt is not None:
            run.font.size = Pt(pt)
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_off_scale(self, tmp_path):
        """16pt — ближайший шаг 18pt, отклонение 11% > 10% → issue."""
        issues = _issues_by_rule(
            audit_deck(self._deck_with_size(tmp_path, 16)), RULE_FONT_SCALE
        )
        assert len(issues) == 1
        assert issues[0].measured_value > 0.10

    def test_good_on_scale_and_tolerance(self, tmp_path):
        """Шаг шкалы (18pt) и граница допуска (19.8pt = ровно +10%) — чисто."""
        assert _issues_by_rule(
            audit_deck(self._deck_with_size(tmp_path, 18)), RULE_FONT_SCALE
        ) == []
        assert _issues_by_rule(
            audit_deck(self._deck_with_size(tmp_path, 19.8)), RULE_FONT_SCALE
        ) == []

    def test_good_inherited_size(self, tmp_path):
        """Ран без явного sz наследует шкалу по построению — не флагаем."""
        assert _issues_by_rule(
            audit_deck(self._deck_with_size(tmp_path, None)), RULE_FONT_SCALE
        ) == []

    def test_limit_from_yaml_changes_behavior(self, tmp_path):
        """font_scale_tolerance=1% делает 18.5pt нарушением."""
        path = self._deck_with_size(tmp_path, 18.5)
        cfg_path = tmp_path / "audit.yaml"
        cfg_path.write_text("font_scale_tolerance: 0.01\n", encoding="utf-8")
        cfg = load_audit_config(cfg_path)
        issues = _issues_by_rule(audit_deck(path, config=cfg), RULE_FONT_SCALE)
        assert len(issues) == 1


class TestObservedFontScale:
    """Q1: наблюдаемая шкала колоды. Размер, явно заданный на ≥3 ранах,
    — ступень шкалы; дробные следы автоподбора приводятся к 0.5pt;
    кегли ≥ font_scale_display_pt (крупные цифры) вне шкалы текста."""

    def _deck(self, tmp_path, sizes: list[float]):
        prs = Presentation()
        slide = _blank_slide(prs)
        for i, pt in enumerate(sizes):
            box = slide.shapes.add_textbox(
                Inches(0.5), Inches(0.2 + 0.6 * i), Inches(8), Inches(0.5)
            )
            run = box.text_frame.paragraphs[0].add_run()
            run.text = f"Строка номер {i}"
            run.font.size = Pt(pt)
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_repeated_native_size_is_not_a_violation(self, tmp_path):
        """16pt вне декларированной шкалы, но на 3 ранах — родная ступень."""
        path = self._deck(tmp_path, [16, 16, 16])
        assert _issues_by_rule(audit_deck(path), RULE_FONT_SCALE) == []

    def test_one_off_size_is_still_flagged(self, tmp_path):
        """Тот же 16pt один раз — выброс, как и раньше."""
        path = self._deck(tmp_path, [16, 18, 18])
        assert len(_issues_by_rule(audit_deck(path), RULE_FONT_SCALE)) == 1

    def test_fractional_autofit_traces_snap_to_a_step(self, tmp_path):
        """8.12/8.48pt (следы «сжать текст») — ступень 8.0/8.5, не свои."""
        path = self._deck(tmp_path, [8.12, 8.12, 8.12, 8.48, 8.48, 8.48])
        assert _issues_by_rule(audit_deck(path), RULE_FONT_SCALE) == []

    def test_display_size_is_outside_the_text_scale(self, tmp_path):
        path = self._deck(tmp_path, [96, 18, 18])
        assert _issues_by_rule(audit_deck(path), RULE_FONT_SCALE) == []

    def test_min_uses_from_yaml_changes_behavior(self, tmp_path):
        path = self._deck(tmp_path, [16, 16, 16])
        cfg_path = tmp_path / "audit.yaml"
        cfg_path.write_text("font_scale_min_uses: 4\n", encoding="utf-8")
        cfg = load_audit_config(cfg_path)
        assert len(_issues_by_rule(audit_deck(path, config=cfg), RULE_FONT_SCALE)) == 3

    def test_organizer_template_native_slides_have_few_violations(self):
        """VK Tech: было 418 ложных нарушений на родных слайдах, стало ≤2."""
        path = Path(__file__).resolve().parents[1] / "fixtures" / "pptx" / "vk_tech.pptx"
        assert len(_issues_by_rule(audit_deck(path), RULE_FONT_SCALE)) <= 2


class TestColorPalette:
    """template.color_palette: явный цвет вне палитры темы (ΔE CIE76).

    Палитра дефолтной темы содержит accent1 #4F81BD и др.; magenta
    #FF00FF — в ~30+ ΔE от ближайшего слота → нарушение при tol=8.
    """

    def _deck(self, tmp_path, *, run_rgb=None, fill_rgb=None, scheme=False):
        prs = Presentation()
        slide = _blank_slide(prs)
        box = slide.shapes.add_textbox(
            Inches(1), Inches(1), Inches(6), Inches(2)
        )
        run = box.text_frame.paragraphs[0].add_run()
        run.text = "Текст для проверки палитры"
        if run_rgb is not None:
            run.font.color.rgb = run_rgb
        if scheme:
            solid = run.font.color._xFill.find(
                "{http://schemas.openxmlformats.org/drawingml/2006/main}srgbClr"
            )
            if solid is not None:
                solid.tag = (
                    "{http://schemas.openxmlformats.org/drawingml/2006/main}"
                    "schemeClr"
                )
                solid.set("val", "accent1")
        if fill_rgb is not None:
            box.fill.solid()
            box.fill.fore_color.rgb = fill_rgb
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_off_palette_run(self, tmp_path):
        """Явный magenta-текст вне палитры → issue."""
        issues = _issues_by_rule(
            audit_deck(
                self._deck(tmp_path, run_rgb=RGBColor(0xFF, 0x00, 0xFF))
            ),
            RULE_COLOR_PALETTE,
        )
        assert len(issues) == 1
        assert issues[0].measured_value > 8
        assert issues[0].evidence[0]["kind"] == "style"

    def test_bad_off_palette_fill(self, tmp_path):
        """Явная magenta-заливка фигуры → issue."""
        issues = _issues_by_rule(
            audit_deck(
                self._deck(tmp_path, fill_rgb=RGBColor(0xFF, 0x00, 0xFF))
            ),
            RULE_COLOR_PALETTE,
        )
        assert len(issues) == 1

    def test_good_palette_color(self, tmp_path):
        """Цвет из палитры темы (#4F81BD = accent1) — чисто."""
        assert _issues_by_rule(
            audit_deck(
                self._deck(tmp_path, run_rgb=RGBColor(0x4F, 0x81, 0xBD))
            ),
            RULE_COLOR_PALETTE,
        ) == []

    def test_good_scheme_color(self, tmp_path):
        """schemeClr — по построению цвет палитры, не проверяем."""
        assert _issues_by_rule(
            audit_deck(
                self._deck(
                    tmp_path, run_rgb=RGBColor(0xFF, 0x00, 0xFF), scheme=True
                )
            ),
            RULE_COLOR_PALETTE,
        ) == []

    def test_good_neutral_gray(self, tmp_path):
        """Нейтральный серый текст — типографическая конвенция, skip."""
        assert _issues_by_rule(
            audit_deck(
                self._deck(tmp_path, run_rgb=RGBColor(0x59, 0x59, 0x59))
            ),
            RULE_COLOR_PALETTE,
        ) == []

    def test_limit_from_yaml_changes_behavior(self, tmp_path):
        """color_tolerance_delta_e=1 делает почти-акцентный оттенок нарушением."""
        path = self._deck(tmp_path, run_rgb=RGBColor(0x50, 0x82, 0xC0))
        cfg_path = tmp_path / "audit.yaml"
        cfg_path.write_text("color_tolerance_delta_e: 1\n", encoding="utf-8")
        cfg = load_audit_config(cfg_path)
        issues = _issues_by_rule(
            audit_deck(path, config=cfg), RULE_COLOR_PALETTE
        )
        assert len(issues) == 1


class TestChartMetadata:
    """chart.metadata: диаграмма без осей/юнитов/легенды (error)."""

    def _deck(self, tmp_path, chart_type):
        prs = Presentation()
        slide = _blank_slide(prs)
        chart_data = CategoryChartData()
        chart_data.categories = ["a", "b"]
        chart_data.add_series("s1", (1.0, 2.0))
        chart_data.add_series("s2", (2.0, 1.0))
        slide.shapes.add_chart(
            chart_type,
            Inches(1),
            Inches(1),
            Inches(6),
            Inches(4),
            chart_data,
        )
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_bare_chart(self, tmp_path):
        """Свежий add_chart: нет подписей осей и нет легенды → 2 missing."""
        issues = _issues_by_rule(
            audit_deck(self._deck(tmp_path, XL_CHART_TYPE.COLUMN_CLUSTERED)),
            RULE_CHART_METADATA,
        )
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].measured_value == 2

    def test_good_legend(self, tmp_path):
        """С включённой легендой остаётся только «нет подписей осей»."""
        prs = Presentation()
        slide = _blank_slide(prs)
        chart_data = CategoryChartData()
        chart_data.categories = ["a", "b"]
        chart_data.add_series("s1", (1.0, 2.0))
        chart = slide.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED,
            Inches(1),
            Inches(1),
            Inches(6),
            Inches(4),
            chart_data,
        ).chart
        chart.has_legend = True
        path = tmp_path / "deck.pptx"
        prs.save(path)
        issues = _issues_by_rule(audit_deck(path), RULE_CHART_METADATA)
        assert len(issues) == 1
        assert "легенд" not in issues[0].message
        assert "осей" in issues[0].message

    def test_good_pie_with_labels(self, tmp_path):
        """Pie без осей по построению; dLbls с showPercent — чисто."""
        prs = Presentation()
        slide = _blank_slide(prs)
        chart_data = CategoryChartData()
        chart_data.categories = ["a", "b"]
        chart_data.add_series("s1", (1.0, 2.0))
        gf = slide.shapes.add_chart(
            XL_CHART_TYPE.PIE,
            Inches(1),
            Inches(1),
            Inches(6),
            Inches(4),
            chart_data,
        )
        plot = gf.chart.plots[0]
        plot.has_data_labels = True
        plot.data_labels.show_percentage = True
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_CHART_METADATA) == []


class TestPackage:
    """integrity.package: файл не открывается — blocker, не exception."""

    def test_bad_not_a_zip(self, tmp_path):
        path = tmp_path / "broken.pptx"
        path.write_bytes(b"not a zip at all")
        issues = audit_deck(path)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.rule_code == RULE_PACKAGE
        assert issue.severity == "blocker"
        assert issue.slide_id is None and issue.slide_index is None
        assert issue.evidence[0]["kind"] == "package"

    def test_bad_missing_parts(self, tmp_path):
        """Валидный zip без ppt/presentation.xml → blocker."""
        path = tmp_path / "empty.pptx"
        import zipfile

        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("readme.txt", "x")
        issues = audit_deck(path)
        assert len(issues) == 1
        assert issues[0].rule_code == RULE_PACKAGE
        assert "missing required part" in issues[0].message

    def test_good_valid_deck(self, tmp_path):
        """Валидная колода → нет integrity.package."""
        prs = Presentation()
        _blank_slide(prs)
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_PACKAGE) == []


class TestAnchorPosition:
    """template.anchor_position: footer/лого сдвинут с задекларированной
    позиции (error). Дефолтный шаблон python-pptx не несёт footerish-xfrm
    на layout/master — задекларированную позицию инжектируем явно, как
    в реальном lct2026 (layout sldNum несёт собственный xfrm)."""

    A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"

    def _set_xfrm(self, sp_el, x, y, cx, cy):
        from lxml import etree

        spPr = sp_el.find(f"{self.P}spPr")
        xfrm = spPr.find(f"{self.A}xfrm")
        if xfrm is None:
            xfrm = etree.fromstring(
                f'<a:xfrm xmlns:a="{self.A[1:-1]}"><a:off x="{x}" y="{y}"/>'
                f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            )
            spPr.insert(0, xfrm)
        else:
            xfrm.find(f"{self.A}off").set("x", str(x))
            xfrm.find(f"{self.A}off").set("y", str(y))
            xfrm.find(f"{self.A}ext").set("cx", str(cx))
            xfrm.find(f"{self.A}ext").set("cy", str(cy))

    def _seed_deck(self, tmp_path, *, dx_pt=0.0, with_declared=True,
                   drop_own_xfrm=False):
        """Слайд с sldNum ph (idx=12) на месте layout, off@x += dx_pt."""
        prs = Presentation()
        lay = prs.slide_layouts[0]
        ph = [p for p in lay.placeholders if p.placeholder_format.idx == 12][0]
        base = (1_000_000, 6_000_000, 500_000, 300_000)
        if with_declared:
            self._set_xfrm(ph._element, *base)
        slide = prs.slides.add_slide(lay)
        from lxml import etree
        from pptx.oxml import parse_xml

        sp_el = parse_xml(etree.tostring(ph._element))
        slide.shapes._spTree.append(sp_el)
        if drop_own_xfrm:
            spPr = sp_el.find(f"{self.P}spPr")
            spPr.remove(spPr.find(f"{self.A}xfrm"))
        else:
            self._set_xfrm(
                sp_el, base[0] + int(dx_pt * 12700), base[1], base[2], base[3]
            )
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_moved_footer(self, tmp_path):
        path = self._seed_deck(tmp_path, dx_pt=10.0)
        issues = _issues_by_rule(audit_deck(path), RULE_ANCHOR_POSITION)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].measured_value == pytest.approx(10.0, abs=0.1)
        assert "x Δ" in issues[0].message

    def test_compliant_verbatim(self, tmp_path):
        """xfrm слово-в-слово как у layout → чисто."""
        path = self._seed_deck(tmp_path, dx_pt=0.0)
        assert _issues_by_rule(audit_deck(path), RULE_ANCHOR_POSITION) == []

    def test_inherited_no_own_xfrm(self, tmp_path):
        """ph без собственного xfrm наследует позицию — compliant."""
        path = self._seed_deck(tmp_path, drop_own_xfrm=True)
        assert _issues_by_rule(audit_deck(path), RULE_ANCHOR_POSITION) == []

    def test_no_declared_honest_skip(self, tmp_path):
        """Лого-фигура без same-named опоры на layout/master — нет
        задекларированной позиции, честный пропуск (не гадание)."""
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[0])
        tb = slide.shapes.add_textbox(
            Inches(1), Inches(6), Inches(0.5), Inches(0.2)
        )
        tb.name = "Logo"
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_ANCHOR_POSITION) == []

    def test_yaml_tolerance_override(self, tmp_path):
        """anchor_tolerance_pt=100 глушит сдвиг 10pt; дефолт 2pt ловит."""
        path = self._seed_deck(tmp_path, dx_pt=10.0)
        assert _issues_by_rule(audit_deck(path), RULE_ANCHOR_POSITION)
        cfg = load_audit_config()
        cfg.anchor_tolerance_pt = 100.0
        assert _issues_by_rule(
            audit_deck(path, config=cfg), RULE_ANCHOR_POSITION
        ) == []

    def test_logo_named_shape(self, tmp_path):
        """Фигура 'Logo' на слайде против same-named опоры на layout."""
        from lxml import etree

        prs = Presentation()
        lay = prs.slide_layouts[0]
        logo_sp = etree.fromstring(
            f'<p:sp xmlns:p="{self.P[1:-1]}" xmlns:a="{self.A[1:-1]}">'
            '<p:nvSpPr><p:cNvPr id="900" name="Logo"/><p:cNvSpPr/><p:nvPr/>'
            '</p:nvSpPr><p:spPr>'
            '<a:xfrm><a:off x="500000" y="6500000"/>'
            '<a:ext cx="800000" cy="250000"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
            '<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
        )
        from pptx.oxml import parse_xml

        lay.shapes._spTree.append(parse_xml(etree.tostring(logo_sp)))
        slide = prs.slides.add_slide(lay)
        tb = slide.shapes.add_textbox(
            Inches(1), Inches(6), Inches(0.5), Inches(0.2)
        )
        tb.name = "Logo"
        path = tmp_path / "deck.pptx"
        prs.save(path)
        issues = _issues_by_rule(audit_deck(path), RULE_ANCHOR_POSITION)
        assert len(issues) == 1
        assert issues[0].measured_value > 2.0


class TestSlideClip:
    """text.slide_clip: рамка внутри слайда, но оценочный экстент текста
    уходит за край (error). Дизъюнктно с text.overflow (текст vs рамка)
    и layout.out_of_bounds (рамка vs слайд)."""

    def _deck(self, tmp_path, text, top, height=None, wrap=True, anchor=None):
        if height is None:
            height = Inches(0.3)
        prs = Presentation()
        slide = _blank_slide(prs)
        box = slide.shapes.add_textbox(Inches(1), top, Inches(4), height)
        tf = box.text_frame
        tf.word_wrap = wrap
        tf.auto_size = MSO_AUTO_SIZE.NONE
        if anchor is not None:
            tf.vertical_anchor = anchor
        tf.text = text
        path = tmp_path / "deck.pptx"
        prs.save(path)
        return path

    def test_bad_bottom_clip(self, tmp_path):
        """Рамка внутри слайда (5.5" из 7.5"), но завёрнутый текст
        оценивается выше рамки и уходит за нижний край → error."""
        path = self._deck(
            tmp_path, "длинный текст который не помещается " * 20, Inches(5.5)
        )
        issues = _issues_by_rule(audit_deck(path), RULE_SLIDE_CLIP)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == "error"
        assert issue.measured_value > issue.threshold
        assert issue.proposed_actions

    def test_good_inside(self, tmp_path):
        """Текст переполняет рамку, но экстент остаётся внутри слайда —
        это зона text.overflow, slide_clip молчит."""
        path = self._deck(
            tmp_path, "длинный текст который не помещается " * 4, Inches(1)
        )
        issues = audit_deck(path)
        assert _issues_by_rule(issues, RULE_SLIDE_CLIP) == []
        assert _issues_by_rule(issues, RULE_TEXT_OVERFLOW)

    def test_frame_outside_is_out_of_bounds(self, tmp_path):
        """Рамка сама за краем — находка out_of_bounds, slide_clip её не
        дублирует (правила disjoint)."""
        path = self._deck(tmp_path, "текст", Inches(7.4), Inches(0.4))
        issues = audit_deck(path)
        assert _issues_by_rule(issues, RULE_SLIDE_CLIP) == []
        assert _issues_by_rule(issues, RULE_OUT_OF_BOUNDS)

    def test_bad_wrap_none_horizontal(self, tmp_path):
        """wrap="none": текст не переносится, строка вылетает за правый
        край → error (горизонтальный клип)."""
        prs = Presentation()
        slide = _blank_slide(prs)
        box = slide.shapes.add_textbox(Inches(7), Inches(3), Inches(2), Inches(0.5))
        tf = box.text_frame
        tf.word_wrap = False
        tf.auto_size = MSO_AUTO_SIZE.NONE
        tf.text = "одна длинная строка без переноса " * 15
        path = tmp_path / "deck.pptx"
        prs.save(path)
        issues = _issues_by_rule(audit_deck(path), RULE_SLIDE_CLIP)
        assert len(issues) == 1
        assert issues[0].severity == "error"

    def test_bad_center_anchor(self, tmp_path):
        """anchor=ctr: переполнение делится в обе стороны, нижний конец
        уходит за край → error."""
        path = self._deck(
            tmp_path,
            "длинный текст который не помещается " * 20,
            Inches(5.5),
            anchor=MSO_ANCHOR.MIDDLE,
        )
        issues = _issues_by_rule(audit_deck(path), RULE_SLIDE_CLIP)
        assert len(issues) == 1

    def test_auto_size_skipped(self, tmp_path):
        """TEXT_TO_FIT_SHAPE — фигура сама ужимает текст, клип невозможен."""
        prs = Presentation()
        slide = _blank_slide(prs)
        box = slide.shapes.add_textbox(Inches(1), Inches(5.5), Inches(4), Inches(0.3))
        tf = box.text_frame
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        tf.text = "длинный текст который не помещается " * 20
        path = tmp_path / "deck.pptx"
        prs.save(path)
        assert _issues_by_rule(audit_deck(path), RULE_SLIDE_CLIP) == []


class TestLayoutOrigin:
    """template.layout_origin: layout-цепочка слайда резолвится в
    master самой колоды (warning). В текущей архитектуре клонирования —
    вечно-зелёное defense-in-depth; сидируем детект напрямую."""

    def test_green(self, tmp_path):
        """Честная колода: layout всех слайдов из мастеров пакета → нет issue."""
        prs = Presentation()
        _blank_slide(prs)
        path = tmp_path / "deck.pptx"
        prs.save(path)
        issues = audit_deck(path)
        assert _issues_by_rule(issues, RULE_LAYOUT_ORIGIN) == []

    def test_foreign_master_detected(self, tmp_path):
        """Master layout-цепочки вне набора мастеров дека → warning
        (сидируем через ctx: внешний master = partname не из пакета)."""
        prs = Presentation()
        slide = _blank_slide(prs)
        ctx = _Ctx(prs, "run-test", 0, load_audit_config())
        real = str(slide.slide_layout.slide_master.part.partname)
        ctx.master_partnames = {"/ppt/slideMasters/slideMaster999.xml"}
        assert real not in ctx.master_partnames
        issues = check_layout_origin(slide, 0, ctx)
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert "layout" in issues[0].message.lower()

    def test_unresolved_chain_detected(self, tmp_path):
        """Цепочка layout→master не резолвится (rel оборвана) → warning;
        на уровне audit_deck такая колода ловится blocker'ом
        integrity.package ещё до обхода правил."""
        prs = Presentation()
        slide = _blank_slide(prs)
        for r_id, rel in list(slide.part.rels.items()):
            if rel.reltype.endswith("/slideLayout"):
                slide.part.drop_rel(r_id)
        ctx = _Ctx(prs, "run-test", 0, load_audit_config())
        issues = check_layout_origin(slide, 0, ctx)
        assert len(issues) == 1
        assert issues[0].severity == "warning"

    def test_broken_layout_rel_is_package_blocker(self, tmp_path):
        """Слайд без rel на layout — структурный дефект: integrity.package
        отдаёт blocker до пер-slide правил (ранее падал KeyError)."""
        prs = Presentation()
        slide = _blank_slide(prs)
        for r_id, rel in list(slide.part.rels.items()):
            if rel.reltype.endswith("/slideLayout"):
                slide.part.drop_rel(r_id)
        path = tmp_path / "nolayout.pptx"
        prs.save(path)
        issues = audit_deck(path)
        assert len(issues) == 1
        assert issues[0].rule_code == RULE_PACKAGE
        assert issues[0].severity == "blocker"
        assert "slide1.xml" in issues[0].message


def test_scheme_aliases_follow_master_clr_map():
    """VK Tech: clrMap bg2=dk2 (тёмный). Раньше bg2 всегда считался lt2
    (белым) — auto-fix перекрашивал белый текст на тёмной карточке в чёрный."""
    from types import SimpleNamespace

    from deckdna.audit.basic import _resolve_color_element, _theme_palette
    from lxml import etree
    from pptx import Presentation

    prs = Presentation("tests/fixtures/pptx/vk_tech_template.pptx")
    palette = _theme_palette(prs.slides[0], SimpleNamespace(theme_palettes={}))
    clr = etree.fromstring(
        '<a:schemeClr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" val="bg2"/>'
    )
    assert _resolve_color_element(clr, palette) == palette["dk2"]
