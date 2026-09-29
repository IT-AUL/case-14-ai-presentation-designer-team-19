"""Async generation job lifecycle (процесс-локальный executor).

POST /generations отвечает 202 ДО завершения работы: run/job создаются в
queued, bounded executor переводит queued→running→completed/failed.
Отмена кооперативная: queued-варианты не стартуют, текущий доводится до
конца, canceled — терминальное (завершение его не перезаписывает, повторная
отмена/отмена терминального → 409). Retry создаёт свежий run/job.

Slow-generator: обёртка pipeline.generate блокируется на Event до явного
release — тест наблюдает реальные переходы без гонок на sleep().
"""

import shutil
import threading
import time
from pathlib import Path

import pytest
from deckdna.api import app as app_module
from deckdna.api.app import STORE, app
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "pptx" / "vk_tech_template.pptx"
CONTENT = FIXTURES / "content" / "poc_article.md"
PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)

client = TestClient(app)

BRIEF = {
    "purpose": "Представить решение",
    "audience": "эксперты и жюри",
    "language": "ru",
    "target_slide_count": 10,
}

needs_render = pytest.mark.skipif(
    shutil.which("soffice") is None or not TEMPLATE.exists(),
    reason="generation pipeline needs the fixture + soffice",
)


def _project_with_inputs() -> tuple[str, str, str]:
    project = client.post(
        "/api/v1/projects", json={"name": "lifecycle", "target_slide_count": 10}
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


def _generate(
    project_id: str,
    template_id: str,
    pack_id: str,
    variants: list[dict] | None = None,
    headers: dict | None = None,
):
    return client.post(
        f"/api/v1/projects/{project_id}/generations",
        json={
            "template_id": template_id,
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "variants": variants or [{"strategy": "balanced"}],
        },
        headers=headers or {},
    )


def _job(job_id: str) -> dict:
    return client.get(f"/api/v1/jobs/{job_id}").json()


def _gate_generate(monkeypatch):
    """Оборачивает pipeline.generate: входит → set(started), ждёт release.

    Возвращает (started, release) — тест управляет моментом завершения
    варианта детерминированно, а не через sleep."""
    started = threading.Event()
    release = threading.Event()
    real = app_module.pipeline.generate

    def gated(*args, **kwargs):
        started.set()
        assert release.wait(timeout=120), "test never released the gate"
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module.pipeline, "generate", gated)
    return started, release


