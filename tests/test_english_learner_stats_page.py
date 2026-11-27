"""统计页（offscreen，本地数据库，无网络）。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from english_learner.gui.pages.stats import StatsPage  # noqa: E402
from english_learner.gui.theme import LOW_PROFILE_PALETTE  # noqa: E402
from english_learner.models.entities import (  # noqa: E402
    AICall,
    Article,
    QuizQuestion,
    ReadingQuiz,
    Word,
    now_iso,
    today_str,
)
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore  # noqa: E402
from english_learner.storage.db import Database  # noqa: E402

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def make_config() -> AIConfig:
    return AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test-model")


def make_page(tmp_path: Path) -> tuple[StatsPage, Database]:
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    context = AppContext(make_config(), store, db)
    return StatsPage(context), db


def seed(db: Database) -> None:
    day = today_str()
    db.stats.bump(day, article_read=1, words_added=3, reviews_done=20)
    for index, status in enumerate(("new", "learning", "mastered")):
        db.words.upsert(
            Word(
                lemma=f"word-{index}",
                status=status,
                in_vocabulary=True,
                created_at=now_iso(),
                updated_at=now_iso(),
            )
        )
    article = db.articles.insert(
        Article(
            article_date=day,
            title="今日文章",
            content="content",
            level="B1",
            created_at=now_iso(),
        )
    )
    assert article.id is not None
    questions = tuple(
        QuizQuestion(prompt=f"q{index}", options=("a", "b"), answer_index=0) for index in range(4)
    )
    quiz = db.quizzes.insert(
        ReadingQuiz(
            article_id=article.id,
            article_date=day,
            level="B1",
            questions=questions,
            created_at=now_iso(),
        )
    )
    assert quiz.id is not None
    db.quizzes.save_result(
        quiz.id,
        answers=(0, 0, 1, 0),
        explanations=(),
        score=3,
        previous_level="B1",
        result_level="B1",
        feedback="",
        completed_at=now_iso(),
    )


def test_empty_database_shows_guidance_and_zero_cards(tmp_path: Path) -> None:
    get_app()
    page, db = make_page(tmp_path)
    page.refresh()

    assert page._streak_card._value_label.text() == "0 天"
    assert page._active_card._value_label.text() == "0 天"
    assert page._words_card._value_label.text() == "0 词"
    assert page._reviews_card._value_label.text() == "0 次"
    assert page._empty_hint.isHidden() is False
    assert "还没有加入生词本" in page._word_summary.text()
    assert "完成阅读检测后" in page._quiz_summary.text()
    assert page._achievement_summary.text() == "已解锁 0/12"
    assert len(page._achievement_wall.badges) == 12
    db.close()


def test_page_renders_seeded_statistics(tmp_path: Path) -> None:
    get_app()
    page, db = make_page(tmp_path)
    seed(db)

    page.refresh()

    assert page._streak_card._value_label.text() == "1 天"
    assert page._active_card._value_label.text() == "1 天"
    assert page._words_card._value_label.text() == "3 词"
    assert page._reviews_card._value_label.text() == "20 次"
    assert page._active_card._hint_label.text() == "最长连续 1 天"
    assert page._empty_hint.isHidden() is True
    assert "近 26 周打卡 1 天" in page._heat_summary.text()
    assert "阅读 1 篇" in page._daily_summary.text()
    assert "已掌握 1 个" in page._word_summary.text()
    assert "最近一次 B1 75%" in page._quiz_summary.text()
    assert page._achievement_summary.text() == "已解锁 2/12"
    assert page._heatmap._days
    assert len(page._cumulative_chart._points) == 90
    assert page._score_chart._series[0].level == "B1"
    assert page._score_chart._series[0].latest_percent == 75
    db.close()


def test_metric_switch_updates_cumulative_summary(tmp_path: Path) -> None:
    get_app()
    page, db = make_page(tmp_path)
    seed(db)
    page.refresh()

    page._select_metric("reviews")
    assert "复习 20 次" in page._cumulative_summary.text()

    page._select_metric("articles")
    assert "阅读 1 篇" in page._cumulative_summary.text()
    db.close()


def test_low_profile_switches_chart_palette_and_copy(tmp_path: Path) -> None:
    get_app()
    page, db = make_page(tmp_path)
    seed(db)
    page.refresh()
    assert "近 26 周打卡" in page._heat_summary.text()

    page.set_low_profile(True)

    assert page._heatmap._palette.surface == LOW_PROFILE_PALETTE["BG_CARD"]
    assert "active days in 26 weeks" in page._heat_summary.text()
    assert "quizzes" in page._quiz_summary.text()
    assert page._achievement_wall._low_profile is True

    page.set_low_profile(False)
    assert "近 26 周打卡" in page._heat_summary.text()
    db.close()


def test_charts_can_paint_without_data(tmp_path: Path) -> None:
    get_app()
    page, db = make_page(tmp_path)
    page.resize(1000, 900)

    for name in (
        "_heatmap",
        "_cumulative_chart",
        "_daily_chart",
        "_word_bar",
        "_score_chart",
        "_due_chart",
        "_error_chart",
    ):
        widget = getattr(page, name)
        widget.setFixedSize(600, 200)
        image = widget.grab()
        assert image.width() == 600
        assert image.height() == 200
    db.close()


def test_heatmap_expands_to_full_26_weeks_after_data(tmp_path: Path) -> None:
    """回归：热力图构造时没有数据，set_days 后必须让布局按 26 周重算宽度。

    之前只调 update() 不调 updateGeometry()，控件会停在 1 周宽的 46px，
    在低调主题下看起来像一条几乎看不见的窄缝。
    """
    get_app()
    page, db = make_page(tmp_path)
    seed(db)
    page.resize(1200, 900)
    page.show()
    try:
        page.refresh()
        get_app().processEvents()
        heatmap = page._heatmap
        assert heatmap.sizeHint().width() >= 26 * (heatmap.CELL + heatmap.GAP)
        assert heatmap.width() >= 500
    finally:
        page.hide()
        db.close()


def test_service_failure_keeps_page_usable(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")

    class BrokenContext:
        def stats_overview(self):
            raise RuntimeError("boom")

    page = StatsPage(BrokenContext())  # type: ignore[arg-type]
    page.refresh()

    assert page._streak_card._value_label.text() == "—"
    assert "统计计算失败" in page._empty_hint.text()
    assert page._empty_hint.isHidden() is False
    db.close()


# ------------------------------------------------------------ M8 布局与新卡片
def test_new_cards_and_achievements_last(tmp_path: Path) -> None:
    get_app()
    page, db = make_page(tmp_path)
    seed(db)
    page.refresh()

    assert page._effect_tiles["first_pass"].value == "—"
    assert page._usage_tiles["today_calls"].value == "0"
    assert "今天到期" in page._due_summary.text()
    assert page._band_summary.text() == ""
    assert page._real_text_card.isHidden() is True

    body = page.findChild(QScrollArea).widget()
    layout = body.layout()
    # 成就是最后一个内容卡片。
    assert layout.indexOf(page._achievement_card) > layout.indexOf(page._real_text_card)
    assert layout.indexOf(page._achievement_card) > layout.indexOf(page._heatmap_card)
    db.close()


def test_effect_and_usage_tiles_fill_after_activity(tmp_path: Path) -> None:
    get_app()
    page, db = make_page(tmp_path)
    seed(db)
    db.ai_calls.insert(
        AICall(
            called_at=now_iso(),
            task="查词",
            model="test-model",
            total_tokens=120,
            latency_ms=400,
        )
    )
    page.refresh()

    assert page._usage_tiles["today_calls"].value == "1"
    assert page._usage_tiles["today_tokens"].value == "120"
    assert page._usage_tiles["latency"].value == "400 ms"
    db.close()


def test_trend_cards_hidden_until_three_active_days(tmp_path: Path) -> None:
    get_app()
    page, db = make_page(tmp_path)
    seed(db)
    page.refresh()

    # 只有今天一条记录：趋势卡收起，提示继续积累。
    assert page._cumulative_card.isHidden() is True
    assert page._tips_hint.isHidden() is False

    for day in ("2026-11-26", "2026-11-25"):
        db.stats.bump(day, article_read=1)
    page.refresh()

    assert page._cumulative_card.isHidden() is False
    assert page._tips_hint.isHidden() is True
    db.close()


def test_low_profile_translates_new_cards_and_window_options(tmp_path: Path) -> None:
    get_app()
    page, db = make_page(tmp_path)
    seed(db)
    page.refresh()
    page.set_low_profile(True)

    assert page._effect_tiles["first_pass"]._caption_label.text() == "First-pass"
    assert page._window_combo.itemText(0) == "30 days"
    assert page._window_combo.itemText(3) == "All"
    assert page._due_chart._palette.surface == LOW_PROFILE_PALETTE["BG_CARD"]
    assert page._error_chart._palette.surface == LOW_PROFILE_PALETTE["BG_CARD"]
    assert page._real_text_card.isHidden() is True
    db.close()


def test_cumulative_window_switch_rebuilds_curve(tmp_path: Path) -> None:
    get_app()
    page, db = make_page(tmp_path)
    seed(db)
    page.refresh()
    assert len(page._cumulative_chart._points) == 90

    page._window_combo.setCurrentIndex(0)  # 近 30 天
    assert len(page._cumulative_chart._points) == 30

    page._window_combo.setCurrentIndex(3)  # 全部
    assert page._cumulative_chart._points
    assert page._cumulative_chart._points[0].day <= today_str()
    db.close()


def test_insight_summary_shows_difficulty_and_reading_strict_hint(tmp_path: Path) -> None:
    get_app()
    page, db = make_page(tmp_path)
    seed(db)
    page.refresh()

    assert "难度 B1 75%" in page._insight_summary.text()
    hint = page._reading_card._hint_label.text()
    assert "完成检测" in hint
    assert "已读真实 0 / AI 0 篇" in hint
    db.close()
