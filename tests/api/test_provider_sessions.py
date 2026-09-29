"""Provider sessions: lease api_token, реальные пробы, session gateway.

Проверяется по настоящему HTTP против локального stub-сервера
(`local_openai` из conftest — пишет каждый запрос и отвечает по имени
JSON-схемы):

- ``POST /provider-sessions`` — lease: токен хранится server-side и
  никогда не возвращается; TTL enforced (истёкшая → 404 и вытеснение);
  DELETE отзывает lease вместе с секретом; project-scoped сессия не
  работает в чужом проекте;
- ``POST .../test`` — реальные minimal capability-пробы
  (structured_output/image_input/embeddings — живой round-trip,
  tool_calls — честный skip);
- ``provider_session_id`` в генерации выбирает gateway по кредам
  сессии (сам включает LLM-путь) — запросы идут на endpoint сессии с
  её токеном, а не по env settings;
- сохранённый deck_plan тождественен плану колоды — LLM-планировщик
  вызывается один раз на стратегию (двойного вызова нет).
"""

import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from deckdna.api.app import STORE, app
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.evidence import build_evidence_graph
from deckdna.planning.story_director import LLM_PLANNER_VERSION
from deckdna.providers.openai_compat import OpenAICompatibleProvider
from deckdna.settings import settings
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "pptx" / "vk_tech_template.pptx"
CONTENT = FIXTURES / "content" / "poc_article.md"
PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)

client = TestClient(app)

SESSION_TOKEN = "session-secret"  # noqa: S105 — фиктивный токен stub'а

BRIEF = {
    "purpose": "Представить решение",
    "audience": "эксперты и жюри",
    "language": "ru",
    "target_slide_count": 10,
}

needs_render = pytest.mark.skipif(
    shutil.which("soffice") is None or not TEMPLATE.exists(),
    reason="generation/audit needs the fixture + soffice",
)


@pytest.fixture(autouse=True)
def _local_stub_operator_setting(monkeypatch):
    # This module uses a loopback HTTP stub. Only the test server operator,
    # never the API caller, may allow private provider destinations.
    monkeypatch.setattr(settings, "provider_allow_private_networks", True)


def _create_session(server, **overrides):
    body = {
        "label": "stub provider",
        "base_url": server.base_url,
        "api_token": SESSION_TOKEN,
        "models": {"text": "sess-text", "vision": "sess-vision"},
        "capabilities": {
            "structured_output": True,
            "image_input": True,
            "embeddings": False,
            "tool_calls": True,
        },
        **overrides,
    }
    response = client.post("/api/v1/provider-sessions", json=body)
    assert response.status_code == 201, response.text
    return response


