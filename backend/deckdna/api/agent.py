"""Чат с помощником (ADR-058): сообщения, журнал действий, правки колоды.

``POST /projects/{id}/agent/messages`` принимает сообщение и сразу отвечает
202 с job: цикл помощника (``assistant/agent.py``) идёт в фоне, события —
в журнале job (SSE ``/jobs/{id}/events``). Все правки собранной колоды
идут через ``regenerate_slide`` и структурные операции ``slide_order``;
каждая даёт новую ревизию с аудитом и превью, как правка из очереди."""

from __future__ import annotations

import asyncio
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from deckdna.assistant.agent import AgentAction, run_agent, slide_number_in
from deckdna.contracts.deck_plan import (
    ContentUnit,
    DeckPlan,
    DesiredVisual,
    Kind,
    Origin,
    Purpose,
    SlidePlan,
)
from deckdna.errors import DeckDNAError

router = APIRouter(prefix="/api/v1", tags=["assistant"])
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="deckdna-agent")
_TEXT_KINDS = {Kind.bullet, Kind.paragraph, Kind.number, Kind.quote, Kind.label}
_VISUAL_KINDS = {Kind.chart, Kind.table, Kind.diagram}
_REMOVE_VISUAL_RE = re.compile(
    r"(?:убер\w*|удал\w*|скро\w*|не\s+нуж\w*|без)\s+(?:этого\s+|этот\s+)?"
    r"(?:график\w*|диаграмм\w*|таблиц\w*|схем\w*|чарт\w*)|"
    r"остав\w*\s+только\s+текст",
    re.I,
)
_NO_CHART_TYPES = {"none", "no_chart", "text", "remove", "delete", "без графика"}
_UNDO_DEPTH = 10
_MAX_THREAD = 200


def _api():
    from deckdna.api import app

    return app


class AgentMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    variant_id: str | None = None
    plan_id: str | None = None
    slide_id: str | None = None
    provider_session_id: str | None = None
    use_llm: bool | None = None


def _thread(project_id: str) -> list[dict[str, Any]]:
    return _api().STORE.agent_threads.setdefault(project_id, [])


def _append(project_id: str, item: dict[str, Any]) -> None:
    api = _api()
    with api._JOB_LOCK:
        thread = _thread(project_id)
        thread.append(item)
        del thread[:-_MAX_THREAD]


def _texts(node: Any, out: list[str]) -> list[str]:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            if not (key == "id" or key.endswith(("_id", "_ids", "_ref", "sha256", "url"))):
                _texts(value, out)
    elif isinstance(node, list):
        for value in node:
            _texts(value, out)
    return out


def _unit_texts(slide: SlidePlan, limit: int = 220) -> list[str]:
    return [u.text[:limit] for u in slide.content_units
            if u.text and u.kind != Kind.title][:8]


def _slide_view(n: int, slide: SlidePlan) -> dict[str, Any]:
    chart = slide.chart or {}
    kinds = {u.kind for u in slide.content_units}
    view: dict[str, Any] = {
        "n": n, "purpose": slide.purpose.value, "title": slide.title_intent,
        "text": _unit_texts(slide, 160),
    }
    visual = [k.value for k in (Kind.chart, Kind.table, Kind.diagram, Kind.image) if k in kinds]
    if visual:
        view["visual"] = visual
    if chart.get("type"):
        view["chart"] = {"type": chart.get("type"), "alternatives": [
            a.get("type") for a in chart.get("alternatives") or [] if a.get("type")][:4]}
    if any(u.locked for u in slide.content_units):
        view["author_locked"] = True
    return view


def _brief_view(brief: Any) -> dict[str, Any]:
    data = brief.model_dump(mode="json") if hasattr(brief, "model_dump") else dict(brief or {})
    keys = ("purpose", "audience", "key_message", "call_to_action", "tone",
            "target_slide_count", "slide_count_policy", "language")
    return {k: data.get(k) for k in keys if data.get(k) not in (None, "")}


def _pack_context(pack: Any) -> dict[str, Any]:
    if pack is None:
        return {}
    insights = [
        {
            "fact": ins.fact[:220],
            **({"headline": ins.headline[:140]} if ins.headline else {}),
            **({"meaning": ins.meaning[:160]} if ins.meaning else {}),
            **({"implication": ins.implication[:160]} if ins.implication else {}),
            **({"view_id": ins.view_id} if ins.view_id else {}),
        }
        for ins in (getattr(pack, "insights", None) or [])[:16]
    ]
    charts = [
        {
            "id": c.id,
            "title": c.title or "",
            "type": c.chart_type or "bar",
            "categories": c.categories[:8],
            "unit": c.unit,
        }
        for c in (getattr(pack, "charts", None) or [])[:12]
    ]
    tables = [
        {
            "id": t.id,
            "title": t.title or "",
            "headers": t.headers[:8],
            "rows_count": len(t.rows),
        }
        for t in (getattr(pack, "tables", None) or [])[:8]
    ]
    sections = []
    for sec in (getattr(pack, "sections", None) or [])[:10]:
        snippets = []
        for blk in sec.blocks[:4]:
            if blk.text:
                snippets.append(blk.text[:180])
            elif blk.items:
                snippets.extend(it[:140] for it in blk.items[:3])
        if sec.heading or snippets:
            sections.append({"heading": sec.heading[:120], "snippets": snippets[:4]})
    ctx: dict[str, Any] = {}
    if insights:
        ctx["insights"] = insights
    if charts:
        ctx["available_charts"] = charts
    if tables:
        ctx["available_tables"] = tables
    if sections:
        ctx["material_sections"] = sections
    return ctx


def _renumber(plan: DeckPlan, slides: list[SlidePlan]) -> DeckPlan:
    from deckdna.assistant.plan_edit import _renumber as renumber

    return renumber(plan, slides)


def _new_slide_id(plan: DeckPlan) -> str:
    taken = {s.id for s in plan.slides}
    n = len(plan.slides)
    while f"slide-{n}" in taken:
        n += 1
    return f"slide-{n}"


