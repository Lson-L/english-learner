"""M7.0：真实文章库的加载、校验、种子与供给历史。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from english_learner.models.entities import ContentSource
from english_learner.services.real_text_library import (
    RealTextCorpusError,
    load_corpus,
    seed_real_texts,
    validate_corpus,
)
from english_learner.storage.db import Database

SENTENCE = "The sun is warm and the wind is gentle."
LEVELS = ("A2", "B1", "B2", "C1")


def make_text(level: str, index: int) -> dict:
    content = " ".join([SENTENCE] * 18)  # 162 词，落在 150~2000 的校验区间
    return {
        "id": f"{level.lower()}-{index}",
        "title": f"{level} Story {index}",
        "level": level,
        "level_source": "manual",
        "source": "Test fixture",
        "source_url": "https://example.com/story",
        "license": "Public Domain",
        "license_url": "https://example.com/license",
        "published": "1900",
        "topics": ["fiction"],
        "word_count": len(content.split()),
        "excerpt": False,
        "attribution": "Test fixture.",
        "paragraphs": [content],
        "questions": [
            {
                "prompt": f"Question {number}?",
                "options": ["A", "B", "C", "D"],
                "answer_index": 0,
                "explanation": "Because A.",
                "kind": "choice",
            }
            for number in range(1, 5)
        ],
    }


def valid_corpus_dict() -> dict:
    return {
        "version": 1,
        "texts": [make_text(level, index) for level in LEVELS for index in (1, 2)],
    }


def write_corpus(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_corpus_parses_text_and_questions(tmp_path: Path) -> None:
    path = write_corpus(tmp_path / "corpus.json", valid_corpus_dict())

    corpus = load_corpus(path)

    assert corpus.version == 1
    assert len(corpus.texts) == 8
    first = corpus.texts[0]
    assert first.id == "a2-1"
    assert first.level == "A2"
    assert first.word_count == len(first.content.split())
    assert len(first.questions) == 4
    assert first.questions[0].options == ("A", "B", "C", "D")
    assert first.topics == ("fiction",)


def test_validate_corpus_accepts_valid_fixture(tmp_path: Path) -> None:
    corpus = load_corpus(write_corpus(tmp_path / "corpus.json", valid_corpus_dict()))
    assert validate_corpus(corpus) == []


def test_load_corpus_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(RealTextCorpusError):
        load_corpus(tmp_path / "missing.json")


def test_load_corpus_wraps_bad_field_types(tmp_path: Path) -> None:
    """字段类型错误翻译成 RealTextCorpusError，内容包导入只处理一种异常。"""
    data = valid_corpus_dict()
    data["version"] = "not-a-number"

    with pytest.raises(RealTextCorpusError):
        load_corpus(write_corpus(tmp_path / "corpus.json", data))


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda data: data["texts"][0].update(license="All Rights Reserved"), "license"),
        (lambda data: data["texts"][0].update(level="D1"), "level"),
        (lambda data: data["texts"][0].update(word_count=1), "word_count"),
        (lambda data: data["texts"][0]["questions"].pop(), "题目数"),
        (lambda data: data["texts"][0]["questions"][0].update(explanation=""), "explanation"),
    ],
)
def test_validate_corpus_catches_invalid_fields(tmp_path: Path, mutate, fragment: str) -> None:
    data = copy.deepcopy(valid_corpus_dict())
    mutate(data)
    corpus = load_corpus(write_corpus(tmp_path / "corpus.json", data))

    errors = validate_corpus(corpus)

    assert any(fragment in error for error in errors), errors


@pytest.mark.parametrize(
    "bad_content",
    [
        "<p>The sun is warm.</p>",
        "<!-- hidden --> The sun is warm.",
        "<!DOCTYPE html> The sun is warm.",
        "*** START OF THE PROJECT GUTENBERG EBOOK *** The sun is warm.",
        "The sun is warm. *** END OF THE PROJECT GUTENBERG EBOOK ***",
    ],
)
def test_validate_corpus_rejects_html_and_gutenberg_markers(
    tmp_path: Path, bad_content: str
) -> None:
    """只去掉 Gutenberg 头、留下尾标记的语料也必须被拦下。"""
    data = copy.deepcopy(valid_corpus_dict())
    text = data["texts"][0]
    content = bad_content + " " + text["paragraphs"][0]
    text["paragraphs"] = [content]
    text["word_count"] = len(content.split())
    corpus = load_corpus(write_corpus(tmp_path / "corpus.json", data))

    errors = validate_corpus(corpus)

    assert any("HTML" in error or "Gutenberg" in error for error in errors), errors


def test_validate_corpus_requires_two_texts_per_level(tmp_path: Path) -> None:
    data = copy.deepcopy(valid_corpus_dict())
    data["texts"] = [item for item in data["texts"] if item["level"] != "C1"]
    corpus = load_corpus(write_corpus(tmp_path / "corpus.json", data))

    errors = validate_corpus(corpus)

    assert any("C1" in error and "至少需要" in error for error in errors), errors


def test_seed_real_texts_is_idempotent_and_only_upgrades(tmp_path: Path) -> None:
    db = Database(tmp_path / "data.db")
    try:
        first = load_corpus(write_corpus(tmp_path / "corpus-v1.json", valid_corpus_dict()))
        assert seed_real_texts(db, first) == 8
        assert len(db.real_texts.list_all()) == 8
        assert seed_real_texts(db, first) == 0

        upgraded = copy.deepcopy(valid_corpus_dict())
        upgraded["version"] = 2
        upgraded["texts"][0]["title"] = "Updated title"
        seeded = seed_real_texts(
            db, load_corpus(write_corpus(tmp_path / "corpus-v2.json", upgraded))
        )
        assert seeded == 8
        assert db.real_texts.get("a2-1").title == "Updated title"  # type: ignore[union-attr]

        # 再灌旧版本不降级
        assert seed_real_texts(db, first) == 0
        assert db.real_texts.get("a2-1").title == "Updated title"  # type: ignore[union-attr]
    finally:
        db.close()


def test_seed_real_texts_rolls_back_on_failure_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """中途失败必须整体回滚：版本号不能提前推进，下次启动还能补齐。"""
    db = Database(tmp_path / "data.db")
    try:
        corpus = load_corpus(write_corpus(tmp_path / "corpus.json", valid_corpus_dict()))
        original_upsert = db.real_texts.upsert
        calls = {"count": 0}

        def flaky_upsert(text):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("disk full")
            return original_upsert(text)

        monkeypatch.setattr(db.real_texts, "upsert", flaky_upsert)
        with pytest.raises(RuntimeError):
            seed_real_texts(db, corpus)

        # 事务回滚：第 1 篇也不能留下，版本闸门保持 0，重试才能补全。
        assert db.real_texts.list_all() == []
        assert db.real_texts.max_corpus_version() == 0

        monkeypatch.setattr(db.real_texts, "upsert", original_upsert)
        assert seed_real_texts(db, corpus) == 8
        assert len(db.real_texts.list_all()) == 8
    finally:
        db.close()


def test_history_flow_mark_served_complete_override_and_expire(tmp_path: Path) -> None:
    db = Database(tmp_path / "data.db")
    try:
        corpus = load_corpus(write_corpus(tmp_path / "corpus.json", valid_corpus_dict()))
        seed_real_texts(db, corpus)
        first = db.real_texts.get("a2-1")
        second = db.real_texts.get("a2-2")
        assert first is not None and second is not None

        db.real_texts.mark_served(first, served_on="2026-11-25", article_id=11)
        assert db.real_texts.last_history().status == "served"  # type: ignore[union-attr]

        db.real_texts.record_completion(
            first.id,
            score=0.75,
            self_report="ok",
            outcome="success",
            completed_at="2026-11-25T20:00:00",
        )
        completed = db.real_texts.last_history()
        assert completed is not None
        assert completed.status == "completed"
        assert completed.outcome == "success"
        assert completed.score == pytest.approx(0.75)

        db.real_texts.mark_served(second, served_on="2026-11-01", article_id=12)
        db.real_texts.mark_overridden(second.id)
        overridden = db.real_texts.last_history()
        assert overridden is not None
        assert overridden.status == "overridden"
        assert overridden.outcome == "skip"

        # 只剩一条更早的 served 记录需要被结算
        third = db.real_texts.get("b1-1")
        assert third is not None
        db.real_texts.mark_served(third, served_on="2026-11-01", article_id=13)
        assert db.real_texts.expire_abandoned("2026-12-01", days=7) == 1
        latest = db.real_texts.last_history()
        assert latest is not None
        assert latest.status == "abandoned"
        assert latest.outcome == "abandon"

        assert db.real_texts.served_ids() == {"a2-1", "a2-2", "b1-1"}
        assert len(db.real_texts.list_unread()) == 5
    finally:
        db.close()


def test_content_source_repo_roundtrip(tmp_path: Path) -> None:
    db = Database(tmp_path / "data.db")
    try:
        source = ContentSource(
            id="gutenberg-alice",
            url="https://www.gutenberg.org/files/11/11-0.txt",
            title="Alice",
            level="B1",
            license="Public Domain",
            source="Project Gutenberg",
        )
        db.content_sources.upsert(source)

        loaded = db.content_sources.get("gutenberg-alice")
        assert loaded is not None
        assert loaded.url == source.url
        assert loaded.enabled is True
        assert [item.id for item in db.content_sources.list_all()] == ["gutenberg-alice"]
    finally:
        db.close()


def test_production_corpus_is_structurally_valid() -> None:
    """打包的 corpus.json 必须能被加载且结构合法；内容量（每档 2 篇）后续再补。"""
    corpus = load_corpus()

    assert corpus.version >= 1
    assert len(corpus.texts) >= 1
    assert validate_corpus(corpus, require_levels=False) == []
    assert all(len(text.questions) == 4 for text in corpus.texts)
    assert all(text.level in LEVELS for text in corpus.texts)


def test_validate_corpus_does_not_treat_math_angles_as_html(tmp_path: Path) -> None:
    """`a < b > c` 这类比较符号不能被当成 HTML 标签（P3 误判边界）。"""
    data = copy.deepcopy(valid_corpus_dict())
    text = data["texts"][0]
    content = "The result is a < b > c in this example. " + text["paragraphs"][0]
    text["paragraphs"] = [content]
    text["word_count"] = len(content.split())
    corpus = load_corpus(write_corpus(tmp_path / "corpus.json", data))

    assert validate_corpus(corpus) == []
