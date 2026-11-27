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
        db.articles,
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


def test_reading_volume_counts_opened_articles_only(tmp_path) -> None:
    """M7.3：累计阅读 = 打开过的文章词数，含真实文章，排除没打开的文章。"""
    today = date(2026, 11, 24)
    db = Database(tmp_path / "data.db")
    try:

        def add(day: str, words: int, *, read: bool = True) -> None:
            db.articles.insert(
                Article(
                    article_date=day,
                    title=day,
                    content="content",
                    level="B1",
                    word_count=words,
                    read_at=f"{day}T10:00:00" if read else None,
                    created_at=now_iso(),
                )
            )

        add("2026-11-24", 300)
        add("2026-11-20", 200)
        add("2026-10-01", 100)
        add("2026-11-23", 50, read=False)

        overview = StatsOverviewService(
            db.stats,
            db.words,
            db.quizzes,
            db.articles,
            today=lambda: today.isoformat(),
        ).build(on=today)
    finally:
        db.close()

    volume = overview.reading_volume
    assert volume.total_words == 600
    assert volume.read_articles == 3
    assert volume.words_7d == 500
    assert volume.words_30d == 500
    assert volume.est_books == round(600 / 90000, 1)
    assert overview.has_data is True


# ------------------------------------------------------------ M8 新增口径
def test_due_forecast_and_overdue(tmp_path) -> None:
    today = date(2026, 11, 27)
    db = Database(tmp_path / "data.db")
    try:
        for lemma, due, status in (
            ("due-today-a", "2026-11-27", "learning"),
            ("due-today-b", "2026-11-27", "new"),
            ("due-tomorrow", "2026-11-28", "learning"),
            ("overdue", "2026-11-20", "learning"),
            ("mastered", "2026-11-27", "mastered"),
        ):
            db.words.upsert(
                Word(
                    lemma=lemma,
                    status=status,
                    in_vocabulary=True,
                    due_date=due,
                    created_at=now_iso(),
                    updated_at=now_iso(),
                )
            )
        overview = make_service(db, today).build(on=today)
    finally:
        db.close()

    assert [day.count for day in overview.due_forecast] == [2, 1, 0, 0, 0, 0, 0]
    assert overview.due_forecast[0].day == "2026-11-27"
    # 今日到期口径含逾期；逾期单独统计且不含已掌握。
    assert overview.due_today == 3
    assert overview.overdue_words == 1


def test_word_bands_and_stalled_words(tmp_path) -> None:
    today = date(2026, 11, 27)
    db = Database(tmp_path / "data.db")
    try:
        for lemma, band, status, due in (
            ("a2-word", "A2", "learning", "2026-11-01"),
            ("b1-word-a", "B1", "learning", "2026-12-10"),
            ("b1-word-b", "B1", "new", None),
            ("no-band", "", "new", None),
        ):
            db.words.upsert(
                Word(
                    lemma=lemma,
                    cefr_band=band,
                    status=status,
                    in_vocabulary=True,
                    due_date=due,
                    created_at=now_iso(),
                    updated_at=now_iso(),
                )
            )
        overview = make_service(db, today).build(on=today)
    finally:
        db.close()

    assert dict(overview.word_bands.bands) == {"A2": 1, "B1": 2}
    assert overview.word_bands.unlabeled == 1
    # 只有 a2-word 是 learning 且到期日早于 7 天前。
    assert overview.stalled_words == 1


def test_today_active_and_reading_volume_strict_measure(tmp_path) -> None:
    today = date(2026, 11, 27)
    db = Database(tmp_path / "data.db")
    try:
        db.stats.bump(today.isoformat(), article_read=1, words_added=1, reviews_done=1)
        article = db.articles.insert(
            Article(
                article_date=today.isoformat(),
                title="read-and-quizzed",
                content="content",
                level="B1",
                word_count=500,
                read_at=f"{today.isoformat()}T09:00:00",
                source="real",
                created_at=now_iso(),
            )
        )
        assert article.id is not None
        other = db.articles.insert(
            Article(
                article_date="2026-11-26",
                title="read-only",
                content="content",
                level="B1",
                word_count=300,
                read_at="2026-11-26T09:00:00",
                source="generated",
                created_at=now_iso(),
            )
        )
        assert other.id is not None
        questions = tuple(
            QuizQuestion(prompt=f"q{index}", options=("a", "b"), answer_index=0)
            for index in range(2)
        )
        quiz = db.quizzes.insert(
            ReadingQuiz(
                article_id=article.id,
                article_date=today.isoformat(),
                level="B1",
                questions=questions,
                created_at=now_iso(),
            )
        )
        assert quiz.id is not None
        db.quizzes.save_result(
            quiz.id,
            answers=(),
            explanations=(),
            score=2,
            previous_level="B1",
            result_level="B1",
            feedback="",
            completed_at=f"{today.isoformat()}T10:00:00",
        )
        overview = make_service(db, today).build(on=today)
    finally:
        db.close()

    assert overview.today_active is True
    volume = overview.reading_volume
    assert volume.total_words == 800
    assert volume.completed_words == 500
    assert volume.completed_articles == 1
    assert volume.real_articles == 1
    assert volume.generated_articles == 1