class _Base:
    """Общее: факты ресёрча, проверка чисел, сборка юнитов текста."""

    mode = "brief"

    def __init__(self, project_id: str, gateway: Any, history: list[dict[str, Any]]):
        self.project_id = project_id
        self.gateway = gateway
        self.history = history
        self.facts: list[dict[str, Any]] = []
        self.revision: int | None = None

    def capabilities(self) -> dict[str, Any]:
        from deckdna.planning.image_brief import can_generate
        from deckdna.research.config import load_research_config

        try:
            web = bool(load_research_config().web.available)
        except Exception:  # noqa: BLE001 — нет конфигурации — нет поиска
            web = False
        return {"mode": self.mode, "web_search": web and self.gateway is not None,
                "image_generation": self.mode == "deck" and can_generate(self.gateway)}

    def _corpus_parts(self) -> list[str]:
        return [*(str(m.get("text") or "") for m in self.history if m.get("role") == "user"),
                *(f["text"] for f in self.facts)]

    def grounding_corpus(self) -> str:
        return "\n".join(self._corpus_parts())

    def _check_numbers(self, texts: list[str]) -> None:
        from deckdna.assistant.agent import ungrounded_numbers

        missing = ungrounded_numbers(texts, self.grounding_corpus())
        if missing:
            raise DeckDNAError(
                "invalid_input",
                "numbers not found in the author's material or research: "
                + ", ".join(missing[:5]) + ". Use web_research or drop them.",
                http_status=422,
            )

    def _body_units(self, action: AgentAction, slide: SlidePlan | None) -> list[ContentUnit]:
        items = [i.strip() for i in action.items if i and i.strip()]
        if not items and action.text:
            items = [action.text.strip()]
        if not items:
            raise DeckDNAError("invalid_input", "items are empty", http_status=422)
        self._check_numbers([*items, action.title or ""])
        evidence = (slide.evidence_ids[:1] if slide else None) or ["model:assistant"]
        units = []
        for item in items:
            fact = next((f for f in self.facts if f["text"] in item or item in f["text"]), None)
            if action.source == "web" or fact is not None:
                origin = Origin.web
                ev = [fact["evidence_id"]] if fact else evidence
            elif action.source == "model":
                origin, ev = Origin.model, ["model:assistant"]
            else:
                origin, ev = Origin.user, evidence
            units.append(ContentUnit(role="bullet", kind=Kind.bullet, text=item[:400],
                                     origin=origin, evidence_ids=ev,
                                     source_id=fact.get("source_id") if fact else None))
        return units

    def _title_unit(self, text: str, slide: SlidePlan | None) -> ContentUnit:
        authored = any(text in str(m.get("text") or "") for m in self.history
                       if m.get("role") == "user")
        return ContentUnit(role="title", kind=Kind.title, text=text,
                           origin=Origin.user if authored else Origin.model,
                           locked=True if authored else None,
                           evidence_ids=(slide.evidence_ids[:1] if slide else None))

    def _wants_visual_removed(self) -> bool:
        last_user = next(
            (str(m.get("text") or "") for m in reversed(self.history) if m.get("role") == "user"),
            "",
        )
        return bool(_REMOVE_VISUAL_RE.search(last_user))

    def _rewritten(self, slide: SlidePlan, action: AgentAction) -> SlidePlan:
        body = self._body_units(action, slide)
        title = action.title.strip() if action.title and action.title.strip() else None
        drop_visual = self._wants_visual_removed()
        keep = [
            u
            for u in slide.content_units
            if u.kind not in _TEXT_KINDS
            and not (title and u.kind == Kind.title)
            and not (drop_visual and u.kind in _VISUAL_KINDS)
        ]
        if title:
            keep.insert(0, self._title_unit(title, slide))
        updates: dict[str, Any] = {
            "content_units": [*keep, *body],
            **({"title_intent": title} if title else {}),
            "key_message": body[0].text or slide.key_message,
        }
        if drop_visual:
            updates["desired_visual"] = DesiredVisual.none
            updates["chart"] = None
        return slide.model_copy(update=updates)

    def _new_slide(self, plan: DeckPlan, action: AgentAction,
                   anchor: SlidePlan | None) -> SlidePlan:
        title = (action.title or "").strip()
        if not title:
            raise DeckDNAError("invalid_input", "add_slide needs a title", http_status=422)
        base = anchor or next(s for s in plan.slides if s.purpose not in (
            Purpose.title, Purpose.thank_you))
        body = self._body_units(action, base)
        return SlidePlan(
            id=_new_slide_id(plan), index=0, purpose=Purpose.overview, title_intent=title,
            key_message=body[0].text or title,
            evidence_ids=sorted({e for u in body for e in (u.evidence_ids or [])}) or
            base.evidence_ids[:1],
            content_units=[self._title_unit(title, base), *body],
            desired_visual=DesiredVisual.none, density_budget=base.density_budget,
        )

    async def _research(self, action: AgentAction, plan: DeckPlan, pack_id: str,
                        slide: SlidePlan | None) -> tuple[dict[str, Any], str | None]:
        from deckdna.research.config import load_research_config
        from deckdna.research.slide import research_slide

        api = _api()
        rc = load_research_config()
        if not rc.web.available or self.gateway is None:
            return {"ok": False, "error": "web search is not available now"}, None
        target = slide or next((s for s in plan.slides if s.purpose not in (
            Purpose.title, Purpose.thank_you, Purpose.agenda)), plan.slides[0])
        brief = plan.brief
        if action.query:
            brief = brief.model_copy(update={"purpose": action.query})
        pack = api.STORE.content_packs[pack_id]
        found = await research_slide(plan, target, pack, brief, self.gateway, rc, focus="facts")
        web = found.web
        sources = [{"id": s.id, "title": s.title, "url": s.url, "publisher": s.publisher}
                   for s in (web.sources if web else [])][:6]
        if not found.facts:
            return {"ok": True, "summary": "ничего проверяемого не нашёл",
                    "degraded": found.degraded or (web.degraded if web else None),
                    "facts": [], "sources": sources}, None
        new_pack = api._register_filled_pack(pack_id, found.pack, self.project_id)
        source_id = sources[0]["id"] if sources else None
        added = []
        for text, section_id, block in found.facts[:8]:
            fact = {"text": text, "evidence_id": f"{new_pack}:{section_id}:b{block}",
                    "source_id": source_id}
            self.facts.append(fact)
            added.append(text)
        return {"ok": True, "summary": f"нашёл фактов: {len(added)}", "facts": added,
                "sources": sources}, new_pack


class BriefWorkspace(_Base):
    """Нет ни плана, ни колоды: можно обсуждать и менять задачу."""

    def overview(self) -> dict[str, Any]:
        api = _api()
        brief = api.STORE.project_briefs.get(self.project_id, {})
        project = api.STORE.projects.get(self.project_id)
        pack = (
            api.STORE.content_packs.get(project.content_pack_id)
            if project and project.content_pack_id
            else None
        )
        return {"stage": "brief", "brief": _brief_view(brief), "slides": [],
                "context": _pack_context(pack)}

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        if action.tool == "update_brief":
            return _update_brief(self.project_id, action)
        return {"ok": False, "error": "no plan or deck is open yet"}


