"""ADR-034 §0: формы ADR-030, которые фронт уже читает
(``docs/API contract`` §8a, F1–F7). По тесту на каждую
строку таблицы §0. Модель — MockProvider, поиск и загрузка страниц
подменены, сети нет."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import deckdna.api.app as app_module
import deckdna.research.fill as fill_module
import deckdna.research.web as web
from deckdna.api.app import app
from deckdna.providers.mock import MockProvider
from deckdna.research.config import ResearchConfig, WebResearchConfig
from deckdna.research.expand import EXPAND_PROMPT
from deckdna.research.web import EXTRACT_PROMPT, QUERIES_PROMPT, Page, SearchHit, web_source_id
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "pptx" / "vk_tech_template.pptx"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
A = "/api/v1"
ONE_LINER = "# Идея\n\nСервис собирает презентацию по шаблону компании за несколько минут.\n"
URLS = ["https://stats.example.org/market", "https://news.example.com/brand"]
PAGES = {
    URLS[0]: ("Рынок", "Рынок корпоративных презентаций растёт на 12% в год. " * 6),
    URLS[1]: ("Бренд", "Девять из десяти компаний требуют единый фирменный стиль. " * 6),
}
FACTS = {
    URLS[0]: [{"text": "Рынок корпоративных презентаций растёт на 12% в год",
               "quote": "Рынок корпоративных презентаций растёт на 12% в год",
               "topic": "Рынок"},
              {"text": "Рост рынка презентаций держится на уровне 12% в год",
               "quote": "Рынок корпоративных презентаций растёт на 12% в год",
               "topic": "Рынок"}],
    URLS[1]: [{"text": "Девять из десяти компаний требуют единый фирменный стиль",
               "quote": "Девять из десяти компаний требуют единый фирменный стиль",
               "topic": "Фирменный стиль"},
              {"text": "Единый фирменный стиль нужен девяти компаниям из десяти",
               "quote": "Девять из десяти компаний требуют единый фирменный стиль",
               "topic": "Фирменный стиль"}],
}

client = TestClient(app)


def _brief(target: int = 6, fill: str = "expand") -> dict:
    return {
        "purpose": "Цифровой дизайнер презентаций",
        "audience": "руководство",
        "language": "ru",
        "target_slide_count": target,
        "content_fill": fill,
    }


def _project() -> tuple[str, str]:
    pid = client.post(f"{A}/projects", json={"name": "adr034-0"}).json()["id"]
    pack = client.post(
        f"{A}/projects/{pid}/content-packs",
        files=[("files", ("idea.md", ONE_LINER.encode(), "text/markdown"))],
        data={"brief": json.dumps({"language": "ru"})},
    )
    assert pack.status_code == 202, pack.text
    return pid, pack.json()["content_pack"]["id"]


def _web(monkeypatch, *, delay_s: float = 0.0):
    """Веб-добор на моках: два источника по два факта."""

    class FakeSearx:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, query, limit, language):
            return [SearchHit(url=u, title=PAGES[u][0]) for u in URLS]

    async def fake_fetch(url, cfg, transport=None):
        title, text = PAGES[url]
        return Page(url=url, title=title, text=text)

    def extract(payload):
        if delay_s:
            time.sleep(delay_s)
        page = json.dumps(payload, ensure_ascii=False)
        url = next(u for u in URLS if PAGES[u][1][:30] in page)
        return {"facts": FACTS[url]}

    monkeypatch.setattr(web, "SearxngSearch", FakeSearx)
    monkeypatch.setattr(web, "fetch_page", fake_fetch)
    monkeypatch.setattr(
        fill_module, "load_research_config",
        lambda cfg=None: ResearchConfig(
            budget_s=30, web=WebResearchConfig(search_url="http://searxng")
        ),
    )

    def expand(payload):
        return {"sections": [
            {"heading": f"Раздел модели {i}",
             "bullets": [f"Содержательный пункт {k} раздела {i} для руководства компании"
                         for k in range(4)]}
            for i in range(payload["sections_needed"])
        ]}

    gateway = MockProvider(fixtures={
        QUERIES_PROMPT: {"queries": ["рынок презентаций", "фирменный стиль компаний"]},
        EXTRACT_PROMPT: extract,
        EXPAND_PROMPT: expand,
    })
    monkeypatch.setattr(app_module, "build_gateway", lambda config=None: gateway)
    return gateway


def _wait_job(job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"{A}/jobs/{job_id}").json()
        if job["state"] in ("completed", "failed", "canceled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def _events(job_id: str) -> list[dict]:
    """События job'а (SSE ``GET /jobs/{id}/events``)."""
    text = client.get(f"{A}/jobs/{job_id}/events").text
    return [
        json.loads(line.split(":", 1)[1])
        for line in text.splitlines() if line.startswith("data:")
    ]


