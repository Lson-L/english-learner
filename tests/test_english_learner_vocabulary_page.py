"""生词本页 UI 测试（offscreen）：列表、筛选、搜索、编辑释义、删除。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QMessageBox,
    QTableWidgetSelectionRange,
)

from english_learner.gui.pages.vocabulary import VocabularyPage  # noqa: E402
from english_learner.models.entities import Article, Word, now_iso, today_str  # noqa: E402
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore  # noqa: E402
from english_learner.services.srs import (  # noqa: E402
    RATING_FORGOT,
    RATING_KNOWN,
    RATING_VAGUE,
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
        "context_sentence": f"A sentence with {lemma}.",
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


def test_inflected_word_shows_article_form_and_lemma(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_word("go", display_form="went", meaning_cn="去；走"))

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()

    assert page._table.item(0, 0).text() == "went → go"
    assert page._lemma_label.text() == "原形：go"
    assert page._title_label.text() == "went"


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


def test_sort_options_reorder_rows(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_word("alpha", created_at="2026-11-01T09:00:00", due_date="2026-12-03"))
    db.words.upsert(make_word("beta", created_at="2026-11-02T09:00:00", due_date="2026-12-01"))
    db.words.upsert(make_word("gamma", created_at="2026-11-03T09:00:00", due_date=None))

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()

    def column_words() -> list[str]:
        return [page._table.item(row, 0).text() for row in range(page._table.rowCount())]

    assert column_words() == ["gamma", "beta", "alpha"]  # 默认最新加入

    page._sort_combo.setCurrentIndex(1)  # 最早加入
    assert column_words() == ["alpha", "beta", "gamma"]

    page._sort_combo.setCurrentIndex(2)  # 字母顺序
    assert column_words() == ["alpha", "beta", "gamma"]

    page._sort_combo.setCurrentIndex(3)  # 到期日，无到期排最后
    assert column_words() == ["beta", "alpha", "gamma"]


def test_unsaved_meaning_can_be_cancelled_discarded_or_saved(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_app()
    stored = db.words.upsert(make_word("alpha"))
    assert stored.id is not None

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()
    page._meaning_cn_edit.setPlainText("未保存的修改")
    assert page._has_unsaved_changes() is True

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )
    assert page.can_deactivate() is False
    assert page._has_unsaved_changes() is True

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Discard,
    )
    assert page.can_deactivate() is True
    assert page._has_unsaved_changes() is False
    assert page._meaning_cn_edit.toPlainText() == "alpha 的中文释义"

    page._meaning_cn_edit.setPlainText("保存这个释义")
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Save,
    )
    assert page.can_deactivate() is True
    reloaded = db.words.get_by_id(stored.id)
    assert reloaded is not None
    assert reloaded.meaning_cn == "保存这个释义"
    assert page._has_unsaved_changes() is False


def test_switching_word_with_unsaved_meaning_asks_first(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """直接点另一行也不能静默丢弃当前释义：取消 / 放弃 / 保存三选一。"""
    get_app()
    alpha = db.words.upsert(make_word("alpha", created_at="2026-11-01T09:00:00"))
    beta = db.words.upsert(make_word("beta", created_at="2026-11-02T09:00:00"))
    assert alpha.id is not None and beta.id is not None

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()
    alpha_row = page._row_of(alpha.id)
    beta_row = page._row_of(beta.id)
    assert alpha_row is not None and beta_row is not None

    page._table.selectRow(alpha_row)
    page._meaning_cn_edit.setPlainText("未保存的修改")

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )
    page._table.selectRow(beta_row)
    assert page._table.currentRow() == alpha_row
    assert page._selected is not None and page._selected.id == alpha.id
    assert page._has_unsaved_changes() is True

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Discard,
    )
    page._table.selectRow(beta_row)
    assert page._selected is not None and page._selected.id == beta.id
    assert page._has_unsaved_changes() is False

    # 再改 alpha，切到 beta 时选「保存」：alpha 的释义应落库，界面停在 beta。
    page._table.selectRow(alpha_row)
    page._meaning_cn_edit.setPlainText("保存这个释义")
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Save,
    )
    page._table.selectRow(beta_row)

    reloaded = db.words.get_by_id(alpha.id)
    assert reloaded is not None and reloaded.meaning_cn == "保存这个释义"
    assert page._selected is not None and page._selected.id == beta.id
    assert page._has_unsaved_changes() is False


def test_cancelled_filter_change_keeps_current_row(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_app()
    db.words.upsert(make_word("alpha"))
    db.words.upsert(make_word("beta", status="learning"))

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()
    assert page._table.rowCount() == 2

    page._meaning_cn_edit.setPlainText("dirty")
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )

    page._filter_combo.setCurrentIndex(2)  # 学习中

    assert page._filter_combo.currentIndex() == 0
    assert page._table.rowCount() == 2
    assert page._has_unsaved_changes() is True


def test_detail_shows_and_generates_mnemonic(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_app()
    stored = db.words.upsert(make_word("resilient"))
    assert stored.id is not None
    context = make_context(db, tmp_path)
    page = VocabularyPage(context)
    page.on_activated()

    assert "还没有记忆方法" in page._mnemonic_label.text()
    assert page._mnemonic_button.text() == "AI 生成记忆方法"

    class FakeDictionaryService:
        def generate_mnemonic(self, word: Word, level: str) -> Word:
            updated = db.words.update_mnemonic(word.id, "re-silient：压弯后弹回来")
            assert updated is not None
            return updated

    monkeypatch.setattr(context, "dictionary_service", lambda: FakeDictionaryService())

    page._generate_mnemonic()
    worker = page._worker
    assert worker is not None
    assert worker.wait(3000) is True
    get_app().processEvents()

    reloaded = db.words.get_by_id(stored.id)
    assert reloaded is not None
    assert reloaded.mnemonic == "re-silient：压弯后弹回来"
    assert "re-silient" in page._mnemonic_label.text()
    assert page._mnemonic_button.text() == "重新生成记忆方法"
    assert "已生成" in page._status.text()


def test_mnemonic_generation_blocked_by_unsaved_meaning(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_word("resilient"))
    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()
    page._meaning_cn_edit.setPlainText("未保存的修改")

    page._generate_mnemonic()

    assert page._worker is None
    assert "先保存" in page._status.text()
    assert page._has_unsaved_changes() is True


def test_large_word_list_refreshes(db: Database, tmp_path: Path) -> None:
    """几百条词时列表仍能正常刷新与筛选（M3 性能回归）。"""
    get_app()
    for index in range(300):
        db.words.upsert(make_word(f"word{index:03d}"))

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()

    assert page._table.rowCount() == 300
    assert page._count_label.text() == "共 300 个词"

    page._search_edit.setText("word12")
    assert 0 < page._table.rowCount() < 300
    assert page._sort_combo.count() == 4


def test_detail_shows_review_help_records(db: Database, tmp_path: Path) -> None:
    """复习求助写入 review_logs 后，生词本详情要能看到用户输入与 AI 建议。"""
    get_app()
    stored = db.words.upsert(make_word("resilient"))
    assert stored.id is not None
    db.reviews.log(
        word_id=stored.id,
        rating=RATING_FORGOT,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-19T09:30:00",
        note="总是和 resistant 搞混",
        advice="resistant 是抵抗，resilient 是恢复，对比记。",
    )
    # 没有原因/建议的普通评分记录不应出现在求助记录里。
    db.reviews.log(
        word_id=stored.id,
        rating=RATING_KNOWN,
        prev_interval=1,
        next_interval=6,
        reviewed_at="2026-11-19T10:00:00",
    )

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()

    text = page._help_log_label.text()
    assert "2026-11-19 09:30 · 忘记" in text
    assert "我的输入：总是和 resistant 搞混" in text
    assert "AI 帮记：resistant 是抵抗，resilient 是恢复，对比记。" in text
    assert "认识" not in text


def test_detail_help_records_empty_hint(db: Database, tmp_path: Path) -> None:
    get_app()
    db.words.upsert(make_word("resilient"))

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()

    assert "还没有求助记录" in page._help_log_label.text()


def test_detail_help_records_show_latest_five(db: Database, tmp_path: Path) -> None:
    get_app()
    stored = db.words.upsert(make_word("resilient"))
    assert stored.id is not None
    for index in range(1, 7):
        db.reviews.log(
            word_id=stored.id,
            rating=RATING_VAGUE,
            prev_interval=index,
            next_interval=index + 1,
            reviewed_at=f"2026-11-19T0{index}:00:00",
            note=f"原因 {index}",
            advice=f"建议 {index}",
        )

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()

    text = page._help_log_label.text()
    assert text.startswith("2026-11-19 06:00 · 模糊")
    assert "原因 6" in text
    assert "原因 2" in text
    assert "原因 1" not in text
    assert "更早的 1 次求助已省略" in text


def test_detail_help_record_with_only_note_or_advice(db: Database, tmp_path: Path) -> None:
    """跳过 AI 时只有 note；AI 建议也可能只在 advice 一侧，缺的一行要省略。"""
    get_app()
    stored = db.words.upsert(make_word("resilient"))
    assert stored.id is not None
    db.reviews.log(
        word_id=stored.id,
        rating=RATING_VAGUE,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-19T08:00:00",
        note="只记得大概意思",
    )
    db.reviews.log(
        word_id=stored.id,
        rating=RATING_FORGOT,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-19T09:00:00",
        advice="把词根 re- 和 silient 拆开记。",
    )

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()

    text = page._help_log_label.text()
    assert "我的输入：只记得大概意思" in text
    assert "AI 帮记：把词根 re- 和 silient 拆开记。" in text
    # 每条记录各自只显示存在的那一行，不出现空的“我的输入：/ AI 帮记：”。
    assert text.count("我的输入：") == 1
    assert text.count("AI 帮记：") == 1


def test_help_records_not_missed_behind_many_plain_reviews(db: Database, tmp_path: Path) -> None:
    """大量无原因/建议的普通评分不能把更早的求助记录挤出窗口。"""
    get_app()
    stored = db.words.upsert(make_word("resilient"))
    assert stored.id is not None
    for index in range(6):
        db.reviews.log(
            word_id=stored.id,
            rating=RATING_VAGUE,
            prev_interval=index,
            next_interval=index + 1,
            reviewed_at=f"2026-11-18T0{index}:00:00",
            note=f"旧原因 {index}",
            advice=f"旧建议 {index}",
        )
    for index in range(60):
        db.reviews.log(
            word_id=stored.id,
            rating=RATING_KNOWN,
            prev_interval=index,
            next_interval=index + 1,
            reviewed_at=f"2026-11-19T{index // 60:02d}:{index % 60:02d}:00",
        )

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()

    text = page._help_log_label.text()
    assert "旧原因 5" in text
    assert "旧原因 1" in text
    assert "旧原因 0" not in text
    assert "更早的 1 次求助已省略" in text


def test_batch_delete_selected_words(db: Database, tmp_path: Path) -> None:
    get_app()
    for lemma in ("alpha", "beta", "gamma"):
        db.words.upsert(make_word(lemma))

    page = VocabularyPage(make_context(db, tmp_path))
    page.on_activated()
    assert page._batch_delete_button.isEnabled() is True
    assert "(1)" in page._batch_delete_button.text()

    page._table.clearSelection()
    page._table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 3), True)
    assert len(page._selected_word_ids()) == 2
    assert "(2)" in page._batch_delete_button.text()

    deleted = page._delete_selected_words()

    assert deleted == 2
    assert db.words.count() == 1
    assert page._table.rowCount() == 1
    assert "已删除 2 个词" in page._status.text()
