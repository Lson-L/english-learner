"""存储层单测：建表幂等、CRUD 往返、唯一约束、级联删除、统计。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from english_learner.models.entities import Article, Profile, Word, now_iso
from english_learner.storage.db import SCHEMA_VERSION, Database, DuplicateError


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def make_article(**overrides) -> Article:
    values = {
        "article_date": "2026-11-18",
        "title": "Small Habits",
        "content": "Small habits are powerful.\n\nThey add up over time.",
        "level": "B1",
        "topic": "psychology",
        "word_count": 9,
        "target_words": ("habits",),
        "created_at": now_iso(),
    }
    values.update(overrides)
    return Article(**values)


def make_word(**overrides) -> Word:
    values = {
        "lemma": "resilient",
        "display_form": "resilient",
        "phonetic": "/rɪˈzɪliənt/",
        "pos": "adj.",
        "meaning_cn": "有韧性的",
        "meaning_en": "able to recover quickly",
        "due_date": "2026-11-18",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    values.update(overrides)
    return Word(**values)


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "data.db"
    first = Database(path)
    first.close()
    second = Database(path)

    row = second.query_one("PRAGMA user_version")
    assert row is not None
    assert int(row[0]) == SCHEMA_VERSION
    tables = {
        str(item["name"])
        for item in second.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"profile", "articles", "words", "highlights", "review_logs", "daily_stats"} <= tables
    second.close()


def test_profile_roundtrip_and_overwrite(db: Database) -> None:
    profile = Profile(
        cefr_level="B1",
        vocab_estimate=3800,
        strengths=("阅读理解", "基础语法"),
        weaknesses=("学术词汇",),
        comment="整体 B1 偏上",
        assessed_at="2026-11-18T10:00:00",
    )
    stored = db.profiles.save(profile)

    assert stored.updated_at  # 空值会被自动补上
    loaded = db.profiles.get()
    assert loaded == stored
    assert loaded is not None
    assert loaded.strengths == ("阅读理解", "基础语法")

    db.profiles.save(Profile(cefr_level="B2", assessed_at="2026-11-19T10:00:00"))
    updated = db.profiles.get()
    assert updated is not None
    assert updated.cefr_level == "B2"
    row = db.query_one("SELECT COUNT(*) AS n FROM profile")
    assert row is not None and int(row["n"]) == 1


def test_article_insert_get_and_duplicate(db: Database) -> None:
    stored = db.articles.insert(make_article())
    assert stored.id is not None
    assert stored.target_words == ("habits",)

    assert db.articles.get_by_date("2026-11-18") == stored
    assert db.articles.get_by_id(stored.id) == stored
    assert [item.id for item in db.articles.list_recent()] == [stored.id]

    with pytest.raises(DuplicateError):
        db.articles.insert(make_article(title="另一篇"))

    assert db.articles.delete_by_date("2026-11-18") is True
    assert db.articles.get_by_date("2026-11-18") is None
    assert db.articles.delete_by_date("2026-11-18") is False


def test_word_upsert_is_case_insensitive(db: Database) -> None:
    stored = db.words.upsert(make_word(lemma="Resilient"))
    assert stored.id is not None

    again = db.words.upsert(make_word(lemma="resilient", meaning_cn="更新的释义"))
    assert again.id == stored.id
    assert again.meaning_cn == "更新的释义"

    assert db.words.count() == 1
    assert db.words.get_by_lemma("RESILIENT") is not None
    assert db.words.count_unmastered() == 1


def test_highlight_is_idempotent_and_cascades(db: Database) -> None:
    article = db.articles.insert(make_article())
    word = db.words.upsert(make_word(article_id=article.id))
    assert article.id is not None and word.id is not None

    inserted = db.words.add_highlight(
        article_id=article.id,
        word_id=word.id,
        start_offset=0,
        end_offset=5,
        sentence="Small habits are powerful.",
    )
    assert inserted is True
    duplicate = db.words.add_highlight(
        article_id=article.id,
        word_id=word.id,
        start_offset=0,
        end_offset=5,
    )
    assert duplicate is False
    assert len(db.words.list_highlights(article.id)) == 1

    db.articles.delete_by_date("2026-11-18")
    assert db.words.list_highlights(article.id) == []
    # 生词本不依赖某篇文章
    assert db.words.get_by_id(word.id) is not None
    assert db.words.get_by_id(word.id).article_id is None  # type: ignore[union-attr]


def test_replace_by_date_clears_highlights_and_keeps_words(db: Database) -> None:
    old = db.articles.insert(make_article())
    word = db.words.upsert(make_word(article_id=old.id))
    assert old.id is not None and word.id is not None
    db.words.add_highlight(
        article_id=old.id,
        word_id=word.id,
        start_offset=0,
        end_offset=5,
    )

    new = db.articles.replace_by_date(
        make_article(title="New One", content="A brand new article.", word_count=4)
    )

    assert new.id is not None and new.id != old.id
    stored = db.articles.get_by_date("2026-11-18")
    assert stored is not None and stored.title == "New One"
    assert db.words.list_highlights(old.id) == []
    assert db.words.get_by_id(word.id) is not None


def test_list_highlighted_words_and_created_on(db: Database) -> None:
    article = db.articles.insert(make_article())
    first = db.words.upsert(
        make_word(lemma="alpha", created_at="2026-11-18T09:00:00", updated_at="2026-11-18T09:00:00")
    )
    second = db.words.upsert(
        make_word(lemma="beta", created_at="2026-11-18T10:00:00", updated_at="2026-11-18T10:00:00")
    )
    db.words.upsert(
        make_word(lemma="old", created_at="2026-11-17T10:00:00", updated_at="2026-11-17T10:00:00")
    )
    assert article.id is not None and first.id is not None and second.id is not None
    # beta 先标在高亮位置前，验证按偏移而不是按插入顺序返回
    db.words.add_highlight(article_id=article.id, word_id=second.id, start_offset=0, end_offset=4)
    db.words.add_highlight(article_id=article.id, word_id=first.id, start_offset=10, end_offset=15)

    assert [word.lemma for word in db.words.list_highlighted_words(article.id)] == [
        "beta",
        "alpha",
    ]
    assert [word.lemma for word in db.words.list_created_on("2026-11-18")] == ["alpha", "beta"]


def test_word_update_meaning_and_delete_cascade(db: Database) -> None:
    article = db.articles.insert(make_article())
    word = db.words.upsert(make_word(article_id=article.id))
    assert article.id is not None and word.id is not None
    db.words.add_highlight(
        article_id=article.id,
        word_id=word.id,
        start_offset=0,
        end_offset=5,
    )
    db.reviews.log(word_id=word.id, rating=3, prev_interval=0, next_interval=1)

    updated = db.words.update_meaning(word.id, meaning_cn="新的释义", meaning_en="a new meaning")
    assert updated is not None
    assert updated.meaning_cn == "新的释义"
    assert updated.meaning_en == "a new meaning"
    assert db.words.update_meaning(9999, meaning_cn="x") is None

    assert db.words.delete(word.id) is True
    assert db.words.get_by_id(word.id) is None
    assert db.words.list_highlights(article.id) == []
    assert db.reviews.list_for_word(word.id) == []
    assert db.words.delete(word.id) is False


def test_review_repo_logs_recent_and_count(db: Database) -> None:
    word = db.words.upsert(make_word())
    assert word.id is not None

    first = db.reviews.log(
        word_id=word.id,
        rating=3,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-18T09:00:00",
    )
    second = db.reviews.log(
        word_id=word.id,
        rating=4,
        prev_interval=1,
        next_interval=19,
        reviewed_at="2026-11-19T09:00:00",
    )

    assert first.id is not None and second.id is not None
    logs = db.reviews.list_for_word(word.id)
    assert [log.rating for log in logs] == [4, 3]  # 最新的在前
    assert db.reviews.list_recent(1) == [second]
    assert db.reviews.count_for_date("2026-11-18") == 1
    assert db.reviews.count_for_date("2026-11-19") == 1
    assert db.reviews.count_for_date("2026-11-20") == 0


def test_save_schedule_only_touches_scheduling_fields(db: Database) -> None:
    word = db.words.upsert(make_word(meaning_cn="原释义"))
    assert word.id is not None

    updated = db.words.save_schedule(
        replace(
            word,
            status="learning",
            ease=2.7,
            interval_days=6,
            repetitions=2,
            due_date="2026-11-25",
            updated_at="2026-11-19T12:00:00",
        )
    )

    assert updated.meaning_cn == "原释义"
    assert updated.status == "learning"
    assert updated.ease == pytest.approx(2.7)
    assert updated.interval_days == 6
    assert updated.repetitions == 2
    assert updated.due_date == "2026-11-25"
    assert updated.updated_at == "2026-11-19T12:00:00"


def test_stats_bump_streak_and_due(db: Database) -> None:
    db.stats.bump("2026-11-15", article_read=1)
    db.stats.bump("2026-11-16", words_added=2)
    stats = db.stats.bump("2026-11-17", reviews_done=1)
    assert stats.reviews_done == 1

    db.stats.bump("2026-11-17", reviews_done=1)
    assert db.stats.get("2026-11-17").reviews_done == 2

    assert db.stats.streak(date(2026, 11, 17)) == 3
    assert db.stats.streak(date(2026, 11, 18)) == 3  # 今天没学，从昨天数
    assert db.stats.streak(date(2026, 11, 20)) == 0

    db.words.upsert(make_word(lemma="due", due_date="2026-11-16"))
    db.words.upsert(make_word(lemma="future", due_date="2026-12-01"))
    db.words.upsert(make_word(lemma="mastered", due_date="2026-11-01", status="mastered"))
    assert db.stats.count_due("2026-11-17") == 1
    assert [item.lemma for item in db.words.list_due("2026-11-17")] == ["due"]
