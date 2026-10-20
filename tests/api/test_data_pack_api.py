"""ADR-033 через API: CSV загружается как данные, план строится от
выводов, добор контента ADR-030 факты не дописывает."""

from __future__ import annotations

import json
from pathlib import Path

from deckdna.api.app import app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "examples" / "stage2" / "purchases.csv"
A = "/api/v1"
client = TestClient(app)


def test_csv_upload_gives_datasets_and_a_plan_from_insights():
    pid = client.post(f"{A}/projects", json={"name": "data"}).json()["id"]
    up = client.post(
        f"{A}/projects/{pid}/content-packs",
        files=[("files", ("purchases.csv", CSV.read_bytes(), "text/csv"))],
        data={"brief": json.dumps({"language": "ru"})},
    )
    assert up.status_code == 202, up.text
    pack = up.json()["content_pack"]
    [dataset] = pack["datasets"]
    assert dataset["file_name"] == "purchases.csv" and dataset["row_count"] > 8000
    assert any(c.get("chart_type") == "line" for c in pack["charts"])
    assert pack["insights"]

    resp = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": pack["id"], "strategies": ["balanced"], "use_llm": False,
        "brief": {"purpose": "Итоги продаж за два года", "audience": "руководство",
                  "language": "ru", "target_slide_count": 7, "content_fill": "expand"},
    })
    assert resp.status_code == 200, resp.text
    plan_set = resp.json()
    assert plan_set["content_fill"]["resolved"] == "data"
    assert plan_set["content_pack_id"] == pack["id"]  # пак не добирался
    slides = plan_set["plans"][0]["deck_plan"]["slides"]
    assert slides[1]["title_intent"] == "Ключевые выводы"
    charts = [u for s in slides for u in s["content_units"] if u["kind"] == "chart"]
    assert charts[0]["chart_spec"]["chart_type"] == "line"
    # заметки докладчика — текст выступления, не технический разбор
    assert all("Данные:" not in s["speaker_note"] for s in slides if s["purpose"] == "data")
    assert all(s["speaker_note"] for s in slides if s["purpose"] == "data")


def test_regions_dataset_plan_is_a_story_not_three_slides():
    """BE3: один срез данных — это «титул + слайд + спасибо»; у регионов
    есть второе честное представление (доли от итога) и сводка выводов."""
    pid = client.post(f"{A}/projects", json={"name": "regions"}).json()["id"]
    xlsx = ROOT / "examples" / "stage2" / "regions.xlsx"
    up = client.post(
        f"{A}/projects/{pid}/content-packs",
        files=[("files", (xlsx.name, xlsx.read_bytes(),
                          "application/vnd.openxmlformats-officedocument."
                          "spreadsheetml.sheet"))],
        data={"brief": json.dumps({"language": "ru"})},
    )
    assert up.status_code == 202, up.text
    pack = up.json()["content_pack"]

    resp = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": pack["id"], "strategies": ["balanced"], "use_llm": False,
        "brief": {"purpose": "итоги квартала по регионам", "audience": "",
                  "language": "ru", "target_slide_count": 8, "content_fill": "expand"},
    })
    assert resp.status_code == 200, resp.text
    slides = resp.json()["plans"][0]["deck_plan"]["slides"]
    data = [s for s in slides if s["purpose"] == "data"]
    assert len(slides) >= 5
    assert len(data) >= 2
    assert any(s["title_intent"] == "Ключевые выводы" for s in slides)
    chart_types = [
        u["chart_spec"]["chart_type"] for s in data for u in s["content_units"]
        if u["kind"] == "chart" and u.get("chart_spec")
    ]
    assert "bar_horizontal" in chart_types
    # при хвосте «прочие» > 25% доли честно уходят в горизонтальный бар
    assert any(t in ("pie", "doughnut", "bar_stacked_100", "bar_horizontal")
               for t in chart_types)
