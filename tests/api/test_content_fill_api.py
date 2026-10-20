"""ADR-030 через API: POST /plans оценивает достаточность контента,
спрашивает пользователя (content_fill=ask), сокращает колоду или
добирает контент; добранный пак получает свой id. Модель — MockProvider
(build_gateway подменён), сети нет."""

from __future__ import annotations

import json
from pathlib import Path

import deckdna.api.app as app_module
from deckdna.api.app import app
from deckdna.providers.mock import MockProvider
from deckdna.research.expand import EXPAND_PROMPT
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "pptx" / "vk_tech_template.pptx"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
A = "/api/v1"
ONE_LINER = "# Идея\n\nСервис собирает презентацию по шаблону компании за несколько минут.\n"

client = TestClient(app)


def _brief(target: int = 8, fill: str | None = None) -> dict:
    brief = {
        "purpose": "Цифровой дизайнер презентаций",
        "audience": "руководство",
        "language": "ru",
        "target_slide_count": target,
    }
    if fill:
        brief["content_fill"] = fill
    return brief


def _project_with_one_liner() -> tuple[str, str]:
    pid = client.post(f"{A}/projects", json={"name": "fill"}).json()["id"]
    pack = client.post(
        f"{A}/projects/{pid}/content-packs",
        files=[("files", ("idea.md", ONE_LINER.encode(), "text/markdown"))],
        data={"brief": json.dumps({"language": "ru"})},
    )
    assert pack.status_code == 202, pack.text
    return pid, pack.json()["content_pack"]["id"]


