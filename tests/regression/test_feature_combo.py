"""Сквозная регрессия фич-комбо: все новые типы контента x все
variant-стратегии на органайзерском шаблоне.

За ночь смержены попарно проверенные фичи — OR-007 (три стратегии),
chart/table/image/diagram-aware pipeline, docx image_ref, новые
audit-правила. Этот тест прогоняет их вместе, не изолированно:

1. markdown, содержащий одновременно GFM-таблицу, ```chart-блок,
   ```diagram-блок и `![alt](file.png)`, прогоняется через generate()
   для всех трёх стратегий (faithful/balanced/visual) на шаблоне
   lct2026_submission.pptx (chart-слайды 21-23 — самый богатый
   chart/pic-набор из органайзерских).
2. docx с таблицей и встроенной картинкой прогоняется один раз
   (docx не несёт fenced-блоки — chart/diagram от него не ждём).

Цель — поймать РЕГРЕССИИ (упавший пайплайн, исключение, испорченный
файл), а не совершенство покрытия: честные границы фич зафиксированы,
dropped_units по какой-то комбинации ожидаемы. Поэтому тест требует
лишь чтобы generate/audit/render не падали и pptx был валидным, а
срез dropped_units и sha256 колод проверяется итоговым тестом.
"""

import hashlib
import shutil
from io import BytesIO
from pathlib import Path

import pytest
from deckdna.audit.basic import audit_deck
from deckdna.contracts.deck_plan import Brief, Kind
from deckdna.contracts.variant_spec import Strategy
from deckdna.generation.pipeline import generate
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.story_director import plan_deck
from docx import Document
from PIL import Image
from pptx import Presentation

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "tests" / "fixtures" / "pptx" / "lct2026_submission.pptx"

HAS_SOFFICE = shutil.which("soffice") is not None

pytestmark = [
    pytest.mark.skipif(not TEMPLATE.exists(), reason="organizer fixture missing"),
    pytest.mark.skipif(not HAS_SOFFICE, reason="needs soffice for real render"),
]

BRIEF = {
    "purpose": "Фич-комбо регрессия",
    "audience": "эксперты",
    "language": "ru",
    "target_slide_count": 12,
}

STRATEGIES = [Strategy.faithful, Strategy.balanced, Strategy.visual]

# Срез по прогонам: имя прогона -> (dropped_units, sha256 pptx).
# Заполняется в _check_run, читается итоговым тестом.
_SNAPSHOTS: dict[str, dict] = {}

# Известные виды дропаемого контента (см. minimal.py/_slide_texts и
# fill-отчёты). Незнакомый kind в dropped_units — регрессия учёта.
_KNOWN_DROP_KINDS = {"text", "image", "chart", "diagram", "table", "icon"}