def _sync_plan(pid: str, cp: str, **extra) -> dict:
    resp = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": cp, "brief": _brief(), "strategies": ["balanced"],
        "use_llm": True, **extra,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- строка 1: respond_async ------------------------------------------------

def test_respond_async_is_accepted_like_run_async(monkeypatch):
    _web(monkeypatch)
    pid, cp = _project()
    resp = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": cp, "brief": _brief(), "strategies": ["balanced"],
        "use_llm": True, "respond_async": True,
    })
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert set(body) >= {"plan_id", "job_id"}
    assert _wait_job(body["job_id"])["state"] == "completed"


# --- строка 2: sufficiency --------------------------------------------------

def test_plan_set_carries_sufficiency_in_the_frontend_shape():
    pid, cp = _project()
    resp = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": cp, "brief": _brief(10, "shrink"), "strategies": ["balanced"],
        "use_llm": False,
    })
    plan_set = resp.json()
    suff = plan_set["sufficiency"]
    assert suff["status"] in ("short", "ok", "excess")
    assert suff["content_slides"] == plan_set["content_fill"]["after"]["capacity"]
    assert suff["target_slides"] == plan_set["content_fill"]["target_slides"]

    ask = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": cp, "brief": _brief(10, "ask"), "strategies": ["balanced"],
        "use_llm": False,
    }).json()
    assert ask["sufficiency"] == {"status": "short", "content_slides": ask["sufficiency"][
        "content_slides"], "target_slides": 10}
    assert ask["sufficiency"]["content_slides"] < 10


# --- строки 3–4: research и ContentUnit.source_id ---------------------------

def test_research_block_and_unit_source_ids(monkeypatch):
    _web(monkeypatch)
    pid, cp = _project()
    plan_set = _sync_plan(pid, cp)
    research = plan_set["research"]
    assert research["queries"] == ["рынок презентаций", "фирменный стиль компаний"]
    assert research["duration_s"] >= 0
    ids = {s["id"] for s in research["sources"]}
    assert ids == {web_source_id(u) for u in URLS}
    for source in research["sources"]:
        assert set(source) >= {"id", "title", "url", "accessed_at", "facts_count"}
        assert source["facts_count"] == 2
    units = [u for s in plan_set["plans"][0]["deck_plan"]["slides"] for u in s["content_units"]]
    web_units = [u for u in units if u.get("origin") == "web"]
    assert web_units, "веб-факты должны попасть в план"
    assert all(u["source_id"] in ids for u in web_units)
    # origin остаётся строкой (фронт читает и строку, и объект)
    assert all(isinstance(u["origin"], str) for u in web_units)


def test_source_ids_are_stable_between_plan_builds(monkeypatch):
    _web(monkeypatch)
    pid, cp = _project()
    first = {s["id"] for s in _sync_plan(pid, cp)["research"]["sources"]}
    second = {s["id"] for s in _sync_plan(pid, cp)["research"]["sources"]}
    assert first == second


# --- строка 5: события в форме фронта ---------------------------------------

def test_async_plan_events_in_the_frontend_shape(monkeypatch):
    _web(monkeypatch)
    pid, cp = _project()
    accepted = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": cp, "brief": _brief(), "strategies": ["balanced"],
        "use_llm": True, "respond_async": True,
    }).json()
    assert _wait_job(accepted["job_id"])["state"] == "completed"
    events = _events(accepted["job_id"])
    by_kind: dict[str, list[dict]] = {}
    for e in events:
        by_kind.setdefault(e["event"], []).append(e)

    assert [e["stage"] for e in by_kind["stage.started"]][:2] == ["research", "plan"]
    assert [e["query"] for e in by_kind["research.query"]] == [
        "рынок презентаций", "фирменный стиль компаний"
    ]
    for e in by_kind["research.source"]:
        assert set(e["source"]) >= {"id", "title", "url", "site", "accessed_at", "facts_count"}
        assert e["source"]["facts_count"] == 2
        assert e["url"] == e["source"]["url"]  # старая форма осталась
    slides = by_kind["plan.slide"]
    assert slides and all(e["slide"]["id"] and e["slide"]["title_intent"] for e in slides)
    assert [e["slide"]["index"] for e in slides] == [e["index"] for e in slides]


