"""Сквозная HTTP-интеграция LLM/VLM-пути: реальный TCP round-trip.

В отличие от `test_openai_compat.py` (httpx.MockTransport — транспорт
подменён внутри клиента) здесь `OpenAICompatibleProvider` ходит по
настоящему HTTP в локальный stub-сервер (`local_openai` из conftest),
который пишет каждый запрос — проверяется контракт на проводе, а не
мок транспорта:

- `mock_provider=false` + заполненные provider_* настройки →
  `build_gateway()` возвращает провайдер, который реально шлёт
  `POST /chat/completions` с `Authorization: Bearer <key>`;
- LLM-планирование = `text_json` (`storyline`, schema `DeckPlan`);
- VLM contextual-аудит = `vision_json` (`slide_checks`, schema
  `SlideChecksResult`) с `image_url` PNG data-URL в контенте;
- тот же путь через три точки входа: `generate()`, CLI
  `deckdna generate --llm` и API `POST /generations {use_llm: true}`.

Секретный материал (api_key) — выдуманный `test-secret`; реальных env
и сети наружу нет.
"""

import json
import shutil
from pathlib import Path

import deckdna.generation.pipeline as pipeline_module
import deckdna.providers.factory as factory_module
import pytest
from deckdna.generation.pipeline import generate
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.evidence import build_evidence_graph
from deckdna.planning.story_director import LLM_PLANNER_VERSION
from deckdna.providers.openai_compat import OpenAICompatibleProvider
from deckdna.settings import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "pptx" / "vk_tech_template.pptx"
CONTENT = FIXTURES / "content" / "poc_article.md"

BRIEF = {
    "purpose": "Проверить сквозной LLM/VLM путь DeckDNA",
    "audience": "эксперты VK Tech",
    "language": "ru",
    "target_slide_count": 10,
}

API_KEY = "test-secret"  # noqa: S105 — фиктивный ключ локального stub'а

needs_render = pytest.mark.skipif(
    shutil.which("soffice") is None or not TEMPLATE.exists(),
    reason="generate() рендерит pdf/png через soffice",
)


def _settings(base_url: str) -> Settings:
    """mock_provider=false + реальный локальный endpoint — конфиг-путь,
    который документирован для живого провайдера."""
    return Settings(
        mock_provider=False,
        provider_base_url=base_url,
        provider_api_key=API_KEY,
        model_text="stub-text-model",
        model_vision="stub-vision-model",
    )


def _storyline_payload() -> dict:
    """Схема-валидный SlideOutlineBatch-dict — «ответ LLM» на storyline v2.

    Стаб-сервер (``LocalOpenAIServer``) отдаёт ОДИН зарегистрированный
    ответ на все запросы с данным именем схемы, независимо от тела
    запроса — а ``plan_deck_llm`` шлёт несколько storyline-вызовов
    (по батчу слайдов на вызов). Поэтому здесь заранее покрываются ВСЕ
    индексы колоды разом: какой бы батч ``batch.indices`` ни пришёл,
    в этом списке найдётся элемент под каждый индекс. evidence_id —
    настоящий узел из реального EvidenceGraph контента (не выдумка), so
    заземление в ``_run_outline_batch`` проходит."""
    pack = parse_file(CONTENT)
    graph = build_evidence_graph(pack)
    claim_id = next(n.id for n in graph.nodes if n.type.value == "claim")
    return {
        "slides": [
            {
                "index": i,
                "purpose": "overview",
                "title_intent": f"LLM-вывод {i}",
                "key_message": "Заключение по данным.",
                "evidence_ids": [claim_id],
            }
            for i in range(BRIEF["target_slide_count"])
        ]
    }


def _slide_checks_payload() -> dict:
    """SlideChecksResult: одна fail-проверка — доказательство, что
    VLM-ответ доезжает до issues пайплайна."""
    return {
        "verdicts": [
            {
                "check": "5",
                "verdict": "fail",
                "rationale": "stub-VLM: слайд выглядит пустым",
                "confidence": 0.9,
            }
        ]
    }


def _configure(server) -> None:
    server.responses["SlideOutlineBatch"] = _storyline_payload()
    server.responses["SlideChecksResult"] = _slide_checks_payload()


def _calls(server, schema_name: str) -> list[dict]:
    """Тела записанных запросов по имени output-схемы."""
    return [
        r["json"]
        for r in server.requests
        if r["json"]
        .get("response_format", {})
        .get("json_schema", {})
        .get("name")
        == schema_name
    ]


def _assert_openai_contract(server) -> None:
    """Общие инварианты провода: путь, auth-заголовок, json_schema."""
    assert server.requests
    for req in server.requests:
        assert req["path"] == "/chat/completions"
        assert req["authorization"] == f"Bearer {API_KEY}"
        body = req["json"]
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["messages"]


