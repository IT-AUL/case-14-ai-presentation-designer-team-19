"""Recovery regressions: grounded questions, deduplication and transient caching."""

import importlib
import json

from fastapi.testclient import TestClient

api = importlib.import_module("deckdna.api.app")
client = TestClient(api.app)
A = "/api/v1"


def upload(text, brief=None):
    pid = client.post(f"{A}/projects", json={"name": "recovery"}).json()["id"]
    response = client.post(
        f"{A}/projects/{pid}/content-packs",
        files=[("files", ("input.md", text.encode(), "text/markdown"))],
        data={"brief": json.dumps(brief or {})},
    )
    assert response.status_code == 202, response.text
    return pid, response.json()["content_pack"]["id"]


def test_numeric_topic_does_not_duplicate_number_card():
    _, pack = upload("# Результат\n\nВыручка выросла на 42 процента.\n")
    result = client.get(f"{A}/content-packs/{pack}/understanding?use_llm=false").json()
    cards = [b for b in result["blocks"] if b["text"] == "Выручка выросла на 42 процента."]
    assert len(cards) == 1
    assert cards[0]["kind"] == "number"


def test_uploaded_explicit_brief_suppresses_answered_questions():
    pid, _ = upload(
        "# Бриф\n\nАудитория: совет директоров / технический комитет\n\n"
        "Цель: утвердить план пилота\n\nКоличество слайдов: 5\n\n"
        "## Проблема\n\nСогласование занимает много времени.\n\n"
        "## Решение\n\nЕдиный процесс согласования.\n"
    )
    result = client.get(f"{A}/projects/{pid}/questions").json()
    brief = result["suggested_brief"]
    assert brief["audience"] == "совет директоров / технический комитет"
    assert brief["purpose"] == "утвердить план пилота"
    assert brief["target_slide_count"] == 5
    assert not ({"audience", "key_message", "length"} & {q["kind"] for q in result["questions"]})
    assert api.STORE.project_briefs[pid]["audience"] == brief["audience"]


def test_source_labels_do_not_override_user_answers():
    pid, _ = upload("# Бриф\n\nАудитория: совет директоров\n", {"audience": "инженеры"})
    result = client.get(f"{A}/projects/{pid}/questions").json()
    assert result["suggested_brief"]["audience"] == "инженеры"


def test_understanding_cache_and_invalidation(monkeypatch):
    pid, pack = upload("# Результат\n\nВыручка выросла на 42 процента.\n")
    original = api.build_understanding
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "build_understanding", counted)
    url = f"{A}/content-packs/{pack}/understanding?use_llm=false"
    first = client.get(url)
    assert first.status_code == 200
    assert client.get(url).json() == first.json()
    alias = client.get(f"{A}/projects/{pid}/understanding?use_llm=false")
    assert alias.status_code == 200 and alias.json() == first.json()
    assert len(calls) == 1
    api.STORE.project_briefs[pid]["audience"] = "инженеры"
    assert client.get(url).json()["brief"]["audience"] == "инженеры"
    assert len(calls) == 2
    api.STORE.project_brand[pid] = {"priority": "template"}
    assert client.get(url).status_code == 200
    assert len(calls) == 3
    project = api.STORE.projects[pid]
    api.STORE.projects[pid] = project.model_copy(update={"template_id": "changed"})
    assert client.get(url).status_code == 200
    assert len(calls) == 4
    old = api.STORE.content_packs[pack]
    api.STORE.content_packs[pack] = old.model_copy(update={"language": "en"})
    assert client.get(url).status_code == 200
    assert len(calls) == 5


def test_project_research_disabled_returns_honest_status(monkeypatch):
    from deckdna.research.config import ResearchConfig, WebResearchConfig

    pid, pack = upload("# Результат\n\nВыручка выросла на 42 процента.\n")
    monkeypatch.setattr(
        api,
        "load_research_config",
        lambda: ResearchConfig(
            web=WebResearchConfig(enabled=False),
        ),
    )
    response = client.post(f"{A}/projects/{pid}/research", json={"use_llm": False})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["added_facts"] == 0
    assert result["status"] == "skipped" and result["reason"] == "web_disabled"
    assert result["content_pack_id"] == pack


