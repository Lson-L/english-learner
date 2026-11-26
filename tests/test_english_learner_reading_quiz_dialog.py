"""阅读检测弹窗测试（offscreen，AI 用桩服务替身）。

Phase 3 起弹窗有两轮：
- 第一轮 3 道当前等级选择题；
- 第二轮锁定第一轮答案，补 2 道边界选择题 + 2 道英译中题。
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel  # noqa: E402

from english_learner.gui.reading_quiz_dialog import (  # noqa: E402
    MODE_QUIZ,
    MODE_RESULT,
    ReadingQuizDialog,
)
from english_learner.models.entities import (  # noqa: E402
    Article,
    Profile,
    QuizProduction,
    QuizQuestion,
    ReadingQuiz,
    now_iso,
    today_str,
)
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore  # noqa: E402
from english_learner.services.reading_quiz_service import (  # noqa: E402
    UNKNOWN_ANSWER,
    QuizOutcome,
)
from english_learner.storage.db import Database  # noqa: E402

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def make_context(db: Database, tmp_path: Path) -> AppContext:
    return AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(tmp_path / "config.json"),
        db,
    )


def make_article(db: Database) -> Article:
    return db.articles.insert(
        Article(
            article_date=today_str(),
            title="Small Habits",
            content="Small habits are powerful. They add up over time.",
            level="B1",
            word_count=10,
            created_at=now_iso(),
        )
    )


def choice_question(index: int, *, difficulty: str = "B1") -> QuizQuestion:
    return QuizQuestion(
        prompt=f"Question {index}?",
        options=("Option A", "Option B", "Option C", "Option D"),
        answer_index=1,
        skill="detail",
        difficulty=difficulty,
        explanation=f"第 {index} 题解析",
    )


def production_question(index: int, *, skill: str = "translation") -> QuizQuestion:
    if skill == "translation":
        return QuizQuestion(
            prompt='"Small habits add up over time."',
            skill="translation",
            difficulty="B1",
            kind="short",
            reference_answer="小习惯会随着时间的推移不断累积。",
            explanation="评分要点：add up 译为累积。",
        )
    return QuizQuestion(
        prompt=(
            "Rewrite the sentence in your own words."
            if skill == "rewrite"
            else "Summarize the passage in 1-2 sentences."
        ),
        skill=skill,
        difficulty="B1",
        kind="short",
        reference_answer="Small habits add up over time.",
        explanation="评分要点：内容 / 语法 / 词汇。",
    )


def make_stage_one_quiz(article: Article) -> ReadingQuiz:
    return ReadingQuiz(
        id=1,
        article_id=article.id or 1,
        article_date=article.article_date,
        level="B1",
        questions=tuple(choice_question(index) for index in range(1, 4)),
        stage=1,
        created_at=now_iso(),
    )


def make_stage_two_quiz(article: Article, *, answers: tuple[int, ...] = (1, 1, 1)) -> ReadingQuiz:
    questions = tuple(choice_question(index) for index in range(1, 6)) + (
        production_question(6),
        production_question(7),
    )
    return ReadingQuiz(
        id=1,
        article_id=article.id or 1,
        article_date=article.article_date,
        level="B1",
        questions=questions,
        answers=answers,
        stage=2,
        created_at=now_iso(),
    )


def make_completed_quiz(
    article: Article,
    *,
    practice_mode: bool = False,
) -> ReadingQuiz:
    quiz = make_stage_two_quiz(article)
    return replace(
        quiz,
        answers=(1, 1, 1, 1, 0),
        explanations=tuple(f"讲评第 {index} 题" for index in range(1, 8)),
        error_types=("none", "none", "none", "none", "careless", "translation", "translation"),
        short_answer_text="小习惯会随着时间的推移不断累积。",
        short_answer_score=1,
        productions=(
            QuizProduction("小习惯会随着时间的推移不断累积。", 1, "译文准确。"),
            QuizProduction("它们以我们很少察觉的方式改变生活。", 1, "关键词译对。"),
        ),
        self_report="ok",
        score=6,
        recommended_level="B2",
        previous_level="B1",
        result_level="B1",
        pending_level="B2",
        pending_count=1,
        feedback="细节题稳定，推断题再练。",
        evidence_ratio=0.6,
        level_confidence=0.72,
        posterior=(0.03, 0.18, 0.62, 0.17),
        practice_mode=practice_mode,
        completed_at=now_iso(),
    )


class StubQuizService:
    """不访问网络的替身：直接返回预置题目/结果。"""

    def __init__(
        self,
        cached: ReadingQuiz | None = None,
        generated: ReadingQuiz | None = None,
        advanced: ReadingQuiz | None = None,
        outcome: QuizOutcome | None = None,
        practice_day: bool = False,
    ) -> None:
        self.cached = cached
        self.generated = generated
        self.advanced = advanced
        self.outcome = outcome
        self.practice_day = practice_day
        self.get_cached_calls = 0
        self.get_or_create_calls = 0
        self.advance_calls = 0
        self.submit_calls = 0
        self.last_advance_answers: tuple[int, ...] = ()
        self.last_advance_notes: tuple[str, ...] = ()
        self.last_submit_answers: tuple[int, ...] = ()
        self.last_submit_notes: tuple[str, ...] = ()
        self.saved_self_reports: list[tuple[int | None, str]] = []

    def get_cached(self, article_id: int) -> ReadingQuiz | None:
        self.get_cached_calls += 1
        return self.cached

    def save_self_report(self, quiz_id: int | None, self_report: str) -> None:
        self.saved_self_reports.append((quiz_id, self_report))

    def is_practice_day(self, article, quiz_id=None) -> bool:
        return self.practice_day

    def get_or_create(self, article: Article, level: str) -> ReadingQuiz:
        self.get_or_create_calls += 1
        assert self.generated is not None
        return self.generated

    def advance_stage(self, article, quiz, answers, *, choice_notes=()) -> ReadingQuiz:
        self.advance_calls += 1
        self.last_advance_answers = tuple(answers)
        self.last_advance_notes = tuple(choice_notes)
        assert self.advanced is not None
        return self.advanced

    def submit(self, article, quiz, answers, profile, **kwargs) -> QuizOutcome:
        self.submit_calls += 1
        self.last_submit_answers = tuple(answers)
        self.last_submit_notes = tuple(kwargs.get("choice_notes") or ())
        assert self.outcome is not None
        return self.outcome


def test_cached_stage_one_shows_three_questions(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    quiz = make_stage_one_quiz(article)
    service = StubQuizService(cached=quiz)

    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    assert dialog._mode == MODE_QUIZ
    assert len(dialog._groups) == 3
    assert dialog._production_edits == []
    assert dialog._primary_button.text() == "提交第一轮"
    assert dialog._primary_button.isEnabled() is True
    dialog.close()
    db.close()


def test_practice_day_is_warned_before_answering(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubQuizService(
        cached=make_stage_one_quiz(article),
        practice_day=True,
    )

    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    assert dialog._practice_hint is True
    dialog.close()
    db.close()


def test_cached_stage_one_restores_saved_answers(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    quiz = replace(make_stage_one_quiz(article), answers=(1, 0, 1))
    service = StubQuizService(cached=quiz)

    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    assert [group.checkedId() for group in dialog._groups] == [1, 0, 1]
    assert all(
        group.button(group.checkedId()).isEnabled()  # type: ignore[union-attr]
        for group in dialog._groups
    )
    assert dialog._primary_button.text() == "提交第一轮"
    dialog.close()
    db.close()


def test_cached_stage_two_locks_first_round_answers(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    quiz = make_stage_two_quiz(article, answers=(1, 1, 1))
    service = StubQuizService(cached=quiz)

    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    assert dialog._mode == MODE_QUIZ
    assert len(dialog._groups) == 5
    assert len(dialog._production_edits) == 2
    assert dialog._primary_button.text() == "提交答案"
    for group in dialog._groups[:3]:
        assert group.checkedId() == 1
        button = group.button(1)
        assert button is not None and button.isEnabled() is False
    for group in dialog._groups[3:]:
        assert group.checkedId() == -1
    dialog.close()
    db.close()


def test_stage_two_shows_chinese_translation_inputs(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubQuizService(cached=make_stage_two_quiz(article))

    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    assert len(dialog._production_edits) == 2
    assert all("中文" in edit.placeholderText() for edit in dialog._production_edits)
    label_texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("英译中" in text for text in label_texts)
    assert all("英文" not in edit.placeholderText() for edit in dialog._production_edits)
    dialog.close()
    db.close()


def test_legacy_writing_question_keeps_english_placeholder(tmp_path: Path) -> None:
    """旧缓存的英文短答/改写仍按原标签展示，不能误标成英译中。"""
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    quiz = make_stage_two_quiz(article)
    legacy = replace(
        quiz,
        questions=quiz.questions[:-1] + (production_question(7, skill="writing"),),
    )
    service = StubQuizService(cached=legacy)

    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    assert len(dialog._production_edits) == 2
    assert "English" in dialog._production_edits[1].placeholderText()
    label_texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("[短答]" in text for text in label_texts)
    dialog.close()
    db.close()


def test_stage_one_requires_all_answers(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubQuizService(
        cached=make_stage_one_quiz(article),
        advanced=make_stage_two_quiz(article),
    )
    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    dialog._advance_stage()

    assert dialog._worker is None
    assert "没有作答" in dialog._quiz_status.text()
    assert service.advance_calls == 0
    dialog.close()
    db.close()


def test_stage_one_submit_generates_stage_two(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    stage_two = replace(make_stage_two_quiz(article), answers=(1, 1, 1))
    service = StubQuizService(
        cached=make_stage_one_quiz(article),
        advanced=stage_two,
    )
    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    for group in dialog._groups:
        button = group.button(1)
        assert button is not None
        button.setChecked(True)
    dialog._advance_stage()
    worker = dialog._worker
    assert worker is not None
    assert worker.wait(3000) is True
    get_app().processEvents()

    assert service.advance_calls == 1
    assert service.last_advance_answers == (1, 1, 1)
    assert dialog._mode == MODE_QUIZ
    assert len(dialog._groups) == 5
    assert len(dialog._production_edits) == 2
    assert dialog._primary_button.text() == "提交答案"
    dialog.close()
    db.close()


def test_stage_two_requires_production_text(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubQuizService(cached=make_stage_two_quiz(article))
    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    for group in dialog._groups[3:]:
        button = group.button(1)
        assert button is not None
        button.setChecked(True)
    dialog._submit_answers()

    assert dialog._worker is None
    assert "英译中" in dialog._quiz_status.text()
    assert service.submit_calls == 0
    dialog.close()
    db.close()


def test_submit_shows_result_and_keeps_outcome(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    db.profiles.save(Profile(cefr_level="B1", vocab_estimate=3800))
    article = make_article(db)
    quiz = make_stage_two_quiz(article)
    profile = db.profiles.get()
    assert profile is not None
    completed = make_completed_quiz(article)
    outcome = QuizOutcome(
        quiz=completed,
        profile=replace(profile, cefr_level="B1", level_confidence=0.72),
        result=None,  # type: ignore[arg-type]
        previous_level="B1",
        recommended_level="B2",
        pending_level="B2",
        pending_count=1,
        confidence=0.72,
    )
    service = StubQuizService(cached=quiz, outcome=outcome)
    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    for group in dialog._groups[3:]:
        button = group.button(1)
        assert button is not None
        button.setChecked(True)
    for edit, text in zip(
        dialog._production_edits,
        ("Small habits add up over time.", "Small habits change lives."),
        strict=False,
    ):
        edit.setPlainText(text)
    dialog._self_report_combo.setCurrentIndex(2)

    dialog._submit_answers()
    worker = dialog._worker
    assert worker is not None
    assert worker.wait(3000) is True
    get_app().processEvents()

    assert service.submit_calls == 1
    assert dialog._mode == MODE_RESULT
    assert dialog.result_quiz is not None and dialog.result_quiz.completed is True
    assert dialog._primary_button.text() == "完成"
    assert "B1" in dialog._subtitle.text() and "B2" in dialog._subtitle.text()
    dialog.close()
    db.close()


def test_completed_quiz_opens_result_without_new_ai(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    quiz = make_completed_quiz(article)
    service = StubQuizService(cached=quiz)

    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    assert dialog._mode == MODE_RESULT
    assert dialog.result_quiz is quiz
    assert dialog._primary_button.text() == "完成"
    assert dialog._cancel_button.isHidden() is True
    assert service.get_or_create_calls == 0
    assert service.submit_calls == 0
    # 结果页展示置信度与本地后验
    assert "置信度 0.72" in dialog._subtitle.text() or "置信度" in dialog._subtitle.text()
    dialog.close()
    db.close()


def test_fullscreen_toggle_and_escape_exits_fullscreen(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubQuizService(cached=make_stage_one_quiz(article))
    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    assert dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
    assert dialog._fullscreen_button.text() == "全屏"

    dialog._toggle_fullscreen()
    assert dialog.isFullScreen() is True
    assert dialog._fullscreen_button.text() == "退出全屏"

    event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier,
    )
    dialog.keyPressEvent(event)
    assert dialog.isFullScreen() is False
    assert dialog._fullscreen_button.text() == "全屏"

    dialog._toggle_fullscreen()
    dialog._toggle_fullscreen()
    assert dialog.isFullScreen() is False
    dialog.close()
    db.close()


def test_missing_cache_generates_stage_one_in_background(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubQuizService(cached=None, generated=make_stage_one_quiz(article))

    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]
    worker = dialog._worker
    assert worker is not None

    assert worker.wait(3000) is True
    get_app().processEvents()

    assert service.get_or_create_calls == 1
    assert dialog._mode == MODE_QUIZ
    assert len(dialog._groups) == 3
    assert dialog._primary_button.text() == "提交第一轮"
    dialog.close()
    db.close()


# ------------------------------------------------- 首次检测的强制阅读自评
def test_first_detection_asks_self_report_while_ai_generates(tmp_path: Path) -> None:
    """首次检测：出题 worker 先启动，自评弹窗只是并行等一个选择。"""
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    generated = make_stage_two_quiz(article)
    service = StubQuizService(cached=None, generated=generated)

    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    assert dialog._needs_self_report is True
    # 还没打开检测（没有调用 exec），AI 出题已经在后台跑。
    assert dialog._worker is not None

    seen: dict[str, object] = {}

    def fake_ask() -> str:
        seen["worker"] = dialog._worker
        return "hard"

    dialog._ask_self_report = fake_ask  # type: ignore[method-assign]
    assert dialog._prepare_self_report() is True
    assert seen["worker"] is not None
    assert dialog._initial_self_report == "hard"

    worker = dialog._worker
    assert worker is not None
    assert worker.wait(3000) is True
    get_app().processEvents()

    assert dialog._mode == MODE_QUIZ
    assert dialog._self_report_combo is not None
    assert dialog._self_report_combo.currentData() == "hard"
    assert service.saved_self_reports == [(generated.id, "hard")]
    dialog.close()
    db.close()


def test_exec_runs_self_report_popup_and_opens_ready_quiz(tmp_path: Path) -> None:
    """exec() 先弹自评；期间 AI 已出完题，选完直接看到题目而不是加载页。"""
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubQuizService(cached=None, generated=make_stage_one_quiz(article))

    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    def fake_ask() -> str:
        worker = dialog._worker
        assert worker is not None
        assert worker.wait(3000) is True
        get_app().processEvents()  # 自评期间出题线程已经跑完
        QTimer.singleShot(0, dialog.accept)
        return "hard"

    dialog._ask_self_report = fake_ask  # type: ignore[method-assign]
    result = dialog.exec()

    assert result == QDialog.DialogCode.Accepted
    assert dialog._mode == MODE_QUIZ
    assert len(dialog._groups) == 3
    assert dialog._initial_self_report == "hard"
    dialog.close()
    db.close()


def test_cancelling_self_report_abandons_generation(tmp_path: Path) -> None:
    """不选自评直接关窗：本次检测不打开，后台出题请求被放弃。"""
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubQuizService(cached=None, generated=make_stage_one_quiz(article))

    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]
    dialog._ask_self_report = lambda: ""  # type: ignore[method-assign]

    assert dialog._prepare_self_report() is False
    assert dialog._worker is None
    assert dialog.result_quiz is None
    dialog.close()
    db.close()


def test_cached_quiz_does_not_ask_self_report(tmp_path: Path) -> None:
    """已有题目（继续检测 / 查看结果）不再弹自评。"""
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubQuizService(cached=make_stage_one_quiz(article))

    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    assert dialog._needs_self_report is False
    assert dialog._prepare_self_report() is True
    dialog.close()
    db.close()


# ------------------------------------------------- 选择题「不知道 + 补充说明」
def test_choice_card_offers_unknown_option_and_reason_input(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubQuizService(cached=make_stage_one_quiz(article))
    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    group = dialog._groups[0]
    unknown = group.button(UNKNOWN_ANSWER)
    assert unknown is not None
    assert unknown.text() == "E. 不知道"

    note = dialog._choice_notes[0]
    assert note.isHidden() is True

    unknown.setChecked(True)
    assert note.isHidden() is False
    note.setText("这个单词不认识")

    # 只有选「不知道」的题才提交说明。
    assert dialog._collect_choice_notes() == ("这个单词不认识", "", "")
    assert dialog._collect_answers()[0] == UNKNOWN_ANSWER
    dialog.close()
    db.close()


def test_locked_stage_one_unknown_answer_keeps_note(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    quiz = replace(
        make_stage_two_quiz(article, answers=(UNKNOWN_ANSWER, 1, 1)),
        choice_notes=("这个单词不认识", "", ""),
    )
    service = StubQuizService(cached=quiz)
    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    group = dialog._groups[0]
    unknown = group.button(UNKNOWN_ANSWER)
    assert unknown is not None and unknown.isChecked() is True
    assert unknown.isEnabled() is False
    note = dialog._choice_notes[0]
    assert note.text() == "这个单词不认识"
    assert note.isHidden() is False
    assert note.isEnabled() is False
    dialog.close()
    db.close()


def test_submit_passes_unknown_note_to_service(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    db.profiles.save(Profile(cefr_level="B1", assessed_at=now_iso()))
    service = StubQuizService(
        cached=make_stage_two_quiz(article),
        outcome=QuizOutcome(
            quiz=make_completed_quiz(article),
            profile=Profile(cefr_level="B1"),
            result=None,  # type: ignore[arg-type]
            previous_level="B1",
        ),
    )
    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    # 第一轮已锁定；给第 4 题（position 3）选「不知道」并写原因。
    for group in dialog._groups[3:]:
        button = group.button(1)
        assert button is not None
        button.setChecked(True)
    unknown = dialog._groups[3].button(UNKNOWN_ANSWER)
    assert unknown is not None
    unknown.setChecked(True)
    dialog._choice_notes[3].setText("句子结构没看懂")
    for edit in dialog._production_edits:
        edit.setPlainText("小习惯会不断累积。")

    dialog._submit_answers()
    worker = dialog._worker
    assert worker is not None
    assert worker.wait(3000) is True
    get_app().processEvents()

    assert service.submit_calls == 1
    assert service.last_submit_answers[3] == UNKNOWN_ANSWER
    assert service.last_submit_notes == ("", "", "", "句子结构没看懂", "")
    dialog.close()
    db.close()


def test_result_page_shows_unknown_answer_and_note(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    completed = replace(
        make_completed_quiz(article),
        answers=(UNKNOWN_ANSWER, 1, 1, 1, 0),
        choice_notes=("这个单词不认识", "", "", "", ""),
    )
    service = StubQuizService(cached=completed)
    dialog = ReadingQuizDialog(make_context(db, tmp_path), article, service=service)  # type: ignore[arg-type]

    assert dialog._mode == MODE_RESULT
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert "第 1 题 · 主动表示不会" in texts
    assert "你的答案：不知道" in texts
    assert "补充说明：这个单词不认识" in texts
    dialog.close()
    db.close()
