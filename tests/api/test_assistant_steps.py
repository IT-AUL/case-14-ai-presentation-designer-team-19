"""ADR-034 §2–§3: «Вот что я понял» и «Пара вопросов» через API.

- одна фраза → вопросы с ``content_short`` (и без варианта веба, когда
  веб-поиск не настроен);
- богатый материал с явной целью и аудиторией → пустой список;
- данные → блок ``data`` с превью и выводами, подтверждение вывода;
- правка блоков и тезисы пользователя → новая версия пака;
- ответы → бриф (``key_message``, ``call_to_action``) и план держится за
  главную мысль."""

from __future__ import annotations

import json
from pathlib import Path

from deckdna.api.app import app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "examples" / "stage2" / "purchases.csv"
A = "/api/v1"
client = TestClient(app)
ONE_LINER = "# Итоги года\n\nИтоги продаж за год для совета директоров.\n"
RICH = (
    "# Продукт\n\n## Проблема\n\n"
    + "".join(
        f"- Пункт о проблеме номер {i} с понятным пояснением для руководства.\n" for i in range(5)
    )
    + "\n## Решение\n\n"
    + "".join(
        f"- Пункт о решении номер {i} с понятным пояснением для руководства.\n" for i in range(5)
    )
    + "\n## Результаты\n\nПилот сократил время подготовки отчётов на 40 процентов.\n"
)


def _project(name: str, filename: str, data: bytes, mime: str, brief: dict | None = None):
    pid = client.post(f"{A}/projects", json={"name": name}).json()["id"]
    up = client.post(
        f"{A}/projects/{pid}/content-packs",
        files=[("files", (filename, data, mime))],
        data={"brief": json.dumps(brief or {"language": "ru"})},
    )
    assert up.status_code == 202, up.text
    return pid, up.json()["content_pack"]["id"]


def test_one_liner_asks_content_short_and_audience(monkeypatch):
    from deckdna.settings import settings

    monkeypatch.setattr(settings, "research_search_url", "")
    pid, cp = _project(
        "one",
        "idea.md",
        ONE_LINER.encode(),
        "text/markdown",
        {"language": "ru", "target_slide_count": 8},
    )
    understanding = client.get(f"{A}/content-packs/{cp}/understanding").json()
    assert understanding["summary"]["assistant_note"].startswith("Материала мало")
    assert 1 <= len(understanding["blocks"]) <= 2
    resp = client.get(f"{A}/projects/{pid}/questions").json()
    kinds = [q["kind"] for q in resp["questions"]]
    assert "content_short" in kinds and "audience" in kinds
    short = next(q for q in resp["questions"] if q["kind"] == "content_short")
    values = [o["value"] for o in short["options"]]
    assert values[0] == "shrink" and short["options"][0]["recommended"]
    assert "notes" in values and "expand_model" in values
    assert "expand_web" not in values  # SearXNG в тестах не настроен
    assert short["reason"] and resp["assistant_note"]


def test_rich_material_with_explicit_goal_has_no_questions():
    pid, _cp = _project(
        "rich",
        "rich.md",
        RICH.encode(),
        "text/markdown",
        {
            "language": "ru",
            "purpose": "Утвердить пилот у руководства",
            "audience": "руководство",
            "target_slide_count": 5,
        },
    )
    resp = client.get(f"{A}/projects/{pid}/questions").json()
    assert resp["questions"] == []
    assert resp["assistant_note"].startswith("Всё понятно")


def test_data_blocks_insights_and_confirmation():
    pid, cp = _project("data", "purchases.csv", CSV.read_bytes(), "text/csv")
    und = client.get(f"{A}/content-packs/{cp}/understanding").json()
    data = [b for b in und["blocks"] if b["kind"] == "data"]
    assert len(data) == 1 and data[0]["preview"]["chart_type"] == "line"
    assert data[0]["text"].startswith("Выручка выросла в 2")
    [card] = und["datasets"]
    assert card["task"] == "trend" and card["recommendations"][0]["chart_type"] == "line"
    assert "24 точки" in card["why"]
    assert card["insights"] and "Выручка" in und["summary"]["main_points"][0]
    insight = card["insights"][0]
    resp = client.patch(
        f"{A}/content-packs/{cp}/insights/{insight['id']}", json={"confirmed": True}
    )
    assert resp.status_code == 200 and resp.json()["confirmed"] is True
    again = client.get(f"{A}/content-packs/{cp}/understanding").json()
    confirmed = [i for i in again["datasets"][0]["insights"] if i["id"] == insight["id"]]
    assert confirmed[0]["confirmed"] is True
    assert client.patch(f"{A}/content-packs/{cp}/insights/nope", json={}).status_code == 404

    # данные без главной мысли и цели: помощник спросит главную мысль
    questions = client.get(f"{A}/projects/{pid}/questions").json()["questions"]
    key = next(q for q in questions if q["kind"] == "key_message")
    assert key["options"][0]["recommended"] and len(key["options"]) >= 2