def _update_brief(project_id: str, action: AgentAction) -> dict[str, Any]:
    from deckdna.api.dependencies import save_brief

    if not action.field or not action.value:
        return {"ok": False, "error": "field and value are required"}
    saved = save_brief(project_id, {action.field: action.value.strip()})
    return {"ok": True, "summary": f"{action.field} обновлено",
            "invalidated": saved.get("invalidated", [])}


class PlanWorkspace(_Base):
    """План до сборки: правки карточек плана."""

    mode = "plan"

    def __init__(self, project_id, gateway, history, plan_id: str):
        super().__init__(project_id, gateway, history)
        self.plan_id = plan_id

    def _state(self):
        api = _api()
        plan_set = api._require(api.STORE.plans, self.plan_id, "plan set")
        entry = api._plan_entry(plan_set, None)
        return plan_set, entry, entry.deck_plan

    def overview(self) -> dict[str, Any]:
        api = _api()
        plan_set, _, plan = self._state()
        pack = api.STORE.content_packs.get(plan_set.content_pack_id)
        return {"stage": "plan", "brief": _brief_view(plan.brief),
                "slides": [_slide_view(i + 1, s) for i, s in enumerate(plan.slides)],
                "context": _pack_context(pack)}

    def grounding_corpus(self) -> str:
        api = _api()
        plan_set, _, plan = self._state()
        pack = api.STORE.content_packs.get(plan_set.content_pack_id)
        parts = _texts(plan.model_dump(mode="json"), [])
        if pack is not None:
            parts = _texts(pack.model_dump(mode="json"), parts)
        return "\n".join([*parts, *self._corpus_parts()])

    def _slide(self, plan: DeckPlan, n: int | None) -> SlidePlan:
        if n is None or not 1 <= n <= len(plan.slides):
            raise DeckDNAError("invalid_input", f"there is no slide {n}", http_status=422)
        return plan.slides[n - 1]

    def _store(self, plan_set, entry, plan, pack_id=None) -> dict[str, Any]:
        api = _api()
        undo = api.STORE.agent_undo.setdefault(self.plan_id, [])
        undo.append({"plan_set": plan_set})
        del undo[:-_UNDO_DEPTH]
        api._store_plan(plan_set, entry, plan, pack_id)
        return {"ok": True}

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        from deckdna.assistant.plan_edit import apply_action, change_chart

        api = _api()
        plan_set, entry, plan = self._state()
        pack = api.STORE.content_packs[plan_set.content_pack_id]
        tool = action.tool
        if tool == "update_brief":
            return _update_brief(self.project_id, action)
        if tool == "undo":
            stack = api.STORE.agent_undo.get(self.plan_id) or []
            if not stack:
                return {"ok": False, "error": "nothing to undo"}
            api.STORE.plans[self.plan_id] = stack.pop()["plan_set"]
            return {"ok": True, "summary": "вернул план к прошлой версии"}
        if tool == "view_slide":
            slide = self._slide(plan, action.slide)
            return {"ok": True, "slide": {**_slide_view(action.slide, slide),
                                          "text": _unit_texts(slide, 600),
                                          "key_message": slide.key_message}}
        if tool == "web_research":
            slide = self._slide(plan, action.slide) if action.slide else None
            result, pack_id = await self._research(action, plan, plan_set.content_pack_id, slide)
            if pack_id:
                plan_set = plan_set.model_copy(update={"content_pack_id": pack_id})
                api.STORE.plans[self.plan_id] = plan_set
            return result
        if tool in {"audit", "explain_chart", "other_layout", "larger_chart", "restyle",
                    "generate_image"}:
            return {"ok": False, "error": "available after the deck is built"}
        if tool == "add_slide":
            anchor = self._slide(plan, action.slide) if action.slide else plan.slides[-2]
            at = plan.slides.index(anchor) + 1
            new = self._new_slide(plan, action, anchor)
            slides = list(plan.slides)
            slides.insert(at, new)
            self._store(plan_set, entry, _renumber(plan, slides))
            return {"ok": True, "summary": f"добавил слайд {at + 1}"}
        slide = self._slide(plan, action.slide)
        if tool == "rewrite_slide":
            new = self._rewritten(slide, action)
            new_plan = plan.model_copy(update={"slides": [
                new if s.id == slide.id else s for s in plan.slides]})
            return {**self._store(plan_set, entry, new_plan), "summary": "переписал слайд"}
        if tool == "set_title":
            if not action.text:
                return {"ok": False, "error": "text is required"}
            title = self._title_unit(action.text.strip(), slide)
            new = slide.model_copy(update={"title_intent": title.text, "content_units": [
                title, *[u for u in slide.content_units if u.kind != Kind.title]]})
            new_plan = plan.model_copy(update={"slides": [
                new if s.id == slide.id else s for s in plan.slides]})
            return {**self._store(plan_set, entry, new_plan), "summary": "поменял заголовок"}
        if tool == "move_slide":
            if action.to is None or not 1 <= action.to <= len(plan.slides):
                return {"ok": False, "error": "target position is out of range"}
            slides = [s for s in plan.slides if s.id != slide.id]
            slides.insert(action.to - 1, slide)
            self._store(plan_set, entry, _renumber(plan, slides))
            return {"ok": True, "summary": f"переставил на место {action.to}"}
        if tool == "change_chart" and (action.chart_type or "").strip().lower() in _NO_CHART_TYPES:
            tool = "remove_chart"
        if tool == "change_chart":
            card = slide.chart or {}
            kind = action.chart_type or next((a["type"] for a in card.get("alternatives") or []
                                              if a.get("type") != card.get("type")), None)
            if kind is None:
                return {"ok": False, "error": "this slide has no alternative chart"}
            self._store(plan_set, entry, change_chart(plan, slide.id, kind, pack))
            return {"ok": True, "summary": f"график: {kind}"}
        mapping = {"shorter": "shorten", "split": "split", "merge_next": "merge_next",
                   "remove_slide": "remove", "to_chart": "to_chart", "to_diagram": "to_diagram",
                   "remove_chart": "remove_chart"}
        if tool in mapping:
            new_plan, new_pack = apply_action(plan, slide.id, mapping[tool], pack)
            pack_id = (api._register_filled_pack(plan_set.content_pack_id, new_pack,
                                                 self.project_id) if new_pack else None)
            self._store(plan_set, entry, new_plan, pack_id)
            return {"ok": True, "summary": "готово"}
        return {"ok": False, "error": f"unsupported here: {tool}"}


