# HTTP API

Базовый путь — `/api/v1`, формат тел — JSON в UTF-8, загрузка файлов — `multipart/form-data`,
время — UTC. Полная спецификация OpenAPI 3.1 находится в [`openapi.json`](openapi.json)
(генерируется напрямую из FastAPI-приложения) и доступна на `/docs` запущенного сервера.

## Соглашения

- Длительные операции возвращают `202 Accepted` с `job_id`; статус опрашивается через
  `GET /jobs/{id}`, а поток обновлений доступен по SSE `GET /jobs/{id}/events`.
- Потоковый чат помощника (`POST /projects/{id}/agent/messages`) передаёт события
  `message_start`, `delta`, `proposed_actions`, `message_end` по `text/event-stream`.
- Мутирующие запросы поддерживают заголовок `Idempotency-Key`: повтор с тем же телом
  возвращает сохранённый ответ, а с изменённым телом — `409 idempotency_conflict`.
- Каждый ответ содержит `X-Request-ID`. Списки используют курсорную пагинацию
  (`next_cursor` → `cursor`, `limit` ≤ 200).
- Ошибки возвращаются в едином формате `{error: {code, message, stage, retryable, request_id, details}}`.

## Сквозной пример: от проекта до готового PPTX

Скрипт ниже выполняется целиком при запущенном стеке (`docker compose up -d --build`):

```bash
set -euo pipefail
API="http://127.0.0.1:8000/api/v1"

# 1. Создание проекта → 201 {"id": "prj_..."}
PJ=$(curl -fsS "$API/projects" -H 'Content-Type: application/json' \
  -d '{"name":"demo","target_slide_count":10}' | jq -r .id)

# 2. Загрузка шаблона PPTX → 201 {"id": "tpl_...", "validation_status": "valid"}
TPL=$(curl -fsS "$API/projects/$PJ/templates" \
  -F 'file=@tests/fixtures/pptx/synthetic_unseen.pptx' | jq -r .id)

# 3. Анализ шаблона и получение Design DNA
curl -fsS -X POST "$API/templates/$TPL/analyze" \
  -H 'Content-Type: application/json' -d '{}' >/dev/null
curl -fsS "$API/templates/$TPL/design-dna" | jq '.declared | keys'

# 4. Загрузка материалов → 202 {"content_pack": {"id": "pack_..."}}
PACK=$(curl -fsS "$API/projects/$PJ/content-packs" \
  -F 'files=@tests/fixtures/content/poc_article.md' | jq -r .content_pack.id)

# 5. Запуск генерации колоды → 202 {"generation_id", "variant_ids"}
GEN=$(curl -fsS "$API/projects/$PJ/generations" -H 'Content-Type: application/json' -d "{
  \"template_id\": \"$TPL\",
  \"content_pack_id\": \"$PACK\",
  \"brief\": {\"purpose\":\"Представить решение\",\"audience\":\"жюри\",\"language\":\"ru\",\"target_slide_count\":10},
  \"variants\": [{\"strategy\":\"faithful\"}]
}" | jq -r .generation_id)

# 6. Ожидание завершения сборки
for _ in $(seq 1 150); do
  STATE=$(curl -fsS "$API/generations/$GEN" | jq -r .state)
  case "$STATE" in
    completed) break ;;
    failed|canceled) echo "generation $STATE"; exit 1 ;;
  esac
  sleep 2
done
[ "$STATE" = "completed" ] || { echo "timeout waiting for generation"; exit 1; }

# 7. Экспорт в PPTX, PDF, HTML и Quality Passport
VAR=$(curl -fsS "$API/generations/$GEN/variants" | jq -r '.items[0].id')
EXP=$(curl -fsS "$API/variants/$VAR/exports" -H 'Content-Type: application/json' \
  -d '{"formats":["pptx","pdf","html","quality_passport"]}' | jq -r .export_id)
ART=$(curl -fsS "$API/exports/$EXP" \
  | jq -r '.artifacts[] | select(.format=="pptx") | .artifact_id')
curl -fsSL "$API/artifacts/$ART/download" -o deck.pptx
```

## Основные группы маршрутов

| Группа | Маршруты |
|---|---|
| Проекты и мастер | `POST/GET /projects`, `GET/PATCH/DELETE /projects/{id}`, `GET /projects/{id}/generations`, `GET/PUT /projects/{id}/assistant-flow` |
| Диалоговый помощник | `GET /projects/{id}/agent/messages`, `POST /projects/{id}/agent/messages` (SSE), `POST /projects/{id}/agent/actions/{action_id}/apply` |
| Шаблоны и брендбук | `POST /projects/{id}/templates`, `GET /templates/{id}`, `POST /templates/{id}/analyze`, `POST /templates/{id}/brandbook`, `GET /templates/{id}/design-dna`, `GET /templates/{id}/slides` |
| Контент и факты | `POST /projects/{id}/content-packs`, `GET /content-packs/{id}`, `GET /content-packs/{id}/evidence-graph` |
| Планирование | `POST /projects/{id}/plans`, `GET/PATCH /plans/{id}` |
| Генерация | `POST /projects/{id}/generations`, `GET /generations/{id}`, `GET /generations/{id}/variants`, `POST /generations/{id}/cancel`, `POST /generations/{id}/retry` |
| Слайды и пересборка | `GET /variants/{id}`, `GET /variants/{id}/slides`, `GET /slides/{id}`, `GET /slides/{id}/preview`, `POST /variants/{id}/slides/{slide_id}/regenerate` |
| Аудит и ремонт | `POST /variants/{id}/audits`, `GET /audits/{id}`, `GET /audits/{id}/issues`, `POST /audits/{id}/repairs`, `POST /issues/{id}/dismiss`, `GET /audit/rules` |
| Экспорт и артефакты | `POST /variants/{id}/exports`, `GET /exports/{id}`, `GET /artifacts/{id}/download` (`ETag`, `Range`, `If-None-Match`) |
| Система и провайдеры | `GET /health/live`, `GET /health/ready`, `GET /version`, `GET /capabilities`, `GET /skill/manifest`, `POST/GET/DELETE /provider-sessions` |

## Коды ответов

`200 / 201` синхронный успех · `202` фоновая задача принята · `409` конфликт состояния или идемпотентности ·
`413` превышен `DECKDNA_MAX_UPLOAD_MB` · `415` неподдерживаемый формат файла ·
`422` ошибка доменной валидации · `429` превышен лимит параллелизма ·
`503` рендерер или внешний провайдер недоступен.
