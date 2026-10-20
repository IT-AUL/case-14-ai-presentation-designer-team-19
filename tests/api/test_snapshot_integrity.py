"""Durable startup must not silently discard accepted work on corrupt storage."""
import hashlib

import pytest
from deckdna.api import app as api
from deckdna.api.persistence import load_store, save_store


def test_durable_snapshot_corruption_is_fatal_but_legacy_mode_is_unchanged(tmp_path):
    path = tmp_path / 'store.pkl'
    path.write_bytes(b'incomplete snapshot')
    assert not load_store(api._Store(), path)
    with pytest.raises(RuntimeError, match='unreadable'):
        load_store(api._Store(), path, require_integrity=True)


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_durable_artifact_integrity_is_verified(tmp_path, damage):
    state = api._Store()
    raw = b'input needed by an accepted job'
    artifact = api._Artifact(id='a', project_id=None, type='input', mime_type='text/plain',
                             filename='input.txt', data=raw, size_bytes=len(raw),
                             sha256=hashlib.sha256(raw).hexdigest(), created_at=api._utcnow())
    state.artifacts['a'] = artifact
    path = tmp_path / 'store.pkl'
    save_store(state, path)
    assert load_store(api._Store(), path, require_integrity=True)
    blob = tmp_path / 'blobs' / artifact.sha256
    if damage == 'missing':
        blob.unlink()
    else:
        blob.write_bytes(b'x' * len(raw))
    with pytest.raises(RuntimeError, match='artifact is missing or corrupt'):
        load_store(api._Store(), path, require_integrity=True)
