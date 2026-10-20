"""Live check for ADR-058: the chat assistant against a real provider (VK Qwen).

Builds one deck without the model (fast, deterministic), then sends free-form
chat messages through the real assistant endpoint and prints what the
assistant did, the deck revisions and timings. Requires DECKDNA_PROVIDER_*.

    .venv/bin/python scripts/agent_live_check.py [--messages "..." "..."]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DEFAULT_MESSAGES = [
    "Что на этой презентации стоит улучшить перед питчем? Только советы, ничего не меняй.",
    "Перепиши слайд 2 короче и убедительнее, заголовок должен быть выводом.",
    "Добавь после второго слайда слайд про рынок с актуальными цифрами из интернета.",
    "Убери последний добавленный слайд и сделай третий слайд в визуальном стиле.",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--messages", nargs="*", default=DEFAULT_MESSAGES)
    parser.add_argument("--template", default="tests/fixtures/pptx/vk_tech_template.pptx")
    parser.add_argument("--content", default="tests/fixtures/content/poc_article.md")
    parser.add_argument("--out", default="artifacts/agent-live")
    args = parser.parse_args()

    from deckdna.settings import settings

    if settings.mock_provider or not settings.provider_api_key:
        raise SystemExit("Configure a real provider (DECKDNA_PROVIDER_*, mock off)")
    from deckdna.api import app as api
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    a = "/api/v1"
    pid = client.post(f"{a}/projects", json={"name": "agent live"}).json()["id"]
    template = ROOT / args.template
    tid = client.post(f"{a}/projects/{pid}/templates", files={
        "file": (template.name, template.read_bytes(), api._PPTX_MIME)}).json()["id"]
    content = ROOT / args.content
    pack = client.post(f"{a}/projects/{pid}/content-packs", files={
        "files": (content.name, content.read_bytes(), "text/markdown")},
        data={"brief": json.dumps({"language": "ru"})}).json()["content_pack"]["id"]
    started = time.monotonic()
    accepted = client.post(f"{a}/projects/{pid}/generations", json={
        "template_id": tid, "content_pack_id": pack, "variants": [{"strategy": "balanced"}],
        "brief": {"purpose": "Питч сервиса для инвесторов", "audience": "инвесторы",
                  "language": "ru", "target_slide_count": 6}, "use_llm": False}).json()
    gen_id, vid = accepted["generation_id"], accepted["variant_ids"][0]
    while client.get(f"{a}/generations/{gen_id}").json()["state"] not in (
            "completed", "failed", "canceled"):
        time.sleep(.5)
    print(f"deck built in {time.monotonic() - started:.1f}s, slides:",
          [s.title for s in api.STORE.slides[vid]])
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    report = []
    for text in args.messages:
        t0 = time.monotonic()
        sent = client.post(f"{a}/projects/{pid}/agent/messages",
                           json={"text": text, "variant_id": vid})
        sent.raise_for_status()
        job_id = sent.json()["job_id"]
        while api.STORE.jobs[job_id].state not in api._TERMINAL_JOB_STATES:
            time.sleep(.3)
        reply = client.get(f"{a}/projects/{pid}/agent/thread").json()["items"][-1]
        run = api.STORE.runs[gen_id]
        row = {"message": text, "seconds": round(time.monotonic() - t0, 1),
               "state": api.STORE.jobs[job_id].state.value, "reply": reply["text"],
               "question": reply.get("question"), "actions": reply["actions"],
               "revision": run.deck_revision.get(vid, 1),
               "slides": [s.title for s in api.STORE.slides[vid]],
               "events": [e["event"] for e in api.STORE.job_events.get(job_id, [])]}
        report.append(row)
        print(json.dumps(row, ensure_ascii=False, indent=1))
    run = api.STORE.runs[gen_id]
    deck = api._current_deck(run, vid)
    (out / "deck.pptx").write_bytes(deck.data)
    pdf = api._ensure_pdf(run, vid)
    (out / "deck.pdf").write_bytes(pdf.data)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print("saved", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