def test_cumulative_window_can_be_changed(tmp_path) -> None:
    today = date(2026, 11, 27)
    db = Database(tmp_path / "data.db")
    try:
        db.stats.bump("2026-06-01", article_read=1, words_added=1, reviews_done=1)
        db.stats.bump(today.isoformat(), article_read=1, words_added=1, reviews_done=1)
        service = make_service(db, today)
        thirty = service.build(on=today, cumulative_days=30)
        full = service.build(on=today, cumulative_days=0)
    finally:
        db.close()

    assert len(thirty.cumulative) == 30
    assert thirty.cumulative_days == 30
    # 30 天窗口：6 月的活动折成基线，窗口内增量 = 末点 - 基线。
    assert thirty.cumulative_baseline.words == 1
    assert thirty.cumulative[-1].words == 2
    assert thirty.cumulative[-1].words - thirty.cumulative_baseline.words == 1
    # 全部窗口从最早一条记录开始，基线为 0，首点就是最早一天的活动。
    assert full.cumulative[0].day == "2026-06-01"
    assert full.cumulative[0].words == 1
    assert full.cumulative_baseline.words == 0
    assert full.cumulative[-1].words - full.cumulative_baseline.words == 2
    assert full.cumulative_days == 0


def test_achievement_unlocked_on_first_reach_day(tmp_path) -> None:
    today = date(2026, 11, 27)
    db = Database(tmp_path / "data.db")
    try:
        db.stats.bump("2026-11-25", article_read=1, words_added=1, reviews_done=1)
        db.stats.bump("2026-11-26", article_read=1, words_added=1, reviews_done=1)
        db.stats.bump("2026-11-27", article_read=1, words_added=1, reviews_done=1)
        overview = make_service(db, today).build(on=today)
    finally:
        db.close()

    by_key = {item.key: item for item in overview.achievements}
    assert by_key["first_read"].unlocked is True
    assert by_key["first_read"].unlocked_on == "2026-11-25"
    assert by_key["streak_7"].unlocked is False
    assert by_key["streak_7"].unlocked_on == ""


def test_real_text_history_stats(tmp_path) -> None:
    from english_learner.models.entities import RealText

    today = date(2026, 11, 27)
    db = Database(tmp_path / "data.db")
    try:
        text = db.real_texts.upsert(
            RealText(
                id="china-daily-b1",
                title="Sample",
                content="content",
                level="B1",
                word_count=300,
                created_at=now_iso(),
            )
        )
        served = db.real_texts.mark_served(text, served_on=today.isoformat(), article_id=None)
        assert served.id is not None
        db.real_texts.record_completion(
            text.id,
            score=0.8,
            self_report="ok",
            outcome="success",
            completed_at=f"{today.isoformat()}T10:00:00",
        )
        db.real_texts.mark_served(text, served_on="2026-11-26", article_id=None)
        db.real_texts.mark_overridden(text.id)
        overview = StatsOverviewService(
            db.stats,
            db.words,
            db.quizzes,
            db.articles,
            db.real_texts,
            today=lambda: today.isoformat(),
        ).build(on=today)
    finally:
        db.close()

    stats = overview.real_text
    assert stats.has_data is True
    assert stats.total == 2
    assert stats.completed == 1
    assert stats.overridden == 1
    assert dict(stats.outcomes)["success"] == 1
    assert stats.completion_rate == 0.5


def test_cumulative_delta_includes_first_window_day(tmp_path) -> None:
    """窗口第一天的活动属于窗口内增量，不能被基线扣掉。"""
    today = date(2026, 11, 27)
    first_day = today - timedelta(days=CUMULATIVE_DAYS - 1)
    db = Database(tmp_path / "data.db")
    try:
        db.stats.bump(first_day.isoformat(), article_read=2, words_added=4, reviews_done=6)
        overview = make_service(db, today).build(on=today)
    finally:
        db.close()

    assert overview.cumulative_baseline.words == 0
    assert overview.cumulative[0].day == first_day.isoformat()
    assert overview.cumulative[-1].words == 4
    assert overview.cumulative[-1].words - overview.cumulative_baseline.words == 4