def _plans(pid: str, cp: str, brief: dict, **extra) -> dict:
    resp = client.post(
        f"{A}/projects/{pid}/plans",
        json={"content_pack_id": cp, "brief": brief, "strategies": ["balanced"], **extra},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_ask_returns_the_sufficiency_report_instead_of_plans():
    pid, cp = _project_with_one_liner()
    plan_set = _plans(pid, cp, _brief(10, "ask"), use_llm=False)
    fill = plan_set["content_fill"]
    assert plan_set["plans"] == []
    assert fill["needs_decision"] is True
    assert fill["before"]["status"] == "short"
    assert fill["before"]["capacity"] < 10 and fill["before"]["missing_slides"] > 0


def test_shrink_plans_only_what_the_content_fills():
    pid, cp = _project_with_one_liner()
    plan_set = _plans(pid, cp, _brief(10, "shrink"), use_llm=False)
    plan = plan_set["plans"][0]["deck_plan"]
    assert plan_set["content_fill"]["resolved"] == "shrink"
    assert len(plan["slides"]) == plan["brief"]["target_slide_count"] < 10
    assert not any(s["title_intent"].startswith("Раздел") for s in plan["slides"])
    assert plan_set["content_pack_id"] == cp  # пак не менялся


def test_expand_without_a_model_is_disclosed_as_shrink():
    pid, cp = _project_with_one_liner()
    plan_set = _plans(pid, cp, _brief(10), use_llm=False)
    assert plan_set["content_fill"]["mode"] == "expand"
    assert plan_set["content_fill"]["resolved"] == "no_model"
    plan = plan_set["plans"][0]["deck_plan"]
    # контента на одну фразу: колода короче, без заглушек
    assert len(plan["slides"]) == plan["brief"]["target_slide_count"] < 10


def test_expand_with_a_model_fills_a_new_pack(monkeypatch):
    def expand(payload):
        return {"sections": [
            {"heading": f"Раздел модели {i}",
             "bullets": [f"Содержательный пункт {k} раздела {i} для руководства компании"
                         for k in range(4)]}
            for i in range(payload["sections_needed"])
        ]}

    gateway = MockProvider(fixtures={EXPAND_PROMPT: expand})
    monkeypatch.setattr(app_module, "build_gateway", lambda config=None: gateway)
    monkeypatch.setattr(app_module.pipeline, "planning_pipeline_version", lambda: "v3")
    pid, cp = _project_with_one_liner()
    plan_set = _plans(pid, cp, _brief(8), use_llm=True)

    fill = plan_set["content_fill"]
    assert fill["resolved"] == "expand" and fill["facts_by_origin"]["model"] > 0
    plan = plan_set["plans"][0]["deck_plan"]
    assert len(plan["slides"]) == 8
    # добранный пак — новая запись; исходный пак пользователя не тронут
    filled = plan_set["content_pack_id"]
    assert filled != cp
    assert client.get(f"{A}/content-packs/{cp}").json().get("external_sources", []) == []
    sources = client.get(f"{A}/content-packs/{filled}").json()["external_sources"]
    assert [s["origin"] for s in sources] == ["model"]
    origins = {u.get("origin") for s in plan["slides"] for u in s["content_units"]}
    assert "model" in origins
    # тот же планировщик, что у генерации (v3 → v2-оркестрация Stage 1+2)
    assert plan["provenance"]["prompt_version"].startswith("deck_structure/")


def test_plan_on_a_filled_pack_accepts_the_original_pack_id(monkeypatch, wait_generation):
    """План из /plans построен на добранном паке. Фронт, не знающий про
    добор, присылает исходный content_pack_id — генерация принимается и
    идёт по добранному паку (иначе экран плана сломался бы)."""
    gateway = MockProvider(fixtures={EXPAND_PROMPT: lambda p: {"sections": [
        {"heading": f"Раздел {i}", "bullets": [
            f"Пункт {k} раздела {i} с достаточно длинным пояснением" for k in range(4)]}
        for i in range(p["sections_needed"])
    ]}})
    monkeypatch.setattr(app_module, "build_gateway", lambda config=None: gateway)
    pid, cp = _project_with_one_liner()
    tpl = client.post(
        f"{A}/projects/{pid}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    )
    plan_set = _plans(pid, cp, _brief(6), use_llm=True)
    original_id = client.post(f"{A}/projects/{pid}/generations", json={
        "template_id": tpl.json()["id"],
        "content_pack_id": cp,
        "brief": _brief(6),
        "variants": [{"strategy": "balanced"}],
        "deck_plan_id": plan_set["plan_id"],
        "use_llm": False,
    })
    assert original_id.status_code == 202, original_id.text
    run = wait_generation(client, original_id.json()["generation_id"])
    assert run["content_pack_id"] == plan_set["content_pack_id"] != cp
    assert run["state"] == "completed", run


def _wait_job(job_id: str, timeout_s: float = 30.0) -> dict:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = client.get(f"{A}/jobs/{job_id}").json()
        if job["state"] in ("completed", "failed", "canceled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def _events(job_id: str, after: int = 0) -> list[tuple[str, dict]]:
    text = client.get(f"{A}/jobs/{job_id}/events", params={"after": after}).text
    events, kind = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            kind = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and kind:
            events.append((kind, json.loads(line.split(":", 1)[1])))
    return events


def test_async_plan_streams_research_and_slides(monkeypatch):
    def expand(payload):
        return {"sections": [
            {"heading": f"Раздел модели {i}",
             "bullets": [f"Содержательный пункт {k} раздела {i} для руководства компании"
                         for k in range(4)]}
            for i in range(payload["sections_needed"])
        ]}

    gateway = MockProvider(fixtures={EXPAND_PROMPT: expand})
    monkeypatch.setattr(app_module, "build_gateway", lambda config=None: gateway)
    pid, cp = _project_with_one_liner()
    resp = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": cp, "brief": _brief(6), "strategies": ["balanced"],
        "use_llm": True, "run_async": True,
    })
    assert resp.status_code == 202, resp.text
    accepted = resp.json()
    job = _wait_job(accepted["job_id"])
    assert job["state"] == "completed" and job["kind"] == "planning"
    assert job["result_ids"]["plan_id"] == accepted["plan_id"]

    events = _events(accepted["job_id"])
    kinds = [k for k, _ in events]
    assert kinds[0] == "job.state_changed" and kinds[-1] == "job.completed"
    assert "research.started" in kinds and "research.model" in kinds
    assert kinds.index("research.finished") < kinds.index("plan.slide")
    slides = [d for k, d in events if k == "plan.slide"]
    assert [d["index"] for d in slides] == list(range(6))
    assert [d["sequence"] for _, d in events] == list(range(1, len(events) + 1))
    # продолжение с after= отдаёт только новые события
    assert _events(accepted["job_id"], after=len(events) - 1) == [events[-1]]

    plan_set = client.get(f"{A}/plans/{accepted['plan_id']}").json()
    assert plan_set["content_pack_id"] == job["result_ids"]["content_pack_id"] != cp
    assert len(plan_set["plans"][0]["deck_plan"]["slides"]) == 6


def test_async_plan_failure_is_a_typed_job_error():
    pid, cp = _project_with_one_liner()
    # пустой контент без модели: планировать не из чего
    empty = client.post(
        f"{A}/projects/{pid}/content-packs",
        files=[("files", ("empty.md", b"# \n", "text/markdown"))],
    )
    assert empty.status_code == 202, empty.text
    resp = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": empty.json()["content_pack"]["id"], "brief": _brief(5, "shrink"),
        "strategies": ["balanced"], "use_llm": False, "run_async": True,
    })
    assert resp.status_code == 202
    job = _wait_job(resp.json()["job_id"])
    assert job["state"] == "failed"
    assert job["error"]["code"] == "invalid_input"
    assert client.get(f"{A}/plans/{resp.json()['plan_id']}").status_code == 404


RICH = (
    "# Продукт\n\n## Проблема\n\n"
    + "".join(f"- Пункт о проблеме номер {i} с понятным пояснением для руководства.\n"
              for i in range(5))
    + "\n## Решение\n\n"
    + "".join(f"- Пункт о решении номер {i} с понятным пояснением для руководства.\n"
              for i in range(5))
)


