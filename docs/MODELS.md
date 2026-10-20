# Модели

Внешние модели подключаются автоматически, когда настроен серверный провайдер
через `.env` или создана сессия провайдера из веб-интерфейса. По умолчанию
включён детерминированный `MockProvider` (`DECKDNA_MOCK_PROVIDER=true`), при
котором весь пайплайн работает офлайн без сетевых вызовов.

## Карта стадий и ролей

| Стадия | Роль | Промпт | Поведение без внешней модели |
|---|---|---|---|
| Структура, SCQA и тезисы слайдов | text | `prompts/content_planning/*.yaml` | Детерминированный планировщик `plan_deck` + `FactLedger`; модель уточняет заголовки-выводы и распределение опор по слайдам с проверкой чисел и цитат |
| Стилизация текста | text | `prompts/content_styling/content_style.v1.yaml` | Дословный текст из источника; при работе модели выполняется проверка сохранности всех чисел, дат и собственных имён |
| Выбор эталонного макета | text | `prompts/exemplar_rerank/*.yaml` | Детерминированный ранжировщик по 8 архетипам (`hero_stat`, `kpi_grid`, `process_steps`, `comparison_2col`, `cards_grid`, `chart_takeaway`, `table_summary`, `timeline`) |
| Описание эталона | text | `prompts/exemplar_rerank/exemplar_describe.v1.yaml` | Выполняется при анализе шаблона вне бюджета генерации колоды |
| Вписывание текста в рамку | text | `prompts/text_fitting/text_fit.v1.yaml` | Геометрическое расширение рамки или аккуратное сокращение с проверкой через повторный аудит |
| Диалоговый помощник | text | встроенный системный контур `assistant/` | Детерминированный разбор намерений и команд редактирования плана с проверкой фактов по `EvidenceGraph` |
| Контекстный аудит | vision | `prompts/contextual_audit/slide_checks.v1.yaml` | Выполняются 26 детерминированных правил |
| Генерация иллюстраций | image | `prompts/content_planning/image_scene.v1.yaml` | Построение векторных схем и карточек OOXML; при подключении модели — генерация иллюстраций в палитре шаблона (`black-forest-labs/flux.2-klein-4b`) |

Параметр `use_llm` в `POST /projects/{id}/generations`, `POST /projects/{id}/plans`
и `POST /audits/{id}/repairs` управляет режимом запуска:
- `null` (по умолчанию) — использовать модель при наличии активной сессии или серверного провайдера;
- `true` — принудительно использовать серверный `ModelGateway`;
- `false` — принудительно выполнить только детерминированный проход.

## Настройка провайдера и резервного контура

```dotenv
DECKDNA_MOCK_PROVIDER=false
DECKDNA_PROVIDER_BASE_URL=https://api.inference.example/v1
DECKDNA_PROVIDER_API_KEY=...
DECKDNA_PROVIDER_PROJECT_ID=...                 # опционально, заголовок Project-ID для VK Inference
DECKDNA_MODEL_TEXT=qwen38-27b-fp8
DECKDNA_MODEL_VISION=qwen38-27b-fp8

# Резервный провайдер (автоматический fallback при таймауте или 5xx основного)
DECKDNA_FALLBACK_PROVIDER_BASE_URL=https://fallback.inference.example/v1
DECKDNA_FALLBACK_PROVIDER_API_KEY=...
DECKDNA_FALLBACK_MODEL_TEXT=qwen-3.8-27b
DECKDNA_FALLBACK_MODEL_VISION=qwen-3.8-27b

# Генерация иллюстраций
DECKDNA_IMAGE_BASE_URL=https://image.inference.example/v1
DECKDNA_IMAGE_API_KEY=...
DECKDNA_MODEL_IMAGE=black-forest-labs/flux.2-klein-4b
```

Эндпоинт `POST /api/v1/provider-sessions/{id}/test` выполняет живые минимальные пробы
по заявленным возможностям провайдера (`structured_output`, `image_input`, `embeddings`)
и возвращает вердикты `ok` / `fail` / `skip`.

## Заявленные модели и лицензии

Реестр разрешённых моделей хранится в `configs/model_licenses.yaml` и проверяется
командой `deckdna check-models`:

| Роль | `model_id` | Провайдер | Лицензия | Размер |
|---|---|---|---|---|
| text | `qwen38-27b-fp8` (`qwen-3.8-27b`) | VK Inference / OpenAI-compatible | Apache-2.0 | 27B |
| vision | `qwen38-27b-fp8` (`qwen-3.8-27b`) | VK Inference / OpenAI-compatible | Apache-2.0 | 27B |
| image | `black-forest-labs/flux.2-klein-4b` | VK Inference / OpenAI-compatible | Apache-2.0 | 4B |

Все используемые модели удовлетворяют ограничениям: открытые веса под лицензией
Apache-2.0 / MIT, размер языковой и визуальной модели ≤ 35B параметров, модели
генерации изображений ≤ 20B параметров.

## Трассировка в Quality Passport

Каждый `QualityPassport` фиксирует `provenance.prompt_versions` (версии и хеши
реально вызванных промптов) и `provenance.model_profiles` (`model_id`, очищенный
хост провайдера без ключей, размер и лицензия). При детерминированном прогоне
`usage.model_calls` равен `0`.