def test_provider_real_http_text_and_vision(local_openai):
    """Провайдер-уровень: text_json и vision_json доезжают по TCP."""
    _configure(local_openai)
    gateway = factory_module.build_gateway(_settings(local_openai.base_url))
    assert isinstance(gateway, OpenAICompatibleProvider)

    import asyncio

    async def _roundtrip():
        from deckdna.audit.contextual import SlideChecksResult
        from deckdna.planning.story_director import SlideOutlineBatch

        plan = await gateway.text_json(
            "storyline", {"brief": BRIEF}, SlideOutlineBatch
        )
        assert plan.slides
        checks = await gateway.vision_json(
            "slide_checks",
            ["data:image/png;base64,iVBORw0KGgo="],
            {"slide_text": "тест", "deck_language": "ru"},
            SlideChecksResult,
        )
        return plan, checks

    plan, checks = asyncio.run(_roundtrip())
    assert plan.slides and checks.verdicts[0].verdict == "fail"

    storyline = _calls(local_openai, "SlideOutlineBatch")
    assert len(storyline) == 1
    assert storyline[0]["model"] == "stub-text-model"
    assert storyline[0]["messages"][0]["role"] == "user"
    assert "storyline" not in storyline[0]["messages"][0]["content"]
    assert storyline[0]["messages"][0]["content"]  # реальный промпт-текст

    vision = _calls(local_openai, "SlideChecksResult")
    assert len(vision) == 1
    assert vision[0]["model"] == "stub-vision-model"
    content = vision[0]["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    images = [c for c in content if c["type"] == "image_url"]
    assert images and all(
        c["image_url"]["url"].startswith("data:image/png;base64,")
        for c in images
    )
    _assert_openai_contract(local_openai)


def test_provider_real_http_exemplar_rerank_strict_schema(local_openai):
    """text_json(exemplar_rerank) по реальному TCP: strict json_schema
    ExemplarRerankOutput на проводе — закрытые объекты + required =
    все поля (инвариант PR #129 действует и для rerank-промпта)."""
    from deckdna.pptx.cloning.rerank import ExemplarRerankOutput

    local_openai.responses["ExemplarRerankOutput"] = {
        "assignments": [
            {"slide_index": 0, "candidate_index": 1, "reason": "fit"}
        ]
    }
    gateway = factory_module.build_gateway(_settings(local_openai.base_url))

    import asyncio

    out = asyncio.run(
        gateway.text_json(
            "exemplar_rerank",
            {"requests": [], "candidates": [], "strategy": "balanced"},
            ExemplarRerankOutput,
        )
    )
    assert out.assignments[0].candidate_index == 1

    calls = _calls(local_openai, "ExemplarRerankOutput")
    assert len(calls) == 1
    body = calls[0]
    assert body["model"] == "stub-text-model"
    schema = body["response_format"]["json_schema"]["schema"]
    assert body["response_format"]["json_schema"]["strict"] is True
    assert schema.get("additionalProperties") is False
    assert set(schema.get("required") or []) >= {"assignments"}
    item = schema["$defs"]["ExemplarAssignment"]
    assert item.get("additionalProperties") is False
    assert set(item.get("required") or []) == {
        "slide_index",
        "candidate_index",
        "reason",
    }
    _assert_openai_contract(local_openai)


@needs_render
def test_generate_llm_path_over_real_http(tmp_path, local_openai, monkeypatch):
    """generate(gateway=build_gateway()) — план и VLM-аудит через HTTP.

    Пин на v1 — тест проверяет storyline-специфичную схему
    (SlideOutlineBatch) и prompt_versions на проводе; v3 (дефолт с 27.09,
    ADR-016) использует deck_structure/content_writer/layout_fit."""
    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v1")
    _configure(local_openai)
    gateway = factory_module.build_gateway(_settings(local_openai.base_url))

    report = generate(
        TEMPLATE, CONTENT, BRIEF, tmp_path, gateway=gateway
    )

    assert report["planner"] == LLM_PLANNER_VERSION
    assert report["contextual_audit"]["ran"] is True
    ctx_issues = [
        i for i in report["audit_issues"] if not i["deterministic"]
    ]
    assert ctx_issues  # VLM-fail из stub'а доехал в issues

    # Паспорт несёт правдивый provenance по реально ушедшим вызовам:
    # оба принятых промпта (storyline + contextual_slide_audit —
    # registry-declared имена), обе успешные модели с sanitized
    # host[:port] провайдером; секрет и полный base_url не утекают.
    passport_path = Path(report["artifacts"]["quality_passport"])
    passport = json.loads(passport_path.read_text())
    prov = passport["provenance"]
    assert prov["prompt_versions"] == {
        "storyline": "2.0.1",
        "contextual_slide_audit": "1.1.0",
    }
    assert {
        (p["model_id"], p["provider"]) for p in prov["model_profiles"]
    } == {
        ("stub-text-model", local_openai.base_url.split("://")[1]),
        ("stub-vision-model", local_openai.base_url.split("://")[1]),
    }
    assert API_KEY not in passport_path.read_text()
    assert local_openai.base_url not in passport_path.read_text()

    # план разбит на батчи слайдов -> несколько storyline-вызовов, не один
    assert len(_calls(local_openai, "SlideOutlineBatch")) >= 1
    vision_calls = _calls(local_openai, "SlideChecksResult")
    # один vision-вызов на слайд сгенерированной колоды
    assert len(vision_calls) == report["slides_out"]
    for body in vision_calls:
        images = [
            c
            for c in body["messages"][0]["content"]
            if c["type"] == "image_url"
        ]
        assert images, "vision-вызов без картинки"
        assert all(
            i["image_url"]["url"].startswith("data:image/png;base64,")
            for i in images
        )
    _assert_openai_contract(local_openai)


@needs_render
def test_cli_generate_llm_over_real_http(tmp_path, monkeypatch, local_openai):
    """`deckdna generate --llm` собирает gateway из настроек и ходит по HTTP.

    Патчится только `settings` в factory-модуле (тот же объект, что
    читает build_gateway) — конфиг-путь mock_provider=false честный,
    build_gateway/generate не подменяются.

    Пин на v1 — тест проверяет storyline-специфичную схему over real HTTP.
    """
    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v1")
    _configure(local_openai)
    monkeypatch.setattr(
        factory_module, "settings", _settings(local_openai.base_url)
    )
    from deckdna.cli.main import app
    from typer.testing import CliRunner

    out_dir = tmp_path / "out"
    result = CliRunner().invoke(
        app,
        [
            "generate",
            str(TEMPLATE),
            str(CONTENT),
            str(out_dir),
            "--llm",
            "--slides",
            str(BRIEF["target_slide_count"]),
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["planner"] == LLM_PLANNER_VERSION
    assert summary["contextual_audit"]["ran"] is True

    assert _calls(local_openai, "SlideOutlineBatch")
    assert _calls(local_openai, "SlideChecksResult")
    _assert_openai_contract(local_openai)


@needs_render
def test_api_generation_use_llm_over_real_http(
    monkeypatch, local_openai, wait_generation
):
    """POST /generations {use_llm: true} — тот же HTTP-путь через API.

    Ничего из api/app не патчится: меняется только `settings`, так
    `build_gateway()` внутри обработчика честно собирает
    OpenAICompatibleProvider и ходит в локальный stub.

    Пин на v1 — тест проверяет storyline-специфичную схему over real HTTP.
    """
    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v1")
    _configure(local_openai)
    monkeypatch.setattr(
        factory_module, "settings", _settings(local_openai.base_url)
    )
    from deckdna.api.app import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    project = client.post(
        "/api/v1/projects",
        json={"name": "http e2e", "target_slide_count": 10},
    ).json()
    tpl = client.post(
        f"/api/v1/projects/{project['id']}/templates",
        files={
            "file": (
                TEMPLATE.name,
                TEMPLATE.read_bytes(),
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation",
            )
        },
    )
    assert tpl.status_code == 201, tpl.text
    pack = client.post(
        f"/api/v1/projects/{project['id']}/content-packs",
        files={
            "files": (CONTENT.name, CONTENT.read_bytes(), "text/markdown")
        },
    )
    assert pack.status_code == 202, pack.text

    accepted = client.post(
        f"/api/v1/projects/{project['id']}/generations",
        json={
            "template_id": tpl.json()["id"],
            "content_pack_id": pack.json()["content_pack"]["id"],
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
            "use_llm": True,
        },
    )
    assert accepted.status_code == 202, accepted.text
    run = wait_generation(client, accepted.json()["generation_id"])
    assert run["state"] == "completed", run
    variant = run["variants"][0]
    assert variant["planner"] == LLM_PLANNER_VERSION
    assert variant["contextual_issues"] >= 1

    # text_json идёт и в _plan_for (для записи), и внутри generate() —
    # минимум один, оба по HTTP; vision — по слайду на вариант.
    assert _calls(local_openai, "SlideOutlineBatch")
    assert _calls(local_openai, "SlideChecksResult")
    _assert_openai_contract(local_openai)


def test_smoke_sanitized_host_drops_userinfo():
    """smoke_llm_endpoint: host в выводе не тащит credentials из URL."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "smoke_llm_endpoint",
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "smoke_llm_endpoint.py",
    )
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)

    assert (
        smoke.sanitized_host("https://user:secret-token@api.example.com/v1")
        == "api.example.com"
    )
    assert "secret-token" not in smoke.sanitized_host(
        "https://user:secret-token@api.example.com:8443/v1"
    )
    assert (
        smoke.sanitized_host("https://u:p@api.example.com:8443/v1")
        == "api.example.com:8443"
    )
    assert (
        smoke.sanitized_host("http://[2001:db8::1]:9000/v1")
        == "[2001:db8::1]:9000"
    )
    assert smoke.sanitized_host("http://[2001:db8::1]/v1") == "[2001:db8::1]"
