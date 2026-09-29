"""Audit thresholds — загружаются из ``configs/audit.default.yaml``.

Тот же паттерн, что ``planning/config.py``: файл резолвится от
расположения пакета, а не от CWD процесса. Пороги — конфигурационные
данные, а не константы внутри правил: правки yaml реально меняют
поведение ``audit_deck`` (ранее файл был мёртвым — задокументирован,
но нигде не читался).

Поля модели покрывают только ключи, потребляемые реализованными
правилами; остальные ключи yaml (пороги roadmap-правил, секции
``contextual:``/``repair:``) сохраняются в ``raw`` без потери.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

# backend/deckdna/audit/config.py → <repo>/configs/
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AUDIT_CONFIG_PATH = _REPO_ROOT / "configs" / "audit.default.yaml"


class AuditConfig(BaseModel):
    """Пороги deterministic-правил Render Arena.

    Дефолты полей повторяют отгружаемый ``audit.default.yaml`` —
    модель валидна и без файла (например при частичной выборке ключей);
    неизвестные ключи yaml не ломают старые сборки и лежат в ``raw``.
    """

    version: str = "1"
    # text.overflow — доля usable-рамки, на которую оценочный overflow
    # может превысить границы до срабатывания (допуск на метрики шрифта)
    overflow_tolerance: float = 0.05
    # image.aspect_ratio — допуск отклонения отображаемого aspect от исходного
    aspect_tolerance: float = 0.03
    # editability.raster_only — доля площади слайда одной картинкой
    raster_only_slide_area: float = 0.90
    # layout.out_of_bounds — допуск выхода bbox за [0,1] (доля стороны)
    out_of_bounds_tolerance: float = 0.005
    # integrity.duplicate_slide — jaccard-порог текстовой идентичности
    duplicate_similarity_threshold: float = 0.90
    # integrity.placeholder_text — дополнительные WHOLE-ярус паттерны:
    # фигура флагается, если весь её нормализованный текст целиком
    # матчится любым паттерном (регистронезависимо). Маркер внутри
    # длинного текста остаётся зоной STRONG-яруса в коде — yaml-список
    # осознанно трактуется консервативно (whole-match), чтобы правка
    # конфига не превратила частые слова в ложные срабатывания.
    placeholder_patterns: list[str] = Field(default_factory=list)
    # density.bullet_count — максимум буллет-параграфов в одном txBody
    max_bullets_per_slide: int = 6
    # layout.unintended_overlap — гейты частичного пересечения
    overlap_min_cover: float = 0.25
    overlap_containment: float = 0.90
    overlap_min_shape_area: float = 0.01
    overlap_bg_area: float = 0.80
    # layout.edge_margin — зазор до края и span edge-to-edge плашки
    edge_margin: float = 0.03
    edge_bleed_span: float = 0.95
    # text.font_floor — минимальный читаемый кегль (как FONT_FLOOR_PT
    # компилятора: ниже текст нечитабелен)
    font_floor_pt: float = 8.0
    # accessibility.contrast — WCAG-подобное отношение контраста текст/фон
    contrast_min_ratio: float = 4.5
    # density.bullet_length — максимум слов в одном буллет-параграфе
    max_words_per_bullet: int = 15
    # density.table_size — максимум строк/колонок таблицы
    max_table_rows: int = 7
    max_table_cols: int = 5
    # template.font_family — максимум различных семейств шрифтов на слайде
    max_font_families: int = 2
    # density.chart_series — максимум серий (c:ser) в одной диаграмме
    max_chart_series: int = 5
    # density.occupancy — доля площади слайда под контентом
    occupancy_min: float = 0.25
    occupancy_max: float = 0.75
    # template.font_scale — допуск отклонения явного кегля от ближайшего
    # шага задекларированной шкалы (доля от шага)
    font_scale_tolerance: float = 0.10
    # template.font_scale — размер, явно заданный на ≥ N ранах колоды,
    # считается ступенью шкалы (наблюдаемая шкала); реже — выброс
    font_scale_min_uses: int = 3
    # template.font_scale — кегли ≥ порога (pt) — дисплейная типографика
    # (крупные цифры, обложечные заголовки): не часть шкалы текста
    font_scale_display_pt: float = 40.0
    # template.color_palette — допуск ΔE (CIE76) до ближайшего цвета палитры
    color_tolerance_delta_e: float = 8.0
    # template.anchor_position — допуск сдвига footer/лого-фигуры от
    # задекларированной позиции (pt по каждой компоненте xfrm)
    anchor_tolerance_pt: float = 2.0

    raw: dict[str, Any] = Field(default_factory=dict)


def load_audit_config(path: str | Path = DEFAULT_AUDIT_CONFIG_PATH) -> AuditConfig:
    """Загрузить пороги аудита из YAML-файла.

    Неизвестные ключи сохраняются в ``raw`` — forward-compatible файлы
    не ломают старые сборки (как ``load_generation_config``).
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    known = {
        k: v
        for k, v in data.items()
        if k in AuditConfig.model_fields and k != "raw"
    }
    return AuditConfig(**known, raw=data)


@lru_cache(maxsize=1)
def default_audit_config() -> AuditConfig:
    """Отгружаемый конфиг, загруженный один раз на процесс.

    Кеш осознанный: ``audit_deck(path, config=...)`` принимает явный
    экземпляр для тестов/нестандартных профилей, поэтому кеш не мешает
    подмене файла — кастомный конфиг передаётся напрямую.
    """
    return load_audit_config()