class VariantWorkspace(_Base):
    """Собранная колода: каждая правка — новая ревизия с аудитом и превью."""

    mode = "deck"

    def __init__(self, project_id, gateway, history, variant_id: str, job_id: str):
        super().__init__(project_id, gateway, history)
        self.variant_id = variant_id
        self.job_id = job_id

    def _state(self):
        api = _api()
        run, variant = api._variant_or_404(self.variant_id)
        plan = run.variant_plans.get(self.variant_id)
        if plan is None:
            raise DeckDNAError("not_implemented", "this deck has no plan to edit from")
        return run, variant, plan

    def _cards(self) -> list[Any]:
        return list(_api().STORE.slides.get(self.variant_id, []))

    def overview(self) -> dict[str, Any]:
        api = _api()
        _run, _variant, plan = self._state()
        pack = api._run_pack(_run)
        by_id = {s.id: s for s in plan.slides}
        slides = []
        for card in self._cards():
            source = by_id.get(card.slide_plan_id or "")
            slides.append(_slide_view(card.index, source) if source else
                          {"n": card.index, "title": card.title, "protected": True})
        return {"stage": "deck", "brief": _brief_view(plan.brief), "slides": slides,
                "revision": _run.deck_revision.get(self.variant_id, 1),
                "context": _pack_context(pack)}

    def grounding_corpus(self) -> str:
        api = _api()
        run, _variant, plan = self._state()
        parts = _texts(plan.model_dump(mode="json"), [])
        parts = _texts(api._run_pack(run).model_dump(mode="json"), parts)
        return "\n".join([*parts, *self._corpus_parts()])

    def _target(self, n: int | None):
        _run, _variant, plan = self._state()
        card = next((c for c in self._cards() if c.index == n), None)
        if card is None:
            raise DeckDNAError("invalid_input", f"there is no slide {n}", http_status=422)
        slide = next((s for s in plan.slides if s.id == card.slide_plan_id), None)
        if slide is None:
            raise DeckDNAError("protected_slide", f"slide {n} is a fixed template slide",
                               http_status=422)
        return card, slide

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        api = _api()
        tool = action.tool
        run, variant, plan = self._state()
        if tool == "update_brief":
            return {"ok": False, "error": ("changing the brief of a built deck requires a full "
                                           "rebuild; ask the author to start a new build")}
        if tool == "audit":
            audit_id = api._audit_id_of(self.variant_id)
            issues = api.STORE.issues.get(audit_id, []) if audit_id else []
            rows = [{"slide": (i.slide_index + 1) if i.slide_index is not None else None,
                     "severity": i.severity.value if hasattr(i.severity, "value")
                     else str(i.severity), "message": i.message[:200]}
                    for i in issues if action.slide is None
                    or i.slide_index == action.slide - 1]
            order = {"blocker": 0, "error": 1, "warning": 2, "info": 3}
            rows.sort(key=lambda r: order.get(r["severity"], 4))
            return {"ok": True, "summary": f"проблем: {len(rows)}", "issues": rows[:10]}
        if tool == "undo":
            return await asyncio.to_thread(self._undo)
        if tool == "web_research":
            slide = self._target(action.slide)[1] if action.slide else None
            result, _pack = await self._research(action, plan, api._run_pack(run).id, slide)
            return result
        if tool == "add_slide":
            anchor_card, anchor = (self._target(action.slide) if action.slide else
                                   self._last_content())
            new = self._new_slide(plan, action, anchor)
            return await asyncio.to_thread(self._structure, "add", anchor_card, anchor, new)
        card, slide = self._target(action.slide)
        if tool == "view_slide":
            return {"ok": True, "slide": {**_slide_view(card.index, slide),
                                          "text": _unit_texts(slide, 600),
                                          "key_message": slide.key_message}}
        if tool == "explain_chart":
            data = await api.explain_slide(card.id)
            return {"ok": True, "summary": data.get("assistant_note") or "разбор графика",
                    "explain": {k: data.get(k) for k in ("chosen", "alternatives", "task",
                                                         "profile_summary") if data.get(k)}}
        if tool in {"remove_slide", "move_slide", "split", "merge_next"}:
            return await asyncio.to_thread(self._structure, tool, card, slide, None, action.to)
        if tool == "generate_image":
            return await self._image(card, slide, action)
        edits = {"shorter": "shorter", "other_layout": "other_layout",
                 "larger_chart": "larger_chart"}
        if tool in edits:
            return await asyncio.to_thread(self._rebuild, card, slide, plan, edits[tool])
        if tool == "restyle":
            return await asyncio.to_thread(self._rebuild, card, slide, plan,
                                           f"style:{action.style or 'visual'}")
        if tool == "change_chart" and (action.chart_type or "").strip().lower() in _NO_CHART_TYPES:
            tool = "remove_chart"
        if tool == "change_chart":
            if not any(u.kind in {Kind.chart, Kind.table} for u in slide.content_units):
                from deckdna.assistant.plan_edit import apply_action, change_chart

                pack = api._run_pack(run)
                if action.chart_type:
                    new_plan = change_chart(plan, slide.id, action.chart_type, pack)
                    new_pack = None
                else:
                    new_plan, new_pack = apply_action(plan, slide.id, "to_chart", pack)
                res = await asyncio.to_thread(self._rebuild, card, slide, new_plan, "relayout",
                                              pack=new_pack)
                if res.get("ok"):
                    res["summary"] = "добавил график на слайд"
                return res
            return await asyncio.to_thread(self._rebuild, card, slide, plan, "other_chart",
                                           chart_type=action.chart_type)
        if tool == "rewrite_slide":
            new = self._rewritten(slide, action)
            rebuild_op = (
                "relayout"
                if new.desired_visual == DesiredVisual.none
                and slide.desired_visual != DesiredVisual.none
                else "rebuild"
            )
            return await asyncio.to_thread(self._rebuild, card, slide,
                                           self._replace(plan, new), rebuild_op,
                                           replaced=_TEXT_KINDS | ({Kind.title} if action.title
                                                                    else set()))
        if tool == "set_title":
            if not action.text:
                return {"ok": False, "error": "text is required"}
            title = self._title_unit(action.text.strip(), slide)
            new = slide.model_copy(update={"title_intent": title.text, "content_units": [
                title, *[u for u in slide.content_units if u.kind != Kind.title]]})
            return await asyncio.to_thread(self._rebuild, card, slide, self._replace(plan, new),
                                           "rebuild", replaced={Kind.title})
        if tool in {"to_chart", "to_diagram", "remove_chart"}:
            from deckdna.assistant.plan_edit import apply_action

            new_plan, new_pack = apply_action(plan, slide.id, tool, api._run_pack(run))
            res = await asyncio.to_thread(self._rebuild, card, slide, new_plan, "relayout",
                                          pack=new_pack)
            if tool == "remove_chart" and res.get("ok"):
                res["summary"] = "убрал график со слайда"
            elif tool == "to_chart" and res.get("ok"):
                res["summary"] = "добавил график на слайд"
            elif tool == "to_diagram" and res.get("ok"):
                res["summary"] = "добавил схему на слайд"
            return res
        return {"ok": False, "error": f"unsupported: {tool}"}

    def _last_content(self):
        _run, _variant, plan = self._state()
        cards = [c for c in self._cards() if c.slide_plan_id]
        by_id = {s.id: s for s in plan.slides}
        for card in reversed(cards):
            slide = by_id.get(card.slide_plan_id)
            if slide and slide.purpose not in (Purpose.thank_you, Purpose.cta, Purpose.qa):
                return card, slide
        return cards[-1], by_id[cards[-1].slide_plan_id]

    @staticmethod
    def _replace(plan: DeckPlan, slide: SlidePlan) -> DeckPlan:
        return plan.model_copy(update={"slides": [
            slide if s.id == slide.id else s for s in plan.slides]})

    async def _image(self, card, slide: SlidePlan, action: AgentAction) -> dict[str, Any]:
        from deckdna.planning.image_brief import can_generate, is_blank_image

        if not can_generate(self.gateway):
            return {"ok": False, "error": "image generation is not configured on this server"}
        prompt = (action.prompt or f"{slide.title_intent}. {slide.key_message}").strip()
        prompt += ", flat corporate illustration, no text, no letters"
        data = await self.gateway.image_generate(prompt, size="1024x1024")
        if not data or is_blank_image(data):
            return {"ok": False, "error": "the image came back empty"}
        _run, _variant, plan = self._state()
        ref = f"generated:{slide.id}:{_api()._new_id('img')}"
        new = slide.model_copy(update={
            "content_units": [*[u for u in slide.content_units
                                if not (u.kind == Kind.image and (u.asset_ref or "")
                                        .startswith("generated:"))],
                              ContentUnit(role="body", kind=Kind.image, asset_ref=ref)],
            "desired_visual": DesiredVisual.image,
        })
        return await asyncio.to_thread(self._rebuild, card, slide, self._replace(plan, new),
                                       "relayout", images={ref: data})

    # --- синхронные правки под замком варианта ---

    def _guard(self, run, variant) -> None:
        api = _api()
        from deckdna.api.dependencies import is_current

        if not is_current(run.detail.project_id, run.detail.created_at):
            raise DeckDNAError("state_conflict", "deck belongs to an older brief", http_status=409)
        base = api.STORE.jobs.get(run.detail.job_id)
        if base is None or base.state in {api.JobState.failed, api.JobState.canceled} or \
                variant.status in {api.JobState.failed, api.JobState.canceled}:
            raise DeckDNAError("state_conflict", "deck is not editable", http_status=409)
        if not variant.deck_artifact_id or not api.STORE.slides.get(self.variant_id):
            raise DeckDNAError("state_conflict", "deck is still being built", http_status=409)
        if any(i["state"] in {"queued", "running"} for i in run.changes.get(self.variant_id, [])):
            raise DeckDNAError("state_conflict", "other slide changes are still applying",
                               http_status=409)

    def _images(self, run) -> dict[str, bytes]:
        api = _api()
        refs = (run.variant_reports.get(self.variant_id) or {}).get("agent_images") or {}
        return {ref: api.STORE.artifacts[aid].data for ref, aid in refs.items()
                if aid in api.STORE.artifacts}

    def _compose(self, run) -> list[dict[str, Any]]:
        info = run.variant_reports.setdefault(self.variant_id, {})
        return (info.setdefault("compose_report", {})).setdefault("slides", [])

    def _template(self, run, tmp: str) -> tuple[Path, Any]:
        api = _api()
        tpl_artifact = api.STORE.artifacts[api.STORE.templates[run.detail.template_id].artifact_id]
        path = Path(tmp) / tpl_artifact.filename
        data = api._run_template_bytes(run)
        path.write_bytes(data)
        dna = api.STORE.design_dna.get(run.detail.template_id)
        if run.brand and run.brand.get("applied") and dna is not None:
            dna = api.dna_builder.restyle_design_dna(dna, data)
        return path, dna

    def _rebuild(self, card, slide: SlidePlan, plan: DeckPlan, action: str, *,
                 chart_type: str | None = None, pack=None, images: dict[str, bytes] | None = None,
                 replaced: set[Kind] | None = None) -> dict[str, Any]:
        from deckdna.generation.regenerate import regenerate_slide
        from deckdna.planning.locked_copy import validate_locked_transition

        api = _api()
        with api._variant_lock(self.variant_id):
            run, variant, old_plan = self._state()
            self._guard(run, variant)
            validate_locked_transition(old_plan, plan, replacement_slide=slide.id,
                                       replaced_kinds=frozenset(replaced or ()))
            run_pack = pack or api._run_pack(run)
            compose = self._compose(run)
            current = next(((c.get("exemplar") or {}).get("slide_part") for c in compose
                            if c.get("id") == slide.id), None)
            all_images = {**self._images(run), **(images or {})}
            deck = api._current_deck(run, self.variant_id).data
            with tempfile.TemporaryDirectory(prefix="deckdna-agent-") as tmp:
                tpl, dna = self._template(run, tmp)
                result = regenerate_slide(
                    tpl, deck, plan.model_copy(deep=True), run_pack, slide.id, card.index,
                    action, strategy=variant.strategy, chart_type=chart_type,
                    current_exemplar=current, design_dna=dna, generated_images=all_images,
                )
            new_compose = [dict(c) for c in compose]
            edited = next(s for s in result.plan.slides if s.id == slide.id)
            for c in new_compose:
                if c.get("id") == slide.id:
                    c["exemplar"] = {**(c.get("exemplar") or {}), "slide_part": result.exemplar}
                    c["title"] = edited.title_intent
            cards = [c.model_copy(update={"title": edited.title_intent,
                                          "preview_artifact_id": None})
                     if c.id == card.id else c for c in self._cards()]
            pack_id = None
            if pack is not None:
                pack_id = api._register_filled_pack(api._run_pack(run).id, result.pack,
                                                    self.project_id)
            return self._commit(run, variant, result.deck, result.plan, cards, new_compose,
                                changed=[card.index], images=images, pack_id=pack_id,
                                summary=_SUMMARY.get(action.split(":")[0], "обновил слайд"))

    def _structure(self, op: str, card, slide: SlidePlan, new: SlidePlan | None = None,
                   to: int | None = None) -> dict[str, Any]:
        from deckdna.assistant.plan_edit import apply_action
        from deckdna.generation.regenerate import regenerate_slide
        from deckdna.pptx.composing.slide_order import (
            insert_blank_slide,
            move_slide,
            remove_slide,
        )

        api = _api()
        with api._variant_lock(self.variant_id):
            run, variant, plan = self._state()
            self._guard(run, variant)
            deck = api._current_deck(run, self.variant_id).data
            cards = self._cards()
            compose = [dict(c) for c in self._compose(run)]
            if len(compose) != len(cards):
                compose = [{"id": c.slide_plan_id, "title": c.title} for c in cards]
            pos = card.index
            pack = api._run_pack(run)
            fresh: list[tuple[str, int]] = []  # (plan id, physical position) to compose
            if op == "remove_slide":
                plan, _ = apply_action(plan, slide.id, "remove", pack)
                deck = remove_slide(deck, pos)
                del cards[pos - 1]
                del compose[pos - 1]
                summary = f"убрал слайд {pos}"
            elif op == "move_slide":
                if to is None or not 1 <= to <= len(cards):
                    raise DeckDNAError("invalid_input", "target position is out of range",
                                       http_status=422)
                deck = move_slide(deck, pos, to)
                cards.insert(to - 1, cards.pop(pos - 1))
                compose.insert(to - 1, compose.pop(pos - 1))
                order = [c.slide_plan_id for c in cards if c.slide_plan_id]
                by_id = {s.id: s for s in plan.slides}
                plan = _renumber(plan, [by_id[i] for i in order if i in by_id])
                summary = f"переставил слайд {pos} на место {to}"
            elif op == "merge_next":
                nxt = cards[pos] if pos < len(cards) else None
                plan, _ = apply_action(plan, slide.id, "merge_next", pack)
                if nxt is None or nxt.slide_plan_id in {s.id for s in plan.slides}:
                    raise DeckDNAError("invalid_input", "no content slide after this one",
                                       http_status=422)
                deck = remove_slide(deck, pos + 1)
                del cards[pos]
                del compose[pos]
                fresh.append((slide.id, pos))
                summary = f"объединил слайды {pos} и {pos + 1}"
            elif op in {"split", "add"}:
                if op == "split":
                    plan, _ = apply_action(plan, slide.id, "split", pack)
                    i = next(k for k, s in enumerate(plan.slides) if s.id == slide.id)
                    new = plan.slides[i + 1]
                    fresh.append((slide.id, pos))
                    summary = f"разбил слайд {pos} на два"
                else:
                    slides = list(plan.slides)
                    slides.insert(slides.index(slide) + 1, new)
                    plan = _renumber(plan, slides)
                    new = next(s for s in plan.slides if s.id == new.id)
                    summary = f"добавил слайд {pos + 1}"
                deck = insert_blank_slide(deck, pos + 1, layout_of=pos)
                cards.insert(pos, api.SlideInfo(
                    id=api._new_id("sld"), variant_id=self.variant_id, index=pos + 1,
                    slide_plan_id=new.id, purpose=new.purpose.value, title=new.title_intent))
                compose.insert(pos, {"id": new.id, "title": new.title_intent,
                                     "purpose": new.purpose.value})
                fresh.append((new.id, pos + 1))
            else:
                raise DeckDNAError("invalid_input", f"unknown structure op: {op}")
            images = self._images(run)
            if fresh:
                with tempfile.TemporaryDirectory(prefix="deckdna-agent-") as tmp:
                    tpl, dna = self._template(run, tmp)
                    for plan_id, position in fresh:
                        current = next(((c.get("exemplar") or {}).get("slide_part")
                                        for c in compose if c.get("id") == plan_id), None)
                        result = regenerate_slide(
                            tpl, deck, plan, pack, plan_id, position,
                            "rebuild" if current else "relayout", strategy=variant.strategy,
                            current_exemplar=current, design_dna=dna, generated_images=images,
                        )
                        deck, plan = result.deck, result.plan
                        compose[position - 1]["exemplar"] = {"slide_part": result.exemplar}
            by_id = {s.id: s for s in plan.slides}
            cards = [c.model_copy(update={
                "index": k + 1,
                "preview_artifact_id": None if (c.slide_plan_id in {p for p, _ in fresh})
                else c.preview_artifact_id,
                **({"title": by_id[c.slide_plan_id].title_intent}
                   if c.slide_plan_id in by_id else {}),
            }) for k, c in enumerate(cards)]
            return self._commit(run, variant, deck, plan, cards, compose,
                                changed=[p for _, p in fresh], summary=summary, structural=True)

    def _snapshot(self, run, variant) -> dict[str, Any]:
        info = run.variant_reports.get(self.variant_id) or {}
        return {
            "revision": run.deck_revision.get(self.variant_id, 1),
            "deck_artifact_id": variant.deck_artifact_id,
            "plan": run.variant_plans[self.variant_id].model_dump(mode="json"),
            "cards": [c.model_dump(mode="json") for c in self._cards()],
            "compose": [dict(c) for c in (info.get("compose_report") or {}).get("slides") or []],
            "images": dict(info.get("agent_images") or {}),
        }

    def _undo(self) -> dict[str, Any]:
        api = _api()
        with api._variant_lock(self.variant_id):
            run, variant, _plan = self._state()
            self._guard(run, variant)
            stack = api.STORE.agent_undo.get(self.variant_id) or []
            if not stack:
                return {"ok": False, "error": "nothing to undo"}
            snap = stack.pop()
            deck = api.STORE.artifacts[snap["deck_artifact_id"]].data
            plan = DeckPlan.model_validate(snap["plan"])
            cards = [api.SlideInfo.model_validate(c) for c in snap["cards"]]
            run.variant_reports.setdefault(self.variant_id, {})["agent_images"] = snap["images"]
            return self._commit(run, variant, deck, plan, cards, snap["compose"],
                                changed=[c.index for c in cards], summary=(
                                    f"вернул колоду к ревизии {snap['revision']}"),
                                structural=True, record_undo=False)

    def _commit(self, run, variant, deck: bytes, plan: DeckPlan, cards: list[Any],
                compose: list[dict[str, Any]], *, changed: list[int], summary: str,
                images: dict[str, bytes] | None = None, pack_id: str | None = None,
                structural: bool = False, record_undo: bool = True) -> dict[str, Any]:
        from deckdna.planning.locked_copy import validate_locked_deck
        from deckdna.planning.request_constraints import validate_deck_count

        api = _api()
        vid = self.variant_id
        validate_deck_count(plan.brief, deck)
        if run.request is not None:
            validate_deck_count(run.request.brief, deck)
        validate_locked_deck(plan, deck, compose if len(compose) == len(cards) else None)
        new_revision = run.deck_revision.get(vid, 1) + 1
        audit_id = api._audit_id_of(vid)
        with tempfile.TemporaryDirectory(prefix="deckdna-agent-audit-") as tmp:
            path = Path(tmp) / "deck.pptx"
            path.write_bytes(deck)
            fresh_raw = api.audit_deck(path, deck_revision=new_revision,
                                       audit_run_id=audit_id or api._new_id("audit"))
        fresh = [api._contract_issue(raw.to_dict()) for raw in fresh_raw]
        with api._JOB_LOCK:
            if record_undo:
                undo = api.STORE.agent_undo.setdefault(vid, [])
                undo.append(self._snapshot(run, variant))
                del undo[:-_UNDO_DEPTH]
            artifact = api._store_artifact(
                deck, type="deck.pptx.revision", mime_type=api._PPTX_MIME,
                filename=f"{variant.strategy}_rev{new_revision}.pptx",
                project_id=run.detail.project_id)
            info = run.variant_reports.setdefault(vid, {})
            if images:
                refs = info.setdefault("agent_images", {})
                for ref, data in images.items():
                    refs[ref] = api._store_artifact(
                        data, type="image.generated", mime_type="image/png",
                        filename=f"{ref.replace(':', '_')}.png",
                        project_id=run.detail.project_id).id
            info.setdefault("compose_report", {})["slides"] = compose
            if pack_id:
                info["agent_pack_id"] = pack_id
            if audit_id is not None:
                api.STORE.issues[audit_id] = fresh
                audit = api.STORE.audits[audit_id]
                by_severity, by_type = api._issue_summaries(fresh)
                audit.deck_revision = new_revision
                audit.contextual_status = "pending"
                audit.issue_count = len(fresh)
                audit.summary_by_severity = by_severity
                audit.summary_by_type = by_type
            run.deck_revision[vid] = new_revision
            run.variant_artifacts[vid] = {"pptx": artifact.id}
            run.variant_plans[vid] = plan
            run.detail = run.detail.model_copy(update={"deck_plan": plan})
            api.STORE.slides[vid] = [c.model_copy(update={"revision": new_revision})
                                     for c in cards]
            idx = next(i for i, v in enumerate(run.detail.variants) if v.id == vid)
            run.detail.variants[idx] = run.detail.variants[idx].model_copy(update={
                "deck_artifact_id": artifact.id, "montage_artifact_id": None,
                "export_ids": [], "contextual_issues": None,
                "metrics": run.detail.variants[idx].metrics.model_copy(
                    update={"issues_total": len(fresh)}),
            })
        api._ensure_pdf(run, vid)
        api._ensure_passport(run, vid)
        api._ensure_previews(run, vid)
        with api._JOB_LOCK:
            run.slide_drafts[vid] = {}
            for card in api.STORE.slides.get(vid, []):
                event = {"variant_id": vid, "slide_id": card.slide_plan_id,
                         "slide_key": f"{vid}:{card.index}", "index": card.index,
                         "revision": new_revision, "preview_revision": 1,
                         "preview_artifact_id": card.preview_artifact_id,
                         "status": "rendered"}
                run.slide_drafts[vid][card.index] = event
                if structural or card.index in changed:
                    api._journal(self.job_id, "slide.updated", event)
            api._journal(self.job_id, "deck.revision", {
                "variant_id": vid, "revision": new_revision, "deck_artifact_id": artifact.id,
                "slide_count": len(cards), "structural": structural})
        api._persist()
        self.revision = new_revision
        slide_issues = [i for i in fresh if i.slide_index is not None
                        and i.slide_index + 1 in changed and i.severity.value in
                        ("error", "blocker")]
        out = {"ok": True, "summary": summary, "revision": new_revision}
        if slide_issues:
            out["issues"] = [i.message[:160] for i in slide_issues[:4]]
        return out


