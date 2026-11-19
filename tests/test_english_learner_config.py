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


@pytest.mark.skipif(os.name != "nt", reason="DPAPI 只在 Windows 上加密")
def test_api_key_is_not_stored_in_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.save(make_config())

    text = path.read_text(encoding="utf-8")
    assert "sk-1234567890abcdef" not in text
    payload = json.loads(text)
    assert payload["ai"]["api_key"].startswith("dpapi:")

    loaded = store.load()
    assert loaded is not None
    assert loaded.api_key == "sk-1234567890abcdef"


def test_legacy_plaintext_config_can_still_load(tmp_path: Path) -> None:
    """M1 及以前的明文配置必须能直接读，不能逼用户重配。"""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "ai": {
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "sk-legacy-plain-key",
                    "model": "deepseek-chat",
                    "temperature": 0.7,
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = ConfigStore(path).load()
    assert loaded is not None
    assert loaded.api_key == "sk-legacy-plain-key"


@pytest.mark.skipif(os.name != "nt", reason="只有 Windows 才会真正加密")
def test_save_does_not_double_encrypt(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.save(make_config())
    store.save(make_config())  # 第二次保存不应把密文再套一层

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["ai"]["api_key"].startswith("dpapi:")
    assert payload["ai"]["api_key"].count("dpapi:") == 1
    loaded = store.load()
    assert loaded is not None
    assert loaded.api_key == "sk-1234567890abcdef"


def test_broken_ciphertext_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "ai": {
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "dpapi:broken-token",
                    "model": "deepseek-chat",
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        ConfigStore(path).load()


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
