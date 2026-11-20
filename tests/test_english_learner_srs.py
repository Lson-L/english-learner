"""SM-2 简化算法与复习服务测试（纯 Python，无 Qt、无网络）。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from english_learner.models.entities import Word, now_iso, today_str
from english_learner.services.srs import (
    MASTERED_INTERVAL_DAYS,
    MAX_INTERVAL_DAYS,
    MIN_EASE,
    RATING_EASY,
    RATING_FORGOT,
    RATING_KNOWN,
    RATING_VAGUE,
    ReviewService,
    schedule,
)
from english_learner.storage.db import Database

DAY = date(2026, 11, 19)


def make_word(**overrides) -> Word:
    values = {
        "lemma": "resilient",
        "display_form": "resilient",
        "meaning_cn": "有韧性的",
        "in_vocabulary": True,
        "status": "new",
        "ease": 2.5,
        "interval_days": 0,
        "repetitions": 0,
        "due_date": "2026-11-19",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    values.update(overrides)
    return Word(**values)


# ---------------------------------------------------------------- 纯算法
def test_forgot_resets_interval_and_penalizes_ease() -> None:
    word = make_word(ease=2.5, interval_days=12, repetitions=4, status="learning")

    updated = schedule(word, RATING_FORGOT, on=DAY)

    assert updated.interval_days == 1
    assert updated.repetitions == 0
    assert updated.ease == pytest.approx(2.3)
    assert updated.status == "learning"
    assert updated.due_date == "2026-11-20"


def test_forgot_never_drops_ease_below_floor() -> None:
    updated = schedule(make_word(ease=1.35, interval_days=6), RATING_FORGOT, on=DAY)
    assert updated.ease == MIN_EASE


def test_known_first_two_reviews_follow_fixed_ladder() -> None:
    first = schedule(make_word(), RATING_KNOWN, on=DAY)
    assert (first.interval_days, first.repetitions) == (1, 1)
    assert first.status == "learning"
    assert first.due_date == "2026-11-20"

    second = schedule(first, RATING_KNOWN, on=DAY)
    assert (second.interval_days, second.repetitions) == (6, 2)
    assert second.ease == pytest.approx(2.5)
    assert second.due_date == "2026-11-25"

    third = schedule(second, RATING_KNOWN, on=DAY)
    assert third.interval_days == round(6 * 2.5)  # 15
    assert third.repetitions == 3


def test_easy_raises_ease_and_multiplies_more() -> None:
    word = make_word(ease=2.5, interval_days=6, repetitions=2, status="learning")

    updated = schedule(word, RATING_EASY, on=DAY)

    assert updated.ease == pytest.approx(2.65)
    assert updated.interval_days == round(6 * 2.65 * 1.3)  # 21
    assert updated.status == "learning"


def test_vague_lowers_ease_and_grows_slowly() -> None:
    word = make_word(ease=2.5, interval_days=6, repetitions=2, status="learning")

    updated = schedule(word, RATING_VAGUE, on=DAY)

    assert updated.ease == pytest.approx(2.35)
    assert updated.interval_days == round(6 * 1.2)  # 7


def test_long_interval_becomes_mastered() -> None:
    word = make_word(ease=2.5, interval_days=12, repetitions=3, status="learning")

    updated = schedule(word, RATING_KNOWN, on=DAY)

    assert updated.interval_days == MASTERED_INTERVAL_DAYS
    assert updated.status == "mastered"


def test_interval_is_capped() -> None:
    word = make_word(ease=3.0, interval_days=300, repetitions=6, status="learning")

    updated = schedule(word, RATING_EASY, on=DAY)

    assert updated.interval_days == MAX_INTERVAL_DAYS


def test_invalid_rating_raises() -> None:
    with pytest.raises(ValueError):
        schedule(make_word(), 5, on=DAY)


# ---------------------------------------------------------------- 复习服务
@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def test_due_words_filters_future_and_mastered(db: Database) -> None:
    due = db.words.upsert(make_word(lemma="due", due_date="2026-11-19"))
    db.words.upsert(make_word(lemma="future", due_date="2026-12-01"))
    db.words.upsert(make_word(lemma="mastered", due_date="2026-11-19", status="mastered"))
    db.words.upsert(make_word(lemma="no-due", due_date=None))

    service = ReviewService(db.words, db.reviews, db.stats, today=lambda: "2026-11-19")
    words = service.due_words()

    assert [word.lemma for word in words] == ["due"]
    assert due.id is not None
    assert service.count_due() == 1


def test_count_due_is_not_truncated_by_queue_limit(db: Database) -> None:
    """到期词超过 200 时，count_due 仍返回精确值（due_words 只加载 200 条）。"""
    for index in range(205):
        db.words.upsert(make_word(lemma=f"word{index}", due_date="2026-11-19"))

    service = ReviewService(db.words, db.reviews, db.stats, today=lambda: "2026-11-19")

    assert len(service.due_words()) == 200
    assert service.count_due() == 205


def test_review_persists_schedule_logs_and_stats(db: Database) -> None:
    stored = db.words.upsert(make_word(due_date="2026-11-19"))
    assert stored.id is not None
    service = ReviewService(db.words, db.reviews, db.stats, today=lambda: "2026-11-19")

    outcome = service.review(stored, RATING_KNOWN)

    assert outcome.prev_interval == 0
    assert outcome.next_interval == 1

    reloaded = db.words.get_by_id(stored.id)
    assert reloaded is not None
    assert reloaded.due_date == "2026-11-20"
    assert reloaded.interval_days == 1
    assert reloaded.repetitions == 1
    assert reloaded.status == "learning"

    logs = db.reviews.list_for_word(stored.id)
    assert len(logs) == 1
    assert logs[0].rating == RATING_KNOWN
    assert logs[0].prev_interval == 0
    assert logs[0].next_interval == 1
    # reviewed_at 用真实时钟（now_iso），这里不能写死日期；调度用的“今天”才是注入值。
    assert db.reviews.count_for_date(today_str()) == 1

    assert db.stats.get("2026-11-19").reviews_done == 1
    # 复习后这个词不再出现在到期队列
    assert service.due_words() == []


def test_review_logs_user_reason_and_ai_advice(db: Database) -> None:
    stored = db.words.upsert(make_word())
    assert stored.id is not None
    service = ReviewService(db.words, db.reviews, db.stats, today=lambda: "2026-11-19")

    service.review(
        stored,
        RATING_FORGOT,
        note="总是和 resistant 混淆",
        advice="resilient 强调恢复力",
    )

    logs = db.reviews.list_for_word(stored.id)
    assert logs[0].note == "总是和 resistant 混淆"
    assert logs[0].advice == "resilient 强调恢复力"


def test_review_persists_spelling_evidence_without_changing_schedule(db: Database) -> None:
    """证据只落库，调度必须仍然只由 rating 决定（与不带证据时一致）。"""
    stored = db.words.upsert(make_word())
    assert stored.id is not None
    service = ReviewService(db.words, db.reviews, db.stats, today=lambda: "2026-11-19")

    outcome = service.review(
        stored,
        RATING_VAGUE,
        answer_text="resilent",
        verdict="near",
        distance=1,
    )

    expected = schedule(stored, RATING_VAGUE, on=DAY)
    assert outcome.next_interval == expected.interval_days
    assert outcome.word.repetitions == expected.repetitions
    assert outcome.word.due_date == expected.due_date

    log = db.reviews.list_for_word(stored.id)[0]
    assert log.answer_text == "resilent"
    assert log.verdict == "near"
    assert log.distance == 1


def test_review_rejects_unknown_verdict_before_writing(db: Database) -> None:
    stored = db.words.upsert(make_word())
    assert stored.id is not None
    service = ReviewService(db.words, db.reviews, db.stats, today=lambda: "2026-11-19")

    with pytest.raises(ValueError):
        service.review(stored, RATING_KNOWN, verdict="guessed")

    assert db.reviews.list_for_word(stored.id) == []
    reloaded = db.words.get_by_id(stored.id)
    assert reloaded is not None and reloaded.interval_days == 0


def test_review_rejects_unsaved_word(db: Database) -> None:
    service = ReviewService(db.words, db.reviews, db.stats, today=lambda: "2026-11-19")
    with pytest.raises(ValueError):
        service.review(make_word(), RATING_KNOWN)


def test_review_works_without_logs_or_stats(db: Database) -> None:
    """服务允许只注入 WordRepo（测试 / 最小用法），不应该崩。"""
    stored = db.words.upsert(make_word())
    assert stored.id is not None
    service = ReviewService(db.words, today=lambda: "2026-11-19")

    outcome = service.review(stored, RATING_FORGOT)

    assert outcome.word.due_date == "2026-11-20"
    assert db.reviews.list_for_word(stored.id) == []