_SUMMARY = {"shorter": "сократил слайд", "other_layout": "поменял раскладку",
            "larger_chart": "увеличил график", "style": "перестроил слайд в другом стиле",
            "other_chart": "поменял тип графика", "rebuild": "обновил текст слайда",
            "relayout": "перестроил слайд"}


def _selected(workspace: Any, slide_id: str | None, text: str) -> int | None:
    named = slide_number_in(text)
    if named:
        return named
    if not slide_id:
        return None
    if isinstance(workspace, VariantWorkspace):
        card = next((c for c in workspace._cards()
                     if slide_id in (c.id, c.slide_plan_id)), None)
        return card.index if card else None
    if isinstance(workspace, PlanWorkspace):
        _, _, plan = workspace._state()
        return next((i + 1 for i, s in enumerate(plan.slides) if s.id == slide_id), None)
    return None


def _finish(job_id: str, state: str, data: dict[str, Any]) -> None:
    api = _api()
    fields: dict[str, Any] = {"state": api.JobState(state), "finished_at": api._utcnow(),
                              "progress": 1.0}
    if state == "failed":
        fields["error"] = api.JobError(**data["error"])
    api._set_job(job_id, **fields)
    api._journal(job_id, "job.completed" if state == "completed" else
                 "job.failed" if state == "failed" else "job.state_changed",
                 {"state": state, **data})


