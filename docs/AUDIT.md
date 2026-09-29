# Аудит качества

Два независимых слоя по ТЗ: **детерминированный** (факты пакета, геометрия,
стили — воспроизводимо, без модели) и **контекстный** (VLM-вердикты по
отрендеренным слайдам + EvidenceGraph). Единый реестр всех 34 правил —
`backend/deckdna/audit/catalog.py` (`GET /api/v1/audit/rules` отдаёт его же).

## Детерминированные правила — 24

`R` = правило чинится через пользовательский repair.

| Код | Проверка | Sev | Порог (configs/audit.default.yaml) | R |
|---|---|---|---|---|
| `text.overflow` | текст не помещается в рамку | error | `overflow_tolerance` | ✓ |
| `text.slide_clip` | текст уходит за край слайда | error | — | ✓ |
| `text.font_floor` | слишком мелкий шрифт | error | `font_floor_pt` (8pt) | |
| `accessibility.contrast` | низкий контраст текста (WCAG) | error | `contrast_min_ratio` (4.5) | ✓ |
| `layout.out_of_bounds` | элемент за границами слайда | error | `out_of_bounds_tolerance` | ✓ |
| `layout.unintended_overlap` | наложение текстовых элементов | error | `overlap_min_cover` | |
| `layout.edge_margin` | текст прижат к краю | warning | `edge_margin` | ✓ |
| `image.aspect_ratio` | картинка искажена | error | `aspect_tolerance` | ✓ |
| `density.bullet_count` | слишком много пунктов | warning | `max_bullets_per_slide` (6) | |
| `density.bullet_length` | слишком длинный пункт | warning | `max_words_per_bullet` (15) | |
| `density.table_size` | слишком большая таблица | warning | `max_table_rows`/`max_table_cols` (7×5) | |
| `density.chart_series` | слишком много рядов диаграммы | warning | `max_chart_series` (5) | |
| `density.occupancy` | слайд перегружен или пуст | warning | `occupancy_min`/`max` (25–75%) | |
| `template.font_family` | шрифт вне шаблона | warning | `max_font_families` (2) | ✓ |
| `template.font_scale` | кегль вне шкалы шаблона | warning | `font_scale_tolerance` (±10%) | |
| `template.color_palette` | цвет вне палитры шаблона | warning | `color_tolerance_delta_e` (ΔE 8) | ✓ |
| `template.anchor_position` | логотип/колонтитул сдвинут | error | `anchor_tolerance_pt` (2pt) | ✓ |
| `template.layout_origin` | макет не из этого шаблона | warning | — | |
| `integrity.empty_slide` | пустой слайд | error | — | ✓ |
| `integrity.duplicate_slide` | повтор слайда (Jaccard ≥0.9) | warning | `duplicate_similarity_threshold` | ✓ |
| `integrity.placeholder_text` | осталась заглушка шаблона | error | — | ✓ |
| `integrity.package` | файл повреждён / не читается | blocker | — | |
| `editability.raster_only` | слайд — одна растровая картинка | blocker | `raster_only_slide_area` | |
| `chart.metadata` | у диаграммы нет осей/легенды | error | — | |

## Контекстные правила — 10 (VLM)

`audit/contextual.py` рендерит каждый слайд (тем же путём soffice→pdftoppm,
что HTML-экспорт) и вызывает `gateway.vision_json` со схемой
`prompts/contextual_audit/slide_checks.v1.yaml`: `content.conclusion_title`,
`content.title_alignment`, `content.single_message`,
`content.source_support` (blocker), `content.nonempty`,
`content.visual_relevance`, `content.prompt_leakage`, `content.spelling`,
`content.data_relevance`, `deck.logical_flow`.

Issue создаётся только из вердикта `fail` с `confidence ≥ 0.5`; `uncertain`
намеренно не становится issue — честная неопределённость вместо ложного
срабатывания. Слой включён в основной пайплайн (`generate(gateway=...)`,
`POST /generations {"use_llm": true}`) и сливается в общий список issues с
флагом `deterministic=false`.

## Issues → выбор → repair → re-audit

Каждый issue несёт измеренные значения и порог (детерминированные) или
вердикт + confidence + версии промпта/модели (контекстные). Issues
переживают ревизии с переходами `open → selected → fixed|unresolved|dismissed`
и стабильным `fingerprint` (`rule_code + slide_id + shape_ids`), поэтому
dismiss не всплывает заново после ремонта.

12 правил помечены `repairable` (`catalog.py`, поле `fix_title_ru`);
планировщик (`repair/planner.py`) строит типизированные действия —
`resize_shape`, `recrop_image`, `move_shape`, `shorten_text`, `map_font`,
`map_color`, `merge_slide`, `remove_placeholder`. `editability.raster_only`
честно возвращает `not_implemented`; остальные неремонтируемые правила —
`unresolved` с указанной причиной. `fixed` ставится только когда re-audit
новой ревизии больше не видит проблему.

Auto-fix до первой ревизии: `template.color_palette`, `image.aspect_ratio`,
`accessibility.contrast` чинятся самим пайплайном и принимаются только если
re-audit показывает меньше issues без новых error/blocker; применённые
правки раскрываются в Quality Passport (`fallbacks`, `strategy: "auto_fix"`).

## Пороги

Реальные значения — `configs/audit.default.yaml` (загружается
`audit/config.py`; правка файла меняет поведение). Кастомный набор правил
допустим с обоснованием: расхождение каталога с планировщиком ломает сборку
(`tests/audit/test_rule_catalog.py`).

## Использование

```bash
deckdna audit out/demo/deck.pptx                    # отчёт по колоде
deckdna repair out/demo/deck.pptx --out fixed.pptx  # план → применить → re-audit
```

```text
POST /api/v1/variants/{id}/audits        # аудит ревизии (идемпотентен)
GET  /api/v1/audits/{id}/issues          # issues с bbox/severity/repairable
POST /api/v1/audits/{id}/repairs         # bounded repair выбранных issues
GET  /api/v1/audit/rules                 # каталог правил
```

