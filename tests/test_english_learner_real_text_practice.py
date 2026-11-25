"""M7.1 / M7.2：真实文章上架、预置题目、本地判分与完成反馈。"""

from __future__ import annotations

from pathlib import Path

import pytest

from english_learner.models.entities import (
    Profile,
    QuizQuestion,
    RealText,
    now_iso,
)
from english_learner.services.article_service import ArticleDraft, ArticleService
from english_learner.services.readability import ReadabilityThresholds
from english_learner.services.reading_quiz_service import ReadingQuizService
from english_learner.storage.db import Database

TODAY = "2026-11-25"


class FailingCompleter:
    """任何 AI 调用都算失败：真实文章路径必须 0 次请求。"""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("真实文章练习不应调用 AI")

    def complete_json(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("真实文章练习不应调用 AI")


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


@pytest.fixture
def profile(db: Database) -> Profile:
    return db.profiles.save(
        Profile(
            cefr_level="B1",
            vocab_estimate=3800,
            assessed_at="2026-11-01T10:00:00",
        )
    )


def make_questions() -> tuple[QuizQuestion, ...]:
    return (
        QuizQuestion(
            prompt="What is the passage mainly about?",
            options=("A", "B", "C", "D"),
            answer_index=0,
            explanation="第一段点明主题。",
        ),
        QuizQuestion(
            prompt="Why did the writer go there?",
            options=("A", "B", "C", "D"),
            answer_index=1,
            explanation="第二段说明原因。",
        ),
        QuizQuestion(
            prompt="What does the word mean?",
            options=("A", "B", "C", "D"),
            answer_index=2,
            explanation="上下文推断。",
        ),
        QuizQuestion(
            prompt="What can we infer?",
            options=("A", "B", "C", "D"),
            answer_index=3,
            explanation="结尾暗示。",
        ),
    )


def make_text() -> RealText:
    return RealText(
        id="b1-test",
        title="A Real Story",
        content="First paragraph of the story.\n\nSecond paragraph with more detail.",
        level="B1",
        source="Test Source",
        source_url="https://example.com/story",
        license="Public Domain",
        topics=("science",),
        word_count=13,
        questions=make_questions(),
        created_at=now_iso(),
    )


def make_services(db: Database) -> tuple[ArticleService, ReadingQuizService, FailingCompleter]:
    client = FailingCompleter()
    quizzes = ReadingQuizService(
        client,
        db.quizzes,
        db.profiles,
        db.evidence,
        db.reviews,
        shuffle_options=False,
        real_text_repo=db.real_texts,
    )
    articles = ArticleService(
        client,
        db.articles,
        db.words,
        readability=ReadabilityThresholds(enabled=False),
        quiz_seeder=quizzes.seed_bundled_quiz,
        real_text_repo=db.real_texts,
    )
    return articles, quizzes, client


def test_serve_real_text_writes_article_quiz_and_history(db: Database, profile: Profile) -> None:
    text = db.real_texts.upsert(make_text())
    articles, quizzes, client = make_services(db)

    article = articles.serve_real_text(text, today=TODAY)

    assert article is not None
    assert article.source == "real"
    assert article.source_ref == "b1-test"
    assert article.source_url == "https://example.com/story"
    assert article.level == "B1"
    assert article.topic == "science"

    quiz = quizzes.get_cached(article.id or 0)
    assert quiz is not None
    assert quiz.stage == 2
    assert quiz.practice_mode is True
    assert len(quiz.questions) == 4

    history = db.real_texts.last_history()
    assert history is not None
    assert history.status == "served"
    assert history.text_id == "b1-test"
    assert history.article_id == article.id
    assert client.calls == 0


def test_serve_real_text_rolls_back_when_quiz_seeding_fails(db: Database) -> None:
    text = db.real_texts.upsert(make_text())

    def broken_seeder(article, real_text):
        raise RuntimeError("quiz seed failed")

    articles = ArticleService(
        FailingCompleter(),
        db.articles,
        db.words,
        readability=ReadabilityThresholds(enabled=False),
        quiz_seeder=broken_seeder,
        real_text_repo=db.real_texts,
    )

    assert articles.serve_real_text(text, today=TODAY) is None
    assert db.articles.get_by_date(TODAY) is None
    assert db.real_texts.last_history() is None


def test_serve_real_text_does_not_overwrite_existing_article(db: Database) -> None:
    from english_learner.models.entities import Article

    db.articles.insert(
        Article(
            article_date=TODAY,
            title="AI article",
            content="Existing content.",
            level="B1",
            word_count=2,
            created_at=now_iso(),
        )
    )
    text = db.real_texts.upsert(make_text())
    articles, _, _ = make_services(db)

    assert articles.serve_real_text(text, today=TODAY) is None
    existing = db.articles.get_by_date(TODAY)
    assert existing is not None and existing.title == "AI article"
    assert db.real_texts.last_history() is None


def test_local_practice_submit_success_is_zero_ai(db: Database, profile: Profile) -> None:
    text = db.real_texts.upsert(make_text())
    articles, quizzes, client = make_services(db)
    article = articles.serve_real_text(text, today=TODAY)
    assert article is not None and article.id is not None
    quiz = quizzes.get_cached(article.id)
    assert quiz is not None

    outcome = quizzes.submit(
        article,
        quiz,
        (0, 1, 2, 3),
        profile,
        self_report="ok",
    )

    assert outcome.practice_mode is True
    assert outcome.quiz.completed
    assert outcome.quiz.score == 4
    assert outcome.quiz.total == 4
    assert outcome.quiz.feedback == "真实文章练习：本地判分，不调整等级"
    assert outcome.profile.cefr_level == profile.cefr_level
    assert outcome.quiz.explanations[0] == "第一段点明主题。"
    assert client.calls == 0

    # 不写等级证据、不改画像。
    assert db.evidence.count_on(TODAY) == 0
    stored_profile = db.profiles.get()
    assert stored_profile is not None
    assert stored_profile.cefr_level == "B1"
    assert stored_profile.assessed_at == profile.assessed_at

    history = db.real_texts.last_history()
    assert history is not None
    assert history.status == "completed"
    assert history.score == pytest.approx(1.0)
    assert history.self_report == "ok"
    assert history.outcome == "success"
    assert history.completed_at


def test_local_practice_struggle_outcome(db: Database, profile: Profile) -> None:
    text = db.real_texts.upsert(make_text())
    articles, quizzes, _ = make_services(db)
    article = articles.serve_real_text(text, today=TODAY)
    assert article is not None and article.id is not None
    quiz = quizzes.get_cached(article.id)
    assert quiz is not None

    outcome = quizzes.submit(article, quiz, (0, 0, 0, 0), profile, self_report="hard")

    assert outcome.quiz.score == 1
    history = db.real_texts.last_history()
    assert history is not None
    assert history.outcome == "struggle"
    assert history.score == pytest.approx(0.25)


def test_replace_today_marks_real_article_overridden_even_if_ui_cancelled(
    db: Database,
) -> None:
    """M7.2：替换在服务层落库时就记 overridden，不依赖界面信号是否被取消。"""
    text = db.real_texts.upsert(make_text())
    articles, _, _ = make_services(db)
    assert articles.serve_real_text(text, today=TODAY) is not None

    articles.replace_today(
        ArticleDraft(
            title="AI replacement",
            level="B1",
            topic="",
            paragraphs=("Replacement body.",),
        ),
        today=TODAY,
    )

    stored = db.articles.get_by_date(TODAY)
    assert stored is not None
    assert stored.source == "generated"
    history = db.real_texts.last_history()
    assert history is not None
    assert history.status == "overridden"
    assert history.outcome == "skip"