# --- строка 6: пути проекта и отмена ----------------------------------------

def test_project_scoped_plan_path():
    pid, cp = _project()
    plan_set = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": cp, "brief": _brief(4, "shrink"), "strategies": ["balanced"],
        "use_llm": False,
    }).json()
    got = client.get(f"{A}/projects/{pid}/plans/{plan_set['plan_id']}")
    assert got.status_code == 200
    assert got.json() == client.get(f"{A}/plans/{plan_set['plan_id']}").json()
    other = client.post(f"{A}/projects", json={"name": "other"}).json()["id"]
    assert client.get(f"{A}/projects/{other}/plans/{plan_set['plan_id']}").status_code == 404
    # готовый план не отменить
    done = client.post(f"{A}/projects/{pid}/plans/{plan_set['plan_id']}/cancel")
    assert done.status_code == 409


def test_cancel_a_running_plan(monkeypatch):
    _web(monkeypatch, delay_s=0.6)
    pid, cp = _project()
    accepted = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": cp, "brief": _brief(), "strategies": ["balanced"],
        "use_llm": True, "respond_async": True,
    }).json()
    deadline = time.monotonic() + 10
    while client.get(f"{A}/jobs/{accepted['job_id']}").json()["state"] == "queued":
        assert time.monotonic() < deadline
        time.sleep(0.02)
    resp = client.post(f"{A}/projects/{pid}/plans/{accepted['plan_id']}/cancel")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "canceled"
    # повтор — тот же ответ, без ошибки
    assert client.post(
        f"{A}/projects/{pid}/plans/{accepted['plan_id']}/cancel"
    ).json()["state"] == "canceled"
    # фоновая работа дорабатывает шаг и не сохраняет план
    time.sleep(2.0)
    assert _wait_job(accepted["job_id"])["state"] == "canceled"
    assert client.get(f"{A}/projects/{pid}/plans/{accepted['plan_id']}").status_code == 404
    states = [
        e.get("state") for e in _events(accepted["job_id"]) if e["event"] == "job.state_changed"
    ]
    assert "canceled" in states and "completed" not in states
    assert threading.active_count() >= 1


# --- строка 7: exclude_source_ids -------------------------------------------

def test_exclude_source_ids_drops_a_source(monkeypatch):
    _web(monkeypatch)
    pid, cp = _project()
    first = _sync_plan(pid, cp)
    dropped = web_source_id(URLS[1])
    assert dropped in {s["id"] for s in first["research"]["sources"]}

    # фронт повторяет POST /plans с исходным паком
    again = _sync_plan(pid, cp, exclude_source_ids=[dropped])
    assert {s["id"] for s in again["research"]["sources"]} == {web_source_id(URLS[0])}
    units = [u for s in again["plans"][0]["deck_plan"]["slides"] for u in s["content_units"]]
    assert all(u.get("source_id") != dropped for u in units)
    texts = " ".join(u.get("text") or "" for u in units)
    assert "фирменный стиль" not in texts.casefold()
    pack = client.get(f"{A}/content-packs/{again['content_pack_id']}").json()
    assert dropped not in {s["id"] for s in pack["external_sources"]}

    # и с уже добранным паком: факты источника убираются из него
    from_filled = _sync_plan(pid, first["content_pack_id"], exclude_source_ids=[dropped])
    pack = client.get(f"{A}/content-packs/{from_filled['content_pack_id']}").json()
    assert dropped not in {s["id"] for s in pack["external_sources"]}


# --- строка 8: паспорт ------------------------------------------------------