def _run_message(project_id: str, job_id: str, body: AgentMessageRequest,
                 history: list[dict[str, Any]], user_id: str) -> None:
    api = _api()
    api._set_job(job_id, state=api.JobState.running, started_at=api._utcnow())
    api._journal(job_id, "job.state_changed", {"state": "running"})

    def emit(event: str, data: dict[str, Any]) -> None:
        api._journal(job_id, event, data)

    def canceled() -> bool:
        return api.STORE.jobs[job_id].state == api.JobState.canceled

    async def main():
        gateway = None
        if body.use_llm is not False and (body.provider_session_id
                                          or api._server_provider_ready()):
            gateway = api._gateway_for(body, project_id)
        try:
            if body.variant_id:
                ws: Any = VariantWorkspace(project_id, gateway, history, body.variant_id, job_id)
            elif body.plan_id:
                ws = PlanWorkspace(project_id, gateway, history, body.plan_id)
            else:
                ws = BriefWorkspace(project_id, gateway, history)
            async with api._maybe_gateway_session(gateway):
                result = await run_agent(
                    body.text, workspace=ws, gateway=gateway, history=history[:-1],
                    selected_slide=_selected(ws, body.slide_id, body.text), emit=emit,
                    canceled=canceled)
            return result, ws.revision
        finally:
            close = getattr(gateway, "aclose", None)
            if close is not None:
                await close()

    try:
        result, revision = asyncio.run(main())
        message = {"id": api._new_id("msg"), "role": "assistant", "text": result.reply,
                   "question": result.question.model_dump() if result.question else None,
                   "actions": result.actions, "mode": result.mode, "job_id": job_id,
                   "reply_to": user_id, "created_at": api._utcnow().isoformat(),
                   "variant_id": body.variant_id, "plan_id": body.plan_id,
                   "revision": revision}
        _append(project_id, message)
        api._journal(job_id, "agent.message", {"message": message})
        _finish(job_id, "completed", {"message_id": message["id"], "revision": revision})
    except Exception as exc:  # noqa: BLE001 — каждое сообщение получает честный ответ
        if canceled():
            text, state = "Остановил. Изменения, которые я уже успел внести, сохранены.", \
                "canceled"
        else:
            text, state = ("Не смог ответить: языковая модель сейчас недоступна. "
                           "Попробуйте ещё раз чуть позже." if isinstance(exc, DeckDNAError)
                           and exc.http_status == 503 else
                           "Что-то пошло не так, колода не изменилась. Попробуйте ещё раз."), \
                "failed"
        message = {"id": api._new_id("msg"), "role": "assistant", "text": text,
                   "question": None, "actions": [], "mode": "error", "job_id": job_id,
                   "reply_to": user_id, "created_at": api._utcnow().isoformat(),
                   "variant_id": body.variant_id, "plan_id": body.plan_id}
        _append(project_id, message)
        api._journal(job_id, "agent.message", {"message": message})
        if state == "canceled":
            api._journal(job_id, "job.state_changed", {"state": "canceled"})
        else:
            code = exc.code if isinstance(exc, DeckDNAError) else "internal_error"
            _finish(job_id, "failed", {"error": {"code": code, "message": text},
                                       "message_id": message["id"]})
    finally:
        api._persist()


