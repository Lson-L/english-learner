"""复习页 UI 测试（offscreen，无网络、无 AI）。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from english_learner.gui.pages.review import ReviewPage  # noqa: E402
from english_learner.models.entities import Word, now_iso, today_str  # noqa: E402
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore  # noqa: E402
from english_learner.services.recall import (  # noqa: E402
    STATUS_CORRECT,
    STATUS_NEAR,
    STATUS_WRONG,
)
from english_learner.services.review_plan import (  # noqa: E402
    CARD_DUAL,
    CARD_RECOGNITION,
    CARD_SPELLING,
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


def make_spelling_word(lemma: str = "alpha", **overrides) -> Word:
    """已熟悉到可以纯拼写的词：repetitions >= 2。释义里不带英文原词，避免泄题。"""
    values = {
        "repetitions": 2,
        "interval_days": 6,
        "status": "learning",
        "meaning_cn": "第一个字母",
        "example_en": f"This is {lemma}.",
        "context_sentence": f"A sentence with {lemma} in it.",
    }
    values.update(overrides)
    return make_word(lemma, **values)


def make_dual_word(lemma: str = "alpha", **overrides) -> Word:
    """刚成功复习过一次的词：自动排成看词 + 拼写双阶段。"""
    values = {
        "repetitions": 1,
        "interval_days": 1,
        "status": "learning",
        "meaning_cn": "第一个字母",
        "example_en": f"This is {lemma}.",
        "context_sentence": f"A sentence with {lemma} in it.",
    }
    values.update(overrides)
    return make_word(lemma, **values)


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
    assert "自动安排" in page._idle_hint.text()


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

    # 新词默认先看词回忆。
    assert page._current is not None
    assert page._current.lemma == "alpha"
    assert page._current_mode == CARD_RECOGNITION
    assert page._mode_label.text() == "看词回忆"
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
    assert "看词回忆 2 个" in page._summary_detail.text()
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
    assert len(page._reveal_shortcuts) == 2
    assert len(page._rating_shortcuts) == 4


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


# ---------------------------------------------------------------- 自动编排
def test_session_plan_picks_modes_by_stage(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_word("fresh"))
    db.words.upsert(make_dual_word("second"))
    db.words.upsert(make_spelling_word("mature"))

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()

    assert [card.mode for card in page._session] == [
        CARD_RECOGNITION,
        CARD_DUAL,
        CARD_SPELLING,
    ]
    page._reveal()
    page._rate(RATING_KNOWN)
    assert page._current_mode == CARD_DUAL
    assert page._mode_label.text() == "看词 + 拼写"


def test_recent_vague_review_forces_dual(db: Database, tmp_path: Path) -> None:
    """久经复习但上次评“模糊”的词，也要先认一遍再默写。"""
    get_app()
    stored = db.words.upsert(make_spelling_word("alpha", repetitions=5))
    assert stored.id is not None
    db.reviews.log(word_id=stored.id, rating=2, prev_interval=6, next_interval=7)

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()

    assert page._current_mode == CARD_DUAL


def test_phrase_word_falls_back_to_recognition(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(
        make_word("look up", meaning_cn="查阅", example_en="Look up the word.", repetitions=5)
    )

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()

    assert page._current_mode == CARD_RECOGNITION
    assert page._spelling_box.isHidden() is True
    assert page._word_label.isHidden() is False


# ---------------------------------------------------------------- 拼写卡片
def test_spelling_card_shows_prompt_and_hides_word(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_spelling_word())

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()

    assert page._current_mode == CARD_SPELLING
    assert page._mode_label.text() == "拼写回忆"
    assert page._spelling_box.isHidden() is False
    assert page._word_label.isHidden() is True
    assert page._phonetic_label.isHidden() is True
    assert page._prompt_meaning.text() == "第一个字母"
    assert page._prompt_context.text() == "This is ____."
    assert "alpha" not in page._prompt_context.text()


def test_spelling_correct_suggests_known_and_rates(db: Database, tmp_path: Path) -> None:
    get_app()
    stored = db.words.upsert(make_spelling_word())
    assert stored.id is not None

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
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
    assert "拼写回忆 1 个" in page._summary_detail.text()
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


def test_spelling_front_disables_all_shortcuts_until_reveal(db: Database, tmp_path: Path) -> None:
    """输入阶段不能让空格/回车/数字被 QShortcut 抢走。"""
    get_app()
    db.words.upsert(make_spelling_word())

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()

    assert all(not shortcut.isEnabled() for shortcut in page._shortcuts)

    page._spelling_input.setText("alpha")
    page._submit_spelling()
    assert all(shortcut.isEnabled() for shortcut in page._shortcuts)


def test_spelling_enter_submits(db: Database, tmp_path: Path) -> None:
    """回车必须走 QLineEdit.returnPressed，而不是页面级 Return 快捷键。"""
    get_app()
    db.words.upsert(make_spelling_word())

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()

    page._spelling_input.setText("alpha")
    page._spelling_input.returnPressed.emit()

    assert page._revealed is True
    assert page._recall_result is not None
    assert page._recall_result.status == STATUS_CORRECT


# ---------------------------------------------------------------- 双阶段卡片
def test_dual_card_reveal_then_spell_then_rate(db: Database, tmp_path: Path) -> None:
    get_app()
    stored = db.words.upsert(make_dual_word())
    assert stored.id is not None

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()

    # 正面仍是看词回忆：原词可见，输入框隐藏。
    assert page._current_mode == CARD_DUAL
    assert page._mode_label.text() == "看词 + 拼写"
    assert page._word_label.isHidden() is False
    assert page._spelling_box.isHidden() is True
    assert page._reveal_button.text() == "显示释义并默写（空格）"

    page._reveal()

    # 翻面进入默写：词形、音标、上下文和答案区都藏起来。
    assert page._revealed is True
    assert page._word_label.isHidden() is True
    assert page._phonetic_label.isHidden() is True
    assert page._context_label.isHidden() is True
    assert page._spelling_box.isHidden() is False
    assert page._answer_box.isHidden() is True
    assert page._rating_bar.isHidden() is False
    assert page._prompt_meaning.text() == "第一个字母"
    assert page._prompt_context.text() == "This is ____."
    # 空格/回车禁用（回车交给输入框），1~4 保留用于跳过默写。
    assert all(not shortcut.isEnabled() for shortcut in page._reveal_shortcuts)
    assert all(shortcut.isEnabled() for shortcut in page._rating_shortcuts)

    page._spelling_input.setText("alpha")
    page._submit_spelling()

    assert page._recall_result is not None
    assert page._recall_result.status == STATUS_CORRECT
    assert "拼写正确" in page._spelling_result.text()
    assert page._word_label.isHidden() is False
    assert page._answer_box.isHidden() is False
    assert page._spelling_submit.isHidden() is True
    assert all(shortcut.isEnabled() for shortcut in page._shortcuts)

    page._rate(RATING_KNOWN)

    assert page._stack.currentWidget() is page._summary_page
    assert "看词 + 拼写 1 个" in page._summary_detail.text()
    assert db.stats.get().reviews_done == 1

    log = db.reviews.list_for_word(stored.id)[0]
    assert log.answer_text == "alpha"
    assert log.verdict == STATUS_CORRECT
    # 一张卡只调度一次：interval 从 1 天长到 6 天。
    reloaded = db.words.get_by_id(stored.id)
    assert reloaded is not None
    assert reloaded.repetitions == 2
    assert reloaded.interval_days == 6


def test_dual_card_can_skip_spelling_and_rate(db: Database, tmp_path: Path) -> None:
    get_app()
    stored = db.words.upsert(make_dual_word())
    assert stored.id is not None

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()
    page._reveal()

    # 直接按 1~4：不默写，证据记 skipped。
    page._rate(RATING_KNOWN)

    assert page._stack.currentWidget() is page._summary_page
    log = db.reviews.list_for_word(stored.id)[0]
    assert log.verdict == VERDICT_SKIPPED
    assert log.answer_text == ""
    assert log.distance == 0


def test_summary_breaks_down_card_modes(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_word("fresh"))
    db.words.upsert(make_spelling_word("mature"))

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()

    page._reveal()
    page._rate(RATING_KNOWN)

    page._spelling_input.setText("mature")
    page._submit_spelling()
    page._rate(RATING_KNOWN)

    detail = page._summary_detail.text()
    assert "看词回忆 1 个" in detail
    assert "拼写回忆 1 个" in detail
    assert "2 个记得" in detail


def test_cancelled_help_keeps_dual_spell_shortcuts(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """双阶段默写中取消求助弹窗后，空格/回车仍须保持禁用，回车才能提交默写。"""
    get_app()
    db.words.upsert(make_dual_word())

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()
    page._reveal()
    assert all(not shortcut.isEnabled() for shortcut in page._reveal_shortcuts)

    class FakeDialog:
        def __init__(self, *_args, **_kwargs) -> None:
            self.result_reason = ""
            self.result_advice = ""

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr("english_learner.gui.review_help_dialog.ReviewHelpDialog", FakeDialog)
    page._rate(RATING_FORGOT)

    assert page._index == 0
    assert all(not shortcut.isEnabled() for shortcut in page._reveal_shortcuts)
    assert all(shortcut.isEnabled() for shortcut in page._rating_shortcuts)

    page._spelling_input.setText("alpha")
    page._spelling_input.returnPressed.emit()
    assert page._recall_result is not None
    assert page._recall_result.status == STATUS_CORRECT
