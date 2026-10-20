"""The CLI must not invent a deck purpose or its audience."""

from deckdna.cli.main import app
from deckdna.errors import DeckDNAError
from typer.testing import CliRunner


def test_cli_defaults_are_neutral_and_explicit_preferences_survive(monkeypatch, tmp_path):
    captured = []

    def capture(template, content, brief, *args, **kwargs):
        captured.append(brief)
        raise DeckDNAError("invalid_input", "captured planning input")

    monkeypatch.setattr("deckdna.generation.pipeline.generate", capture)
    template = tmp_path / "template.pptx"
    content = tmp_path / "content.md"
    template.write_bytes(b"unused: generation intercepted")
    content.write_text("A presentation assistant")
    base = ["generate", str(template), str(content), str(tmp_path / "output")]
    CliRunner().invoke(app, base)
    assert len(captured) == 1
    assert captured[-1].purpose == "" and captured[-1].audience == ""
    CliRunner().invoke(app, [*base, "--purpose", "Review", "--audience", "team",
                            "--language", "en", "--slides", "6", "--content-fill", "shrink",
                            "--slide-count-policy", "maximum"])
    assert len(captured) == 2
    brief = captured[-1]
    assert brief.purpose == "Review" and brief.audience == "team" and brief.language == "en"
    assert brief.target_slide_count == 6 and brief.content_fill.value == "shrink"
    assert brief.slide_count_policy == "maximum" and brief.requested_slide_count == 6