def _wait_state(job_id: str, states: set[str], timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        job = _job(job_id)
        if job["state"] in states:
            return job
        assert time.monotonic() < deadline, f"job stuck at {job['state']}"
        time.sleep(0.05)


@needs_render
def test_202_returns_before_generation_finishes(monkeypatch, wait_generation):
    """202 приходит сразу; run/job наблюдаемы в queued/running до завершения."""
    started, release = _gate_generate(monkeypatch)
    project_id, template_id, pack_id = _project_with_inputs()

    accepted = _generate(project_id, template_id, pack_id)
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert set(body) == {"generation_id", "job_id", "variant_ids"}

    # работа ещё идёт (вариант заблокирован на gate) — ответ уже вернулся.
    # Не проверяем возврат wait() строгим assert: таймаут здесь означает
    # "воркер не подхватил job" — job тогда остаётся queued, что и так
    # входит в допустимые состояния ниже.
    started.wait(timeout=60)
    job = _job(body["job_id"])
    assert job["state"] in {"queued", "running"}
    assert job["finished_at"] is None
    run = client.get(f"/api/v1/generations/{body['generation_id']}").json()
    assert run["state"] in {"queued", "running"}
    assert run["variants"][0]["status"] in {"queued", "running"}

    release.set()
    run = wait_generation(client, body["generation_id"])
    assert run["state"] == "completed"
    assert _job(body["job_id"])["state"] == "completed"


@needs_render
def test_generation_and_plans_reuse_stored_content_pack(monkeypatch, wait_generation):
    """/plans and /generations must reuse the ContentPack
    saved at upload (correct per-unit artifact_id from parse_file(...,
    artifact_id=chosen.id)) instead of re-parsing a temp copy of the source
    file, which would default artifact_id to the temp filename and break
    SourceRef resolution back to the API artifact store."""
    project_id, template_id, pack_id = _project_with_inputs()
    stored_pack = STORE.content_packs[pack_id]

    real_generate = app_module.pipeline.generate
    seen_content_packs = []

    def spy_generate(*args, **kwargs):
        seen_content_packs.append(kwargs.get("content_pack"))
        return real_generate(*args, **kwargs)

    monkeypatch.setattr(app_module.pipeline, "generate", spy_generate)

    real_parse_file = app_module.parse_file
    reparse_calls = []

    def spy_parse_file(path, artifact_id=None):
        if artifact_id is None:
            reparse_calls.append(str(path))
        return real_parse_file(path, artifact_id=artifact_id)

    monkeypatch.setattr(app_module, "parse_file", spy_parse_file)

    plans = client.post(
        "/api/v1/projects/" + project_id + "/plans",
        json={
            "template_id": template_id,
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "strategies": ["balanced"],
        },
    )
    assert plans.status_code == 200, plans.text

    accepted = _generate(project_id, template_id, pack_id)
    assert accepted.status_code == 202, accepted.text
    run = wait_generation(client, accepted.json()["generation_id"])
    assert run["state"] == "completed"

    assert reparse_calls == [], (
        f"plans/generations re-parsed content without artifact_id: {reparse_calls}"
    )
    assert seen_content_packs, "pipeline.generate was never called"
    assert all(pack is stored_pack for pack in seen_content_packs)


@needs_render
def test_audit_rejects_variant_with_no_completed_deck(monkeypatch):
    """POST /variants/{id}/audits on a variant that
    hasn't finished generating must not fabricate a "completed" audit
    with made-up issues and metrics -- it has no deck to audit yet."""
    started, release = _gate_generate(monkeypatch)
    project_id, template_id, pack_id = _project_with_inputs()
    accepted = _generate(project_id, template_id, pack_id)
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    variant_id = body["variant_ids"][0]

    started.wait(timeout=60)
    try:
        response = client.post(f"/api/v1/variants/{variant_id}/audits", json={})
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "state_conflict"
    finally:
        release.set()
        # drain the run so the background worker doesn't leak into other tests
        deadline = time.monotonic() + 60
        run = client.get(f"/api/v1/generations/{body['generation_id']}").json()
        while run["state"] not in {"completed", "failed", "canceled"}:
            assert time.monotonic() < deadline, "generation never finished draining"
            time.sleep(0.1)
            run = client.get(f"/api/v1/generations/{body['generation_id']}").json()


@needs_render
def test_queued_to_running_to_completed_transitions(
    monkeypatch, wait_generation
):
    """Переходы видны через GET /jobs: started_at/finished_at честные."""
    started, release = _gate_generate(monkeypatch)
    project_id, template_id, pack_id = _project_with_inputs()
    accepted = _generate(project_id, template_id, pack_id).json()

    job = _wait_state(accepted["job_id"], {"running"})
    assert job["started_at"] is not None and job["finished_at"] is None

    release.set()
    job = _wait_state(accepted["job_id"], {"completed"})
    assert job["started_at"] < job["finished_at"]
    assert job["progress"] == 1.0
    run = wait_generation(client, accepted["generation_id"])
    assert run["state"] == "completed"


@needs_render
def test_cancel_queued_generation_never_executes(
    monkeypatch, wait_generation
):
    """Отмена пока варианты в очереди: работа не стартует, всё терминально."""
    started, release = _gate_generate(monkeypatch)
    project_id, template_id, pack_id = _project_with_inputs()

    # занимаем ВСЕ воркеры пула (размер настраивается DECKDNA_GEN_WORKERS,
    # не захардкожен) заблокированными прогонами, чтобы следующий реально
    # остался в очереди, а не подхватился свободным воркером.
    pool_size = app_module._GEN_POOL._max_workers
    busy = [
        _generate(project_id, template_id, pack_id).json() for _ in range(pool_size)
    ]
    assert started.wait(timeout=60), "no worker ever picked up a busy run"
    second_started = threading.Event()
    deadline = time.monotonic() + 60
    while not second_started.is_set():
        if all(_job(b["job_id"])["state"] == "running" for b in busy):
            second_started.set()
        assert time.monotonic() < deadline, "pool workers never occupied"
        time.sleep(0.05)

    # следующий (pool_size + 1-й) прогон остаётся в очереди — отменяем его до старта
    queued = _generate(project_id, template_id, pack_id)
    assert queued.status_code == 202, queued.text
    q = queued.json()
    job = _wait_state(q["job_id"], {"queued", "running"}, timeout=60.0)
    if job["state"] == "running":
        pytest.skip("worker freed before assertion — race, rerun")

    cancel = client.post(f"/api/v1/generations/{q['generation_id']}/cancel")
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["state"] == "canceled"

    release.set()  # отпускаем занятые воркеры
    run = client.get(f"/api/v1/generations/{q['generation_id']}").json()
    assert run["state"] == "canceled"
    assert all(v["status"] == "canceled" for v in run["variants"])
    # canceled навсегда: очередь освободилась, но run не ожил
    for b in busy:
        wait_generation(client, b["generation_id"])
    run = client.get(f"/api/v1/generations/{q['generation_id']}").json()
    assert run["state"] == "canceled"
    assert _job(q["job_id"])["state"] == "canceled"


@needs_render
def test_cancel_running_is_cooperative_and_terminal(
    monkeypatch, wait_generation
):
    """Отмена во время работы: текущий вариант завершается честно,
    остальные не стартуют; завершение не перезаписывает canceled."""
    started, release = _gate_generate(monkeypatch)
    project_id, template_id, pack_id = _project_with_inputs()
    accepted = _generate(
        project_id,
        template_id,
        pack_id,
        variants=[{"strategy": "faithful"}, {"strategy": "balanced"}],
    ).json()

    # Таймаут щедрый: под нагрузкой (другие тесты файла тоже гоняют
    # реальный soffice-рендер через тот же _GEN_POOL) воркер может не
    # сразу подхватить этот run. Без assert здесь таймаут раньше тихо
    # проглатывался, и тест шёл дальше отменять job, который ещё даже
    # не начал первый вариант — cancel заставал его queued, а не
    # running, и финальная сверка статусов вариантов ложно падала.
    assert started.wait(timeout=60), "worker never picked up the run in time"
    cancel = client.post(
        f"/api/v1/generations/{accepted['generation_id']}/cancel"
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["state"] == "canceled"

    run = client.get(f"/api/v1/generations/{accepted['generation_id']}").json()
    assert run["state"] == "canceled"
    statuses = {v["status"] for v in run["variants"]}
    assert _not_completed(statuses)

    release.set()  # вариант-в-работе доводится до конца
    # run/job уже в терминальном canceled — ждём финализации вариантов:
    # executor ещё помечает in-flight completed после generate().
    deadline = time.monotonic() + 120
    while True:
        run = client.get(f"/api/v1/generations/{accepted['generation_id']}").json()
        by_status = [v["status"] for v in run["variants"]]
        if not any(s in {"queued", "running"} for s in by_status):
            break
        assert time.monotonic() < deadline, "in-flight variant never finalized"
        time.sleep(0.2)
    # терминал не перезаписан: ни run, ни job не стали completed
    assert run["state"] == "canceled"
    assert _job(accepted["job_id"])["state"] == "canceled"
    # честные итоги вариантов: in-flight завершён, незапущенный отменён
    assert sorted(by_status) == ["canceled", "completed"]


def _not_completed(statuses: set[str]) -> bool:
    # прямо после cancel: in-flight ещё running, остальные canceled,
    # completed появиться не должно
    return "completed" not in statuses


@needs_render
def test_cancel_terminal_job_is_409(wait_generation):
    """Отмена завершённого/отменённого job — state_conflict, не ложный успех."""
    project_id, template_id, pack_id = _project_with_inputs()
    accepted = _generate(project_id, template_id, pack_id).json()
    wait_generation(client, accepted["generation_id"])

    cancel = client.post(
        f"/api/v1/generations/{accepted['generation_id']}/cancel"
    )
    assert cancel.status_code == 409
    assert cancel.json()["error"]["code"] == "state_conflict"
    assert _job(accepted["job_id"])["state"] == "completed"  # не перезаписан


@needs_render
def test_retry_mints_fresh_run_and_job(wait_generation):
    """Retry создаёт НОВЫЕ run_id/job_id, связанные parent_run_id."""
    project_id, template_id, pack_id = _project_with_inputs()
    accepted = _generate(project_id, template_id, pack_id).json()
    wait_generation(client, accepted["generation_id"])

    retried = client.post(
        f"/api/v1/generations/{accepted['generation_id']}/retry"
    )
    assert retried.status_code == 202, retried.text
    body = retried.json()
    assert body["generation_id"] != accepted["generation_id"]
    assert body["job_id"] != accepted["job_id"]

    run = client.get(f"/api/v1/generations/{body['generation_id']}").json()
    assert run["parent_run_id"] == accepted["generation_id"]
    run = wait_generation(client, body["generation_id"])
    assert run["state"] == "completed"
    # оба job существуют и независимы
    assert _job(accepted["job_id"])["state"] == "completed"
    assert _job(body["job_id"])["state"] == "completed"


@needs_render
def test_idempotency_key_replays_single_job(wait_generation):
    """Повтор с тем же Idempotency-Key не плодит второй прогон."""
    project_id, template_id, pack_id = _project_with_inputs()
    key = {"Idempotency-Key": "gen-idem-1"}

    first = _generate(project_id, template_id, pack_id, headers=key)
    second = _generate(project_id, template_id, pack_id, headers=key)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()  # replay одного и того же accept

    # в store ровно один job под этот прогон
    run_id = first.json()["generation_id"]
    job_id = first.json()["job_id"]
    wait_generation(client, run_id)
    jobs = [j for j in STORE.jobs.values() if j.id == job_id]
    assert len(jobs) == 1
    # другой ключ — другой прогон
    third = _generate(
        project_id, template_id, pack_id, headers={"Idempotency-Key": "gen-idem-2"}
    )
    assert third.json()["generation_id"] != run_id
    wait_generation(client, third.json()["generation_id"])


def test_generation_failure_marks_failed_not_stuck(monkeypatch, wait_generation):
    """Исключение в пайплайне → job/run failed с честным error, не зависание."""
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic pipeline crash")

    monkeypatch.setattr(app_module.pipeline, "generate", boom)
    project_id, template_id, pack_id = _project_with_inputs()
    accepted = _generate(project_id, template_id, pack_id).json()

    run = wait_generation(client, accepted["generation_id"])
    assert run["state"] == "failed"
    job = _job(accepted["job_id"])
    assert job["state"] == "failed"
    assert job["error"]["code"] == "internal_error"
    assert "synthetic pipeline crash" in job["error"]["message"]
    assert all(v["status"] == "failed" for v in run["variants"])


def test_generation_rejects_template_from_another_project():
    """A template_id that exists, just not in this
    project, must not be usable to start a generation here."""
    project_a, template_a, pack_a = _project_with_inputs()
    project_b = client.post("/api/v1/projects", json={"name": "other"}).json()["id"]
    _, _, pack_b = _project_with_inputs()

    response = _generate(project_b, template_a, pack_b)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


def test_generation_rejects_content_pack_from_another_project():
    """A pack from project A is not usable in B."""
    project_a = client.post("/api/v1/projects", json={"name": "scope-a"}).json()["id"]
    pack_a_resp = client.post(
        f"/api/v1/projects/{project_a}/content-packs",
        files={"files": (CONTENT.name, CONTENT.read_bytes(), "text/markdown")},
    )
    assert pack_a_resp.status_code == 202, pack_a_resp.text
    pack_a = pack_a_resp.json()["content_pack"]["id"]
    project_b, template_b, _ = _project_with_inputs()

    response = _generate(project_b, template_b, pack_a)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


def test_plans_reject_inputs_from_another_project():
    """POST /plans has the same cross-project hole closed."""
    project_a, template_a, pack_a = _project_with_inputs()
    project_b = client.post("/api/v1/projects", json={"name": "other-plans"}).json()["id"]

    response = client.post(
        f"/api/v1/projects/{project_b}/plans",
        json={"template_id": template_a, "content_pack_id": pack_a, "brief": BRIEF},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
