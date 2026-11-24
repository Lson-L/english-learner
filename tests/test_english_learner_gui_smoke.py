"""GUI 冒烟测试：不弹真实窗口（offscreen），只验证控件能创建、状态机正确。

注意：环境变量必须在 import PySide6 之前设置。
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox  # noqa: E402

from english_learner.gui.assessment_dialog import AssessmentDialog  # noqa: E402
from english_learner.gui.level_feedback_dialog import LevelFeedbackDialog  # noqa: E402
from english_learner.gui.main_window import (  # noqa: E402
    PAGE_REVIEW,
    PAGE_TODAY,
    PAGE_VOCABULARY,
    MainWindow,
)
from english_learner.gui.onboarding import OnboardingDialog  # noqa: E402
from english_learner.models.entities import (  # noqa: E402
    Article,
    Profile,
    QuizQuestion,
    ReadingQuiz,
    WeeklyReview,
    Word,
    now_iso,
    today_str,
)
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.assessment_bank import UNKNOWN_ANSWER  # noqa: E402
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


def test_user_facing_copy_has_no_milestone_placeholders(tmp_path: Path) -> None:
    """配置向导与侧边栏不再向用户暴露 M1/M2/M3.11 这类开发期文案。"""
    get_app()
    dialog = OnboardingDialog(ConfigStore(tmp_path / "config.json"))
    joined = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    assert "M1" not in joined
    assert "M2" not in joined

    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    window = MainWindow(make_config(), store, db)
    try:
        assert "M3.11" not in window._footer_label.text()
        assert "v" in window._footer_label.text()
    finally:
        db.close()


def test_onboarding_requires_successful_test(tmp_path: Path) -> None:
    get_app()
    dialog = OnboardingDialog(ConfigStore(tmp_path / "config.json"))
    dialog._show_step(1)

    assert not dialog._primary_button.isEnabled()

    dialog._tested_config = make_config()
    dialog._show_step(1)
    assert dialog._primary_button.isEnabled()

    dialog._invalidate_test()
    assert not dialog._primary_button.isEnabled()


def test_main_window_builds_all_pages(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")

    window = MainWindow(make_config(), store, db)

    assert window._stack.count() == 6
    assert window._today_page is not None
    assert window._reader_page is not None
    assert window._vocabulary_page is not None
    assert window._review_page is not None
    assert window._stats_page is not None
    assert window._settings_page is not None
    assert window._nav_group.buttons()[0].isChecked()
    window.close()


def test_today_page_reflects_database(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    db.profiles.save(Profile(cefr_level="B2", vocab_estimate=5200))
    db.stats.bump(today_str(), words_added=2)
    for lemma in ("resilient", "consistent"):
        db.words.upsert(
            Word(
                lemma=lemma,
                in_vocabulary=True,
                due_date=today_str(),
                created_at=now_iso(),
                updated_at=now_iso(),
            )
        )

    window = MainWindow(make_config(), store, db)

    assert "B2" in window._today_page._level_label.text()
    # M6.4：词汇量移到等级下方的元信息行，等级首行改为进度口径。
    assert "5,200" in window._today_page._level_meta.text()
    # 没有检测数据时不能显示 0% 进度条，只给一句引导。
    assert window._today_page._level_progress.isHidden()
    assert "完成首次阅读检测后显示进度" in window._today_page._level_evidence.text()
    assert window._today_page._words_card._value_label.text() == "2 词"
    assert window._today_page._due_card._value_label.text() == "2 词"
    assert window._today_page._streak_card._value_label.text() == "1 天"
    assert window._today_page._review_button.text() == "开始复习"
    window.close()


def test_context_exposes_level_progress_and_difficulty_hint(tmp_path: Path) -> None:
    """M6.4 / M6.5：页面通过 AppContext 取档内进度与状态提示，不自己拼 SQL。"""
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    db.profiles.save(
        Profile(
            cefr_level="B1",
            vocab_estimate=3200,
            pending_level="B2",
            pending_count=1,
        )
    )
    context = AppContext(make_config(), store, db)

    progress = context.level_progress()
    hint = context.difficulty_hint()

    assert progress is not None
    assert progress.current_level == "B1"
    assert progress.next_level == "B2"
    assert progress.progress == 0.5  # 旧数据无 posterior 时回退到 pending 进度
    assert hint.within_level == "high"
    db.close()


def test_context_practice_only_uses_latest_completed_quiz(tmp_path: Path) -> None:
    """正式检测 + 当天练习检测：进度取正式，practice_only 必须为 True。"""
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    db.profiles.save(Profile(cefr_level="B1"))
    # 相对日期：避免系统日期早于固定日期时“练习比正式晚”的前提失效。
    formal_day = (date.today() - timedelta(days=2)).isoformat()
    formal_article = db.articles.insert(
        Article(
            article_date=formal_day,
            title="Formal",
            content="text",
            level="B1",
            created_at=now_iso(),
        )
    )
    formal = db.quizzes.insert(
        ReadingQuiz(
            article_id=int(formal_article.id or 0),
            article_date=formal_day,
            level="B1",
            questions=(),
            created_at=now_iso(),
        )
    )
    db.quizzes.save_result(
        int(formal.id or 0),
        answers=(),
        explanations=(),
        score=0,
        previous_level="B1",
        result_level="B1",
        feedback="",
        completed_at=formal_day + "T10:00:00",
        posterior=(0.10, 0.60, 0.30, 0.0),
    )
    practice_article = db.articles.insert(
        Article(
            article_date=today_str(),
            title="Practice",
            content="text",
            level="B1",
            created_at=now_iso(),
        )
    )
    practice = db.quizzes.insert(
        ReadingQuiz(
            article_id=int(practice_article.id or 0),
            article_date=today_str(),
            level="B1",
            questions=(),
            created_at=now_iso(),
        )
    )
    db.quizzes.save_result(
        int(practice.id or 0),
        answers=(),
        explanations=(),
        score=0,
        previous_level="B1",
        result_level="B1",
        feedback="",
        completed_at=today_str() + "T10:00:00",
        practice_mode=True,
        posterior=(0.0, 0.50, 0.50, 0.0),
    )
    context = AppContext(make_config(), store, db)

    progress = context.level_progress()

    assert progress is not None
    assert progress.practice_only is True
    # 进度来自正式检测（0.30 / 0.90），不是练习检测的 0.50。
    assert progress.progress is not None
    assert abs(progress.progress - 0.30 / 0.90) < 1e-6
    db.close()


def test_today_page_uses_pending_wording_without_posterior(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    db.profiles.save(Profile(cefr_level="B1", pending_level="B2", pending_count=1))

    window = MainWindow(make_config(), store, db)

    assert "已积累 50%" in window._today_page._level_label.text()
    assert "连续升级证据" in window._today_page._level_progress.toolTip()
    window.close()


def test_today_page_distinguishes_last_week_review(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    db.profiles.save(Profile(cefr_level="B1"))
    today = date.today()
    last_week_start = (today - timedelta(days=7 + today.weekday())).isoformat()
    db.weekly_reviews.save(
        WeeklyReview(
            week_start=last_week_start,
            summary="上周的复盘内容。",
            suggested_level="B2",
            created_at=now_iso(),
        )
    )

    window = MainWindow(make_config(), store, db)

    assert "最近复盘" in window._today_page._weekly_desc.text()
    assert "上周的复盘内容" in window._today_page._weekly_desc.text()
    assert "本周复盘" not in window._today_page._weekly_desc.text()
    window.close()


def test_today_page_shows_quiz_completion(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    db.profiles.save(Profile(cefr_level="B1", vocab_estimate=3800))
    article = db.articles.insert(
        Article(
            article_date=today_str(),
            title="Small Habits",
            content="Small habits are powerful.",
            level="B1",
            word_count=4,
            created_at=now_iso(),
        )
    )
    assert article.id is not None
    quiz = db.quizzes.insert(
        ReadingQuiz(
            article_id=article.id,
            article_date=today_str(),
            level="B1",
            questions=(
                QuizQuestion(prompt="Q1?", options=("a", "b", "c"), answer_index=0),
                QuizQuestion(prompt="Q2?", options=("a", "b", "c"), answer_index=1),
                QuizQuestion(prompt="Q3?", options=("a", "b", "c"), answer_index=2),
            ),
            created_at=now_iso(),
        )
    )
    assert quiz.id is not None
    db.quizzes.save_result(
        quiz.id,
        answers=(0, 1, 0),
        explanations=("", "", ""),
        score=2,
        previous_level="B1",
        result_level="A2",
        feedback="推断题需要再练。",
    )

    window = MainWindow(make_config(), store, db)
    window._today_page.refresh()

    assert "已完成检测" in window._today_page._article_desc.text()
    assert "2/3" in window._today_page._article_desc.text()
    window.close()


def test_navigate_to_review_and_vocabulary(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    db.words.upsert(
        Word(
            lemma="resilient",
            meaning_cn="有韧性的",
            in_vocabulary=True,
            due_date=today_str(),
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )

    window = MainWindow(make_config(), store, db)

    # 首页「开始复习」→ 复习页
    assert window._today_page._review_button.text() == "开始复习"
    window._today_page._review_button.click()
    assert window._stack.currentIndex() == PAGE_REVIEW
    assert "今天到期 1 词" in window._review_page._due_label.text()

    # 侧边栏 → 生词本页
    window._go(PAGE_VOCABULARY)
    assert window._stack.currentIndex() == PAGE_VOCABULARY
    assert window._vocabulary_page._table.rowCount() == 1
    window.close()


def test_navigation_blocked_by_unsaved_meaning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    db.words.upsert(
        Word(
            lemma="resilient",
            meaning_cn="有韧性的",
            in_vocabulary=True,
            due_date=today_str(),
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )

    window = MainWindow(make_config(), store, db)
    window._go(PAGE_VOCABULARY)
    page = window._vocabulary_page
    page._meaning_cn_edit.setPlainText("未保存的修改")

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )
    window._go(PAGE_TODAY)

    assert window._stack.currentIndex() == PAGE_VOCABULARY
    assert window._nav_group.button(PAGE_VOCABULARY).isChecked() is True

    # 允许丢弃后能正常关闭窗口
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Discard,
    )
    window.close()


def test_level_feedback_dialog_validation_and_quick_fill(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    db = Database(tmp_path / "data.db")
    db.profiles.save(Profile(cefr_level="B1", vocab_estimate=3800))
    article = db.articles.insert(
        Article(
            article_date=today_str(),
            title="Too Hard",
            content="Small habits are powerful.",
            level="B1",
            word_count=4,
            created_at=now_iso(),
        )
    )
    context = AppContext(make_config(), store, db)

    dialog = LevelFeedbackDialog(context, article)
    dialog._submit()
    assert dialog._worker is None
    assert "先写一句" in dialog._status.text()

    dialog._quick_buttons[0].click()
    assert "太难" in dialog._feedback_edit.toPlainText()
    dialog.close()
    db.close()


def test_assessment_dialog_builds_questionnaire(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    context = AppContext(make_config(), ConfigStore(tmp_path / "config.json"), db)

    dialog = AssessmentDialog(context)

    assert len(dialog._comp_groups) == 5
    assert len(dialog._vocab_groups) == 5
    assert len(dialog._comp_notes) == 5
    assert len(dialog._vocab_notes) == 5
    # 每组是 4 个选项 + 1 个“不知道”
    assert all(len(group.buttons()) == 5 for group in dialog._comp_groups)
    assert dialog._unanswered_questions() == 10
    assert dialog._stack.currentIndex() == 0
    assert dialog._primary_button.text() == "提交并评分"

    # 未作答时提交会被拦下，不会启动评分线程
    dialog._submit()
    assert dialog._worker is None
    assert "没有作答" in dialog._test_status.text()

    # 主动选“不知道”算有效作答，补充说明随答题卡一起收集
    dialog._comp_groups[0].buttons()[-1].setChecked(True)
    dialog._comp_notes[0].setText("这题问的是什么？")
    sheet = dialog._collect_sheet()
    assert sheet.comprehension[0] == UNKNOWN_ANSWER
    assert sheet.comprehension_notes[0] == "这题问的是什么？"
    assert dialog._unanswered_questions() == 9

    dialog.close()
    db.close()
