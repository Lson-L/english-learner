"""复习页 UI 测试（offscreen，无网络、无 AI）。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from english_learner.gui.pages.review import ReviewPage  # noqa: E402
from english_learner.models.entities import Word, now_iso, today_str  # noqa: E402
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import (  # noqa: E402
    REVIEW_MODE_RECOGNITION,
    REVIEW_MODE_SPELLING,
    AIConfig,
    ConfigStore,
)
from english_learner.services.recall import (  # noqa: E402
    STATUS_CORRECT,
    STATUS_NEAR,
    STATUS_WRONG,
)
from english_learner.services.srs import (  # noqa: E402
    RATING_FORGOT,
    RATING_KNOWN,
    VERDICT_SKIPPED,
)
from english_learner.storage.db import Database  # noqa: E402

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def make_context(db: Database, tmp_path: Path) -> AppContext:
    return AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(tmp_path / "config.json"),
        db,
    )


def make_word(lemma: str, **overrides) -> Word:
    values = {
        "lemma": lemma,
        "display_form": lemma,
        "phonetic": "/test/",
        "pos": "adj.",
        "meaning_cn": f"{lemma} 的中文释义",
        "meaning_en": f"meaning of {lemma}",
        "example_en": f"This is {lemma}.",
        "example_cn": f"这是 {lemma}。",
        "context_sentence": f"A sentence with {lemma} in it.",
        "in_vocabulary": True,
        "status": "new",
        "ease": 2.5,
        "interval_days": 0,
        "repetitions": 0,
        "due_date": today_str(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    values.update(overrides)
    return Word(**values)


def test_idle_state_shows_due_count(db: Database, tmp_path: Path) -> None:
    get_app()
    for lemma in ("alpha", "beta"):
        db.words.upsert(make_word(lemma))
    db.words.upsert(make_word("later", due_date="2099-01-01"))

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()

    assert page._due_label.text().startswith("今天到期 2 词")
    assert page._start_button.isEnabled() is True
    assert page._stack.currentWidget() is page._idle_page
    assert "2" in page._idle_title.text()


def test_rate_is_ignored_before_reveal(db: Database, tmp_path: Path) -> None:
    get_app()
    stored = db.words.upsert(make_word("alpha"))
    assert stored.id is not None

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()
    page._rate(RATING_KNOWN)

    assert page._index == 0
    reloaded = db.words.get_by_id(stored.id)
    assert reloaded is not None
    assert reloaded.interval_days == 0


def test_session_reveal_rate_and_finish(db: Database, tmp_path: Path) -> None:
    get_app()
    alpha = db.words.upsert(make_word("alpha"))
    beta = db.words.upsert(make_word("beta"))
    assert alpha.id is not None and beta.id is not None

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()
    # “忘记”会弹复习帮助框；单测直接跳过 AI 弹窗，验证评分与调度。
    page._collect_review_help = lambda _rating: ("", "")

    # 第一张：alpha
    assert page._current is not None
    assert page._current.lemma == "alpha"
    assert page._word_label.text() == "alpha"
    assert page._answer_box.isHidden() is True
    assert page._rating_bar.isHidden() is True
    assert page._progress_label.text() == "第 1 / 2 个"

    page._reveal()
    assert page._answer_box.isHidden() is False
    assert page._rating_bar.isHidden() is False
    assert page._reveal_button.isHidden() is True

    page._rate(RATING_KNOWN)
    assert page._index == 1
    assert page._word_label.text() == "beta"
    assert page._answer_box.isHidden() is True

    page._reveal()
    page._rate(RATING_FORGOT)

    assert page._stack.currentWidget() is page._summary_page
    assert "复习了 2 个词" in page._summary_detail.text()
    assert "1 个记得" in page._summary_detail.text()
    assert db.stats.get().reviews_done == 2

    reloaded_alpha = db.words.get_by_id(alpha.id)
    reloaded_beta = db.words.get_by_id(beta.id)
    assert reloaded_alpha is not None and reloaded_alpha.interval_days == 1
    assert reloaded_beta is not None and reloaded_beta.repetitions == 0
    assert db.reviews.count_for_date(today_str()) == 2

    # 看词回忆没有用户输入，证据统一记 skipped（M5.1）。
    alpha_log = db.reviews.list_for_word(alpha.id)[0]
    assert alpha_log.verdict == VERDICT_SKIPPED
    assert alpha_log.answer_text == ""
    assert alpha_log.distance == 0


def test_no_due_words_disables_start(db: Database, tmp_path: Path) -> None:
    get_app()
    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()

    assert page._due_label.text().startswith("今天到期 0 词")
    assert page._start_button.isEnabled() is False

    page._start_session()
    assert page._stack.currentWidget() is page._idle_page
    assert "没有到期" in page._status.text()


def test_forgot_logs_reason_and_advice(db: Database, tmp_path: Path) -> None:
    get_app()
    stored = db.words.upsert(make_word("alpha"))
    assert stored.id is not None

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()
    page._reveal()
    page._collect_review_help = lambda _rating: ("总是和 beta 混淆", "alpha 更像…")

    page._rate(RATING_FORGOT)

    logs = db.reviews.list_for_word(stored.id)
    assert len(logs) == 1
    assert logs[0].note == "总是和 beta 混淆"
    assert logs[0].advice == "alpha 更像…"


def test_cancelled_help_does_not_rate(db: Database, tmp_path: Path) -> None:
    get_app()
    stored = db.words.upsert(make_word("alpha"))
    assert stored.id is not None

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()
    page._reveal()
    page._collect_review_help = lambda _rating: None

    page._rate(RATING_FORGOT)

    assert page._index == 0
    assert db.reviews.list_for_word(stored.id) == []
    reloaded = db.words.get_by_id(stored.id)
    assert reloaded is not None and reloaded.repetitions == 0


def test_shortcuts_are_installed(db: Database, tmp_path: Path) -> None:
    get_app()
    page = ReviewPage(make_context(db, tmp_path))
    # 空格/回车 + 4 个评分键
    assert len(page._shortcuts) == 6


def test_session_respects_queue_limit(db: Database, tmp_path: Path) -> None:
    """复习队列一次最多 200 词，避免积压时一次性加载过多。"""
    get_app()
    for index in range(205):
        db.words.upsert(make_word(f"word{index:03d}"))

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()

    assert len(page._session) == 200
    assert page._progress_label.text() == "第 1 / 200 个"


# ---------------------------------------------------------------- 拼写模式
def make_spelling_word(lemma: str = "alpha", **overrides) -> Word:
    """拼写模式测试词：释义里不带英文原词，避免题面泄题。"""
    values = {
        "meaning_cn": "第一个字母",
        "example_en": f"This is {lemma}.",
        "context_sentence": f"A sentence with {lemma} in it.",
    }
    values.update(overrides)
    return make_word(lemma, **values)


def enable_spelling(page: ReviewPage) -> None:
    """通过下拉框切换到拼写模式（会顺带验证偏好落盘）。"""
    page._mode_combo.setCurrentIndex(page._mode_combo.findData(REVIEW_MODE_SPELLING))


def test_spelling_mode_persists_and_shows_prompt(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_spelling_word())
    context = make_context(db, tmp_path)
    page = ReviewPage(context)
    page.on_activated()
    assert page._mode_combo.currentData() == REVIEW_MODE_RECOGNITION

    enable_spelling(page)

    assert context.store.load_ui().review_mode == REVIEW_MODE_SPELLING
    assert page._review_mode == REVIEW_MODE_SPELLING

    page._start_session()
    assert page._spelling_active is True
    assert page._spelling_box.isHidden() is False
    assert page._word_label.isHidden() is True
    assert page._phonetic_label.isHidden() is True
    assert page._prompt_meaning.text() == "第一个字母"
    assert page._prompt_context.text() == "This is ____."
    assert "alpha" not in page._prompt_context.text()
    assert page._mode_combo.isEnabled() is False


def test_spelling_correct_suggests_known_and_rates(db: Database, tmp_path: Path) -> None:
    get_app()
    stored = db.words.upsert(make_spelling_word())
    assert stored.id is not None

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    enable_spelling(page)
    page._start_session()

    page._spelling_input.setText("  ALPHA ")
    page._submit_spelling()

    assert page._recall_result is not None
    assert page._recall_result.status == STATUS_CORRECT
    assert page._revealed is True
    assert "拼写正确" in page._spelling_result.text()
    assert "建议评分：认识（3）" in page._rating_hint.text()
    assert page._answer_box.isHidden() is False
    assert page._word_label.isHidden() is False
    assert page._spelling_submit.isHidden() is True

    page._rate(RATING_KNOWN)
    assert page._stack.currentWidget() is page._summary_page
    assert db.stats.get().reviews_done == 1

    log = db.reviews.list_for_word(stored.id)[0]
    assert log.answer_text == "ALPHA"
    assert log.verdict == STATUS_CORRECT
    assert log.distance == 0


def test_spelling_near_miss_suggests_vague(db: Database, tmp_path: Path) -> None:
    get_app()
    stored = db.words.upsert(make_spelling_word())
    assert stored.id is not None

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    enable_spelling(page)
    page._start_session()

    page._spelling_input.setText("alphx")
    page._submit_spelling()

    assert page._recall_result is not None
    assert page._recall_result.status == STATUS_NEAR
    assert "差一点" in page._spelling_result.text()
    assert "建议评分：模糊（2）" in page._rating_hint.text()

    page._rate(RATING_KNOWN)
    log = db.reviews.list_for_word(stored.id)[0]
    assert log.answer_text == "alphx"
    assert log.verdict == STATUS_NEAR
    assert log.distance == 1


def test_spelling_wrong_suggests_forgot(db: Database, tmp_path: Path) -> None:
    get_app()
    stored = db.words.upsert(make_spelling_word())
    assert stored.id is not None

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    enable_spelling(page)
    page._start_session()

    page._spelling_input.setText("beta")
    page._submit_spelling()

    assert page._recall_result is not None
    assert page._recall_result.status == STATUS_WRONG
    assert "不对" in page._spelling_result.text()
    assert "建议评分：忘记（1）" in page._rating_hint.text()

    page._rate(RATING_KNOWN)
    log = db.reviews.list_for_word(stored.id)[0]
    assert log.answer_text == "beta"
    assert log.verdict == STATUS_WRONG
    assert log.distance > 0


def test_spelling_empty_answer_keeps_card(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_spelling_word())

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    enable_spelling(page)
    page._start_session()

    page._submit_spelling()

    assert page._revealed is False
    assert page._recall_result is None
    assert "先输入" in page._status.text()


def test_spelling_skip_reveals_without_suggestion(db: Database, tmp_path: Path) -> None:
    get_app()
    stored = db.words.upsert(make_spelling_word())
    assert stored.id is not None

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    enable_spelling(page)
    page._start_session()

    page._reveal()

    assert page._recall_result is None
    assert page._revealed is True
    assert "已跳过" in page._spelling_result.text()
    assert page._rating_hint.text() == "按 1~4 或点击按钮：忘记 / 模糊 / 认识 / 太简单"
    assert page._answer_box.isHidden() is False

    page._rate(RATING_KNOWN)
    log = db.reviews.list_for_word(stored.id)[0]
    assert log.verdict == VERDICT_SKIPPED
    assert log.answer_text == ""
    assert log.distance == 0


def test_spelling_mode_disables_shortcuts_until_reveal(db: Database, tmp_path: Path) -> None:
    """输入阶段不能让空格/回车/数字被 QShortcut 抢走。"""
    get_app()
    db.words.upsert(make_spelling_word())

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    enable_spelling(page)
    page._start_session()

    assert all(not shortcut.isEnabled() for shortcut in page._shortcuts)

    page._spelling_input.setText("alpha")
    page._submit_spelling()
    assert all(shortcut.isEnabled() for shortcut in page._shortcuts)


def test_phrase_word_falls_back_to_recognition(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_word("look up", meaning_cn="查阅", example_en="Look up the word."))

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    enable_spelling(page)
    page._start_session()

    assert page._spelling_active is False
    assert page._spelling_box.isHidden() is True
    assert page._word_label.isHidden() is False


def test_mode_combo_reenabled_after_session(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_word("alpha"))

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()
    assert page._mode_combo.isEnabled() is False

    page._reveal()
    page._rate(RATING_KNOWN)
    assert page._mode_combo.isEnabled() is True


def test_spelling_enter_submits(db: Database, tmp_path: Path) -> None:
    """回车必须走 QLineEdit.returnPressed，而不是页面级 Return 快捷键。"""
    get_app()
    db.words.upsert(make_spelling_word())

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    enable_spelling(page)
    page._start_session()

    page._spelling_input.setText("alpha")
    page._spelling_input.returnPressed.emit()

    assert page._revealed is True
    assert page._recall_result is not None
    assert page._recall_result.status == STATUS_CORRECT


def test_spelling_mode_reloads_from_config(db: Database, tmp_path: Path) -> None:
    """下次启动时复习页要从 config.json 恢复上次选的模式。"""
    get_app()
    context = make_context(db, tmp_path)
    first = ReviewPage(context)
    enable_spelling(first)

    second = ReviewPage(context)

    assert second._review_mode == REVIEW_MODE_SPELLING
    assert second._mode_combo.currentData() == REVIEW_MODE_SPELLING