@router.post("/projects/{project_id}/agent/messages", status_code=202)
async def post_agent_message(project_id: str, body: AgentMessageRequest) -> dict[str, Any]:
    api = _api()
    api._require(api.STORE.projects, project_id, "project")
    if body.provider_session_id:
        api._require_session(body.provider_session_id, project_id)
    if body.variant_id:
        run, _ = api._variant_or_404(body.variant_id)
        if run.detail.project_id != project_id:
            raise DeckDNAError("not_found", "variant not found in project")
    if body.plan_id:
        plan = api._require(api.STORE.plans, body.plan_id, "plan")
        if plan.project_id != project_id:
            raise DeckDNAError("not_found", "plan not found in project")
    with api._JOB_LOCK:
        busy = next((j for j in api.STORE.jobs.values() if j.project_id == project_id
                     and j.kind == api.JobKind.assistant
                     and j.state in {api.JobState.queued, api.JobState.running}), None)
        if busy is not None:
            raise DeckDNAError("state_conflict", "the assistant is still answering",
                               http_status=409, details={"job_id": busy.id})
        job_id, msg_id = api._new_id("job"), api._new_id("msg")
        user = {"id": msg_id, "role": "user", "text": body.text, "job_id": job_id,
                "created_at": api._utcnow().isoformat(), "variant_id": body.variant_id,
                "plan_id": body.plan_id, "slide_id": body.slide_id}
        thread = _thread(project_id)
        thread.append(user)
        del thread[:-_MAX_THREAD]
        history = [dict(m) for m in thread]
        api.STORE.jobs[job_id] = api.Job(
            id=job_id, kind=api.JobKind.assistant, state=api.JobState.queued,
            stage="assistant", project_id=project_id, created_at=api._utcnow(),
            progress=0.0, result_ids={"message_id": msg_id})
        api._journal(job_id, "job.state_changed", {"state": "queued"})
    api._persist()
    _POOL.submit(_run_message, project_id, job_id, body, history, msg_id)
    return {"job_id": job_id, "message_id": msg_id,
            "events_url": f"/api/v1/jobs/{job_id}/events",
            "thread_url": f"/api/v1/projects/{project_id}/agent/thread"}


