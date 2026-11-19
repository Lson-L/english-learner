"""Phase 3 每周复盘：缓存、建议钳制、用户确认后改级。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from english_learner.models.entities import (
    Article,
    Profile,
    QuizQuestion,
    ReadingQuiz,
    Word,
    now_iso,
)
from english_learner.services.ai_client import AIError
from english_learner.services.weekly_review_service import WeeklyReviewService
from english_learner.storage.db import Database

ON = date(2026, 11, 19)  # 周四；本周一 = 2026-11-16


class FakeCompleter:
    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.messages: list[list[dict[str, str]]] = []

    def complete_json(self, messages, *, temperature=None, max_tokens=None, task="") -> str:
        self.calls += 1
        self.messages.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def review_payload(
    *,
    suggested: str = "B2",
    confidence: float = 0.7,
    focus: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "summary": "本周完成 1 次检测，阅读稳定性提升，推断题仍需练习。",
            "suggested_level": suggested,
            "focus": focus or ["推断题", "B2 高频词"],
            "confidence": confidence,
        },
        ensure_ascii=False,
    )


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def seed_week(db: Database) -> Profile:
    profile = db.profiles.save(
        Profile(
            cefr_level="B1",
            vocab_estimate=3800,
            assessed_at="2026-11-01T10:00:00",
        )
    )
    article = db.articles.insert(
        Article(
            article_date="2026-11-19",
            title="Small Habits",
            content="Small habits add up.",
            level="B1",
            word_count=4,
            created_at=now_iso(),
        )
    )
    assert article.id is not None
    quiz = db.quizzes.insert(
        ReadingQuiz(
            article_id=article.id,
            article_date=article.article_date,
            level="B1",
            questions=(
                QuizQuestion(prompt="Q1?", options=("a", "b"), answer_index=0, difficulty="B1"),
                QuizQuestion(
                    prompt="Summarize.",
                    kind="short",
                    skill="writing",
                    difficulty="B1",
                ),
            ),
            created_at=now_iso(),
        )
    )
    assert quiz.id is not None
    db.quizzes.save_result(
        quiz.id,
        answers=(0,),
        explanations=("好", "达意"),
        error_types=("none", "short_answer"),
        short_answer_text="Habits add up.",
        short_answer_score=1,
        score=2,
        previous_level="B1",
        result_level="B1",
        feedback="本周检测完成。",
        completed_at="2026-11-19T20:00:00",
    )
    word = db.words.upsert(
        Word(
            lemma="resilient",
            meaning_cn="有韧性的",
            in_vocabulary=True,
            cefr_band="B2",
            due_date="2026-11-19",
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    assert word.id is not None
    db.reviews.log(
        word_id=word.id,
        rating=3,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-19T21:00:00",
    )
    db.stats.bump("2026-11-19", article_read=1, words_added=1, reviews_done=1)
    return profile


def make_service(db: Database, fake: FakeCompleter) -> WeeklyReviewService:
    return WeeklyReviewService(
        fake,
        db.profiles,
        db.quizzes,
        db.reviews,
        db.stats,
        db.weekly_reviews,
        db.words,
        db.evidence,
    )


def test_week_start_is_monday() -> None:
    assert WeeklyReviewService.week_start(date(2026, 11, 19)) == "2026-11-16"
    assert WeeklyReviewService.week_start(date(2026, 11, 16)) == "2026-11-16"
    assert WeeklyReviewService.week_start(date(2026, 11, 22)) == "2026-11-16"


def test_get_or_generate_caches_per_week(db: Database) -> None:
    profile = seed_week(db)
    fake = FakeCompleter(review_payload())
    service = make_service(db, fake)

    first = service.get_or_generate(profile, on=ON)
    second = service.get_or_generate(profile, on=ON)

    assert fake.calls == 1
    assert first.id is not None
    assert second.id == first.id
    assert first.week_start == "2026-11-16"
    assert first.suggested_level == "B2"
    assert first.focus == ("推断题", "B2 高频词")
    assert first.quiz_count == 1
    assert first.review_count == 1
    assert first.words_added == 1

    prompt = fake.messages[0][-1]["content"]
    assert "2026-11-16" in prompt
    assert "Small Habits" in prompt
    assert "B2 词档" in prompt


def test_no_data_raises_without_calling_ai(db: Database) -> None:
    profile = db.profiles.save(Profile(cefr_level="B1"))
    fake = FakeCompleter()
    service = make_service(db, fake)

    with pytest.raises(AIError, match="学习数据"):
        service.get_or_generate(profile, on=ON)
    assert fake.calls == 0


def test_articles_only_week_is_enough_for_review(db: Database) -> None:
    profile = db.profiles.save(Profile(cefr_level="B1"))
    db.stats.bump("2026-11-19", article_read=1)
    fake = FakeCompleter(review_payload(suggested="B1", confidence=0.7))
    service = make_service(db, fake)

    review = service.get_or_generate(profile, on=ON)

    assert fake.calls == 1
    assert review.quiz_count == 0
    assert review.words_added == 0
    assert review.review_count == 0


def test_invalid_suggestion_keeps_current(db: Database) -> None:
    profile = seed_week(db)
    fake = FakeCompleter(review_payload(suggested="Z9"))
    service = make_service(db, fake)

    review = service.get_or_generate(profile, on=ON)

    assert review.suggested_level == "B1"


def test_low_confidence_suggestion_keeps_current(db: Database) -> None:
    profile = seed_week(db)
    fake = FakeCompleter(review_payload(suggested="C1", confidence=0.2))
    service = make_service(db, fake)

    review = service.get_or_generate(profile, on=ON)

    assert review.suggested_level == "B1"


def test_suggestion_is_clamped_to_one_step(db: Database) -> None:
    profile = seed_week(db)
    fake = FakeCompleter(review_payload(suggested="C1", confidence=0.9))
    service = make_service(db, fake)

    review = service.get_or_generate(profile, on=ON)

    assert review.suggested_level == "B2"


def test_apply_suggestion_updates_profile_only_after_confirmation(db: Database) -> None:
    profile = seed_week(db)
    fake = FakeCompleter(review_payload(suggested="B2", confidence=0.7))
    service = make_service(db, fake)
    review = service.get_or_generate(profile, on=ON)

    # 生成阶段不改画像
    stored = db.profiles.get()
    assert stored is not None and stored.cefr_level == "B1"

    updated = service.apply_suggestion(review, stored)

    assert updated.cefr_level == "B2"
    assert updated.pending_level == ""
    assert updated.pending_count == 0
    assert updated.level_confidence >= 0.6
    assert "每周复盘" in updated.level_updated_reason

    applied = db.weekly_reviews.get_by_week("2026-11-16")
    assert applied is not None
    assert applied.applied is True
    assert applied.applied_level == "B2"

    # 幂等：再次调用不会重复写
    again = service.apply_suggestion(applied, updated)
    assert again == updated


def test_apply_same_level_just_marks_applied(db: Database) -> None:
    profile = seed_week(db)
    fake = FakeCompleter(review_payload(suggested="B1", confidence=0.7))
    service = make_service(db, fake)
    review = service.get_or_generate(profile, on=ON)

    updated = service.apply_suggestion(review, profile)

    assert updated.cefr_level == "B1"
    assert updated.level_confidence == profile.level_confidence
    applied = db.weekly_reviews.get_by_week("2026-11-16")
    assert applied is not None and applied.applied_level == "B1"
