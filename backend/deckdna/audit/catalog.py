"""Единый каталог правил аудита (источник правды для API и фронта).

Один реестр на все правила — детерминированные (``audit/basic.py``) и
контекстные VLM-проверки (``audit/contextual.py``): код, человеческое
название, категория, серьёзность по умолчанию, порог из конфига и —
главное — честный флаг ``repairable``.

``repairable`` здесь — статическое множество правил, для которых
repair-планировщик УМЕЕТ построить действие, а исполнитель — применить
(``repair/planner.py::_RULE_HANDLERS`` минус ``NON_EXECUTABLE_RULES``).
``audit_deck`` проставляет флаг из этого каталога, а не «True по
умолчанию»: issue с ``repairable=true`` без обработчика давал
``unresolved`` и обещание, которого никто не выполнял (contract B5).
Соответствие каталога планировщику проверяет
``tests/audit/test_rule_catalog.py`` — расхождение ломает сборку.

Названия и заголовки исправлений — русские, для интерфейса; каталог
не тянет ни pydantic, ни python-pptx: его безопасно импортировать из
любого слоя.
"""

from __future__ import annotations

from dataclasses import dataclass

CATEGORIES = ("brand", "layout", "text", "density", "integrity", "meaning")


@dataclass(frozen=True)
class RuleInfo:
    code: str
    title_ru: str
    category: str
    default_severity: str
    deterministic: bool
    # имя поля AuditConfig с порогом правила (None — порога нет)
    threshold_key: str | None = None
    # заголовок исправления для интерфейса; None ⇒ правило не чинится
    fix_title_ru: str | None = None

    @property
    def repairable(self) -> bool:
        return self.fix_title_ru is not None


def _det(
    code: str,
    title: str,
    category: str,
    severity: str,
    threshold_key: str | None = None,
    fix: str | None = None,
) -> RuleInfo:
    return RuleInfo(code, title, category, severity, True, threshold_key, fix)


def _ctx(code: str, title: str, severity: str) -> RuleInfo:
    return RuleInfo(code, title, "meaning", severity, False)


_RULES: tuple[RuleInfo, ...] = (
    # --- текст ---
    _det("text.overflow", "Текст не помещается в рамку", "text", "error",
         "overflow_tolerance", "Расширить рамку или сократить текст"),
    _det("text.slide_clip", "Текст уходит за край слайда", "text", "error",
         None, "Сдвинуть рамку и сократить текст"),
    _det("text.font_floor", "Слишком мелкий шрифт", "text", "error",
         "font_floor_pt"),
    _det("accessibility.contrast", "Низкий контраст текста", "text", "error",
         "contrast_min_ratio", "Подобрать читаемый цвет текста"),
    # --- вёрстка ---
    _det("layout.out_of_bounds", "Элемент выходит за границы слайда", "layout",
         "error", "out_of_bounds_tolerance", "Вернуть элемент в границы слайда"),
    _det("layout.unintended_overlap", "Элементы наложились друг на друга",
         "layout", "error", "overlap_min_cover"),
    _det("layout.edge_margin", "Текст прижат к краю слайда", "layout", "warning",
         "edge_margin", "Отодвинуть текст от края"),
    _det("image.aspect_ratio", "Картинка растянута", "layout", "error",
         "aspect_tolerance", "Восстановить пропорции картинки кадрированием"),
    # --- плотность ---
    _det("density.bullet_count", "Слишком много пунктов", "density", "warning",
         "max_bullets_per_slide"),
    _det("density.bullet_length", "Слишком длинный пункт", "density", "warning",
         "max_words_per_bullet"),
    _det("density.table_size", "Слишком большая таблица", "density", "warning",
         "max_table_rows"),
    _det("density.chart_series", "Слишком много рядов диаграммы", "density",
         "warning", "max_chart_series"),
    _det("density.occupancy", "Слайд перегружен или пуст", "density", "warning",
         "occupancy_max"),
    # --- соответствие шаблону ---
    _det("template.font_family", "Лишний шрифт вне шаблона", "brand", "warning",
         "max_font_families", "Заменить шрифт на шрифт шаблона"),
    _det("template.font_scale", "Кегль вне шкалы шаблона", "brand", "warning",
         "font_scale_tolerance"),
    _det("template.color_palette", "Цвет вне палитры шаблона", "brand",
         "warning", "color_tolerance_delta_e",
         "Заменить цвет на ближайший цвет палитры шаблона"),
    _det("template.anchor_position", "Логотип или колонтитул сдвинут", "brand",
         "error", "anchor_tolerance_pt", "Вернуть элемент на место шаблона"),
    _det("template.layout_origin", "Макет не из этого шаблона", "brand",
         "warning"),
    # --- целостность ---
    _det("integrity.empty_slide", "Пустой слайд", "integrity", "error", None,
         "Убрать пустой слайд"),
    _det("integrity.duplicate_slide", "Повтор слайда", "integrity", "warning",
         "duplicate_similarity_threshold", "Убрать повторяющийся слайд"),
    _det("integrity.placeholder_text", "Осталась заглушка шаблона", "integrity",
         "error", None, "Удалить заглушку"),
    _det("integrity.package", "Файл презентации повреждён", "integrity",
         "blocker"),
    _det("editability.raster_only", "Слайд — одна картинка", "integrity",
         "blocker", "raster_only_slide_area"),
    _det("chart.metadata", "У диаграммы нет подписей осей или легенды",
         "integrity", "error"),
    # --- смысл (VLM) ---
    _ctx("content.conclusion_title", "Заголовок — тема, а не вывод", "warning"),
    _ctx("content.title_alignment", "Заголовок не отражает содержание", "error"),
    _ctx("content.single_message", "На слайде несколько мыслей", "warning"),
    _ctx("content.source_support", "Утверждение без опоры на источник",
         "blocker"),
    _ctx("content.nonempty", "Слайд без содержательного текста", "error"),
    _ctx("content.visual_relevance", "Картинка не по теме", "warning"),
    _ctx("content.prompt_leakage", "В тексте остались следы промпта", "error"),
    _ctx("content.spelling", "Ошибки в тексте", "warning"),
    _ctx("content.data_relevance", "Данные не подтверждают мысль", "warning"),
    _ctx("deck.logical_flow", "Нарушена логика повествования", "warning"),
)

RULES: dict[str, RuleInfo] = {r.code: r for r in _RULES}

# Правила, для которых планировщик строит действие, но исполнитель его
# применить не умеет (тип действия не реализован) — не repairable.
NON_EXECUTABLE_RULES = frozenset({"editability.raster_only"})


def rule_info(code: str) -> RuleInfo | None:
    return RULES.get(code)


def is_repairable(code: str) -> bool:
    """Чинится ли правило автоматически (неизвестный код — нет)."""
    info = RULES.get(code)
    return info is not None and info.repairable


def repairable_rules() -> frozenset[str]:
    return frozenset(code for code, info in RULES.items() if info.repairable)
