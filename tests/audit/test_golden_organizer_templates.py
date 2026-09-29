"""Golden-тесты на реальных organizer-шаблонах (tests/fixtures/pptx).

Зафиксированные счётчики issues по правилам — регрессия детектится при
любом изменении логики, сдвигающем числа на реальных файлах. Симлинки в
tests/fixtures/pptx указывают на отслеживаемые dop-data шаблоны, чтобы не
дублировать ~70MB бинарников в git.
"""

from collections import Counter
from pathlib import Path

import pytest
from deckdna.audit.basic import (
    RULE_ANCHOR_POSITION,
    RULE_ASPECT_RATIO,
    RULE_CHART_METADATA,
    RULE_COLOR_PALETTE,
    RULE_CONTRAST,
    RULE_DUPLICATE_SLIDE,
    RULE_EDGE_MARGIN,
    RULE_EMPTY_SLIDE,
    RULE_FONT_FLOOR,
    RULE_FONT_SCALE,
    RULE_OCCUPANCY,
    RULE_OUT_OF_BOUNDS,
    RULE_PLACEHOLDER_TEXT,
    RULE_SLIDE_CLIP,
    RULE_TABLE_SIZE,
    RULE_TEXT_OVERFLOW,
    RULE_UNINTENDED_OVERLAP,
    _measure_font,
    audit_deck,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pptx"

# (fixture name, ожидаемые счётчики rule_code -> count)
GOLDEN = [
    (
        "vk_tech.pptx",
        {
            RULE_TEXT_OVERFLOW: 66,
            RULE_ASPECT_RATIO: 75,
            RULE_DUPLICATE_SLIDE: 8,
            RULE_EDGE_MARGIN: 6,
            RULE_OUT_OF_BOUNDS: 7,
            RULE_PLACEHOLDER_TEXT: 271,
            RULE_UNINTENDED_OVERLAP: 7,
            RULE_FONT_FLOOR: 67,
            RULE_CONTRAST: 128,
            RULE_OCCUPANCY: 11,
            RULE_FONT_SCALE: 2,  # Q1: наблюдаемая шкала; было 418 (шкала только из defRPr)
            RULE_COLOR_PALETTE: 40,
        },
    ),
    (
        "vk_workspace.pptx",
        {
            RULE_TEXT_OVERFLOW: 11,
            RULE_DUPLICATE_SLIDE: 8,
            RULE_EDGE_MARGIN: 2,
            RULE_OUT_OF_BOUNDS: 2,
            RULE_PLACEHOLDER_TEXT: 137,
            RULE_UNINTENDED_OVERLAP: 3,
            # Alpha-compositing (ad87cf9, 28.09) now finds contrast hidden
            # behind semi-transparent fills -- was 21 before that rule got
            # more accurate; other two fixtures' counts are unaffected.
            RULE_CONTRAST: 29,
            RULE_TABLE_SIZE: 1,
            RULE_OCCUPANCY: 5,
            RULE_FONT_SCALE: 1,  # Q1; было 53
            RULE_COLOR_PALETTE: 49,
            RULE_SLIDE_CLIP: 1,
        },
    ),
    (
        "lct2026_submission.pptx",
        {
            RULE_TEXT_OVERFLOW: 11,
            RULE_ASPECT_RATIO: 5,
            RULE_EMPTY_SLIDE: 1,
            RULE_EDGE_MARGIN: 3,
            RULE_OUT_OF_BOUNDS: 3,
            RULE_UNINTENDED_OVERLAP: 4,
            RULE_CONTRAST: 6,
            RULE_OCCUPANCY: 1,
            RULE_COLOR_PALETTE: 1,
            RULE_CHART_METADATA: 2,
            RULE_ANCHOR_POSITION: 3,
        },
    ),
]


def test_metric_font_available():
    """Golden-числа откалиброваны по метрикам DejaVu Sans; без шрифта
    (fallback на эвристику ширины) оценки overflow съезжают.

    Это тест окружения, а не логики: если ни DejaVu, ни Liberation, ни
    любой системный .ttf не найден — skip с инструкцией, а не fail."""
    if _measure_font(18.0) is None:
        pytest.skip(
            "нет ни одного системного .ttf для метрик текста — "
            "golden-тесты откалиброваны под DejaVu/Liberation: "
            "apt install fonts-dejavu / brew install font-dejavu"
        )


@pytest.mark.parametrize("fixture,expected", GOLDEN, ids=[g[0] for g in GOLDEN])
def test_audit_counts_on_organizer_templates(fixture, expected):
    path = FIXTURES / fixture
    assert path.exists(), f"fixture missing (broken symlink?): {path}"
    counts = Counter(i.rule_code for i in audit_deck(path))
    for rule, n in expected.items():
        assert counts[rule] == n, f"{fixture}: {rule} — ожидалось {n}, получено {counts[rule]}"
    # никаких неучтённых правил — иначе тест не зафиксировал регрессию
    assert set(counts) <= set(expected), (
        f"{fixture}: неожиданные правила {set(counts) - set(expected)}"
    )