def _png_bytes(color=(200, 30, 30), size=(240, 160)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


_MD = """# Комбо-контент: данные, процесс, визуал

DeckDNA собирает колоду без ручной вёрстки — таблица, chart-блок, diagram-блок и картинка.

## Показатели квартала

| Метрика | Q1 | Q2 |
| --- | --- | --- |
| Выручка, млрд ₽ | 120.5 | 135.2 |
| Чистая маржа | 30% | 32% |

## Динамика выручки

```chart id=ch-1
categories: 2021, 2022, 2023, 2024
Выручка: 4.3, 2.5, 3.5, 4.5
Маржа: 2.4, 4.4, 1.5, 2.8
```

Рост выручки подтверждается всеми кварталами года.

## Процесс поставки

```diagram id=dg-1
Сбор требований -> Анализ -> План -> Релиз -> Поддержка
```

Каждый шаг процесса имеет ответственную команду.

## Архитектура решения

![Схема архитектуры](combo_arch.png)

Схема показывает связку ingestion → planning → composing.

## Надёжность

- Детерминированный аудит на каждую колоду
- Typed repair только с подтверждением пользователя
- Quality Passport фиксирует метрики

## Риски

- Внешние зависимости рендер-арены
- Качество исходного контента ограничивает результат

## Выводы

Все типы контента идут одним пайплайном: планер раскладывает их, компилятор сохраняет нативность.
"""


@pytest.fixture(scope="module")
def combo_md(tmp_path_factory):
    d = tmp_path_factory.mktemp("combo_md")
    (d / "combo_arch.png").write_bytes(_png_bytes())
    md = d / "combo.md"
    md.write_text(_MD, encoding="utf-8")
    return md


@pytest.fixture(scope="module")
def combo_docx(tmp_path_factory):
    d = tmp_path_factory.mktemp("combo_docx")
    path = d / "combo.docx"
    doc = Document()
    doc.add_heading("Комбо docx", level=1)
    doc.add_paragraph("Таблица и встроенная картинка в одном документе.")
    doc.add_heading("Показатели", level=2)
    table = doc.add_table(rows=3, cols=3)
    rows = (
        ("Метрика", "Q1", "Q2"),
        ("Выручка", "120.5", "135.2"),
        ("Маржа", "30%", "32%"),
    )
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            table.rows[r].cells[c].text = val
    doc.add_heading("Архитектура", level=2)
    png = d / "combo_pic.png"
    png.write_bytes(_png_bytes(color=(30, 60, 200)))
    doc.add_picture(str(png))
    doc.add_paragraph("Схема связки пайплайна.")
    doc.save(str(path))
    return path


@pytest.fixture(scope="module", params=STRATEGIES, ids=lambda s: s.value)
def md_run(request, combo_md, tmp_path_factory):
    """generate() по каждой стратегии на одном и том же combo-markdown."""
    strategy = request.param
    out_dir = tmp_path_factory.mktemp(f"combo_md_{strategy.value}")
    report = generate(
        TEMPLATE, combo_md, dict(BRIEF), out_dir / "gen", strategy=strategy
    )
    return {"report": report, "strategy": strategy}


@pytest.fixture(scope="module")
def docx_run(combo_docx, tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("combo_docx")
    return {
        "report": generate(
            TEMPLATE, combo_docx, dict(BRIEF), out_dir / "gen",
            strategy=Strategy.balanced,
        ),
        "strategy": Strategy.balanced,
    }


def _check_run(run, name):
    """Общие инварианты прогона: pptx валиден, аудит/рендер не падают."""
    report = run["report"]
    assert report["slides_out"] == BRIEF["target_slide_count"]
    assert report["strategy"] == run["strategy"].value

    pptx = Path(report["artifacts"]["pptx"])
    assert pptx.exists()
    prs = Presentation(str(pptx))  # падает на испорченном пакете
    assert len(prs.slides) == report["slides_out"]

    pdf = Path(report["artifacts"]["pdf"])
    assert pdf.exists() and pdf.stat().st_size > 0

    issues = audit_deck(pptx)  # новые правила внутри — не должны падать
    assert isinstance(issues, list)

    _SNAPSHOTS[name] = {
        "dropped": dict(report["compose_report"].get("dropped_units", {})),
        "sha256": hashlib.sha256(pptx.read_bytes()).hexdigest(),
        "audit_issues": len(issues),
    }


def test_md_plan_carries_all_structured_kinds(combo_md):
    """Предусловие: combo-markdown парсится в pack с table/chart/
    diagram/image, и план каждой стратегии реально несёт все 4 вида
    структурных юнитов — иначе комбо-прогон был бы вакуумным."""
    pack = parse_file(combo_md)
    assert len(pack.tables) >= 1
    assert len(pack.charts) >= 1
    assert len(pack.diagrams) >= 1
    assert len(pack.assets) >= 1
    want = {Kind.table, Kind.chart, Kind.diagram, Kind.image}
    for strategy in STRATEGIES:
        plan = plan_deck(pack, Brief.model_validate(BRIEF), strategy=strategy)
        kinds = {u.kind for sl in plan.slides for u in sl.content_units}
        missing = want - kinds
        assert not missing, f"{strategy.value}: план не несёт {missing}"


def test_md_pipeline_survives_each_strategy(md_run):
    _check_run(md_run, f"md:{md_run['strategy'].value}")


def test_docx_pipeline_survives(combo_docx, docx_run):
    # Предусловие: docx принёс таблицу и картинку как assets.
    pack = parse_file(combo_docx)
    assert len(pack.tables) >= 1
    assert len(pack.assets) >= 1
    _check_run(docx_run, "docx:balanced")


def test_combo_snapshot_invariants():
    """Итоговый срез по всем прогонам (fixtures уже отработали)."""
    assert set(_SNAPSHOTS) == {
        "md:faithful", "md:balanced", "md:visual", "docx:balanced",
    }

    # Регрессией был бы незнакомый kind в dropped_units — "нормальные"
    # честные границы фичей касаются только известных видов контента.
    for name, snap in _SNAPSHOTS.items():
        for kind in snap["dropped"]:
            assert kind in _KNOWN_DROP_KINDS, (
                f"{name}: неизвестный drop kind {kind!r}"
            )

    # OR-007 на богатом контенте: как минимум часть пар стратегий
    # побитово различается (faithful без agenda/recap; visual иной
    # ranking/кап текста). Полная идентичность всех трёх = регрессия.
    hashes = {s.value: _SNAPSHOTS[f"md:{s.value}"]["sha256"] for s in STRATEGIES}
    assert len(set(hashes.values())) > 1, (
        f"все три варианта побитово идентичны: {hashes}"
    )
