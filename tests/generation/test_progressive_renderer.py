from pathlib import Path

from deckdna.generation import progressive


def test_failed_draft_is_reported_without_claiming_ready(monkeypatch, tmp_path):
    class Package:
        def save(self, path: Path):
            path.write_bytes(b"test-package")

    def fail(*args):
        raise RuntimeError("renderer unavailable")

    events = []
    monkeypatch.setattr(progressive, "render_pdf", fail)
    renderer = progressive.ProgressiveRenderer(
        tmp_path, lambda kind, data: events.append((kind, data)),
    )
    renderer.compiled(0, "slide-a", Package())
    assert [kind for kind, _ in events] == ["slide.compiled", "slide.preview_failed"]
    assert all(data["status"] == "draft" for _, data in events)
    assert not list(tmp_path.rglob("*.png"))


def test_canceled_draft_does_not_start_renderer(tmp_path):
    events = []
    renderer = progressive.ProgressiveRenderer(
        tmp_path, lambda *args: events.append(args), is_canceled=lambda: True,
    )
    renderer.compiled(0, "slide-a", None)
    assert not events and not list(tmp_path.iterdir())


def test_render_slot_is_shared_and_released_after_failure(monkeypatch, tmp_path):
    import threading

    entered, release = threading.Event(), threading.Event()
    calls = []

    class Package:
        def save(self, path):
            path.write_bytes(b"test-package")

    def render(path, output):
        calls.append(path)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        raise RuntimeError("render error")

    monkeypatch.setattr(progressive, "render_pdf", render)
    renderer = progressive.ProgressiveRenderer(tmp_path, lambda *args: None)
    first = threading.Thread(target=renderer.compiled, args=(0, "a", Package()))
    second = threading.Thread(target=renderer.compiled, args=(1, "b", Package()))
    first.start()
    try:
        assert entered.wait(5)
        second.start()
        assert len(calls) == 1
    finally:
        release.set()
        first.join(timeout=5)
        if second.ident is not None:
            second.join(timeout=5)
    assert not first.is_alive() and not second.is_alive()
    assert len(calls) == 2