def test_block_ops_make_a_new_pack_version():
    # Keep three distinct section cards; a numeric-only preview is deduplicated.
    text = RICH + "\n## Следующие шаги\n\nСогласовать пилот с командой.\n"
    pid, cp = _project("ops", "rich.md", text.encode(), "text/markdown")
    und = client.get(f"{A}/content-packs/{cp}/understanding").json()
    topics = [b for b in und["blocks"] if b["kind"] == "topic"]
    assert len(topics) >= 3
    facts = [b for b in und["blocks"] if b["kind"] in ("fact", "number")]
    resp = client.patch(
        f"{A}/content-packs/{cp}/blocks",
        json={
            "ops": [
                {"op": "remove", "block_id": topics[0]["id"]},
                {"op": "edit", "block_id": topics[1]["id"], "title": "Наше решение"},
                {"op": "move", "block_id": topics[2]["id"], "index": 0},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["content_pack_id"] != cp and out["filled_from"] == cp
    titles = [b["title"] for b in out["understanding"]["blocks"] if b["kind"] == "topic"]
    assert titles[0] == topics[2]["title"] and "Наше решение" in titles
    assert topics[0]["title"] not in titles
    # исходная версия цела — отмена на клиенте = вернуться к ней
    assert len(
        [
            b
            for b in client.get(f"{A}/content-packs/{cp}/understanding").json()["blocks"]
            if b["kind"] == "topic"
        ]
    ) == len(topics)
    # проект смотрит на новую версию
    assert client.get(f"{A}/projects/{pid}").json()["content_pack_id"] == out["content_pack_id"]
    bad = client.patch(
        f"{A}/content-packs/{cp}/blocks",
        json={"ops": [{"op": "explode", "block_id": facts[0]["id"]}]},
    )
    assert bad.status_code == 422


def test_notes_and_answers_reach_the_plan():
    pid, cp = _project(
        "answers",
        "idea.md",
        ONE_LINER.encode(),
        "text/markdown",
        {"language": "ru", "target_slide_count": 6},
    )
    answers = client.post(
        f"{A}/projects/{pid}/answers",
        json={
            "answers": {
                "audience": "совет директоров",
                "content_short": "notes",
                "key_message": "Мы выросли вдвое за год",
                "call_to_action": "утвердить бюджет",
            }
        },
    ).json()
    assert answers["follow_up"] == {"notes": True}
    brief = answers["brief"]
    assert brief["key_message"] == "Мы выросли вдвое за год"
    assert brief["call_to_action"] == "утвердить бюджет"
    assert brief["content_fill"] == "shrink" and brief["audience"] == "совет директоров"

    notes = client.post(
        f"{A}/content-packs/{cp}/notes",
        json={
            "items": [
                "Выручка выросла вдвое за год благодаря новым каналам",
                "Москва даёт почти половину продаж компании",
            ]
        },
    ).json()
    new_cp = notes["content_pack_id"]
    kinds = {b["title"] for b in notes["understanding"]["blocks"] if b["kind"] == "topic"}
    assert "Тезисы автора" in kinds

    plan = client.post(
        f"{A}/projects/{pid}/plans",
        json={
            "content_pack_id": new_cp,
            "brief": brief,
            "strategies": ["balanced"],
            "use_llm": False,
        },
    )
    assert plan.status_code == 200, plan.text
    deck = plan.json()["plans"][0]["deck_plan"]
    assert deck["brief"]["key_message"] == "Мы выросли вдвое за год"
    cover = deck["slides"][0]
    assert any(u.get("text") == "Мы выросли вдвое за год" for u in cover["content_units"])
    texts = " ".join(u.get("text") or "" for s in deck["slides"] for u in s["content_units"])
    assert "Москва даёт почти половину" in texts


def _data_plan(brief_extra: dict | None = None):
    pid, cp = _project("plan", "purchases.csv", CSV.read_bytes(), "text/csv")
    brief = {
        "purpose": "Итоги продаж за два года",
        "audience": "руководство",
        "language": "ru",
        "target_slide_count": 7,
        **(brief_extra or {}),
    }
    resp = client.post(
        f"{A}/projects/{pid}/plans",
        json={
            "content_pack_id": cp,
            "brief": brief,
            "strategies": ["balanced"],
            "use_llm": False,
        },
    )
    assert resp.status_code == 200, resp.text
    return pid, resp.json()


def test_plan_cards_carry_chart_reasons_and_alternatives():
    _pid, plan_set = _data_plan()
    slides = plan_set["plans"][0]["deck_plan"]["slides"]
    data = [s for s in slides if s["purpose"] == "data"]
    trend = next(s for s in data if s.get("chart", {}).get("type") == "line")
    card = trend["chart"]
    assert card["type"] == "line" and card["reasons"] and card["why"]
    assert {a["type"] for a in card["alternatives"]} == {"bar", "table"}
    assert trend["insight_ids"]

    # другой график из альтернатив
    resp = client.patch(
        f"{A}/plans/{plan_set['plan_id']}/slides/{trend['id']}", json={"chart_type": "bar"}
    )
    assert resp.status_code == 200, resp.text
    slide = next(
        s for s in resp.json()["plans"][0]["deck_plan"]["slides"] if s["id"] == trend["id"]
    )
    unit = next(u for u in slide["content_units"] if u["kind"] == "chart")
    assert unit["chart_spec"]["chart_type"] == "bar" and slide["chart"]["type"] == "bar"
    # обоснование выбора — в карточке графика, а не в заметках докладчика
    assert any("столбц" in r or "24 точки" in r for r in slide["chart"]["reasons"])

    # круговая для ряда по месяцам невозможна — 422 с причиной
    bad = client.patch(
        f"{A}/plans/{plan_set['plan_id']}/slides/{trend['id']}", json={"chart_type": "pie"}
    )
    assert bad.status_code == 422
    assert "доли" in bad.json()["error"]["message"]
    assert bad.json()["error"]["details"]["rejected"]

    # таблица — тоже альтернатива
    table = client.patch(
        f"{A}/plans/{plan_set['plan_id']}/slides/{trend['id']}", json={"chart_type": "table"}
    ).json()
    slide = next(s for s in table["plans"][0]["deck_plan"]["slides"] if s["id"] == trend["id"])
    assert any(u["kind"] == "table" and u["table_ref"] for u in slide["content_units"])


def test_quick_actions_keep_the_plan_valid():
    _pid, plan_set = _data_plan()
    plan_id = plan_set["plan_id"]
    slides = plan_set["plans"][0]["deck_plan"]["slides"]
    data = [s for s in slides if s["purpose"] == "data"]

    removed = client.post(
        f"{A}/plans/{plan_id}/slides/{data[-1]['id']}/actions", json={"action": "remove"}
    ).json()["plans"][0]["deck_plan"]
    assert len(removed["slides"]) == len(slides) - 1
    assert removed["brief"]["target_slide_count"] == len(removed["slides"])
    assert [s["index"] for s in removed["slides"]] == list(range(len(removed["slides"])))

    key = next(
        s
        for s in removed["slides"]
        if s["purpose"] not in {"title", "cta"}
        and sum(u["kind"] in {"bullet", "paragraph", "quote"} for u in s["content_units"]) >= 2
    )
    split = client.post(
        f"{A}/plans/{plan_id}/slides/{key['id']}/actions", json={"action": "split"}
    ).json()["plans"][0]["deck_plan"]
    assert len(split["slides"]) == len(removed["slides"]) + 1
    assert split["slides"][key["index"] + 1]["title_intent"].endswith("(продолжение)")
    assert len({s["id"] for s in split["slides"]}) == len(split["slides"])

    merged = client.post(
        f"{A}/plans/{plan_id}/slides/{key['id']}/actions", json={"action": "merge_next"}
    ).json()["plans"][0]["deck_plan"]
    assert len(merged["slides"]) == len(removed["slides"])

    diagram = client.post(
        f"{A}/plans/{plan_id}/slides/{key['id']}/actions", json={"action": "to_diagram"}
    )
    assert diagram.status_code == 200, diagram.text
    out = diagram.json()
    slide = next(s for s in out["plans"][0]["deck_plan"]["slides"] if s["id"] == key["id"])
    ref = next(u["diagram_ref"] for u in slide["content_units"] if u["kind"] == "diagram")
    pack = client.get(f"{A}/content-packs/{out['content_pack_id']}").json()
    assert any(d["id"] == ref for d in pack["diagrams"])

    cover = slides[0]["id"]
    assert (
        client.post(
            f"{A}/plans/{plan_id}/slides/{cover}/actions", json={"action": "remove"}
        ).status_code
        == 422
    )


def test_key_message_marks_slides_that_do_not_support_it():
    _pid, plan_set = _data_plan({"key_message": "Выручка выросла вдвое"})
    assert plan_set["key_message"] == "Выручка выросла вдвое"
    slides = plan_set["plans"][0]["deck_plan"]["slides"]
    marks = {s["title_intent"]: s["supports_key_message"] for s in slides}
    assert all(isinstance(v, bool) for v in marks.values())
    trend = next(s for s in slides if (s.get("chart") or {}).get("type") == "line")
    assert trend["supports_key_message"] is True  # «Выручка: рост…»
    assert any(v is False for v in marks.values())  # разрезы не про рост
