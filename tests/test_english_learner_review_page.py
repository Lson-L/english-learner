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
from english_learner.services.config_store import AIConfig, ConfigStore, ReviewConfig  # noqa: E402
from english_learner.services.recall import (  # noqa: E402
    STATUS_CORRECT,
    STATUS_NEAR,
    STATUS_WRONG,
)
from english_learner.services.review_plan import (  # noqa: E402
    CARD_CONTEXT,
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


def make_context(
    db: Database,
    tmp_path: Path,
    *,
    spelling: bool = True,
    daily_limit: int = 20,
) -> AppContext:
    """默认给旧用例开启拼写，避免它们同时验证新开关；新用例显式关闭。"""
    store = ConfigStore(tmp_path / "config.json")
    store.save_review(ReviewConfig(daily_limit=daily_limit, spelling_enabled=spelling))
    return AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        store,
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


def test_undo_last_rating_restores_schedule_logs_and_stats(db: Database, tmp_path: Path) -> None:
    """误按 1~4 后可撤销：词条排期、review_logs、今日复习数都要回滚。"""
    get_app()
    alpha = db.words.upsert(make_word("alpha"))
    assert alpha.id is not None

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()
    assert page._undo_button.isHidden() is True

    page._reveal()
    page._rate(RATING_KNOWN)
    assert page._stack.currentWidget() is page._summary_page
    assert page._undo_button.isHidden() is False
    assert db.stats.get().reviews_done == 1
    assert db.reviews.count_for_date(today_str()) == 1

    page._undo_last_rating()

    assert page._stack.currentWidget() is page._card_page
    assert page._index == 0
    assert page._current is not None and page._current.lemma == "alpha"
    reloaded = db.words.get_by_id(alpha.id)
    assert reloaded is not None
    assert reloaded.interval_days == 0
    assert reloaded.repetitions == 0
    assert reloaded.status == "new"
    assert db.reviews.list_for_word(alpha.id) == []
    assert db.stats.get().reviews_done == 0
    assert page._last_undo is None
    assert page._undo_button.isHidden() is True

    # 撤销后可以重新评分，统计只算一次。
    page._reveal()
    page._rate(RATING_KNOWN)
    assert db.stats.get().reviews_done == 1
    assert db.reviews.count_for_date(today_str()) == 1


def test_undo_mid_session_returns_to_previous_card(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_word("alpha"))
    db.words.upsert(make_word("beta"))

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()
    page._reveal()
    page._rate(RATING_KNOWN)

    assert page._index == 1
    assert page._current is not None and page._current.lemma == "beta"
    assert page._undo_button.isHidden() is False

    page._undo_last_rating()

    assert page._index == 0
    assert page._current is not None and page._current.lemma == "alpha"
    assert db.stats.get().reviews_done == 0


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

    page = ReviewPage(make_context(db, tmp_path, daily_limit=200))
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


# ---------------------------------------------------------------- 每日目标与分批
def test_spelling_is_off_by_default(db: Database, tmp_path: Path) -> None:
    """没有 review 配置时默认只做看词回忆，不出现拼写卡片。"""
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        store,
        db,
    )
    db.words.upsert(make_dual_word("alpha"))
    db.words.upsert(make_spelling_word("beta"))

    page = ReviewPage(context)
    page.on_activated()
    page._start_session()

    assert [card.mode for card in page._session] == [CARD_RECOGNITION, CARD_RECOGNITION]
    assert page._spelling_box.isHidden() is True
    assert page._mode_label.text() == "看词回忆"


def test_daily_goal_limits_session_and_reports_progress(db: Database, tmp_path: Path) -> None:
    get_app()
    for index in range(8):
        db.words.upsert(make_word(f"word{index}"))

    page = ReviewPage(make_context(db, tmp_path, spelling=False, daily_limit=5))
    page.on_activated()
    page._start_session()

    assert len(page._session) == 5
    for _ in range(5):
        page._reveal()
        page._rate(RATING_KNOWN)

    assert page._stack.currentWidget() is page._summary_page
    assert "今日已完成 5 / 5" in page._summary_detail.text()
    assert "加练一轮" in page._continue_button.text()
    assert db.stats.get().reviews_done == 5
    assert page._due_label.text().startswith("今天到期 3 词")


