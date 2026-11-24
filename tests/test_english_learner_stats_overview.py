"""统计页数据聚合（纯本地，无网络、无 GUI）。"""

from __future__ import annotations

from datetime import date, timedelta

from english_learner.models.entities import (
    Article,
    QuizQuestion,
    ReadingQuiz,
    Word,
    now_iso,
)
from english_learner.services.stats_overview import (
    ACTIVITY_DAYS,
    CUMULATIVE_DAYS,
    HEATMAP_WEEKS,
    Achievement,
    StatsOverviewService,
    heat_level,
    nice_max,
)
from english_learner.storage.db import Database


def make_service(db: Database, today: date) -> StatsOverviewService:
    return StatsOverviewService(
        db.stats,
        db.words,
        db.quizzes,
        today=lambda: today.isoformat(),
    )


def add_word(db: Database, lemma: str, *, status: str = "new", in_vocabulary: bool = True) -> None:
    db.words.upsert(
        Word(
            lemma=lemma,
            status=status,
            in_vocabulary=in_vocabulary,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )


def add_quiz(
    db: Database,
    *,
    day: str,
    level: str,
    score: int,
    total: int,
    practice: bool = False,
    completed: bool = True,
) -> None:
    article = db.articles.insert(
        Article(
            article_date=day,
            title=f"{level}-{day}",
            content="content",
            level=level,
            created_at=now_iso(),
        )
    )
    assert article.id is not None
    questions = tuple(
        QuizQuestion(prompt=f"q{index}", options=("a", "b"), answer_index=0)
        for index in range(total)
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
    if completed:
        db.quizzes.save_result(
            quiz.id,
            answers=(),
            explanations=(),
            score=score,
            previous_level=level,
            result_level=level,
            feedback="",
            completed_at=f"{day}T10:00:00",
            practice_mode=practice,
        )


# ------------------------------------------------------------------ 纯函数
def test_heat_level_thresholds() -> None:
    assert heat_level(0) == 0
    assert heat_level(1) == 1
    assert heat_level(2) == 1
    assert heat_level(3) == 2
    assert heat_level(5) == 2
    assert heat_level(6) == 3
    assert heat_level(10) == 3
    assert heat_level(11) == 4


def test_nice_max_rounds_up_to_clean_numbers() -> None:
    assert nice_max(0) == 1
    assert nice_max(1) == 1
    assert nice_max(3) == 5
    assert nice_max(7) == 10
    assert nice_max(37) == 50
    assert nice_max(120) == 200


def test_achievement_progress_and_unlock() -> None:
    item = Achievement("x", "标题", "说明", current=3, target=10)
    assert item.unlocked is False
    assert item.progress == 0.3
    assert Achievement("x", "标题", "说明", current=10, target=10).unlocked is True


# ------------------------------------------------------------------ 空库
def test_empty_database_returns_complete_zero_snapshot(tmp_path) -> None:
    today = date(2026, 11, 24)
    db = Database(tmp_path / "data.db")
    try:
        overview = make_service(db, today).build(on=today)
    finally:
        db.close()

    assert overview.today == today.isoformat()
    assert overview.has_data is False
    assert overview.current_streak == 0
    assert overview.longest_streak == 0
    assert overview.active_days == 0
    assert overview.total_words == 0
    assert overview.total_reviews == 0
    assert overview.total_quizzes == 0

    assert len(overview.heatmap_days) == HEATMAP_WEEKS * 7
    sunday = today + timedelta(days=6 - today.weekday())
    assert (
        overview.heatmap_days[0].day == (sunday - timedelta(days=HEATMAP_WEEKS * 7 - 1)).isoformat()
    )
    assert all(day.level == 0 for day in overview.heatmap_days)
    assert any(day.future for day in overview.heatmap_days)

    assert len(overview.cumulative) == CUMULATIVE_DAYS
    assert all(point.words == 0 for point in overview.cumulative)
    assert len(overview.daily) == ACTIVITY_DAYS
    assert overview.word_status.total == 0
    assert overview.quiz_series == ()
    assert all(item.unlocked is False for item in overview.achievements)


# ------------------------------------------------------------------ 汇总
def test_totals_streak_and_heatmap_levels(tmp_path) -> None:
    today = date(2026, 11, 24)
    db = Database(tmp_path / "data.db")
    try:
        # 连续三天有活动；更早一天刻意断开，最长连续应为 3。
        db.stats.bump(today.isoformat(), article_read=1, words_added=2, reviews_done=12)
        db.stats.bump((today - timedelta(days=1)).isoformat(), reviews_done=8)
        db.stats.bump((today - timedelta(days=2)).isoformat(), words_added=3)
        db.stats.bump((today - timedelta(days=5)).isoformat(), article_read=1)
        overview = make_service(db, today).build(on=today)
    finally:
        db.close()

    assert overview.current_streak == 3
    assert overview.longest_streak == 3
    assert overview.active_days == 4
    assert overview.total_articles == 2
    assert overview.total_reviews == 20

    by_day = {day.day: day for day in overview.heatmap_days}
    assert by_day[today.isoformat()].level == 4  # 1 + 2 + 12 = 15
    assert by_day[(today - timedelta(days=1)).isoformat()].level == 3  # 8
    assert by_day[(today - timedelta(days=2)).isoformat()].level == 2  # 3
    assert by_day[(today - timedelta(days=5)).isoformat()].level == 1  # 1
    assert all(not day.future for day in overview.daily)
    assert overview.daily[-1].day == today.isoformat()


def test_cumulative_curve_includes_activity_before_window(tmp_path) -> None:
    today = date(2026, 11, 24)
    db = Database(tmp_path / "data.db")
    try:
        old = (today - timedelta(days=CUMULATIVE_DAYS + 30)).isoformat()
        db.stats.bump(old, article_read=2, words_added=5, reviews_done=10)
        db.stats.bump(today.isoformat(), article_read=1, words_added=3, reviews_done=4)
        overview = make_service(db, today).build(on=today)
    finally:
        db.close()

    first = overview.cumulative[0]
    last = overview.cumulative[-1]
    assert first.words == 5  # 窗口外的累计值折成基线
    assert first.articles == 2
    assert first.reviews == 10
    assert last.words == 8
    assert last.articles == 3
    assert last.reviews == 14


def test_word_status_ignores_lookup_cache(tmp_path) -> None:
    today = date(2026, 11, 24)
    db = Database(tmp_path / "data.db")
    try:
        add_word(db, "alpha", status="new")
        add_word(db, "beta", status="learning")
        add_word(db, "gamma", status="mastered")
        add_word(db, "cache-only", status="new", in_vocabulary=False)
        overview = make_service(db, today).build(on=today)
    finally:
        db.close()

    split = overview.word_status
    assert split.total == 3
    assert (split.new, split.learning, split.mastered) == (1, 1, 1)
    assert overview.total_words == 3
    assert split.mastered_rate == 1 / 3


def test_quiz_series_groups_by_level_and_skips_practice(tmp_path) -> None:
    today = date(2026, 11, 24)
    db = Database(tmp_path / "data.db")
    try:
        add_quiz(db, day="2026-11-20", level="B1", score=3, total=4)
        add_quiz(db, day="2026-11-21", level="B1", score=4, total=4)
        add_quiz(db, day="2026-11-22", level="B2", score=2, total=4)
        add_quiz(db, day="2026-11-23", level="B2", score=4, total=4, practice=True)
        add_quiz(db, day="2026-11-24", level="B2", score=0, total=4, completed=False)
        overview = make_service(db, today).build(on=today)
    finally:
        db.close()

    assert overview.total_quizzes == 3  # 练习与未完成都不算
    assert [series.level for series in overview.quiz_series] == ["B1", "B2"]
    b1, b2 = overview.quiz_series
    assert [point.percent for point in b1.points] == [75, 100]
    assert [point.percent for point in b2.points] == [50]
    assert b1.latest_percent == 100


def test_quiz_series_keeps_only_recent_points_and_levels(tmp_path) -> None:
    today = date(2026, 11, 24)
    db = Database(tmp_path / "data.db")
    try:
        for index, level in enumerate(("A2", "B1", "B2", "C1", "C2")):
            for offset in range(10):
                # 每篇检测独占一天：articles.article_date 是 UNIQUE。
                day = (today - timedelta(days=offset + 1 + index * 10)).isoformat()
                add_quiz(
                    db,
                    day=day,
                    level=level,
                    score=index % 4,
                    total=4,
                )
        overview = make_service(db, today).build(on=today)
    finally:
        db.close()

    # 最多 4 条等级线、每条最多 8 个点，避免折线挤成一团。
    assert len(overview.quiz_series) == 4
    assert all(len(series.points) <= 8 for series in overview.quiz_series)


def test_achievements_use_all_time_totals(tmp_path) -> None:
    today = date(2026, 11, 24)
    db = Database(tmp_path / "data.db")
    try:
        db.stats.bump(today.isoformat(), article_read=12, reviews_done=120)
        for index in range(50):
            add_word(db, f"word-{index}", status="new")
        add_quiz(db, day=today.isoformat(), level="B1", score=4, total=4)
        overview = make_service(db, today).build(on=today)
    finally:
        db.close()

    unlocked = {item.key for item in overview.achievements if item.unlocked}
    assert {"first_read", "words_50", "reviews_100", "quiz_1"} <= unlocked
    assert "words_200" not in unlocked
    assert "reviews_500" not in unlocked
    assert overview.unlocked_achievements == 4