def test_user_added_slide_gets_a_body_before_layout(wait_generation):
    """Фронт добавляет слайд с evidence_ids: [] (editPlan.addSlide) — API
    его принимает, а ключевая мысль становится телом слайда."""
    pid = client.post(f"{A}/projects", json={"name": "user-slide"}).json()["id"]
    tpl = client.post(
        f"{A}/projects/{pid}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    ).json()["id"]
    cp = client.post(
        f"{A}/projects/{pid}/content-packs",
        files=[("files", ("rich.md", RICH.encode(), "text/markdown"))],
    ).json()["content_pack"]["id"]
    plan_set = _plans(pid, cp, _brief(4, "shrink"), use_llm=False)
    plan = plan_set["plans"][0]["deck_plan"]
    plan["slides"].insert(-1, {
        "id": "slide-user-9", "index": 0, "purpose": "custom",
        "title_intent": "Планы на квартал",
        "key_message": "Запустить пилот в двух отделах до декабря",
        "evidence_ids": [], "content_units": [],
        "density_budget": {"level": "medium"}, "desired_visual": "none",
    })
    for i, slide in enumerate(plan["slides"]):
        slide["index"] = i
    gen = client.post(f"{A}/projects/{pid}/generations", json={
        "template_id": tpl, "content_pack_id": plan_set["content_pack_id"],
        "brief": _brief(4), "variants": [{"strategy": "balanced"}],
        "deck_plan": plan, "use_llm": False,
    })
    assert gen.status_code == 202, gen.text
    run = wait_generation(client, gen.json()["generation_id"])
    assert run["state"] == "completed", run
    slide = next(s for s in run["deck_plan"]["slides"] if s["id"] == "slide-user-9")
    bodies = [u for u in slide["content_units"] if u["kind"] != "title"]
    assert [(u["text"], u.get("locked")) for u in bodies] == [
        ("Запустить пилот в двух отделах до декабря", True)
    ]
    assert run["content_fill"]["user_slides"]["from_key_message"] == ["slide-user-9"]


def test_slide_research_endpoint_adds_sourced_figures(monkeypatch):
    import deckdna.research.web as web
    from deckdna.research.config import ResearchConfig, WebResearchConfig
    from deckdna.research.web import EXTRACT_PROMPT, QUERIES_PROMPT, Page, SearchHit

    page_text = ("Ежегодный рост рынка презентаций составляет 12% по оценке аналитиков. "
                 "Компании ценят единый фирменный стиль документов. ") * 4

    class FakeSearx:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, query, limit, language):
            return [SearchHit(url="https://stats.example.org/growth", title="Рост")]

    async def fake_fetch(url, cfg, transport=None):
        return Page(url=url, title="Рост рынка", text=page_text)

    monkeypatch.setattr(web, "SearxngSearch", FakeSearx)
    monkeypatch.setattr(web, "fetch_page", fake_fetch)
    monkeypatch.setattr(
        app_module, "load_research_config",
        lambda: ResearchConfig(budget_s=20, web=WebResearchConfig(search_url="http://searxng")),
    )
    gateway = MockProvider(fixtures={
        QUERIES_PROMPT: {"queries": ["рост рынка презентаций"]},
        EXTRACT_PROMPT: {"facts": [{
            "text": "Рынок презентаций растёт на 12% в год",
            "quote": "Ежегодный рост рынка презентаций составляет 12%",
            "topic": "Рост",
        }]},
    })
    monkeypatch.setattr(app_module, "build_gateway", lambda config=None: gateway)

    pid = client.post(f"{A}/projects", json={"name": "slide-research"}).json()["id"]
    cp = client.post(
        f"{A}/projects/{pid}/content-packs",
        files=[("files", ("rich.md", RICH.encode(), "text/markdown"))],
    ).json()["content_pack"]["id"]
    plan_set = _plans(pid, cp, _brief(4, "shrink"), use_llm=False)
    plan = plan_set["plans"][0]["deck_plan"]
    target = plan["slides"][1]["id"]

    resp = client.post(
        f"{A}/plans/{plan_set['plan_id']}/slides/{target}/research",
        json={"focus": "numbers", "use_llm": True},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["added_units"] == 1
    added = [u for u in result["slide"]["content_units"] if u.get("origin") == "web"]
    assert [u["text"] for u in added] == ["Рынок презентаций растёт на 12% в год"]
    new_set = result["plan_set"]
    assert new_set["content_pack_id"] != cp
    assert new_set["content_fill"]["slide_research"][0]["slide_id"] == target
    # сохранённый набор обновлён — генерация пойдёт по нему и новому паку
    stored = client.get(f"{A}/plans/{plan_set['plan_id']}").json()
    assert stored["content_pack_id"] == new_set["content_pack_id"]
    titles = [s["title_intent"] for s in stored["plans"][0]["deck_plan"]["slides"]]
    assert "Источники" in titles

    missing = client.post(
        f"{A}/plans/{plan_set['plan_id']}/slides/nope/research", json={"use_llm": True}
    )
    assert missing.status_code == 404
