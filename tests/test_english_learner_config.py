"""配置存储的单元测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from english_learner.services.config_store import (
    DEFAULT_DAILY_REVIEW_LIMIT,
    MAX_DAILY_REVIEW_LIMIT,
    MIN_DAILY_REVIEW_LIMIT,
    AIConfig,
    ConfigError,
    ConfigStore,
    RealTextConfig,
    ReviewConfig,
    UiConfig,
    config_dir,
    normalize_base_url,
)
from english_learner.services.readability import ReadabilityThresholds


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


def test_readability_thresholds_roundtrip_and_are_preserved(tmp_path: Path) -> None:
    """readability 段可读写，且重新保存 AI 配置不会把它冲掉。"""
    store = ConfigStore(tmp_path / "config.json")
    thresholds = ReadabilityThresholds(
        enabled=True,
        retry_enabled=True,
        max_unknown_ratio=0.1,
        max_avg_sentence_len=18.0,
    )
    store.save_readability(thresholds)
    assert store.load_readability() == thresholds

    store.save(make_config())
    assert store.load_readability() == thresholds
    # ui 段也不受影响。
    store.save_ui(UiConfig(english_font="literata"))
    assert store.load_readability() == thresholds


def test_readability_thresholds_fall_back_to_defaults(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "missing.json")
    assert store.load_readability() == ReadabilityThresholds()

    store.path.write_text('{"readability": "broken"}', encoding="utf-8")
    assert store.load_readability() == ReadabilityThresholds()


def test_review_config_roundtrip_and_is_preserved(tmp_path: Path) -> None:
    """review 段可读写，且保存 AI / UI / readability 不会把它冲掉。"""
    store = ConfigStore(tmp_path / "config.json")
    review = ReviewConfig(daily_limit=35, spelling_enabled=True)

    store.save_review(review)
    assert store.load_review() == review

    store.save(make_config())
    store.save_ui(UiConfig(english_font="literata"))
    store.save_readability(ReadabilityThresholds(enabled=True))
    assert store.load_review() == review

    # 反向：只改 review 段时，ai / ui / readability 也不能被冲掉。
    updated = ReviewConfig(daily_limit=40, spelling_enabled=False)
    store.save_review(updated)
    assert store.load() is not None
    assert store.load_ui().english_font == "literata"
    assert store.load_readability().enabled is True


def test_review_config_falls_back_and_clamps(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "missing.json")
    assert store.load_review() == ReviewConfig()

    store.path.write_text('{"review": "broken"}', encoding="utf-8")
    assert store.load_review() == ReviewConfig()

    store.path.write_text(
        json.dumps({"review": {"daily_limit": 0, "spelling_enabled": "false"}}),
        encoding="utf-8",
    )
    loaded = store.load_review()
    assert loaded.daily_limit == MIN_DAILY_REVIEW_LIMIT
    assert loaded.spelling_enabled is False

    store.save_review(ReviewConfig(daily_limit=9999))
    assert store.load_review().daily_limit == MAX_DAILY_REVIEW_LIMIT

    assert ReviewConfig().daily_limit == DEFAULT_DAILY_REVIEW_LIMIT


def test_save_removes_legacy_flat_ai_fields(tmp_path: Path) -> None:
    """P3 回归：旧扁平格式的顶层明文 api_key 不能在保存后残留在文件里。"""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-plain-legacy",
                "model": "deepseek-chat",
                "temperature": 0.7,
            }
        ),
        encoding="utf-8",
    )
    store = ConfigStore(path)

    assert store.load() is not None
    store.save(make_config(model="deepseek-chat"))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "api_key" not in payload
    assert "base_url" not in payload
    assert "model" not in payload
    assert "temperature" not in payload
    assert payload["ai"]["model"] == "deepseek-chat"


def make_flat_legacy_payload() -> dict:
    payload = make_config().to_dict()
    # 扁平格式：顶层直接放 AI 字段（load() 明确兼容），且 api_key 是明文。
    payload["ui"] = {"english_font": "lexend"}
    return payload


def test_save_readability_migrates_flat_config_without_losing_ai(tmp_path: Path) -> None:
    """P2 回归：只改可读性阈值时，扁平配置的 AI 字段要迁移进 `ai` 段，不能丢。"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps(make_flat_legacy_payload()), encoding="utf-8")
    store = ConfigStore(path)
    before = store.load()
    assert before is not None and before.api_key == "sk-1234567890abcdef"

    store.save_readability(ReadabilityThresholds(max_unknown_ratio=0.1))

    payload = json.loads(path.read_text(encoding="utf-8"))
    for key in ("base_url", "api_key", "model", "temperature"):
        assert key not in payload
    assert payload["readability"]["max_unknown_ratio"] == 0.1
    assert payload["ui"]["english_font"] == "lexend"
    # 迁移后仍然能正常加载，且 Key 已加密存储。
    after = store.load()
    assert after == before
    assert payload["ai"]["api_key"] != "sk-1234567890abcdef"


def test_save_ui_migrates_flat_config_without_losing_ai(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(make_flat_legacy_payload()), encoding="utf-8")
    store = ConfigStore(path)

    store.save_ui(UiConfig(english_font="literata"))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "api_key" not in payload
    assert payload["ui"]["english_font"] == "literata"
    assert store.load() is not None and store.load().model == "deepseek-chat"


def test_real_text_config_roundtrip_and_defaults(tmp_path: Path) -> None:
    """M7.1：real_text 段独立保存，损坏时回退默认（自动安排开启）。"""
    store = ConfigStore(tmp_path / "config.json")

    assert store.load_real_text() == RealTextConfig()

    saved = store.save_real_text(RealTextConfig(auto_enabled=False, last_decision_on="2026-11-25"))
    assert saved.auto_enabled is False
    assert store.load_real_text() == RealTextConfig(
        auto_enabled=False, last_decision_on="2026-11-25"
    )

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["real_text"] == {
        "auto_enabled": False,
        "last_decision_on": "2026-11-25",
    }

    store.path.write_text(json.dumps({"real_text": "broken"}), encoding="utf-8")
    assert store.load_real_text() == RealTextConfig()