def test_project_research_zero_new_facts_preserves_pack(monkeypatch):
    from deckdna.research import web
    from deckdna.research.config import ResearchConfig, WebResearchConfig

    pid, pack = upload("# Результат\n\nВыручка выросла на 42 процента.\n")
    monkeypatch.setattr(
        api,
        "load_research_config",
        lambda: ResearchConfig(
            web=WebResearchConfig(search_url="https://search.example"),
        ),
    )

    async def empty(*args, **kwargs):
        return web.WebResearch(queries=["query"], degraded="no_search_results")

    monkeypatch.setattr(web, "research_web", empty)
    result = client.post(f"{A}/projects/{pid}/research", json={"use_llm": False}).json()
    assert result["added_facts"] == 0
    assert result["status"] == "no_new_facts"
    assert result["reason"] == "no_search_results"
    assert result["content_pack_id"] == pack
    assert api.STORE.projects[pid].content_pack_id == pack


def test_project_research_additions_create_version_with_unique_sections(monkeypatch):
    from deckdna.contracts.content_pack import Block, Kind, Section, SourceRef
    from deckdna.research import web
    from deckdna.research.config import ResearchConfig, WebResearchConfig

    pid, original = upload("# Результат\n\nВыручка выросла на 42 процента.\n")
    monkeypatch.setattr(
        api,
        "load_research_config",
        lambda: ResearchConfig(
            web=WebResearchConfig(search_url="https://search.example"),
        ),
    )

    async def found(*args, **kwargs):
        return web.WebResearch(
            facts_kept=2,
            sections=[
                Section(
                    id="sec-web-0",
                    heading="Новые факты",
                    level=2,
                    blocks=[
                        Block(
                            kind=Kind.list,
                            items=["Первый факт", "Второй факт"],
                            source_ref=SourceRef(artifact_id="web:test"),
                        )
                    ],
                )
            ],
        )

    monkeypatch.setattr(web, "research_web", found)
    current = original
    for _ in range(2):
        response = client.post(f"{A}/projects/{pid}/research")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["added_facts"] == 2 and result["status"] == "added"
        assert result["filled_from"] == current
        current = result["content_pack_id"]
        assert current != original
        assert api.STORE.projects[pid].content_pack_id == current
        ids = [s.id for s in api.STORE.content_packs[current].sections]
        assert len(ids) == len(set(ids))
    assert len(api.STORE.content_packs[original].sections) == 1


def test_markdown_brief_list_and_conflicting_labels():
    pid, _ = upload(
        "# Бриф\n\n- **Аудитория:** технический комитет\n"
        "- **Цель:** согласовать пилот\n- **Количество слайдов:** 4\n",
    )
    brief = client.get(f"{A}/projects/{pid}/questions").json()["suggested_brief"]
    assert brief["audience"] == "технический комитет"
    assert brief["purpose"] == "согласовать пилот"
    assert brief["target_slide_count"] == 4
    pid, _ = upload("# Бриф\n\nАудитория: инженеры\n\nАудитория: инвесторы\n")
    result = client.get(f"{A}/projects/{pid}/questions").json()
    assert not result["suggested_brief"]["audience"]
    assert any(q["kind"] == "audience" for q in result["questions"])


def test_style_preview_receives_effective_brand_dna(monkeypatch):
    from types import SimpleNamespace

    from deckdna.generation import preview

    captured = []
    source_dna = object()
    branded_dna = object()
    pid, pack = upload("# Контент\n\nДостаточно информации для проверки.\n")
    monkeypatch.setitem(
        api.STORE.templates, "recovery-tpl", SimpleNamespace(artifact_id="recovery-art")
    )
    monkeypatch.setitem(
        api.STORE.artifacts, "recovery-art", SimpleNamespace(filename="template.pptx")
    )
    monkeypatch.setitem(api.STORE.design_dna, "recovery-tpl", source_dna)
    monkeypatch.setattr(api, "_template_bytes", lambda *a: (b"branded", {"applied": True}))

    def restyle(design, data):
        assert design is source_dna and data == b"branded"
        return branded_dna

    def render(*args, **kwargs):
        captured.append(kwargs["design_dna"])
        return {"styles": []}

    monkeypatch.setattr(api.dna_builder, "restyle_design_dna", restyle)
    monkeypatch.setattr(preview, "render_style_previews", render)
    plan_set = SimpleNamespace(project_id=pid, content_pack_id=pack, plan_id="plan")
    plan = SimpleNamespace(id="deck", model_dump_json=lambda: "{}")
    api._run_style_previews("preview", None, plan_set, plan, "recovery-tpl", None)
    assert captured == [branded_dna]
    assert api.STORE.design_dna["recovery-tpl"] is source_dna
