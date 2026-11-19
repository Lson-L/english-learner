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
from english_learner.services.config_store import AIConfig, ConfigStore  # noqa: E402
from english_learner.services.srs import (  # noqa: E402
    RATING_FORGOT,
    RATING_KNOWN,
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


def test_no_due_words_disables_start(db: Database, tmp_path: Path) -> None:
    get_app()
    page = ReviewPage(make_context(db, tmp_path))
    page.on_activated()

    assert page._due_label.text().startswith("今天到期 0 词")
    assert page._start_button.isEnabled() is False

    page._start_session()
    assert page._stack.currentWidget() is page._idle_page
    assert "没有到期" in page._status.text()


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