@router.get("/projects/{project_id}/agent/thread")
async def get_agent_thread(project_id: str) -> dict[str, Any]:
    api = _api()
    api._require(api.STORE.projects, project_id, "project")
    with api._JOB_LOCK:
        items = [dict(m) for m in api.STORE.agent_threads.get(project_id, [])]
        active = next((j.id for j in api.STORE.jobs.values() if j.project_id == project_id
                       and j.kind == api.JobKind.assistant
                       and j.state in {api.JobState.queued, api.JobState.running}), None)
    return {"items": items, "active_job_id": active}


@router.delete("/projects/{project_id}/agent/thread", status_code=204)
async def clear_agent_thread(project_id: str) -> None:
    api = _api()
    api._require(api.STORE.projects, project_id, "project")
    with api._JOB_LOCK:
        api.STORE.agent_threads.pop(project_id, None)
    api._persist()


@router.post("/projects/{project_id}/agent/jobs/{job_id}/cancel")
async def cancel_agent_message(project_id: str, job_id: str) -> dict[str, Any]:
    api = _api()
    job = api._require(api.STORE.jobs, job_id, "job")
    if job.project_id != project_id or job.kind != api.JobKind.assistant:
        raise DeckDNAError("not_found", "assistant job not found")
    if job.state in api._TERMINAL_JOB_STATES:
        raise DeckDNAError("state_conflict", "the answer is already finished", http_status=409)
    api._set_job(job_id, state=api.JobState.canceled, finished_at=api._utcnow())
    return {"job_id": job_id, "state": "canceled"}


__all__ = ["BriefWorkspace", "PlanWorkspace", "VariantWorkspace", "router"]
