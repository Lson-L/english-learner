"""M5.3 本地可读性指标：纯函数测试（无网络、无数据库）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from english_learner.services.readability import (
    WORDLIST_PATH,
    ReadabilityMetrics,
    ReadabilityThresholds,
    analyze,
    default_wordlist,
    load_wordlist,
    threshold_warnings,
    tokenize,
)

WORDLIST = {
    "the": "A1",
    "small": "A1",
    "habit": "A2",
    "are": "A1",
    "easy": "A1",
    "to": "A1",
    "start": "A1",
    "and": "A1",
    "repeat": "A2",
    "every": "A1",
    "day": "A1",
    "people": "A1",
    "keep": "A1",
    "go": "A1",
    "dog": "A1",
    "run": "A1",
    "quick": "A2",
}


# ------------------------------------------------------------------ 分词
def test_tokenize_lowercases_and_strips_contractions() -> None:
    assert tokenize("It's Don't We'll 2024!") == ["it", "do", "we"]
    assert tokenize("can't won't I'm") == ["can", "will", "i"]
    assert tokenize("") == []


def test_inflected_forms_are_recognized() -> None:
    """habits / repeated / running 都应还原成词表里的原形。"""
    metrics = analyze(
        "Habits are repeated every day. People are running quickly.",
        WORDLIST,
    )
    assert metrics.unknown_count == 0
    assert metrics.word_total == 9


# ------------------------------------------------------------------ 指标
def test_unknown_ratio_and_sentence_length() -> None:
    text = "Small habits are easy. Xenophobic quixotic zephyr."
    metrics = analyze(text, WORDLIST)

    assert metrics.word_total == 7
    assert metrics.unknown_count == 3
    assert metrics.unknown_ratio == pytest.approx(3 / 7)
    assert metrics.avg_sentence_len == pytest.approx(3.5)
    assert metrics.max_sentence_len == 4


def test_target_words_are_counted_by_lemma() -> None:
    metrics = analyze(
        "Small habits are easy.",
        WORDLIST,
        target_words=["habit", "zephyr"],
    )
    assert metrics.target_total == 2
    assert metrics.target_found == 1
    assert metrics.target_coverage == 0.5


def test_user_known_words_reduce_unknowns() -> None:
    metrics = analyze(
        "Serendipity happens.",
        WORDLIST,
        known_words=["Serendipity", "happen"],
    )
    assert metrics.unknown_count == 0


def test_empty_text_returns_zero_metrics() -> None:
    metrics = analyze("", WORDLIST)
    assert metrics.word_total == 0
    assert metrics.unknown_ratio == 0.0
    assert metrics.avg_sentence_len == 0.0
    assert metrics.max_sentence_len == 0


# ------------------------------------------------------------------ 阈值
def test_thresholds_default_and_clamping() -> None:
    defaults = ReadabilityThresholds.from_dict(None)
    assert defaults.enabled is True
    assert defaults.retry_enabled is False

    clamped = ReadabilityThresholds.from_dict(
        {
            "enabled": 0,
            "retry_enabled": 1,
            "max_unknown_ratio": "bad",
            "max_avg_sentence_len": 999,
        }
    )
    assert clamped.enabled is False
    assert clamped.retry_enabled is True
    assert clamped.max_unknown_ratio == defaults.max_unknown_ratio
    assert clamped.max_avg_sentence_len == 200.0

    negative = ReadabilityThresholds.from_dict({"max_unknown_ratio": -1})
    assert negative.max_unknown_ratio == 0.0


def test_threshold_warnings_skip_when_wordlist_missing() -> None:
    metrics = ReadabilityMetrics(word_total=200, unknown_count=200, wordlist_size=0)
    assert threshold_warnings(metrics, ReadabilityThresholds()) == ()


def test_threshold_warnings_use_tolerance_for_small_wordlist() -> None:
    # 8% 生词：完整词表下超过 7% 上限，词表过小时放宽 1.5 倍（10.5%）不告警。
    small = ReadabilityMetrics(word_total=100, unknown_count=8, wordlist_size=10)
    assert threshold_warnings(small, ReadabilityThresholds()) == ()

    reliable = ReadabilityMetrics(word_total=100, unknown_count=8, wordlist_size=600)
    warnings = threshold_warnings(reliable, ReadabilityThresholds())
    assert len(warnings) == 1
    assert "生词比例" in warnings[0]


def test_threshold_warnings_cover_long_sentences_and_switch() -> None:
    metrics = ReadabilityMetrics(
        word_total=200,
        unknown_count=0,
        avg_sentence_len=30.0,
        wordlist_size=600,
    )
    assert any("平均句长" in item for item in threshold_warnings(metrics, ReadabilityThresholds()))
    assert threshold_warnings(metrics, ReadabilityThresholds(enabled=False)) == ()


def test_threshold_warnings_ignore_short_text() -> None:
    metrics = ReadabilityMetrics(word_total=10, unknown_count=10, wordlist_size=600)
    assert threshold_warnings(metrics, ReadabilityThresholds()) == ()


# ------------------------------------------------------------------ 序列化
def test_metrics_roundtrip_and_invalid_payloads() -> None:
    metrics = ReadabilityMetrics(
        word_total=100,
        unknown_count=5,
        avg_sentence_len=12.34,
        max_sentence_len=20,
        target_total=4,
        target_found=3,
        wordlist_size=3000,
    )
    data = metrics.to_dict()
    restored = ReadabilityMetrics.from_dict(data)
    assert restored is not None
    assert restored.word_total == 100
    assert restored.unknown_count == 5
    assert restored.avg_sentence_len == 12.3
    assert restored.target_found == 3
    assert restored.wordlist_size == 3000

    assert ReadabilityMetrics.from_dict(None) is None
    assert ReadabilityMetrics.from_dict({}) is None
    assert ReadabilityMetrics.from_dict({"word_total": "oops"}) is None


# ------------------------------------------------------------------ 词表
def test_load_wordlist_reads_and_normalizes(tmp_path: Path) -> None:
    path = tmp_path / "words.json"
    path.write_text(
        json.dumps({"version": 1, "words": {"Apple": "A1", " run ": "b1"}}),
        encoding="utf-8",
    )
    assert load_wordlist(path) == {"apple": "A1", "run": "B1"}


def test_load_wordlist_missing_file_is_tolerant(tmp_path: Path) -> None:
    assert load_wordlist(tmp_path / "missing.json") == {}


def test_shipped_wordlist_is_usable() -> None:
    """随程序分发的词表必须存在、规模够、档位合法。"""
    assert WORDLIST_PATH.is_file()
    words = default_wordlist()
    assert len(words) >= 500
    assert all(band in {"A1", "A2", "B1", "B2", "C1"} for band in words.values())
    assert all(word == word.lower() and word.isalpha() for word in words)


def test_irregular_and_derived_forms_are_recognized() -> None:
    """不规则屈折（is/was/has/said/went）与双写后缀都要还原成原形。"""
    metrics = analyze(
        "He is going to say what he said. She was stopped by a bigger dog. "
        "They have gone and he has left.",
        default_wordlist(),
    )
    assert metrics.unknown_count == 0


def test_shipped_wordlist_covers_core_function_words() -> None:
    """P1 回归：随程序分发的词表 + 不规则映射必须覆盖基础文本。"""
    wordlist = default_wordlist()
    for word in ("to", "can", "your", "our", "its", "even", "say", "be", "have", "do"):
        assert word in wordlist, word

    metrics = analyze(
        "Small habits are easy to start and easy to repeat every day. "
        "It is what it is, and that was all he could say.",
        wordlist,
    )
    assert metrics.word_total == 24
    assert metrics.unknown_count == 0


def test_thresholds_accept_string_booleans() -> None:
    """config.json 手改成 "false" 时必须解析为 False，不能因非空字符串变成 True。"""
    parsed = ReadabilityThresholds.from_dict({"enabled": "false", "retry_enabled": "no"})
    assert parsed.enabled is False
    assert parsed.retry_enabled is False

    truthy = ReadabilityThresholds.from_dict({"enabled": "yes", "retry_enabled": "on"})
    assert truthy.enabled is True
    assert truthy.retry_enabled is True

    unknown = ReadabilityThresholds.from_dict({"enabled": "maybe"})
    assert unknown.enabled is True


def test_more_common_words_and_irregular_forms_are_covered() -> None:
    """第 2 轮验收补充：残余常见词与第二批不规则形式。"""
    wordlist = default_wordlist()
    for word in (
        "nothing",
        "whether",
        "suddenly",
        "seven",
        "gray",
        "cool",
        "false",
        "impossible",
        "easily",
        "researcher",
    ):
        assert word in wordlist, word

    metrics = analyze(
        "He laid the book down, fed the dog, stuck the note, stole a glance, "
        "hid it, rode home, and swept the floor.",
        wordlist,
    )
    assert metrics.unknown_count == 0
