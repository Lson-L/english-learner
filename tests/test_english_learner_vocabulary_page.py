"""生词本页 UI 测试（offscreen）：列表、筛选、搜索、编辑释义、删除。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from english_learner.gui.pages.vocabulary import VocabularyPage  # noqa: E402
from english_learner.models.entities import Article, Word, now_iso, today_str  # noqa: E402
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore  # noqa: E402
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
        "context_sentence": f"A sentence with {lemma}.",
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


def test_lists_all_words_and_filters(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_word("alpha"))
    db.words.upsert(make_word("beta", status="learning", interval_days=6))
    db.words.upsert(make_word("gamma", status="mastered", interval_days=40))

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()

    assert page._table.rowCount() == 3
    assert page._count_label.text() == "共 3 个词"
    assert page._empty_label.isHidden() is True

    # 筛选：学习中
    page._filter_combo.setCurrentIndex(2)
    assert page._table.rowCount() == 1
    assert page._table.item(0, 0).text() == "beta"

    # 回到全部，再搜索
    page._filter_combo.setCurrentIndex(0)
    page._search_edit.setText("gam")
    assert page._table.rowCount() == 1
    assert page._table.item(0, 0).text() == "gamma"

    page._search_edit.clear()
    assert page._table.rowCount() == 3


def test_status_and_due_columns(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_word("alpha", status="learning", due_date="2026-12-01"))

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()

    assert page._table.item(0, 2).text() == "学习中"
    assert page._table.item(0, 3).text() == "2026-12-01"


def test_selection_shows_detail_and_saves_meaning(db: Database, tmp_path: Path) -> None:
    get_app()
    article = db.articles.insert(
        Article(
            article_date=today_str(),
            title="Small Habits",
            content="Small habits are powerful.",
            level="B1",
            created_at=now_iso(),
        )
    )
    assert article.id is not None
    stored = db.words.upsert(make_word("resilient", article_id=article.id))
    assert stored.id is not None

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()

    assert page._selected is not None and page._selected.lemma == "resilient"
    assert page._title_label.text() == "resilient"
    assert page._meaning_cn_edit.toPlainText() == "resilient 的中文释义"
    assert "出处：Small Habits" in page._source_label.text()
    assert "状态：未复习" in page._srs_label.text()

    page._meaning_cn_edit.setPlainText("有韧性的；能迅速恢复的")
    page._meaning_en_edit.setPlainText("able to recover quickly")
    page._save_meaning()

    reloaded = db.words.get_by_id(stored.id)
    assert reloaded is not None
    assert reloaded.meaning_cn == "有韧性的；能迅速恢复的"
    assert reloaded.meaning_en == "able to recover quickly"
    assert "已保存" in page._status.text()
    # 表格同步刷新
    assert page._table.item(0, 1).text() == "有韧性的；能迅速恢复的"


def test_empty_meaning_is_rejected(db: Database, tmp_path: Path) -> None:
    get_app()
    stored = db.words.upsert(make_word("alpha"))
    assert stored.id is not None

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()
    page._meaning_cn_edit.setPlainText("   ")
    page._meaning_en_edit.setPlainText("   ")
    page._save_meaning()

    reloaded = db.words.get_by_id(stored.id)
    assert reloaded is not None
    assert reloaded.meaning_cn == "alpha 的中文释义"
    assert "至少填一个" in page._status.text()


def test_delete_selected_word(db: Database, tmp_path: Path) -> None:
    get_app()
    stored = db.words.upsert(make_word("alpha"))
    assert stored.id is not None

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()
    assert page._selected is not None

    page._delete_selected()

    assert db.words.count() == 0
    assert page._table.rowCount() == 0
    assert page._empty_label.isHidden() is False
    assert "已从生词本删除" in page._status.text()


def test_empty_database_shows_hint(db: Database, tmp_path: Path) -> None:
    get_app()
    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()

    assert page._table.rowCount() == 0
    assert page._count_label.text() == "共 0 个词"
    assert page._empty_label.isHidden() is False
    assert page._selected is None