def test_passport_content_origin_and_research(monkeypatch, wait_generation):
    _web(monkeypatch)
    pid, cp = _project()
    tpl = client.post(
        f"{A}/projects/{pid}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    ).json()["id"]
    plan_set = _sync_plan(pid, cp)
    gen = client.post(f"{A}/projects/{pid}/generations", json={
        "template_id": tpl, "content_pack_id": plan_set["content_pack_id"],
        "brief": _brief(), "variants": [{"strategy": "balanced"}],
        "deck_plan_id": plan_set["plan_id"], "use_llm": False,
    })
    assert gen.status_code == 202, gen.text
    run = wait_generation(client, gen.json()["generation_id"])
    assert run["state"] == "completed", run
    variant = run["variants"][0]
    export = client.post(
        f"{A}/variants/{variant['id']}/exports", json={"formats": ["quality_passport"]}
    )
    record = client.get(f"{A}/exports/{export.json()['export_id']}").json()
    passport = client.get(record["artifacts"][0]["download_url"]).json()
    metrics = passport["metrics"]
    origin = metrics["content_origin"]
    assert set(origin) == {"user", "web", "model"}
    assert origin["web"] > 0 and origin["user"] > 0
    research = metrics["research"]
    assert research["sources_count"] == 2
    assert research["content_fill"] == "expand"
    assert research["duration_s"] >= 0


# --- строка 9: capabilities -------------------------------------------------

def test_capabilities_flag_content_fill():
    assert client.get(f"{A}/capabilities").json()["features"]["content_fill"] is True


# --- сценарий §8a целиком: F1–F7 --------------------------------------------

def test_frontend_final_scenario_f1_to_f7(monkeypatch, wait_generation):
    """Как фронт: одна фраза (F2) → общий план job'ом (F1, F5) с плашкой
    достаточности (F3) и источниками (F6) → крестик у источника →
    правка пунктов и новый слайд (F4) → сборка двух вариантов по плану →
    паспорт «Откуда факты» (F7)."""
    _web(monkeypatch)
    pid = client.post(f"{A}/projects", json={"name": "f1-f7"}).json()["id"]
    tpl = client.post(
        f"{A}/projects/{pid}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    ).json()["id"]
    # F2: topic.md + title_hint
    cp = client.post(
        f"{A}/projects/{pid}/content-packs",
        files=[("files", ("topic.md", ONE_LINER.encode(), "text/markdown"))],
        data={"brief": json.dumps({"language": "ru", "title_hint": "Идея"})},
    ).json()["content_pack"]["id"]
    caps = client.get(f"{A}/capabilities").json()["features"]
    assert caps["content_fill"] and caps["plan_only"]

    def build(**extra) -> dict:
        accepted = client.post(f"{A}/projects/{pid}/plans", json={
            "template_id": tpl, "content_pack_id": cp, "brief": _brief(6),
            "strategies": ["balanced"], "use_llm": True, "respond_async": True, **extra,
        })
        assert accepted.status_code == 202, accepted.text
        body = accepted.json()
        assert _wait_job(body["job_id"])["state"] == "completed"
        got = client.get(f"{A}/projects/{pid}/plans/{body['plan_id']}")
        assert got.status_code == 200
        return got.json()

    plan_set = build()
    assert plan_set["sufficiency"]["target_slides"] == 6
    assert len(plan_set["research"]["sources"]) == 2
    dropped = plan_set["research"]["sources"][1]["id"]
    plan_set = build(exclude_source_ids=[dropped])
    assert [s["id"] for s in plan_set["research"]["sources"]] != [dropped]
    assert dropped not in {s["id"] for s in plan_set["research"]["sources"]}

    # F4: правка пункта и новый слайд
    plan = plan_set["plans"][0]["deck_plan"]
    body_slide = next(s for s in plan["slides"] if s["purpose"] not in ("title", "thank_you")
                      and any(u["kind"] != "title" for u in s["content_units"]))
    body_slide["content_units"].append({
        "role": "bullet", "kind": "bullet", "locked": True,
        "text": "Пилот запущен в двух отделах компании",
    })
    plan["slides"].insert(-1, {
        "id": "slide-user-1", "index": 0, "purpose": "custom",
        "title_intent": "Следующие шаги",
        "key_message": "Расширить пилот на все отделы до конца года",
        "evidence_ids": [f"{plan_set['content_pack_id']}:meta"], "content_units": [],
        "density_budget": {"level": "medium"}, "desired_visual": "none",
    })
    for i, slide in enumerate(plan["slides"]):
        slide["index"] = i

    gen = client.post(f"{A}/projects/{pid}/generations", json={
        "template_id": tpl, "content_pack_id": plan_set["content_pack_id"],
        "brief": _brief(6), "deck_plan": plan, "use_llm": False,
        "variants": [{"strategy": "faithful"}, {"strategy": "visual"}],
    })
    assert gen.status_code == 202, gen.text
    run = wait_generation(client, gen.json()["generation_id"])
    assert run["state"] == "completed", run
    assert len(run["variants"]) == 2
    for variant in run["variants"]:
        export = client.post(
            f"{A}/variants/{variant['id']}/exports", json={"formats": ["quality_passport"]}
        )
        record = client.get(f"{A}/exports/{export.json()['export_id']}").json()
        metrics = client.get(record["artifacts"][0]["download_url"]).json()["metrics"]
        assert metrics["content_origin"]["web"] > 0
        assert metrics["research"]["sources_count"] == 1
