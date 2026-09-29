# Архитектура

DeckDNA — двухсервисная система: статический фронтенд с reverse-proxy и один
API-процесс, в котором живёт весь pipeline генерации. Ниже — устройство
текущей сборки как есть.

## Системная диаграмма

```text
                    ┌────────────────────────────────────────────┐
  Browser ── :8080 ▶│  web — React SPA + nginx                   │
                    │  статика UI, прокси /api/* → api:8000      │
                    └───────────────┬────────────────────────────┘
                                    │ /api/v1 (JSON, SSE, multipart)
                    ┌───────────────▼────────────────────────────┐
                    │  api — FastAPI :8000 (uvicorn)             │
                    │                                            │
                    │  _Store — проекты, шаблоны, колоды,        │
                    │  аудиты, артефакты (байты в памяти)        │
                    │                                            │
                    │  Executor — джобы генерации в процессе     │
                    │                                            │
                    │  ModelGateway ── MockProvider (default)    │
                    │                 │ OpenAI-compatible        │
                    │                 │ VKInferenceProvider      │
                    └──────┬───────────────────────┬─────────────┘
                           │                       │
                     LibreOffice + poppler    prompts/, configs/,
                     (PDF/HTML рендер)        schemas/ — файлы в образе
```

`web` собирается из `frontend/Dockerfile` (Vite build → nginx с шаблоном
`frontend/docker/nginx.conf.template`, SSE-совместимый прокси). `api` — из
`backend/Dockerfile` с корневым build-контекстом: `backend/`, `configs/`,
`schemas/`, `prompts/`, `skill/`, `assets/fonts`, `pyproject.toml`; образ
включает LibreOffice и poppler — они нужны для PDF/HTML-рендера и PNG-превью.

## Слои кода

```text
Интерфейс     api/ (FastAPI) · cli/ (typer) · frontend SPA · skill/run_skill.py
Оркестрация   generation/pipeline.py — линейный проход стадий на вариант
Контракты     contracts/ + domain/ — pydantic-модели без I/O-импортов
Документы     pptx/ — OPC-пакет, клонирование эталонов, типизированные слоты
Качество      audit/ + repair/ + evaluation/quality_passport.py
Модели        providers/ (адаптеры) + prompts/ (версионируемые YAML-файлы)
```

Направление зависимостей — внутрь: `contracts/` и `domain/` не импортируют
FastAPI, python-pptx или SDK провайдеров.

## Пять стадий пайплайна (на вариант)

```text
1. ingest   content-файл → ContentPack → EvidenceGraph (источники и опоры)
2. analyze  шаблон → DesignDNA + пул эталонных слайдов (layouts, палитра,
            шрифтовая шкала, якоря, роли, ёмкости)
3. plan     ContentPack + DesignDNA + Brief → DeckPlan
            (детерминированный baseline всегда считается первым;
            опционально LLM-стадии: storyline-батчи, стилизация текста,
            rerank эталонов — каждая с послайдовым фолбэком)
4. compose  клон эталона + заполнение слотов нативными объектами OOXML
            → детерминированный аудит → auto-fix безопасных правил
            → опциональный контекстный VLM-аудит → Quality Passport
5. export   .pptx нативно; .pdf/.png/.html через soffice → pdftoppm
```

Варианты `faithful` / `balanced` / `visual` делят один ContentPack и
EvidenceGraph и различаются конфигом `configs/variants.default.yaml`:
веса rerank-а эталонов, agenda/recap, ограничения плотности текста.
Внутри прогона варианты выполняются последовательно.

## Ремонт

`POST /audits/{id}/repairs` применяет выбранные пользователем действия
(`resize_shape`, `recrop_image`, `move_shape`, `shorten_text`, `map_font`,
`map_color`, `merge_slide`, `remove_placeholder`) в координатах слайда,
записывает новую ревизию и делает re-audit затронутых правил. Геометрия
учитывает трансформации групп. Что не починилось — возвращается `unresolved`
с причиной, без фиктивных `applied`.

## Ошибки и деградация

Единый типизированный конверт `DeckDNAError` (`code`, `stage`, `retryable`,
`details`) от парсинга пакета до экспорта: повреждённый входной PPTX
отклоняется с `package_corrupt` ещё до аудита. Каждая модельная стадия
послайдово откатывается на детерминированный baseline — упавший батч не
теряет колоду.

## Конфигурация рантайма

Всё версионируемое поведение — файлами, а не кодом: пороги аудита
`configs/audit.default.yaml`, генерация `configs/generation.default.yaml`,
стратегии `configs/variants.default.yaml`, модельные роли и лицензии
`configs/model_licenses.yaml`, промпты `prompts/**.yaml` (версия каждого
промпта попадает в Quality Passport). Продуктовый skill —
`skill/SKILL.md` + `skill/manifest.yaml` + `skill/run_skill.py`
(также `GET /api/v1/skill/manifest`).

## Деплой-топология

```text
localhost / доверенная машина
├── web  127.0.0.1:8080   SPA + /api прокси
└── api  127.0.0.1:8000   uvicorn; сборка из корня репо
   внешний доступ → TLS-прокси с авторизацией перед web
```

Провайдерские токены сессий хранятся в памяти с TTL, не сериализуются в
ответы и отзываются `DELETE /provider-sessions/{id}`.

