# Аудит качества

Аудит разделён на два независимых слоя: **детерминированный** (проверка OOXML-пакета,
геометрии, типографики, контраста, диаграмм и соответствия шаблону — воспроизводимо,
без вызова моделей) и **контекстный** (визуально-смысловая проверка отрендеренных
слайдов и `EvidenceGraph` через VLM). Единый каталог всех 36 правил находится в
`backend/deckdna/audit/catalog.py` и доступен по `GET /api/v1/audit/rules`.

## Детерминированные правила — 26

`R` — правило поддерживает автоматическое исправление через `POST /api/v1/audits/{id}/repairs`.

| Код | Проверка | Уровень | Порог (`configs/audit.default.yaml`) | R |
|---|---|---|---|---|
| `text.overflow` | Текст не помещается в рамку | error | `overflow_tolerance` | ✓ |
| `text.slide_clip` | Текст уходит за край слайда | error | — | ✓ |
| `text.font_floor` | Слишком мелкий шрифт | error | `font_floor_pt` (8pt) | |
| `accessibility.contrast` | Низкий контраст текста (WCAG) | error | `contrast_min_ratio` (4.5) | ✓ |
| `layout.out_of_bounds` | Элемент выходит за границы слайда | error | `out_of_bounds_tolerance` | ✓ |
| `layout.unintended_overlap` | Элементы наложились друг на друга | error | `overlap_min_cover` | |
| `layout.edge_margin` | Текст прижат к краю слайда | warning | `edge_margin` | ✓ |
| `image.aspect_ratio` | Картинка растянута | error | `aspect_tolerance` | ✓ |
| `density.bullet_count` | Слишком много пунктов на слайде | warning | `max_bullets_per_slide` (6) | |
| `density.bullet_length` | Слишком длинный пункт | warning | `max_words_per_bullet` (15) | |
| `density.table_size` | Слишком большая таблица | warning | `max_table_rows` / `max_table_cols` (7×5) | |
| `density.chart_series` | Слишком много рядов диаграммы | warning | `max_chart_series` (5) | |
| `density.occupancy` | Слайд перегружен или пуст | warning | `occupancy_min` / `max` (25–75%) | |
| `template.font_family` | Лишний шрифт вне шаблона | warning | `max_font_families` (2) | ✓ |
| `template.font_scale` | Кегль вне шкалы шаблона | warning | `font_scale_tolerance` (±10%) | |
| `template.color_palette` | Цвет вне палитры шаблона | warning | `color_tolerance_delta_e` (ΔE 8) | ✓ |
| `template.anchor_position` | Логотип или колонтитул сдвинут | error | `anchor_tolerance_pt` (2pt) | ✓ |
| `template.layout_origin` | Макет не из этого шаблона | warning | — | |
| `integrity.empty_slide` | Пустой слайд | error | — | ✓ |
| `integrity.filler_slide` | Слайд-заглушка без содержания | error | `filler_min_body_chars` | |
| `integrity.duplicate_slide` | Повтор слайда (Jaccard ≥ 0.9) | warning | `duplicate_similarity_threshold` | ✓ |
| `integrity.placeholder_text` | Осталась заглушка шаблона | error | — | ✓ |
| `integrity.package` | Файл презентации повреждён | blocker | — | |
| `editability.raster_only` | Слайд состоит из одной растровой картинки | blocker | `raster_only_slide_area` | |
| `chart.metadata` | У диаграммы нет подписей осей или легенды | error | — | |
| `chart.readability` | Диаграмма слишком мала для чтения | warning | `chart_min_height_ratio` | |

## Контекстные правила — 10 (VLM)

Модуль `audit/contextual.py` рендерит каждый слайд в изображение и вызывает
`gateway.vision_json` со схемой `prompts/contextual_audit/slide_checks.v1.yaml`:

- `content.conclusion_title` — заголовок сформулирован как тема, а не вывод (`warning`);
- `content.title_alignment` — заголовок не отражает содержание слайда (`error`);
- `content.single_message` — на слайде смешано несколько мыслей (`warning`);
- `content.source_support` — утверждение без опоры на источник (`blocker`);
- `content.nonempty` — слайд без содержательного текста (`error`);
- `content.visual_relevance` — иллюстрация или график не соответствуют теме (`warning`);
- `content.prompt_leakage` — в тексте остались следы инструкций (`error`);
- `content.spelling` — орфографические или пунктуационные ошибки (`warning`);
- `content.data_relevance` — приведённые данные не подтверждают тезис (`warning`);
- `deck.logical_flow` — нарушена логическая связность повествования (`warning`).

Замечание создаётся только при вердикте `fail` с `confidence ≥ 0.5`. При `uncertain`
ложное срабатывание не порождается.

## Жизненный цикл замечаний и ремонт

Каждое замечание хранит измеренное значение, порог, координаты рамки на слайде (`bbox`)
и стабильный `fingerprint` (`rule_code + slide_id + shape_ids`), благодаря чему
состояния `open → selected → fixed | unresolved | dismissed` сохраняются между ревизиями.

Для 12 ремонтируемых правил планировщик (`repair/planner.py`) строит типизированные
операции: `resize_shape`, `recrop_image`, `move_shape`, `shorten_text`, `map_font`,
`map_color`, `merge_slide`, `remove_placeholder`. Статус `fixed` присваивается только
после того, как повторный аудит новой ревизии подтверждает исчезновение проблемы.

Безопасный auto-fix до первой ревизии автоматически исправляет `template.color_palette`,
`image.aspect_ratio` и `accessibility.contrast`, если повторная проверка подтверждает
снижение числа замечаний без появления новых ошибок.

## Использование

```bash
deckdna audit out/demo/deck.pptx                    # отчёт по колоде
deckdna repair out/demo/deck.pptx --out fixed.pptx  # план → применение → повторный аудит
```

```text
POST /api/v1/variants/{id}/audits        # запуск или получение аудита ревизии
GET  /api/v1/audits/{id}/issues          # список замечаний с bbox, severity и repairable
POST /api/v1/audits/{id}/repairs         # точечный ремонт выбранных замечаний
GET  /api/v1/audit/rules                 # полный каталог из 36 правил
```
