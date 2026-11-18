"""配置存储的单元测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from english_learner.services.config_store import (
    AIConfig,
    ConfigError,
    ConfigStore,
    config_dir,
    normalize_base_url,
)


def make_config(**overrides) -> AIConfig:
    values = {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-1234567890abcdef",
        "model": "deepseek-chat",
        "temperature": 0.7,
    }
    values.update(overrides)
    return AIConfig(**values)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    saved = store.save(make_config())

    assert store.exists()
    loaded = store.load()
    assert loaded is not None
    assert loaded.base_url == saved.base_url
    assert loaded.api_key == "sk-1234567890abcdef"
    assert loaded.model == "deepseek-chat"
    assert loaded.temperature == 0.7


def test_load_missing_file_returns_none(tmp_path: Path) -> None:
    assert ConfigStore(tmp_path / "nothing.json").load() is None


def test_corrupt_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ConfigError):
        ConfigStore(path).load()


def test_missing_fields_raise(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ai": {"base_url": "https://x/v1"}}), encoding="utf-8")
    with pytest.raises(ConfigError):
        ConfigStore(path).load()


def test_save_rejects_invalid_config(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    with pytest.raises(ConfigError):
        store.save(make_config(api_key=""))
    with pytest.raises(ConfigError):
        store.save(make_config(base_url="api.deepseek.com"))


def test_mask_key() -> None:
    assert make_config(api_key="sk-1234567890").masked_key() == "sk-1********7890"
    assert make_config(api_key="short").masked_key() == "*****"
    assert make_config(api_key="").masked_key() == "（未配置）"


def test_normalize_base_url() -> None:
    assert normalize_base_url(" https://api.deepseek.com/v1/ ") == "https://api.deepseek.com/v1"
    assert (
        normalize_base_url("https://api.deepseek.com/v1/chat/completions")
        == "https://api.deepseek.com/v1"
    )
    assert normalize_base_url("http://localhost:11434/v1") == "http://localhost:11434/v1"


def test_validation_messages() -> None:
    errors = make_config(base_url="", api_key="", model="").validate()
    assert len(errors) == 3

    config = make_config(temperature=9).normalized()
    assert config.temperature == 2.0


def test_config_dir_uses_appdata_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", r"D:\fake\Roaming")
    if os.name == "nt":
        assert config_dir() == Path(r"D:\fake\Roaming") / "EnglishLearner"
