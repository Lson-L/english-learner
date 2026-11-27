"""M5.5 / M8 学习效果卡片（统计页，offscreen，本地数据库，无网络）。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from english_learner.gui.pages.stats import StatsPage  # noqa: E402
from english_learner.models.entities import (  # noqa: E402
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
    return AIConfig(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="test-model",
    )


def make_page(tmp_path: Path) -> tuple[StatsPage, AppContext, Database]:
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    context = AppContext(make_config(), store, db)
    return StatsPage(context), context, db


def seed_review(db: Database) -> None:
    word = db.words.upsert(
        Word(
            lemma="alpha",
            meaning_cn="第一个",
            in_vocabulary=True,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    assert word.id is not None
    db.reviews.log(
        word_id=word.id,
        rating=4,
        prev_interval=1,
        next_interval=6,
        reviewed_at=now_iso(),
    )


def seed_quiz(db: Database) -> None:
    day = today_str()
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
        answers=(),
        explanations=(),
        score=4,
        previous_level="B1",
        result_level="B1",
        feedback="",
        completed_at=now_iso(),
    )


def test_effect_card_empty_database(tmp_path: Path) -> None:
    get_app()
    page, _context, db = make_page(tmp_path)

    page.refresh()

    assert page._effect_tiles["first_pass"].value == "—"
    assert page._effect_tiles["plan"].value == "—"
    assert page._effect_tiles["article_retry"].value == "—"
    assert "还没有完成的阅读检测" in page._trend_label.text()
    db.close()


def test_effect_card_renders_reviews_and_quiz_trend(tmp_path: Path) -> None:
    get_app()
    page, _context, db = make_page(tmp_path)
    seed_review(db)
    seed_quiz(db)

    page.refresh()

    assert page._effect_tiles["first_pass"].value == "1/1 · 100%"
    assert page._effect_tiles["relearn"].value == "0/1 · 0%"
    assert page._effect_tiles["plan"].value == "—"
    trend_text = page._trend_label.text()
    assert "B1 · 1 次 · 最近一次 100%" in trend_text
    assert "样本不足" in trend_text
    db.close()


def test_effect_card_switches_to_english_in_low_profile(tmp_path: Path) -> None:
    get_app()
    page, _context, db = make_page(tmp_path)
    seed_review(db)
    seed_quiz(db)
    page.refresh()

    page.set_low_profile(True)

    assert "Score trend" in page._trend_label.text()
    assert "not enough samples" in page._trend_label.text()
    db.close()


def test_settings_page_no_longer_owns_effect_or_usage_cards(tmp_path: Path) -> None:
    """M8：学习效果与 AI 用量已迁到统计页，设置页只保留运行日志。"""
    from english_learner.gui.pages.settings import SettingsPage

    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())

    page = SettingsPage(make_config(), store)

    assert not hasattr(page, "_metric_labels")
    assert not hasattr(page, "_usage_button")
    assert page._logs_button.isEnabled() is True
