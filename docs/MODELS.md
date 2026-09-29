# Модели

Модель включается автоматически, когда есть провайдер: leased-сессия из UI
или серверный провайдер через `.env`. Без провайдеров пайплайн полностью
детерминированный — и честно говорит об этом в Quality Passport
(`usage.model_calls = 0`).

## Карта стадий → роли моделей

| Стадия | Роль | Промпт (версионируемый файл) | Фолбэк без модели |
|---|---|---|---|
| Структура и смысл слайдов | text | `prompts/content_planning/*.yaml` (storyline, структура, writer) | детерминированный `plan_deck`; модель только выбирает purpose/title/key_message/evidence маленькими батчами, текст пунктов собирается детерминированно из cited evidence |
| Стилизация текста | text | `prompts/content_styling/content_style.v1.yaml` | verbatim-текст из источника; grounding-проверка чисел/дат/имён, отклонённые пункты не меняются |
| Выбор эталона | text | `prompts/exemplar_rerank/*.yaml` | top-k только над детерминированными кандидатами; каждый кандидат несёт дискретный `layout_archetype` |
| Однострочное описание эталона | text | `prompts/exemplar_rerank/exemplar_describe.v1.yaml` | best-effort при `/templates/{id}/analyze`, вне 5-минутного бюджета генерации |
| Вписывание текста | text | `prompts/text_fitting/text_fit.v1.yaml` | расширить рамку → сократить с «…»; принимается только если re-audit показывает меньше overflow |
| Контекстный аудит | vision | `prompts/contextual_audit/slide_checks.v1.yaml` | только детерминированные правила; `uncertain` ≠ issue |
| Генерация картинок | image | — | stretch-роль, выключена по умолчанию (`content.image_generation.enabled: false`) |

Режим `use_llm` принимается на `POST /projects/{id}/generations`,
`POST /projects/{id}/plans` и `POST /audits/{id}/repairs`:
`null` (по умолчанию) = авто — модель при наличии провайдера; `true` =
серверный gateway; `false` = без модели. `provider_session_id` (сессия,
созданная через `POST /provider-sessions`, в UI — выбор провайдера)
сам включает LLM-путь и приоритетнее env-настроек.

## Настройка провайдера

```dotenv
DECKDNA_MOCK_PROVIDER=false
DECKDNA_PROVIDER_BASE_URL=https://endpoint/v1   # любой OpenAI-compatible
DECKDNA_PROVIDER_API_KEY=...
DECKDNA_PROVIDER_ALLOW_PRIVATE_NETWORKS=true    # для self-hosted/private IP
DECKDNA_MODEL_TEXT=qwen-3.8-27b
DECKDNA_MODEL_VISION=qwen-3.8-27b
DECKDNA_MODEL_IMAGE=black-forest-labs/flux.2-klein-4b
```

Проверка сессии — `POST /provider-sessions/{id}/test` выполняет **живые**
минимальные пробы по каждой заявленной capability: `structured_output`
(json_schema chat), `image_input` (+1px PNG data URL), `embeddings`
(один embed-вызов); `tool_calls` честно `skip` — пробы нет. Вердикты
`ok` / `fail` / `skip`.

`model_text` и `model_vision` — независимые настройки (и независимые в каждой
provider-сессии): на vision-роль можно ставить более быструю/мелкую модель —
контекстный аудит ограничен бюджетами стадий и таймаутами на слайд, а слайд,
не проверенный вовремя, честно помечается `uncertain`, а не блокируется.

## Заявленные модели и лицензии

Манифест — `configs/model_licenses.yaml` (валидируется `deckdna check-models`):

| Роль | model_id | Провайдер | Лицензия | Размер |
|---|---|---|---|---|
| text | qwen-3.8-27b | VK Inference | Apache-2.0* | 27B |
| vision | qwen-3.8-27b | VK Inference | Apache-2.0* | 27B |
| image (stretch) | black-forest-labs/flux.2-klein-4b | VK Inference | Apache-2.0 | 4B |

\* лицензия VK-hosted модели заявлена по ТЗ; манифест — `configs/model_licenses.yaml`.

Ограничения задачи: только open-weight Apache-2.0/MIT; text/vision ≤35B;
text-to-image ≤20B. Требования к VRAM и latency зависят от квантизации и
провайдера — подбирайте хост под конкретную развёртку.

## Версионирование и provenance

Каждый Quality Passport несёт `provenance.prompt_versions` (только промпты
реально выполненных и принятых стадий) и `provenance.model_profiles`
(`model_id`, `provider` — sanitized host без userinfo/ключей, `size_b`,
`license` из манифеста). Полностью детерминированный прогон оставляет оба
поля пустыми.

## Адаптеры

- `OpenAICompatibleProvider` — базовый (`providers/`): chat.completions,
  `response_format` json_schema, картинки как data URL.
- `VKInferenceProvider` (`providers/vk_inference.py`) — тонкий адаптер,
  модель `qwen-3.8-27b` (text+vision), env `DECKDNA_VK_*`.
- `MockProvider` — детерминированный, офлайн, default.