def _configure(server):
    """Stub отвечает схема-валидными payload'ами для всех вызовов.

    ``SlideOutlineBatch`` — stub статически отдаёт ОДИН ответ на все
    запросы с этим именем схемы, независимо от того, какой батч индексов
    реально запросили (``plan_deck_llm`` шлёт несколько storyline-вызовов,
    по батчу слайдов на вызов) — поэтому список покрывает ВЕСЬ диапазон
    индексов колоды разом, заземлённый на реальный узел evidence-графа.
    """
    pack = parse_file(CONTENT)
    graph = build_evidence_graph(pack)
    claim_id = next(n.id for n in graph.nodes if n.type.value == "claim")
    server.responses["SlideOutlineBatch"] = {
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
    server.responses["SlideChecksResult"] = {
        "verdicts": [
            {
                "check": str(check),
                "verdict": "fail" if check == 5 else "pass",
                "rationale": (
                    "stub-VLM: слайд выглядит пустым" if check == 5 else "stub"
                ),
                "confidence": 0.9,
            }
            for check in range(1, 11)
        ]
    }
    server.responses["_ProbeAck"] = {"ok": True}


def _project_with_inputs() -> tuple[str, str, str]:
    project = client.post(
        "/api/v1/projects", json={"name": "sess gen", "target_slide_count": 10}
    ).json()
    tpl = client.post(
        f"/api/v1/projects/{project['id']}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    )
    assert tpl.status_code == 201, tpl.text
    pack = client.post(
        f"/api/v1/projects/{project['id']}/content-packs",
        files={"files": (CONTENT.name, CONTENT.read_bytes(), "text/markdown")},
    )
    assert pack.status_code == 202, pack.text
    return project["id"], tpl.json()["id"], pack.json()["content_pack"]["id"]


def test_session_token_is_lease_held_never_returned(local_openai):
    """api_token не возвращается API и реально хранится в lease-сторе."""
    created = _create_session(local_openai)
    session = created.json()
    assert "api_token" not in session
    assert SESSION_TOKEN not in created.text
    secret = STORE.provider_secrets.get(session["id"])
    assert secret and secret["api_token"] == SESSION_TOKEN


def test_session_test_endpoint_runs_real_probes(local_openai):
    """test-проба ходит по HTTP в endpoint сессии, не эхо declared-флагов."""
    _configure(local_openai)
    session = _create_session(local_openai).json()

    result = client.post(
        f"/api/v1/provider-sessions/{session['id']}/test"
    ).json()
    by_name = {r["capability"]: r for r in result["results"]}
    assert by_name["structured_output"]["status"] == "ok"
    assert by_name["image_input"]["status"] == "ok"
    assert by_name["embeddings"]["status"] == "skip"  # не заявлена
    assert by_name["tool_calls"]["status"] == "skip"  # пробы нет

    # пробы реально дошли по проводу — два json_schema-вызова _ProbeAck
    probe_calls = [
        r for r in local_openai.requests
        if r["json"].get("response_format", {})
        .get("json_schema", {})
        .get("name") == "_ProbeAck"
    ]
    assert len(probe_calls) == 2
    assert all(
        r["authorization"] == f"Bearer {SESSION_TOKEN}" for r in probe_calls
    )
    # image-проба несёт PNG data URL
    image_parts = [
        c
        for r in probe_calls
        for c in r["json"]["messages"][0]["content"]
        if isinstance(c, dict) and c.get("type") == "image_url"
    ]
    assert image_parts and all(
        c["image_url"]["url"].startswith("data:image/png;base64,")
        for c in image_parts
    )


def test_session_lease_ttl_and_revoke(local_openai):
    """Истёкший lease → 404 и вытеснение секрета; DELETE отзывает."""
    _configure(local_openai)
    session = _create_session(local_openai).json()
    sid = session["id"]

    # имитируем истёкший TTL
    STORE.provider_sessions[sid] = STORE.provider_sessions[sid].model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    response = client.post(f"/api/v1/provider-sessions/{sid}/test")
    assert response.status_code == 404
    assert sid not in STORE.provider_sessions  # вытеснена
    assert sid not in STORE.provider_secrets  # секрет уничтожен

    # revoke по DELETE — повторное использование 404
    session2 = _create_session(local_openai).json()
    assert client.delete(
        f"/api/v1/provider-sessions/{session2['id']}"
    ).status_code == 204
    assert (
        client.post(
            f"/api/v1/provider-sessions/{session2['id']}/test"
        ).status_code
        == 404
    )
    assert session2["id"] not in STORE.provider_secrets


@needs_render
def test_generation_uses_session_gateway_and_single_plan(
    monkeypatch,
    local_openai,
    wait_generation,
):
    """provider_session_id → gateway по кредам сессии + план не пересчитан дважды.

    Сохранённый deck_plan тождественен плану колоды (generate принимает
    deck_plan и не пересчитывает его заново) — storyline-батчи (по одному
    вызову на батч слайдов, см. plan_deck_llm v2) в сумме покрывают КАЖДЫЙ
    индекс колоды РОВНО один раз, без пересечений между вызовами; запросы
    идут с токеном сессии.

    Пин на v1 — тест ищет конкретно SlideOutlineBatch (storyline v2's
    схема); v3 (дефолт с 27.09) шлёт SlideStructureBatch вместо неё."""
    import deckdna.generation.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v1")
    _configure(local_openai)
    session = _create_session(local_openai).json()
    project_id, template_id, pack_id = _project_with_inputs()

    # provider_session_id без use_llm: сам включает LLM-путь
    accepted = client.post(
        f"/api/v1/projects/{project_id}/generations",
        json={
            "template_id": template_id,
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
            "provider_session_id": session["id"],
        },
    )
    assert accepted.status_code == 202, accepted.text
    run = wait_generation(client, accepted.json()["generation_id"])
    assert run["state"] == "completed", run
    variant = run["variants"][0]
    assert variant["planner"] == LLM_PLANNER_VERSION
    assert variant["contextual_issues"] == BRIEF["target_slide_count"]

    # все вызовы пошли на endpoint сессии с её токеном
    assert local_openai.requests
    assert all(
        r["authorization"] == f"Bearer {SESSION_TOKEN}"
        for r in local_openai.requests
    )
    # storyline-батчи в сумме не пересчитывают план дважды: индексы слайдов
    # не повторяются между вызовами (regex — тело запроса это рендеренный
    # текст промпта, не структурный payload; "indices" сериализован туда
    # через render_prompt/json.dumps).
    storyline_calls = [
        r for r in local_openai.requests
        if r["json"].get("response_format", {})
        .get("json_schema", {})
        .get("name") == "SlideOutlineBatch"
    ]
    assert storyline_calls
    seen_indices: list[int] = []
    for call in storyline_calls:
        content = call["json"]["messages"][0]["content"]
        match = re.search(r'"indices":\s*\[([\d,\s]*)\]', content)
        assert match, content
        seen_indices.extend(int(x) for x in match.group(1).split(",") if x.strip())
    assert len(seen_indices) == len(set(seen_indices))
    # записанный план == план колоды
    assert run["deck_plan_id"] == variant["deck_plan_id"]
    assert run["deck_plan"]["provenance"]["planner"] == LLM_PLANNER_VERSION


@needs_render
def test_generation_session_scoped_to_project(local_openai):
    """Сессия, привязанная к проекту A, не работает в проекте B."""
    _configure(local_openai)
    project_id, template_id, pack_id = _project_with_inputs()
    other = client.post("/api/v1/projects", json={"name": "other"}).json()
    session = _create_session(local_openai, project_id=other["id"]).json()

    response = client.post(
        f"/api/v1/projects/{project_id}/generations",
        json={
            "template_id": template_id,
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
            "provider_session_id": session["id"],
            "use_llm": True,
        },
    )
    assert response.status_code == 422
    assert local_openai.requests == []  # gateway не строился


def test_validation_error_never_echoes_token(local_openai):
    """422 на битом теле не возвращает api_token — input вырезается."""
    canary = "CANARY-SECRET-TEST"  # noqa: S105 — маркер, не секрет
    response = client.post(
        "/api/v1/provider-sessions",
        json={
            "label": "leaky",
            "base_url": local_openai.base_url,
            "api_token": canary,
            # models отсутствует → ошибка валидации
        },
    )
    assert response.status_code == 422
    assert canary not in response.text
    # и в структуре конверта нет ни одного поля со значением-вводом
    for error in response.json()["error"]["details"]["errors"]:
        assert "input" not in error


def test_base_url_userinfo_query_fragment_and_scheme_rejected(local_openai):
    """base_url is public; credentials in any URL component must be rejected."""
    for bad_url in (
        "http://user:PASS-URL-TEST@local.test/v1",
        "https://public.example/v1?api_key=PASS-URL-TEST",
        "https://public.example/v1#PASS-URL-TEST",
        "ftp://local.test/v1",
        "not-a-url",
    ):
        response = client.post(
            "/api/v1/provider-sessions",
            json={
                "label": "bad url",
                "base_url": bad_url,
                "api_token": SESSION_TOKEN,
                "models": {"text": "t", "vision": "v"},
            },
        )
        assert response.status_code == 422, bad_url
        assert "PASS-URL-TEST" not in response.text


def test_private_provider_targets_blocked_by_default(monkeypatch):
    monkeypatch.setattr(settings, "provider_allow_private_networks", False)
    for base_url in (
        "http://127.0.0.1:9/v1",
        "http://127.1:9/v1",
        "http://2130706433:9/v1",
        "http://0x7f000001:9/v1",
        "http://localhost:9/v1",
        "http://169.254.169.254/latest/meta-data",
    ):
        response = client.post(
            "/api/v1/provider-sessions",
            json={
                "label": "private target",
                "base_url": base_url,
                "api_token": SESSION_TOKEN,
                "models": {"text": "t", "vision": "v"},
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_input"


def test_client_cannot_enable_private_provider_target(monkeypatch):
    monkeypatch.setattr(settings, "provider_allow_private_networks", False)
    response = client.post(
        "/api/v1/provider-sessions",
        json={
            "label": "explicit private target",
            "base_url": "http://127.0.0.1:9/v1",
            "api_token": SESSION_TOKEN,
            "models": {"text": "t", "vision": "v"},
            "allow_private_networks": True,
        },
    )
    assert response.status_code == 422, response.text
    assert "session-secret" not in response.text


def test_operator_can_enable_private_provider_target_for_local_stub():
    response = client.post(
        "/api/v1/provider-sessions",
        json={
            "label": "operator enabled private target",
            "base_url": "http://127.0.0.1:9/v1",
            "api_token": SESSION_TOKEN,
            "models": {"text": "t", "vision": "v"},
        },
    )
    assert response.status_code == 201, response.text
    session_id = response.json()["id"]
    assert "allow_private_networks" not in response.json()
    assert "allow_private_networks" not in STORE.provider_secrets[session_id]


@needs_render
def test_generation_closes_session_gateway(local_openai, wait_generation, monkeypatch):
    """HTTP-клиент session-gateway закрывается после генерации.

    С opt-in session()-переиспользованием клиента (Фаза 3, ADR-012-follow-up)
    ``aclose()`` — уже не единственный defensive no-op в самом конце: каждый
    отдельный asyncio.run-скоуп (план-фетч, rerank/fit/аудит внутри
    generate(), финальная зачистка в _execute_generation) детерминированно
    закрывает СВОЮ сессию на выходе и подстраховывается собственным
    aclose() — несколько вызовов ожидаемы и безопасны (idempotent: не
    закрывает уже закрытое). Реальный инвариант — не "ровно один вызов", а
    "после каждого вызова клиента не осталось открытым"."""
    _configure(local_openai)
    session = _create_session(local_openai).json()
    project_id, template_id, pack_id = _project_with_inputs()

    closes = 0
    original = OpenAICompatibleProvider.aclose

    async def _spy(self):
        nonlocal closes
        closes += 1
        await original(self)
        assert self._session_client is None  # каждый close реально закрывает

    monkeypatch.setattr(OpenAICompatibleProvider, "aclose", _spy)

    accepted = client.post(
        f"/api/v1/projects/{project_id}/generations",
        json={
            "template_id": template_id,
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
            "provider_session_id": session["id"],
        },
    )
    assert accepted.status_code == 202, accepted.text
    run = wait_generation(client, accepted.json()["generation_id"])
    assert run["state"] == "completed", run
    assert closes >= 1  # хотя бы одна зачистка реально произошла, не пропущена


@needs_render
def test_audit_upgrade_deterministic_run_via_session(local_openai, wait_generation):
    """POST audit с живой сессией апгрейдит det-audit до VLM, без дублей."""
    _configure(local_openai)
    session = _create_session(local_openai).json()
    project_id, template_id, pack_id = _project_with_inputs()

    # детерминированная генерация — без use_llm и сессии
    accepted = client.post(
        f"/api/v1/projects/{project_id}/generations",
        json={
            "template_id": template_id,
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
        },
    )
    assert accepted.status_code == 202, accepted.text
    run = wait_generation(client, accepted.json()["generation_id"])
    assert run["state"] == "completed", run
    variant = run["variants"][0]
    assert local_openai.requests == []  # генерация не ходила в LLM

    # генерация уже записала детерминированный audit run
    det_audit = client.post(
        f"/api/v1/variants/{variant['id']}/audits", json={}
    ).json()
    det = client.get(f"/api/v1/audits/{det_audit['audit_id']}").json()
    assert det["contextual_status"] == "skipped"
    det_issues = client.get(
        f"/api/v1/audits/{det_audit['audit_id']}/issues"
    ).json()["items"]
    assert all(i["deterministic"] for i in det_issues)
    det_count = len(det_issues)

    # upgrade по явной сессии — тот же audit run, VLM-прогон сверху
    upgraded = client.post(
        f"/api/v1/variants/{variant['id']}/audits",
        json={"provider_session_id": session["id"]},
    )
    assert upgraded.status_code == 202, upgraded.text
    assert upgraded.json()["audit_id"] == det_audit["audit_id"]
    audit = client.get(f"/api/v1/audits/{det_audit['audit_id']}").json()
    assert audit["contextual_status"] == "completed"

    # VLM-вызовы реально ушли по HTTP с токеном сессии
    ctx_calls = [
        r for r in local_openai.requests
        if r["json"].get("response_format", {})
        .get("json_schema", {})
        .get("name") == "SlideChecksResult"
    ]
    assert ctx_calls
    assert all(
        r["authorization"] == f"Bearer {SESSION_TOKEN}" for r in ctx_calls
    )
    # Повторный contextual-аудит должен получать evidence-граф того же
    # варианта, иначе проверка source support работает на пустом контексте.
    excerpts = []
    for call in ctx_calls:
        prompt_text = next(
            c["text"]
            for c in call["json"]["messages"][0]["content"]
            if c["type"] == "text"
        )
        section = re.search(
            r"## evidence_excerpt\n(.*?)(?:\n## |\Z)",
            prompt_text,
            re.S,
        )
        excerpts.append(section.group(1).strip() if section else "")
    assert any(excerpts), "at least one content slide must carry source evidence"

    issues = client.get(
        f"/api/v1/audits/{det_audit['audit_id']}/issues"
    ).json()["items"]
    det_now = [i for i in issues if i["deterministic"]]
    ctx_now = [i for i in issues if not i["deterministic"]]
    assert len(det_now) == det_count  # детерминированные сохранены
    assert ctx_now  # VLM-issues добавлены

    # повтор — идемпотентен: ни новых VLM-запросов, ни дублей issues
    calls_so_far = len(local_openai.requests)
    again = client.post(
        f"/api/v1/variants/{variant['id']}/audits",
        json={"provider_session_id": session["id"]},
    )
    assert again.json()["audit_id"] == det_audit["audit_id"]
    assert len(local_openai.requests) == calls_so_far
    assert (
        len(client.get(f"/api/v1/audits/{det_audit['audit_id']}/issues").json()["items"])
        == len(issues)
    )


@needs_render
def test_partial_contextual_audit_is_failed_and_can_retry(
    local_openai, wait_generation
):
    _configure(local_openai)
    local_openai.responses["SlideChecksResult"] = {
        "verdicts": [{"check": "5", "verdict": "pass", "confidence": 1.0}]
    }
    session = _create_session(local_openai).json()
    project_id, template_id, pack_id = _project_with_inputs()
    accepted = client.post(
        f"/api/v1/projects/{project_id}/generations",
        json={
            "template_id": template_id,
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
        },
    )
    assert accepted.status_code == 202
    run = wait_generation(client, accepted.json()["generation_id"])
    assert run["state"] == "completed"
    variant_id = run["variants"][0]["id"]
    first = client.post(
        f"/api/v1/variants/{variant_id}/audits",
        json={"provider_session_id": session["id"]},
    )
    assert first.status_code == 202
    audit_id = first.json()["audit_id"]
    assert client.get(f"/api/v1/audits/{audit_id}").json()["contextual_status"] == "failed"
    before_retry = len(local_openai.requests)

    _configure(local_openai)
    retry = client.post(
        f"/api/v1/variants/{variant_id}/audits",
        json={"provider_session_id": session["id"]},
    )
    assert retry.status_code == 202
    assert retry.json()["audit_id"] == audit_id
    assert len(local_openai.requests) > before_retry
    assert client.get(f"/api/v1/audits/{audit_id}").json()["contextual_status"] == "completed"


@needs_render
def test_audit_uses_each_variants_plan(local_openai, wait_generation):
    """VLM-аудит варианта граундится в ЕГО плане, не плане первого."""
    _configure(local_openai)
    session = _create_session(local_openai).json()
    project_id, template_id, pack_id = _project_with_inputs()

    accepted = client.post(
        f"/api/v1/projects/{project_id}/generations",
        json={
            "template_id": template_id,
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "variants": [
                {"strategy": "faithful"},
                {"strategy": "balanced"},
                {"strategy": "visual"},
            ],
        },
    )
    assert accepted.status_code == 202, accepted.text
    detail = wait_generation(client, accepted.json()["generation_id"])
    assert detail["state"] == "completed", detail
    assert len(detail["variants"]) == 3
    run = STORE.runs[detail["id"]]
    # планы вариантов различаются (faithful без agenda/recap-инъекций)
    assert len({v["deck_plan_id"] for v in detail["variants"]}) == 3

    for variant in detail["variants"]:
        # сервер держит фактический план именно этого варианта
        plan = run.variant_plans[variant["id"]]
        assert plan.id == variant["deck_plan_id"]

        before = len(local_openai.requests)
        upgraded = client.post(
            f"/api/v1/variants/{variant['id']}/audits",
            json={"provider_session_id": session["id"]},
        )
        assert upgraded.status_code == 202, upgraded.text
        checks = [
            r
            for r in local_openai.requests[before:]
            if r["json"].get("response_format", {})
            .get("json_schema", {})
            .get("name")
            == "SlideChecksResult"
        ]
        assert checks
        for r in checks:
            content = r["json"]["messages"][0]["content"]
            text = next(c["text"] for c in content if c["type"] == "text")
            slide_idx = int(
                re.search(r"## slide_image\n(\d+)", text).group(1)
            )
            intent = re.search(r"## title_intent\n([^\n]*)", text).group(1)
            # title_intent слайда в промпте — из плана ЭТОГО варианта
            # (позиционно: sp.index 0-based у plan_deck, slide_image 1-based)
            expected = plan.slides[slide_idx - 1].title_intent
            assert intent == expected
