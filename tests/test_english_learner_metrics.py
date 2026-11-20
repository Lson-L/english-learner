"""M5.5 学习效果度量：首次通过率 / 回炉率 / 计划完成率 / 重试率 / 得分趋势。

全部用例只读本地 SQLite，不构造 AI 客户端、不发网络请求。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from english_learner.models.entities import (
    AICall,
    Article,
    QuizQuestion,
    ReadingQuiz,
    WeeklyReview,
    Word,
    WritingAttempt,
    now_iso,
)
from english_learner.services.metrics_service import (
    MetricsService,
    QuizPoint,
    QuizTrend,
    plan_categories,
)
from english_learner.storage.db import Database

ON = date(2026, 11, 19)  # 周四；本周一 = 2026-11-16，上周一 = 2026-11-09


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def make_service(db: Database) -> MetricsService:
    return MetricsService(
        db.reviews,
        db.stats,
        db.quizzes,
        db.weekly_reviews,
        db.ai_calls,
        db.writing_attempts,
        today=lambda: ON.isoformat(),
    )


def seed_word(db: Database, lemma: str) -> Word:
    return db.words.upsert(
        Word(
            lemma=lemma,
            meaning_cn=f"{lemma} 的释义",
            in_vocabulary=True,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )


def log_review(
    db: Database,
    word: Word,
    rating: int,
    at: str,
) -> None:
    assert word.id is not None
    db.reviews.log(
        word_id=word.id,
        rating=rating,
        prev_interval=1,
        next_interval=1,
        reviewed_at=at,
    )


def make_questions(count: int = 4) -> tuple[QuizQuestion, ...]:
    return tuple(
        QuizQuestion(prompt=f"q{index}", options=("a", "b"), answer_index=0)
        for index in range(count)
    )


def seed_quiz(
    db: Database,
    *,
    article_date: str,
    level: str,
    score: int,
    practice: bool = False,
    total: int = 4,
    completed_at: str | None = None,
) -> ReadingQuiz:
    article = db.articles.insert(
        Article(
            article_date=article_date,
            title=f"文章 {article_date}",
            content="content",
            level=level,
            created_at=now_iso(),
        )
    )
    assert article.id is not None
    quiz = db.quizzes.insert(
        ReadingQuiz(
            article_id=article.id,
            article_date=article_date,
            level=level,
            questions=make_questions(total),
            created_at=now_iso(),
        )
    )
    assert quiz.id is not None
    return db.quizzes.save_result(
        quiz.id,
        answers=(),
        explanations=(),
        score=score,
        previous_level=level,
        result_level=level,
        feedback="",
        completed_at=completed_at or f"{article_date}T20:00:00",
        practice_mode=practice,
    )


# ---------------------------------------------------------------- 空库与窗口
def test_empty_database_returns_neutral_snapshot(db: Database) -> None:
    snapshot = make_service(db).build(on=ON)

    assert snapshot.window_days == 30
    assert snapshot.window_start == "2026-10-21"  # 含今天共 30 天
    assert snapshot.window_end == "2026-11-19"
    assert snapshot.first_pass.rate is None
    assert snapshot.relearn.rate is None
    assert snapshot.plan is None
    assert snapshot.article_retry.rate is None
    assert snapshot.quiz_retry.rate is None
    assert snapshot.quiz_trends == ()
    assert snapshot.has_data is False


def test_window_days_is_configurable(db: Database) -> None:
    outside = seed_word(db, "outside")
    inside = seed_word(db, "inside")
    log_review(db, outside, 4, "2026-11-10T20:00:00")
    log_review(db, inside, 2, "2026-11-15T20:00:00")

    snapshot = make_service(db).build(on=ON, window_days=7)

    assert snapshot.window_start == "2026-11-13"
    assert snapshot.first_pass.total == 1
    assert snapshot.first_pass.hit == 0


# ------------------------------------------------------------ 首次通过 / 回炉
def test_first_pass_only_counts_first_review_inside_window(db: Database) -> None:
    first_good = seed_word(db, "alpha")
    first_old = seed_word(db, "bravo")
    first_bad = seed_word(db, "charlie")
    untouched = seed_word(db, "delta")
    never_reviewed = seed_word(db, "echo")

    # alpha：窗口内首次复习评 4，后来评 1，首次通过仍应计入。
    log_review(db, first_good, 4, "2026-11-10T20:00:00")
    log_review(db, first_good, 1, "2026-11-18T20:00:00")
    # bravo：首次复习在窗口外，不能进入首次通过率分母。
    log_review(db, first_old, 3, "2026-10-01T20:00:00")
    log_review(db, first_old, 1, "2026-11-15T20:00:00")
    # charlie：窗口内首次复习评 2，属于未通过。
    log_review(db, first_bad, 2, "2026-11-16T20:00:00")
    # delta：窗口内首次复习评 3，通过。
    log_review(db, untouched, 3, "2026-11-18T20:00:00")

    snapshot = make_service(db).build(on=ON)

    assert snapshot.first_pass.total == 3  # alpha / charlie / delta
    assert snapshot.first_pass.hit == 2  # alpha / delta
    assert snapshot.first_pass.rate == pytest.approx(2 / 3)

    # 回炉率：窗口内复习过的 4 个词里，alpha / bravo / charlie 出现过弱评分。
    assert snapshot.relearn.total == 4
    assert snapshot.relearn.hit == 3
    assert snapshot.relearn.rate == pytest.approx(0.75)
    assert never_reviewed.lemma == "echo"


def test_first_review_tie_breaks_by_id(db: Database) -> None:
    word = seed_word(db, "tie")
    # 同一秒内两条记录：先写 rating=1，按 id 排序它才是“首次”。
    log_review(db, word, 1, "2026-11-18T20:00:00")
    log_review(db, word, 4, "2026-11-18T20:00:00")

    snapshot = make_service(db).build(on=ON)

    assert snapshot.first_pass.total == 1
    assert snapshot.first_pass.hit == 0


def test_future_records_are_excluded_from_window(db: Database) -> None:
    """系统时钟异常 / 手工写入的未来记录不能进入“近 N 天”。"""
    word = seed_word(db, "future")
    log_review(db, word, 4, "2026-11-25T20:00:00")  # 今天之后
    db.ai_calls.insert(AICall(called_at="2026-11-25T10:00:00", task="每日文章", attempt=2, ok=True))

    snapshot = make_service(db).build(on=ON)

    assert snapshot.first_pass.total == 0
    assert snapshot.relearn.total == 0
    assert snapshot.article_retry.attempts == 0


# ---------------------------------------------------------------- 计划完成率
def test_plan_categories_map_and_ignore_unsupported_items() -> None:
    assert plan_categories("每天复习 10 个生词") == frozenset({"review", "vocabulary"})
    assert plan_categories("重点做推断题") == frozenset({"quiz"})
    assert plan_categories("读两篇文章") == frozenset({"reading"})
    assert plan_categories("练习写作和翻译题") == frozenset({"writing", "quiz"})
    assert plan_categories("练习听力") == frozenset()
    assert plan_categories("") == frozenset()


def test_plan_completion_uses_last_week_focus_and_this_week_activity(db: Database) -> None:
    db.weekly_reviews.save(
        WeeklyReview(
            week_start="2026-11-09",
            summary="上周复盘",
            focus=("每天复习 10 个生词", "重点做推断题", "读两篇文章", "练习听力"),
            created_at="2026-11-15T20:00:00",
        )
    )
    # 本周有复习和阅读，没有检测。
    db.stats.bump("2026-11-17", reviews_done=2, article_read=1)

    plan = make_service(db).build(on=ON).plan

    assert plan is not None
    assert plan.plan_week == "2026-11-09"
    assert plan.measured_week == "2026-11-16"
    assert plan.planned == 3  # 听力无法映射，不进分母
    assert plan.completed == 2  # 复习 / 阅读完成，检测未完成
    assert plan.rate == pytest.approx(2 / 3)


def test_plan_completion_requires_last_week_review(db: Database) -> None:
    # 只有本周复盘：不能拿本周计划评估本周（当前周还没结束）。
    db.weekly_reviews.save(
        WeeklyReview(
            week_start="2026-11-16",
            summary="本周复盘",
            focus=("每天复习 10 个生词",),
            created_at="2026-11-19T20:00:00",
        )
    )
    assert make_service(db).build(on=ON).plan is None

    # 上周复盘但 focus 为空：也没有可评估的计划。
    db.weekly_reviews.save(
        WeeklyReview(
            week_start="2026-11-09",
            summary="上周复盘",
            focus=(),
            created_at="2026-11-15T20:00:00",
        )
    )
    assert make_service(db).build(on=ON).plan is None


def test_plan_completion_is_none_when_no_focus_item_maps(db: Database) -> None:
    db.weekly_reviews.save(
        WeeklyReview(
            week_start="2026-11-09",
            summary="上周复盘",
            focus=("练习听力", "练习口语"),
            created_at="2026-11-15T20:00:00",
        )
    )

    snapshot = make_service(db).build(on=ON)

    assert snapshot.plan is None
    assert snapshot.has_data is False


def test_plan_completion_counts_quiz_completed_after_week_boundary(db: Database) -> None:
    """周日读的文章、周一凌晨提交的检测，算在本周完成。"""
    db.weekly_reviews.save(
        WeeklyReview(
            week_start="2026-11-09",
            summary="上周复盘",
            focus=("做一次阅读检测",),
            created_at="2026-11-15T20:00:00",
        )
    )
    seed_quiz(
        db,
        article_date="2026-11-15",  # 上周日
        level="B1",
        score=3,
        completed_at="2026-11-16T00:05:00",  # 本周一凌晨
    )

    plan = make_service(db).build(on=date(2026, 11, 16)).plan

    assert plan is not None
    assert plan.planned == 1
    assert plan.completed == 1
    assert plan.rate == pytest.approx(1.0)


def test_plan_completion_can_use_writing_activity(db: Database) -> None:
    db.weekly_reviews.save(
        WeeklyReview(
            week_start="2026-11-09",
            summary="上周复盘",
            focus=("练习写作",),
            created_at="2026-11-15T20:00:00",
        )
    )
    article = db.articles.insert(
        Article(
            article_date="2026-11-17",
            title="写作文章",
            content="content",
            level="B1",
            created_at=now_iso(),
        )
    )
    assert article.id is not None
    db.writing_attempts.replace_for_article(
        article.id,
        [
            WritingAttempt(
                article_id=article.id,
                article_date="2026-11-17",
                position=0,
                sentence_en="The sky is blue.",
                reference_cn="天空是蓝色的。",
                created_at=now_iso(),
            )
        ],
    )
    db.writing_attempts.save_grades(
        article.id,
        [
            (
                0,
                "The sky is blue.",
                {"edited": "", "reason": "", "error_types": []},
                2,
                "2026-11-17T20:00:00",
            )
        ],
    )

    plan = make_service(db).build(on=ON).plan

    assert plan is not None
    assert plan.planned == 1
    assert plan.completed == 1
    assert plan.rate == pytest.approx(1.0)


# ---------------------------------------------------------------- 重试率
def test_retry_metrics_are_split_by_article_and_quiz_tasks(db: Database) -> None:
    db.ai_calls.insert(AICall(called_at="2026-11-18T10:00:00", task="每日文章", attempt=1, ok=True))
    db.ai_calls.insert(AICall(called_at="2026-11-18T10:00:05", task="每日文章", attempt=2, ok=True))
    db.ai_calls.insert(
        AICall(called_at="2026-11-18T11:00:00", task="阅读检测·讲评", attempt=1, ok=False)
    )
    db.ai_calls.insert(
        AICall(called_at="2026-11-18T11:00:05", task="阅读检测·讲评", attempt=2, ok=True)
    )
    # 其他任务与窗口外的调用都不参与。
    db.ai_calls.insert(AICall(called_at="2026-11-18T12:00:00", task="查词", attempt=1, ok=True))
    db.ai_calls.insert(AICall(called_at="2026-09-01T12:00:00", task="每日文章", attempt=2, ok=True))

    snapshot = make_service(db).build(on=ON)

    assert snapshot.article_retry.attempts == 2
    assert snapshot.article_retry.retries == 1
    assert snapshot.article_retry.failures == 0
    assert snapshot.article_retry.rate == pytest.approx(0.5)

    assert snapshot.quiz_retry.attempts == 2
    assert snapshot.quiz_retry.retries == 1
    assert snapshot.quiz_retry.failures == 1
    assert snapshot.quiz_retry.rate == pytest.approx(0.5)


# ---------------------------------------------------------------- 得分趋势
def test_quiz_trends_group_by_level_and_skip_practice(db: Database) -> None:
    for day, score in (
        ("2026-11-01", 1),
        ("2026-11-05", 2),
        ("2026-11-10", 3),
        ("2026-11-15", 4),
        ("2026-11-18", 4),
    ):
        seed_quiz(db, article_date=day, level="B1", score=score)
    # 练习模式不进趋势（否则会把最近均值拉到 75%）。
    seed_quiz(db, article_date="2026-11-17", level="B1", score=0, practice=True)
    # 另一个等级单独归组，样本不足时没有趋势。
    seed_quiz(db, article_date="2026-11-12", level="A2", score=2)

    trends = make_service(db).build(on=ON).quiz_trends

    assert [trend.level for trend in trends] == ["A2", "B1"]
    a2, b1 = trends
    assert len(a2.points) == 1
    assert a2.delta is None
    assert a2.direction == "unknown"

    assert len(b1.points) == 5
    assert b1.latest_percent == 100
    assert b1.recent_avg == pytest.approx((75 + 100 + 100) / 3)
    assert b1.previous_avg == pytest.approx((25 + 50) / 2)
    assert b1.delta == pytest.approx((75 + 100 + 100) / 3 - 37.5)
    assert b1.direction == "up"


def test_quiz_trend_direction_thresholds() -> None:
    points = (
        QuizPoint(article_date="2026-11-01", level="B1", score=1, total=4),
        QuizPoint(article_date="2026-11-02", level="B1", score=1, total=4),
    )

    assert QuizTrend("B1", points, recent_avg=60, previous_avg=58).direction == "flat"
    # 正好 ±3 个百分点仍算持平（文档口径是“±3 内”）。
    assert QuizTrend("B1", points, recent_avg=63, previous_avg=60).direction == "flat"
    assert QuizTrend("B1", points, recent_avg=60, previous_avg=63).direction == "flat"
    assert QuizTrend("B1", points, recent_avg=63.1, previous_avg=60).direction == "up"
    assert QuizTrend("B1", points, recent_avg=60, previous_avg=56).direction == "up"
    assert QuizTrend("B1", points, recent_avg=50, previous_avg=54).direction == "down"
    assert QuizTrend("B1", points, recent_avg=60, previous_avg=None).direction == "unknown"


def test_quiz_trend_window_uses_completion_date(db: Database) -> None:
    """文章日期在窗口外、完成日期在窗口内的检测仍应计入趋势。"""
    seed_quiz(
        db,
        article_date="2026-10-15",  # 早于 30 天窗口起点 10-21
        level="B1",
        score=3,
        completed_at="2026-11-17T20:00:00",
    )

    trends = make_service(db).build(on=ON).quiz_trends

    assert len(trends) == 1
    assert trends[0].level == "B1"
    assert trends[0].points[0].article_date == "2026-10-15"
    assert trends[0].latest_percent == 75
