"""M8 阅读检测错因 / 薄弱点（纯本地，无网络、无 GUI）。"""

from __future__ import annotations

from datetime import date

from english_learner.models.entities import Article, QuizQuestion, ReadingQuiz, now_iso
from english_learner.services.quiz_insights import QuizInsightsService
from english_learner.storage.db import Database

TODAY = date(2026, 11, 27)


def add_quiz(
    db: Database,
    *,
    day: str,
    answers: tuple[int, ...],
    error_types: tuple[str, ...],
    practice: bool = False,
    level: str = "B1",
) -> None:
    article = db.articles.insert(
        Article(
            article_date=day,
            title=f"{level}-{day}-{practice}",
            content="content",
            level=level,
            created_at=now_iso(),
        )
    )
    assert article.id is not None
    questions = (
        QuizQuestion(
            prompt="q0",
            options=("a", "b"),
            answer_index=0,
            skill="detail",
            difficulty="B1",
        ),
        QuizQuestion(
            prompt="q1",
            options=("a", "b"),
            answer_index=0,
            skill="inference",
            difficulty="B2",
        ),
        QuizQuestion(
            prompt="q2",
            options=("a", "b"),
            answer_index=0,
            skill="main_idea",
            difficulty="B2",
        ),
        QuizQuestion(
            prompt="q3",
            options=("a", "b"),
            answer_index=0,
            skill="detail",
            difficulty="B1",
        ),
    )
    quiz = db.quizzes.insert(
        ReadingQuiz(
            article_id=article.id,
            article_date=day,
            level=level,
            questions=questions,
            practice_mode=practice,
            created_at=now_iso(),
        )
    )
    assert quiz.id is not None
    db.quizzes.save_result(
        quiz.id,
        answers=answers,
        explanations=("", "", "", ""),
        error_types=error_types,
        score=sum(1 for answer in answers if answer == 0),
        previous_level=level,
        result_level=level,
        feedback="",
        completed_at=f"{day}T10:00:00",
        practice_mode=practice,
    )


def test_empty_database_returns_empty_insights(tmp_path) -> None:
    db = Database(tmp_path / "data.db")
    try:
        insights = QuizInsightsService(db.quizzes).build(on=TODAY)
    finally:
        db.close()

    assert insights.has_data is False
    assert insights.quizzes == 0
    assert insights.errors == ()
    assert insights.unknown_rate is None


def test_error_types_skills_and_difficulty(tmp_path) -> None:
    db = Database(tmp_path / "data.db")
    try:
        add_quiz(
            db,
            day=TODAY.isoformat(),
            answers=(0, 1, -2, 0),
            error_types=("none", "inference", "unknown", "none"),
        )
        insights = QuizInsightsService(db.quizzes).build(on=TODAY)
    finally:
        db.close()

    assert insights.has_data is True
    assert insights.quizzes == 1
    assert dict(insights.errors) == {"inference": 1, "unknown": 1}
    assert insights.unknown_choices == 1
    assert insights.choice_total == 4
    assert insights.unknown_rate == 0.25

    skills = {item.skill: item for item in insights.skills}
    assert skills["detail"].correct == 2
    assert skills["detail"].total == 2
    assert skills["inference"].correct == 0
    assert skills["main_idea"].correct == 0
    # 正确率低的技能排前面。
    assert insights.skills[0].skill in {"inference", "main_idea"}

    difficulties = {item.level: item for item in insights.difficulties}
    assert difficulties["B1"].correct == 2
    assert difficulties["B1"].total == 2
    assert difficulties["B2"].correct == 0
    assert difficulties["B2"].total == 2


def test_practice_mode_and_out_of_window_are_excluded(tmp_path) -> None:
    db = Database(tmp_path / "data.db")
    try:
        add_quiz(
            db,
            day=TODAY.isoformat(),
            answers=(0, 1, -2, 0),
            error_types=("none", "inference", "unknown", "none"),
            practice=True,
        )
        add_quiz(
            db,
            day="2026-10-01",
            answers=(0, 1, -2, 0),
            error_types=("none", "inference", "unknown", "none"),
        )
        insights = QuizInsightsService(db.quizzes).build(on=TODAY, window_days=30)
    finally:
        db.close()

    assert insights.has_data is False
    assert insights.quizzes == 0


def test_unanswered_choice_excluded_from_unknown_rate(tmp_path) -> None:
    """-1 是未作答，不进“不知道”比例的分母。"""
    db = Database(tmp_path / "data.db")
    try:
        add_quiz(
            db,
            day=TODAY.isoformat(),
            answers=(0, -1, -2, 0),
            error_types=("none", "unknown", "unknown", "none"),
        )
        insights = QuizInsightsService(db.quizzes).build(on=TODAY)
    finally:
        db.close()

    # 4 道选择题里 1 道未作答，分母应为 3；其中 1 道“不知道”。
    assert insights.choice_total == 3
    assert insights.unknown_choices == 1
    assert insights.unknown_rate == 1 / 3