def test_idle_hint_reports_backlog_when_due_exceeds_goal(db: Database, tmp_path: Path) -> None:
    get_app()
    for index in range(8):
        db.words.upsert(make_word(f"word{index}"))

    page = ReviewPage(make_context(db, tmp_path, spelling=False, daily_limit=5))
    page.on_activated()

    assert page._idle_title.text() == "今天先复习 5 个词"
    assert "本轮先做 5 个" in page._idle_hint.text()
    assert "剩下的会留在积压里" in page._idle_hint.text()


def test_idle_hint_handles_due_shorter_than_remaining_goal(db: Database, tmp_path: Path) -> None:
    get_app()
    for index in range(3):
        db.words.upsert(make_word(f"word{index}"))

    page = ReviewPage(make_context(db, tmp_path, spelling=False, daily_limit=10))
    page.on_activated()

    assert page._idle_title.text() == "今天先复习 3 个词"
    assert "全部做完即可" in page._idle_hint.text()
    assert "暂时没有更多到期词" in page._idle_hint.text()


def test_session_limit_caps_batch(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_app()
    from english_learner.gui.pages import review as review_module

    monkeypatch.setattr(review_module, "SESSION_LIMIT", 3)
    for index in range(10):
        db.words.upsert(make_word(f"word{index}"))

    page = ReviewPage(make_context(db, tmp_path, spelling=False, daily_limit=5))
    page.on_activated()
    assert "今天先复习 3 个词" in page._idle_title.text()

    page._start_session()
    assert len(page._session) == 3


def test_daily_goal_subtracts_reviews_done_today(db: Database, tmp_path: Path) -> None:
    get_app()
    db.stats.bump(today_str(), reviews_done=2)
    for index in range(5):
        db.words.upsert(make_word(f"word{index}"))

    page = ReviewPage(make_context(db, tmp_path, spelling=False, daily_limit=5))
    page.on_activated()
    page._start_session()

    # 目标 5、今天已经复习 2 个，本轮只取剩下的 3 个。
    assert len(page._session) == 3
    assert page._progress_label.text() == "第 1 / 3 个"


def test_round_checkpoint_and_continue(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_app()
    from english_learner.gui.pages import review as review_module

    monkeypatch.setattr(review_module, "ROUND_SIZE", 2)
    for index in range(3):
        db.words.upsert(make_word(f"word{index}"))

    page = ReviewPage(make_context(db, tmp_path, spelling=False, daily_limit=3))
    page.on_activated()
    page._start_session()

    for _ in range(2):
        page._reveal()
        page._rate(RATING_KNOWN)

    assert page._stack.currentWidget() is page._summary_page
    assert page._summary_title.text() == "第 1 轮完成"
    assert "本轮复习 2 个词" in page._summary_detail.text()
    assert page._finish_button.isHidden() is False
    assert "还剩 1 个" in page._continue_button.text()

    page._continue_from_summary()
    assert page._stack.currentWidget() is page._card_page
    assert page._progress_label.text() == "第 3 / 3 个"

    page._reveal()
    page._rate(RATING_KNOWN)
    assert page._stack.currentWidget() is page._summary_page
    assert "本轮复习了 3 个词" in page._summary_detail.text()


def test_on_activated_keeps_checkpoint(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """停在检查点时切走再回来，不应被刷新回概览。"""
    get_app()
    from english_learner.gui.pages import review as review_module

    monkeypatch.setattr(review_module, "ROUND_SIZE", 2)
    for index in range(4):
        db.words.upsert(make_word(f"word{index}"))

    page = ReviewPage(make_context(db, tmp_path, spelling=False, daily_limit=4))
    page.on_activated()
    page._start_session()
    for _ in range(2):
        page._reveal()
        page._rate(RATING_KNOWN)

    page.on_activated()

    assert page._at_checkpoint is True
    assert page._stack.currentWidget() is page._summary_page
    assert page._summary_title.text() == "第 1 轮完成"


def test_on_activated_keeps_active_card_session(db: Database, tmp_path: Path) -> None:
    """卡片会话进行中切走再回来，不应被刷新回概览或重置当前卡片。"""
    get_app()
    for index in range(3):
        db.words.upsert(make_word(f"word{index}"))

    page = ReviewPage(make_context(db, tmp_path, spelling=False, daily_limit=3))
    page.on_activated()
    page._start_session()
    page._reveal()
    page._rate(RATING_KNOWN)

    page.on_activated()

    assert page._stack.currentWidget() is page._card_page
    assert page._index == 1
    assert page._current is not None
    assert page._current.lemma == "word1"


def test_round_of_exactly_ten_finishes_without_empty_checkpoint(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_app()
    from english_learner.gui.pages import review as review_module

    # 显式锁定 10，避免以后调整常量时用例名与语义漂移。
    monkeypatch.setattr(review_module, "ROUND_SIZE", 10)
    for index in range(10):
        db.words.upsert(make_word(f"word{index}"))

    page = ReviewPage(make_context(db, tmp_path, spelling=False, daily_limit=10))
    page.on_activated()
    page._start_session()
    for _ in range(10):
        page._reveal()
        page._rate(RATING_KNOWN)

    # 第 10 张正好是最后一张：直接进总结，不应停在「还剩 0 个」的检查点。
    assert page._at_checkpoint is False
    assert page._stack.currentWidget() is page._summary_page
    assert page._summary_title.text() == "今天的复习已完成"


def test_end_session_early_saves_partial_progress(db: Database, tmp_path: Path) -> None:
    get_app()
    for index in range(5):
        db.words.upsert(make_word(f"word{index}"))

    page = ReviewPage(make_context(db, tmp_path, spelling=False, daily_limit=5))
    page.on_activated()
    page._start_session()
    for _ in range(2):
        page._reveal()
        page._rate(RATING_KNOWN)

    page._end_session_early()

    assert page._stack.currentWidget() is page._summary_page
    assert "今日已完成 2 / 5" in page._summary_detail.text()
    assert "还差 3 个" in page._summary_detail.text()
    assert page._finish_button.isHidden() is True
    assert "继续复习" in page._continue_button.text()
    assert db.stats.get().reviews_done == 2


def test_context_card_masks_sentence_and_logs_mode(db: Database, tmp_path: Path) -> None:
    """M7.4：新词优先语境识别；选中后按实际评分，日志记录 context 模式。"""
    get_app()
    for lemma in ("alpha", "beta", "gamma", "delta"):
        db.words.upsert(make_word(lemma))

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()

    assert page._current_mode == CARD_CONTEXT
    assert page._mode_label.text() == "语境识别"
    question = page._context_question
    assert question is not None
    assert page._context_prompt.text() == question.masked_sentence
    assert "____" in question.masked_sentence
    assert page._context_box.isHidden() is False
    assert page._word_label.isHidden() is True

    page._choose_context(question.answer_index)

    assert page._revealed is True
    assert page._rating_hint.text().startswith("选对了")
    current_id = page._current.id
    assert current_id is not None
    page._rate(RATING_KNOWN)

    logs = db.reviews.list_for_word(current_id)
    assert logs and logs[0].mode == "context"


def test_switch_mode_cycles_between_available_modes(db: Database, tmp_path: Path) -> None:
    """M7.4：允许单卡切换题型，且不写库、不影响排期。"""
    get_app()
    for lemma in ("alpha", "beta", "gamma", "delta"):
        db.words.upsert(make_word(lemma))

    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()
    page._start_session()
    word_id = page._current.id
    assert page._current_mode == CARD_CONTEXT

    page._switch_mode()
    assert page._current_mode != CARD_CONTEXT
    assert page._session[page._index].context is None

    page._switch_mode()
    page._switch_mode()
    assert page._current_mode == CARD_CONTEXT

    # 切换题型只是内存状态：没有评分、没有日志。
    assert db.reviews.list_for_word(word_id) == []
