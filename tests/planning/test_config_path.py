"""Регрессия: load_generation_config не должен зависеть от CWD.

Баг: DEFAULT_CONFIG_PATH был относительным («configs/...») — CLI,
запущенный не из корня репозитория, падал с FileNotFoundError.
Теперь путь резолвится от расположения пакета.
"""

from pathlib import Path

import yaml
from deckdna.planning.config import DEFAULT_CONFIG_PATH, load_generation_config


def test_default_config_path_is_absolute():
    assert DEFAULT_CONFIG_PATH.is_absolute()
    assert DEFAULT_CONFIG_PATH.exists()


def test_config_loads_from_foreign_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # cwd без configs/ — раньше ломалось
    assert not (tmp_path / "configs").exists()
    cfg = load_generation_config()
    assert cfg.deck.min_slides <= cfg.deck.default_slide_count <= cfg.deck.max_slides


def test_explicit_relative_path_still_cwd_relative(tmp_path, monkeypatch):
    """Явный относительный аргумент остаётся CWD-relative — только
    default резолвится от пакета."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "custom.yaml").write_text(
        yaml.safe_dump(
            {"deck": {"default_slide_count": 10, "min_slides": 3, "max_slides": 40}}
        ),
        encoding="utf-8",
    )
    cfg = load_generation_config(Path("custom.yaml"))
    assert cfg.deck.max_slides == 40
