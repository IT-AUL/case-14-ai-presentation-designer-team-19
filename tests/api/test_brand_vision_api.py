"""Brandbook refresh is passive; deliberate bounded analysis is cached."""
import io
from types import SimpleNamespace

from deckdna.api import app as api
from deckdna.template.brand_vision import BrandReadings, ColorReading, FontReading
from fastapi.testclient import TestClient
from PIL import Image

A = '/api/v1'
client = TestClient(api.app)


def _raster_book():
    pid = client.post(f'{A}/projects', json={'name': 'brand vision'}).json()['id']
    buf = io.BytesIO()
    Image.new('RGB', (100, 100), 'white').save(buf, format='PNG')
    book = api._store_brandbook(pid, [('guide.png', buf.getvalue())])
    return pid, book.id


def test_empty_brandbook_does_not_claim_agreement():
    _, bid = _raster_book()
    result = client.get(f'{A}/brandbooks/{bid}/summary').json()
    assert 'не извлечены' in result['assistant_note']
    assert 'не спорит' not in result['assistant_note']
    assert api.STORE.brand_sources[bid]


def test_explicit_analysis_cached_and_refresh_passive(monkeypatch):
    _, bid = _raster_book()
    calls = []
    run = SimpleNamespace(brand={'brandbook_id': bid, 'priority': 'brandbook'},
                          detail=SimpleNamespace(template_id='brand-test-tpl'))
    template = api._store_artifact(b'original', type='template', mime_type=api._PPTX_MIME,
                                   filename='template.pptx')
    monkeypatch.setitem(api.STORE.templates, 'brand-test-tpl',
                        SimpleNamespace(artifact_id=template.id))
    monkeypatch.setitem(api.STORE.runs, 'brand-test-run', run)
    monkeypatch.setitem(api.STORE.brand_templates, ('brand-test-tpl', bid, 'brandbook'),
                        (b'old branded template', {}))

    class Gateway:
        async def vision_json(self, name, images, payload, schema):
            calls.append(name)
            return BrandReadings(
                colors=[ColorReading(image=1, quote='Primary #E4572E',
                                     role='primary', hex='#E4572E')],
                fonts=[FontReading(image=1, quote='Body font: Arial', role='body', family='Arial')])

    monkeypatch.setattr(api, '_gateway_for', lambda *a: Gateway())
    for _ in range(2):
        result = client.patch(f'{A}/brandbooks/{bid}', json={'analyze': True})
        assert result.status_code == 200, result.text
        assert result.json()['palette'][0]['hex'] == '#E4572E'
        assert result.json()['analysis']['pages'] == ['guide.png:page=1']
        assert client.get(f'{A}/brandbooks/{bid}/summary').status_code == 200
    assert calls == ['brandbook_extract']
    assert api._run_template_bytes(run) == b'old branded template'


def test_wrong_provider_scope_rejected_before_analysis():
    _, bid = _raster_book()
    response = client.patch(f'{A}/brandbooks/{bid}', json={
        'analyze': True, 'provider_session_id': 'missing', 'priority': 'template'})
    assert response.status_code == 404
    assert bid not in api.STORE.brand_analysis


def test_failure_releases_inflight_guard(monkeypatch):
    """Vision-провайдер упал: PATCH не падает — детерминированные токены
    брендбука сохраняются, статус разбора «fallback», инфлайт снят."""
    _, bid = _raster_book()

    class Gateway:
        async def vision_json(self, *args):
            raise TimeoutError('provider timeout')

    monkeypatch.setattr(api, '_gateway_for', lambda *a: Gateway())
    response = client.patch(f'{A}/brandbooks/{bid}', json={'analyze': True})
    assert response.status_code == 200, response.text
    assert bid not in api._BRAND_ANALYZING
    assert api.STORE.brand_analysis[bid]['status'] == 'fallback'
    # повторный запрос без force обслуживается из кэша, провайдер не дёргается
    cached = client.patch(f'{A}/brandbooks/{bid}', json={'analyze': True})
    assert cached.status_code == 200
