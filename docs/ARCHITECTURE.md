# Архитектура

DeckDNA — двухсервисная система: статический фронтенд с reverse-proxy (`web`) и
API-процесс (`api`), в котором выполняются разбор шаблонов и брендбуков,
анализ данных, планирование, сборка OOXML, аудит, ремонт и экспорт.

## Системная диаграмма

```text
                    ┌────────────────────────────────────────────┐
  Browser ── :8080 ▶│  web — React 19 SPA + nginx                │
                    │  статика UI, прокси /api/* → api:8000      │
                    └───────────────┬────────────────────────────┘
                                    │ /api/v1 (JSON, SSE, multipart)
                    ┌───────────────▼────────────────────────────┐
                    │  api — FastAPI :8000 (uvicorn)             │
                    │                                            │
                    │  _Store — проекты, шаблоны, брендбуки,     │
                    │  колоды, диалоги помощника, артефакты      │
                    │  (+ снапшот на диск /data/artifacts)       │
                    │                                            │
                    │  Executor — джобы генерации в процессе     │
                    │                                            │
                    │  ModelGateway ── MockProvider (default)    │
                    │                 │ OpenAI-compatible        │
                    │                 │ VKInferenceProvider      │
                    └──────┬───────────────────────┬─────────────┘
                           │                       │
                     LibreOffice + poppler    prompts/, configs/,
                     (PDF/HTML/PNG рендер)    schemas/ — файлы в образе
```

`web` собирается из `frontend/Dockerfile` (Vite build → nginx с шаблоном
`frontend/docker/nginx.conf.template` и поддержкой SSE без буферизации для
событий джобов и потокового чата). `api` собирается из `backend/Dockerfile`
с корневым build-контекстом (`backend/`, `configs/`, `schemas/`, `prompts/`,
`skill/`, `assets/fonts`, `pyproject.toml`) и включает LibreOffice + Poppler
для рендера `.pdf`, `.png` и `.html`.

## Слои кода

```text
Интерфейсы    api/ (FastAPI) · assistant/ (SSE-чат) · mcp/ (stdio) · cli/ · frontend SPA · skill/
Оркестрация   generation/pipeline.py — линейный проход стадий + послайдовая пересборка
Исследование  research/ — веб-поиск с дословной верификацией цитат и библиографией
Планирование  planning/ — FactLedger, beam-search историй по данным, SCQA-дуга, 8 архетипов
Контракты     contracts/ + domain/ — строгие pydantic-модели без I/O-импортов
Документы     pptx/ — OPC-пакет, клонирование эталонов, нативные диаграммы, таблицы, фигуры
Качество      audit/ + repair/ + evaluation/quality_passport.py
Модели        providers/ (адаптеры) + prompts/ (версионируемые YAML-файлы)
```

Направление зависимостей — строго внутрь: `contracts/` и `domain/` не импортируют
FastAPI, `python-pptx` или сетевые клиенты. На фронтенде соблюдается
Feature-Sliced Design (`app → pages → widgets → features → entities → shared`),
контролируемый автоматической проверкой `npm run lint:fsd`.

## Стадии пайплайна

```text
1. ingest     документы и таблицы → ContentPack → EvidenceGraph + FactLedger
              (при дефиците фактов — опциональный добор проверенных цитат через research/)
2. analyze    шаблон PPTX/POTX + брендбук PDF/DOCX/MD → DesignDNA + пул эталонных слайдов
              (палитра, шрифтовая шкала, поля, сетка, якоря логотипов, роли и ёмкости слотов)
3. plan       ContentPack + FactLedger + DesignDNA + Brief → DeckPlan
              (SCQA-нарратив, заголовки-выводы, выбор из 8 архетипов макета и 7 типов диаграмм;
              детерминированный baseline + опциональные LLM-стадии с послайдовым откатом)
4. compose    клон эталона + заполнение слотов нативными объектами OOXML (текст, таблицы,
              диаграммы, векторные карточки и схемы) → детерминированный аудит → auto-fix
              безопасных правил → опциональный контекстный VLM-аудит → Quality Passport
5. export     .pptx нативно; .pdf/.png/.html через soffice → pdftoppm + векторный SVG/DOM-экспорт
```

Варианты `faithful`, `balanced` и `visual` разделяют общий `ContentPack`,
`EvidenceGraph` и `FactLedger`, различаясь параметрами в `configs/variants.default.yaml`:
весами выбора эталонов, долей визуальных архетипов и плотностью текста.

## Диалоговый помощник и точечная доводка

Помимо пакетного прогона, в `api` работает диалоговый контур (`backend/deckdna/assistant/`):
- `POST /api/v1/projects/{id}/agent/messages` принимает сообщение пользователя и стримит
  ответ по SSE (`text/event-stream`) с типизированными карточками действий (`proposed_actions`).
- Все числа и факты в ответах помощника сверяются с `EvidenceGraph` и `FactLedger`.
- Отдельный слайд готового варианта можно пересобрать с новой инструкцией или другим
  архетипом через `POST /api/v1/variants/{id}/slides/{slide_id}/regenerate` без пересчёта
  остальных слайдов колоды.

## Ремонт

`POST /api/v1/audits/{id}/repairs` применяет выбранные пользователем типизированные действия
(`resize_shape`, `recrop_image`, `move_shape`, `shorten_text`, `map_font`, `map_color`,
`merge_slide`, `remove_placeholder`) в координатах слайда, сохраняет новую ревизию и
повторно запускает аудит затронутых правил. Если нарушение не устранено после проверки,
оно честно остаётся в статусе `unresolved` с указанием причины.

## Ошибки и деградация

Все слои используют единый конверт `DeckDNAError` (`code`, `stage`, `retryable`, `details`).
Повреждённый входной архив отклоняется с кодом `package_corrupt` до запуска пайплайна.
Любой сбой внешнего провайдера модели на стадии планирования, стилизации или VLM-аудита
обрабатывается послайдовым откатом на детерминированный алгоритм без потери сборки.

## Конфигурация рантайма

Поведение вынесено в версионируемые конфигурационные файлы:
- `configs/audit.default.yaml` — пороги правил аудита;
- `configs/generation.default.yaml` — лимиты генерации и бюджетов времени;
- `configs/variants.default.yaml` — параметры стратегий `faithful` / `balanced` / `visual`;
- `configs/model_licenses.yaml` — реестр разрешённых open-weight моделей и их лицензий;
- `prompts/**.yaml` — версионированные промпты (хэш и версия фиксируются в `QualityPassport`).
