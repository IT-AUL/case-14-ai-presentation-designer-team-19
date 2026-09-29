# HTTP API

Базовый путь `/api/v1`, JSON в UTF-8, загрузки — multipart, время — UTC.
Авторитетная машиночитаемая схема — [`openapi.json`](openapi.json)
(генерируется из кода) и интерактивный Swagger UI на `/docs` запущенного API.

## Соглашения

- Длинные операции отвечают `202` + `job_id`; статус — `GET /jobs/{id}`,
  события — `GET /jobs/{id}/events` (SSE-снапшот состояния).
- Мутирующие запросы принимают `Idempotency-Key`; повтор с тем же телом
  возвращает исходный ответ, с другим — `409 idempotency_conflict`.
- Ответы несут `X-Request-ID`. Списки — курсорная пагинация
  (`next_cursor` → `cursor`, `limit` ≤ 200).
- Ошибки — единый конверт `{error: {code, message, stage, retryable,
  request_id, details}}`; `stage` указывает стадию пайплайна.

## Сквозной пример: от брифа до файла

Скрипт ниже выполняется целиком — нужны `bash`, `curl`, `jq`, корень
репозитория как рабочая директория и запущенный API
(`docker compose up -d --build` → `127.0.0.1:8000`):

```bash
set -euo pipefail
API="http://127.0.0.1:8000/api/v1"

# 1. Проект → 201 {"id": "prj_..."}
PJ=$(curl -fsS "$API/projects" -H 'Content-Type: application/json' \
  -d '{"name":"demo","target_slide_count":10}' | jq -r .id)

# 2. Шаблон (multipart) → 201 {"id": "tpl_...", "validation_status": "valid"}
TPL=$(curl -fsS "$API/projects/$PJ/templates" \
  -F 'file=@tests/fixtures/pptx/synthetic_unseen.pptx' | jq -r .id)

# 3. Анализ шаблона → 202 {"job_id","analysis_id"}, затем Design DNA
curl -fsS -X POST "$API/templates/$TPL/analyze" \
  -H 'Content-Type: application/json' -d '{}'
curl -fsS "$API/templates/$TPL/design-dna" | jq '.declared | keys'

# 4. Контент-пак → 202 {"content_pack": {"id": "pack_..."}}; ингест идёт job'ом
PACK=$(curl -fsS "$API/projects/$PJ/content-packs" \
  -F 'files=@tests/fixtures/content/poc_article.md' | jq -r .content_pack.id)

# 5. Генерация одного варианта → 202 {"generation_id","variant_ids"}
GEN=$(curl -fsS "$API/projects/$PJ/generations" -H 'Content-Type: application/json' -d "{
  \"template_id\": \"$TPL\",
  \"content_pack_id\": \"$PACK\",
  \"brief\": {\"purpose\":\"Представить решение\",\"audience\":\"жюри\",\"language\":\"ru\",\"target_slide_count\":10},
  \"variants\": [{\"strategy\":\"faithful\"}]
}" | jq -r .generation_id)

# 6. Опрос: queued → running → completed (bounded, до ~5 минут)
for _ in $(seq 1 150); do
  STATE=$(curl -fsS "$API/generations/$GEN" | jq -r .state)
  case "$STATE" in
    completed) break ;;
    failed|canceled) echo "generation $STATE"; exit 1 ;;
  esac
  sleep 2
done
[ "$STATE" = "completed" ] || { echo "timeout waiting for generation"; exit 1; }

# 7. Экспорт варианта → 202 {"export_id"}; артефакты с sha256/size_bytes
VAR=$(curl -fsS "$API/generations/$GEN/variants" | jq -r '.items[0].id')
EXP=$(curl -fsS "$API/variants/$VAR/exports" -H 'Content-Type: application/json' \
  -d '{"formats":["pptx","pdf","html","quality_passport"]}' | jq -r .export_id)
ART=$(curl -fsS "$API/exports/$EXP" \
  | jq -r '.artifacts[] | select(.format=="pptx") | .artifact_id')
curl -fsSL "$API/artifacts/$ART/download" -o deck.pptx   # реальные байты PPTX
```

## Ресурсы

| Группа | Маршруты |
|---|---|
| Проекты | `POST/GET /projects`, `GET/PATCH/DELETE /projects/{id}`, `GET /projects/{id}/generations` |
| Шаблоны | `POST /projects/{id}/templates`, `GET /templates/{id}`, `POST /templates/{id}/analyze`, `GET /templates/{id}/design-dna`, `GET /templates/{id}/slides` |
| Контент | `POST /projects/{id}/content-packs`, `GET /content-packs/{id}`, `GET /content-packs/{id}/evidence-graph` |
| План | `POST /projects/{id}/plans` — DeckPlan по стратегиям без рендера; `deck_plan_id` или отредактированный `deck_plan` подаётся в `/generations` |
| Генерация | `POST /projects/{id}/generations`, `GET /generations/{id}`, `GET /generations/{id}/variants`, `POST /generations/{id}/cancel`, `POST /generations/{id}/retry` (всегда новый прогон через `parent_run_id`) |
| Слайды | `GET /variants/{id}`, `GET /variants/{id}/slides`, `GET /slides/{id}`, `GET /slides/{id}/preview` (PNG) |
| Аудит | `POST /variants/{id}/audits`, `GET /audits/{id}`, `GET /audits/{id}/issues`, `POST /audits/{id}/repairs`, `POST /issues/{id}/dismiss`, `GET /audit/rules` |
| Экспорт | `POST /variants/{id}/exports`, `GET /exports/{id}`, `GET /artifacts/{id}/download` (`ETag` = sha256, `Range`, `If-None-Match` → `304`) |
| Система | `GET /health/live`, `GET /health/ready`, `GET /version`, `GET /capabilities`, `GET /skill/manifest` |

Полезные детали поведения: `POST /audits` идемпотентен (возвращает аудит,
уже посчитанный генерацией; с живой `provider_session_id` может дополнить
его контекстным слоем); `repairs` принимает `selected_issue_ids`,
`max_iterations` и `?dry_run=true` (план без применения); `cancel`
кооперативный — queued-варианты не стартуют, текущий доводится до
`completed`, дальше job терминируется `canceled`.

`brief` в `/generations` — объект `{purpose, audience, language,
target_slide_count, tone?, mandatory_sections?}`; `use_llm`:
`null` = авто (модель при наличии провайдера), `false` = выкл, `true` =
серверный gateway.

## Провайдер-сессии

`POST /provider-sessions` регистрирует endpoint `{label, base_url,
api_token, models{text,vision,embedding,image}, capabilities,
timeout_seconds, max_concurrency, ttl_seconds}`. Токен арендуется в памяти,
TTL соблюдается лениво (просроченная сессия отвечает `404`), `DELETE` —
немедленный отзыв; токен не возвращается ни одним ответом. `base_url`
приватных/loopback/metadata-хостов отклоняется; оператор может разрешить
только через серверную `DECKDNA_PROVIDER_ALLOW_PRIVATE_NETWORKS=true` —
клиентским полем это не переопределяется.
`POST /provider-sessions/{id}/test` — живые пробы capability (см.
[docs/MODELS.md](../MODELS.md)).

## Коды ответов

`200/201` синхронный успех · `202` job принят · `409` конфликт
жизненного цикла/идемпотентности · `413` upload > `DECKDNA_MAX_UPLOAD_MB`
· `415` неподдерживаемый тип · `422` доменная валидация · `429` лимит
провайдера/параллелизма · `503` недоступен рендерер (soffice/pdftoppm)
или провайдер.
