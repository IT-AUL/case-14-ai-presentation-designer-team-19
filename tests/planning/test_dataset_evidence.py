"""Computed facts must reach the contextual auditor through real evidence IDs."""
import json
from pathlib import Path

import pytest
from deckdna.analysis.pipeline import analyze_dataset
from deckdna.audit.contextual import _evidence_excerpt
from deckdna.contracts.deck_plan import Brief, Purpose
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.data_story import plan_data_deck
from deckdna.planning.evidence import build_evidence_graph

ROOT = Path(__file__).parents[2]


def _input():
    pack = parse_file(ROOT/'examples/stage2/regions.xlsx', artifact_id='uploaded-regions')
    plan = plan_data_deck(pack, Brief(purpose='Продажи по регионам',audience='Руководство',
                                    language='ru',target_slide_count=8,content_fill='shrink'))
    return pack,plan


def test_data_story_facts_and_sources_are_in_contextual_evidence():
    pack,plan = _input()
    graph = build_evidence_graph(pack)
    by_id = {n.id:n for n in graph.nodes}
    computed = {i.id:i for d in pack.datasets for i in analyze_dataset(d).insights}
    for slide in plan.slides:
        if slide.purpose not in (Purpose.data,Purpose.overview):
            continue
        excerpt = _evidence_excerpt(slide.index+1,plan,graph)
        assert excerpt and 'uploaded-regions' in excerpt
        for iid in slide.insight_ids:
            node = by_id[f'{pack.id}:{iid}']
            assert computed[iid].fact in node.text
            assert computed[iid].fact in excerpt
            assert node.source_ref.artifact_id == 'uploaded-regions'
            assert any(e.from_==node.id and e.type.value=='derived_from' for e in graph.edges)
    assert {e.from_ for e in graph.edges}|{e.to for e in graph.edges} <= set(by_id)
    for chart in pack.charts:
        node = by_id[f'{pack.id}:{chart.id}']
        assert json.loads(node.text)['categories'] == chart.categories
        assert json.loads(node.text)['series'][0]['values'] == chart.series[0].values
        target = next(s for s in plan.slides if any(u.chart_ref==chart.id for u in s.content_units))
        assert node.text in _evidence_excerpt(target.index+1,plan,graph)
    import jsonschema
    from deckdna.contracts import to_schema_dict
    jsonschema.validate(to_schema_dict(graph),json.loads(
        (ROOT/'schemas/evidence-graph.schema.json').read_text()))


def test_stored_model_text_is_not_promoted_to_independent_fact_evidence():
    pack,_ = _input()
    poisoned = [i.model_copy(update={'fact':'Invented 999999', 'headline':'Invented headline',
                                   'meaning':'Model interpretation',
                                   'implication':'Unconfirmed recommendation'})
                for i in pack.insights]
    charts = [c.model_copy(update={'series':[s.model_copy(update={
        'values':[999999.0]*len(s.values)}) for s in c.series]}) for c in pack.charts]
    changed = pack.model_copy(update={'insights':poisoned,'charts':charts})
    graph = build_evidence_graph(changed)
    texts = '\n'.join(n.text or '' for n in graph.nodes)
    assert f'{pack.id}:{pack.datasets[0].id}' in {n.id for n in graph.nodes}
    assert not any(t in texts for t in ['Invented 999999','Invented headline',
                                        'Model interpretation','Unconfirmed recommendation'])
    assert '999999' not in texts
    assert graph == build_evidence_graph(changed)


@pytest.mark.parametrize('key_kind', ['pack','graph'])
def test_api_upgrades_saved_legacy_graph_for_contextual_audit(monkeypatch,key_kind):
    from deckdna.api import app as api

    pack,plan = _input()
    graph = build_evidence_graph(pack)
    legacy_nodes = [n for n in graph.nodes if not n.id.startswith(pack.id+':')]
    ids = {n.id for n in legacy_nodes}
    legacy = graph.model_copy(update={'nodes':legacy_nodes,
        'edges':[e for e in graph.edges if e.from_ in ids and e.to in ids]})
    key = pack.id if key_kind=='pack' else graph.id
    monkeypatch.setattr(api.STORE,'content_packs',{pack.id:pack})
    monkeypatch.setattr(api.STORE,'evidence_graphs',{key:legacy})
    plan = plan.model_copy(update={'evidence_graph_id':key})
    resolved = api._evidence_graph_for_plan(plan)
    assert f'{pack.id}:{pack.datasets[0].id}' in {n.id for n in resolved.nodes}
    assert _evidence_excerpt(2,plan,resolved)


def test_authored_revision_pack_keeps_original_insight_identity_grounded():
    pack,plan = _input()
    # Local authored edits fork the pack id while keeping the raw dataset and
    # insight identities. Plan evidence_ids may still cite the parent pack.
    derived = pack.model_copy(update={'id':'pack-authored-revision'})
    graph = build_evidence_graph(derived)
    slide = plan.slides[1]
    excerpt = _evidence_excerpt(slide.index+1,plan,graph)
    computed = {i.id:i for d in pack.datasets for i in analyze_dataset(d).insights}
    assert excerpt
    assert all(computed[iid].fact in excerpt for iid in slide.insight_ids)


def test_reference_table_viz_reason_never_leaks_into_slide_text(tmp_path):
    csv_path = tmp_path / 'strategies.csv'
    rows = [
        (
            f"202{5 + i % 3};Стратегия {i};Сегмент B2B-{i % 3};"
            f"Описание подхода и условий применения №{i}"
        )
        for i in range(1, 13)
    ]
    csv_path.write_text(
        'Год;Стратегия;Целевой сегмент;Описание\n' + '\n'.join(rows) + '\n',
        encoding='utf-8',
    )
    pack = parse_file(csv_path, artifact_id='uploaded-strategies')
    plan = plan_data_deck(
        pack,
        Brief(
            purpose='Обзор стратегий работы с сегментами',
            audience='Руководство',
            language='ru',
            target_slide_count=4,
            content_fill='shrink',
        ),
    )
    data_slides = [s for s in plan.slides if s.purpose == Purpose.data]
    assert data_slides
    for slide in data_slides:
        visible_texts = [
            slide.title_intent,
            slide.key_message,
            *(u.text for u in slide.content_units if u.text),
        ]
        joined = ' '.join(t for t in visible_texts if t)
        assert 'не подходят для графика' not in joined.casefold()
        assert 'нет главной метрики' not in joined.casefold()
        assert 'таблица из текста или идентификаторов' not in joined.casefold()

    ref_slide = data_slides[0]
    subtitles = [u.text for u in ref_slide.content_units if u.kind.value != 'title' and u.text]
    assert subtitles, 'Reference table slide should carry a domain description subtitle'
    assert '12' in subtitles[0]
    assert 'Стратегия' in subtitles[0]
    assert ref_slide.chart and ref_slide.chart.get('why')
