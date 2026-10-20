"""Shared preparation must add to variant usage, never replace it."""
import json

from deckdna.api import app as api
from deckdna.providers.mock import MockProvider
from fastapi.testclient import TestClient
from test_generation_llm import BRIEF, _mock_gateway, _project_with_inputs

client = TestClient(api.app)


class MeteredMock(MockProvider):
    def usage_report(self):
        calls = len(self.calls)
        return {'test-meter': {'calls': calls, 'input_tokens': calls * 5,
                               'output_tokens': calls * 7}}


def test_all_variant_passports_account_for_every_gateway_call_once(monkeypatch, wait_generation):
    gateways = []

    def build(*args, **kwargs):
        gateway = MeteredMock(fixtures=_mock_gateway().fixtures)
        gateways.append(gateway)
        return gateway

    monkeypatch.setattr(api, 'build_gateway', build)
    monkeypatch.setattr(api.pipeline, 'planning_pipeline_version', lambda: 'v1')
    pid, tid, pack = _project_with_inputs()
    response = client.post(f'/api/v1/projects/{pid}/generations', json={
        'template_id': tid, 'content_pack_id': pack, 'brief': BRIEF, 'use_llm': True,
        'variants': [{'strategy': s} for s in ('faithful', 'balanced', 'visual')],
    })
    assert response.status_code == 202, response.text
    result = wait_generation(client, response.json()['generation_id'])
    assert result['state'] == 'completed', result
    run = api.STORE.runs[result['id']]
    usage = [run.variant_reports[v.id]['usage'] for v in run.detail.variants]
    expected = sum(len(g.calls) for g in gateways)
    assert len(gateways) == 4 and len(gateways[0].calls) > 0
    assert sum(u['model_calls'] for u in usage) == expected
    assert sum(u['total_tokens'] for u in usage) == expected * 12
    assert sum(m['calls'] for u in usage for m in u['per_model']) == expected
    for variant in run.detail.variants:
        report = run.variant_reports[variant.id]
        artifact_id = run.variant_artifacts[variant.id]['quality_passport']
        passport = json.loads(api.STORE.artifacts[artifact_id].data)
        assert report['usage'] == passport['metrics']['usage']
