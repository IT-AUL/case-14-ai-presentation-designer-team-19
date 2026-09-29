<p align="center">
  <img src="frontend/public/logo-square.svg" alt="DeckDNA" width="120">
</p>

<h1 align="center">DeckDNA — цифровой дизайнер презентаций</h1>

<p align="center">
  <a href="https://github.com/IT-AUL/case-14-ai-presentation-designer-team-19/actions/workflows/ci.yml"><img src="https://github.com/IT-AUL/case-14-ai-presentation-designer-team-19/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://github.com/IT-AUL/case-14-ai-presentation-designer-team-19/releases"><img src="https://img.shields.io/github/v/release/IT-AUL/case-14-ai-presentation-designer-team-19" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License: Apache-2.0"></a>
</p>

<p align="center"><b>Любой PPTX/POTX-шаблон + ваш контент → три редактируемые презентации в фирменном стиле шаблона — за минуты, с аудитом качества и экспортом.</b></p>

Open-source платформа и agent skill (Apache-2.0).

## Возможности

- **Design DNA** из произвольного шаблона: layout'ы, палитра, шрифтовая шкала, якоря, роли слайдов — без хардкода под конкретные файлы.
- **Редактируемый результат**: клонирование эталонных слайдов шаблона и типизированное заполнение слотов — сгруппированные фигуры, тексты, таблицы, диаграммы остаются живыми объектами, а не растром.
- **Аудит → пользовательский repair**: issues с bbox-оверлеями, ограниченные типизированные правки, re-audit и новая ревизия колоды.
- **Quality Passport**: метрики, версии промптов и моделей, применённые фолбэки — честная история каждого прогона.
- **Модели — opt-in**: детерминированный `MockProvider` по умолчанию, без внешних ключей и вызовов; реальный OpenAI-compatible провайдер подключается через `.env`.

## Быстрый старт

```bash
git clone https://github.com/IT-AUL/case-14-ai-presentation-designer-team-19 && cd case-14-ai-presentation-designer-team-19
```

Дальше — один из двух вариантов:

**Готовые образы** (публикуются в GHCR и Docker Hub на каждый релизный тег):

```bash
DECKDNA_VERSION=v1.0.0 docker compose up -d --no-build --pull always
# → http://localhost:8080 (UI), API на 127.0.0.1:8000
```

Тянет `ghcr.io/it-aul/deckdna-{web,api}:<версия>`; Docker Hub — тот же
compose с `DECKDNA_REGISTRY=docker.io/renatgubaudullin`. Публикуются
только версионные теги `vX.Y.Z`, `latest` нет.

**Сборка из исходников**:

```bash
docker compose up --build
# → http://localhost:8080 (UI; OpenAPI: http://127.0.0.1:8000/openapi.json)
```

Два контейнера (`web` + `api`), `.env` не нужен. Первая сборка скачивает
базовые образы и LibreOffice — нужна сеть; дальше инференс моделей не
обращается ни к какому внешнему провайдеру (детерминированный mock-режим).

## От брифа до файла за пять шагов

```text
1. Создайте проект            → POST /api/v1/projects
2. Загрузите шаблон           → POST /api/v1/projects/{id}/templates (PPTX/POTX)
3. Разбор шаблона             → POST /api/v1/templates/{id}/analyze → Design DNA
4. Загрузите контент          → POST /api/v1/projects/{id}/content-packs
5. Генерация и выгрузка       → POST /api/v1/projects/{id}/generations
                                → POST /api/v1/variants/{id}/exports (pptx/pdf/html)
```

Всё то же — кликами в UI. Полный контракт: [docs/contracts/API.md](docs/contracts/API.md),
машиночитаемая схема: `docs/contracts/openapi.json` (генерируется из кода).

## Провайдер моделей (opt-in)

Любой OpenAI-compatible endpoint с open-weight моделями (Apache-2.0/MIT, ≤35B
для text/vision, ≤20B для image по условиям задачи): vLLM, Ollama, TGI.

```dotenv
DECKDNA_MOCK_PROVIDER=false
DECKDNA_PROVIDER_BASE_URL=http://your-vllm-host:8000/v1
DECKDNA_PROVIDER_API_KEY=...
DECKDNA_PROVIDER_ALLOW_PRIVATE_NETWORKS=true  # для self-hosted/private IP
DECKDNA_MODEL_TEXT=qwen2.5-32b-instruct
DECKDNA_MODEL_VISION=qwen2.5-vl-32b-instruct
DECKDNA_MODEL_EMBED=bge-m3
```

Провайдер-сессия из UI приоритетнее env. Модели и лицензии —
`configs/model_licenses.yaml` (`deckdna check-models`), детали —
[docs/MODELS.md](docs/MODELS.md).

## CLI и agent skill (без Docker)

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/deckdna generate tests/fixtures/pptx/vk_tech_template.pptx \
    tests/fixtures/content/poc_article.md out/demo \
    --purpose "Представить решение" --slides 12 --strategy balanced
.venv/bin/deckdna audit out/demo/deck.pptx
.venv/bin/deckdna repair out/demo/deck.pptx --out out/demo/deck-repaired.pptx
.venv/bin/deckdna export out/demo/deck.pptx --format pdf --out out/demo/deck.pdf
python3 skill/run_skill.py tests/fixtures/pptx/vk_tech_template.pptx \
    tests/fixtures/content/poc_article.md out/skill-demo --slides 12
```

Контракт скила: [skill/SKILL.md](skill/SKILL.md), манифест: `skill/manifest.yaml`
(и `GET /api/v1/skill/manifest`).

## Архитектура

```text
web (SPA + nginx) ──/api/*──▶ api (FastAPI)
                               ingest → plan → compose → audit → auto-fix
                               → passport → export   [in-process jobs]
                               model gateway: Mock │ OpenAI-compat │ VK
```

Слои: `api/`·`cli/` · pipeline оркестрация · `contracts/`+`domain/` (pydantic,
без I/O) · `pptx/` (OPC, клонирование, рендер) · `audit/`+`repair/` ·
`providers/`+`prompts/`. Подробно: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
аудит: [docs/AUDIT.md](docs/AUDIT.md).

## Релизы

Тег `vX.Y.Z` на коммите из `main` запускает release-конвейер в том же CI:
обычные проверки → сборка multi-arch образов → публикация
`ghcr.io/it-aul/deckdna-{web,api}:vX.Y.Z` и
`docker.io/renatgubaudullin/deckdna-{web,api}:vX.Y.Z` → GitHub Release с
автосгенерированными заметками.

## Проверки

```bash
pytest tests -q && ruff check backend tests \
    && mypy backend/deckdna/domain backend/deckdna/contracts
cd frontend && npm ci && npm run lint && npm run lint:fsd \
    && npm run typecheck && npm test -- --run && npm run build
docker compose config --quiet && docker compose up -d --build
```

## Требования

Docker 24+ с Compose — для quickstart; bare-metal: Python 3.12, Node 20+,
LibreOffice 24+, poppler-utils.

## Лицензия

Apache-2.0 — [LICENSE](LICENSE). Шрифты в `assets/fonts/` под SIL OFL.
Hackathon build (ЛЦТ 2026, задача «Цифровой дизайнер презентаций»).
