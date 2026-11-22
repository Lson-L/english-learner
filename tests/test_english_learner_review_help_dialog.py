"""复习帮助弹窗测试（offscreen，AI 用 fake completer）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from english_learner.gui import i18n  # noqa: E402
from english_learner.gui.review_help_dialog import ReviewHelpDialog  # noqa: E402
from english_learner.models.entities import Word, now_iso, today_str  # noqa: E402
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore  # noqa: E402
from english_learner.services.review_help_service import (  # noqa: E402
    ReviewHelpResult,
    ReviewHelpService,
)
from english_learner.services.srs import RATING_FORGOT  # noqa: E402
from english_learner.storage.db import Database  # noqa: E402

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


class FakeCompleter:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls = 0

    def complete_json(self, messages, *, temperature=None, max_tokens=None, task="") -> str:
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def make_word(db: Database) -> Word:
    word = db.words.upsert(
        Word(
            lemma="resilient",
            display_form="resilient",
            pos="adj.",
            meaning_cn="有韧性的",
            meaning_en="able to recover quickly",
            context_sentence="Resilient people keep going.",
            due_date=today_str(),
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    assert word.id is not None
    return word


def make_context(db: Database, tmp_path: Path) -> AppContext:
    return AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(tmp_path / "config.json"),
        db,
    )


def test_dialog_never_clips_content_horizontally(db: Database, tmp_path: Path) -> None:
    """回归：窗口曾经按 sizeHint 打开得比正文窄，右侧按钮和左侧正文都被裁掉。"""
    app = get_app()
    i18n.set_low_profile(True)
    try:
        dialog = ReviewHelpDialog(make_context(db, tmp_path), make_word(db), RATING_FORGOT)
        dialog.show()
        app.processEvents()
        dialog._on_help_ready(ReviewHelpResult(advice="很长的建议。" * 120))
        app.processEvents()

        bar = dialog._scroll.horizontalScrollBar()
        assert dialog._scroll_body.minimumSizeHint().width() <= dialog._scroll.viewport().width()
        assert bar.maximum() == 0
        assert bar.value() == 0
        dialog.close()
    finally:
        i18n.set_low_profile(False)


def test_quick_reasons_fill_text(db: Database, tmp_path: Path) -> None:
    get_app()
    dialog = ReviewHelpDialog(make_context(db, tmp_path), make_word(db), RATING_FORGOT)

    dialog._reason_buttons[0].click()
    dialog._reason_buttons[1].click()

    assert "和近义词混淆" in dialog.result_reason
    assert "只记得大概意思" in dialog.result_reason
    dialog.close()


def test_ai_advice_is_shown_and_persisted(db: Database, tmp_path: Path) -> None:
    get_app()
    word = make_word(db)
    payload = {
        "advice": "把 resilient 和 resistant 对比记：前者恢复，后者抵抗。",
        "mnemonic": "re-silient：压弯后弹回来",
    }
    fake = FakeCompleter(json.dumps(payload, ensure_ascii=False))
    service = ReviewHelpService(fake, db.words)
    dialog = ReviewHelpDialog(
        make_context(db, tmp_path),
        word,
        RATING_FORGOT,
        service=service,
    )
    dialog._reason_edit.setPlainText("和 resistant 混淆")

    dialog._submit()
    worker = dialog._worker
    assert worker is not None
    assert worker.wait(3000) is True
    get_app().processEvents()

    assert fake.calls == 1
    assert dialog._result_ready is True
    assert dialog._advice_label.isHidden() is False
    assert "resistant" in dialog._advice_label.text()
    assert "记忆方法" in dialog._advice_label.text()
    assert dialog.result_advice.startswith("把 resilient")
    assert dialog._submit_button.text() == "完成"

    stored = db.words.get_by_id(word.id)
    assert stored is not None and stored.mnemonic == "re-silient：压弯后弹回来"
    dialog.close()


def test_ai_failure_allows_retry_or_skip(db: Database, tmp_path: Path) -> None:
    get_app()
    service = ReviewHelpService(FakeCompleter("not json"), db.words)
    dialog = ReviewHelpDialog(
        make_context(db, tmp_path),
        make_word(db),
        RATING_FORGOT,
        service=service,
    )

    dialog._submit()
    worker = dialog._worker
    assert worker is not None
    assert worker.wait(3000) is True
    get_app().processEvents()

    assert dialog._result_ready is False
    assert dialog._advice_label.isHidden() is True
    assert dialog._submit_button.text() == "重试"
    assert dialog._status.text()
    dialog.close()


def test_skip_accepts_without_ai(db: Database, tmp_path: Path) -> None:
    get_app()
    fake = FakeCompleter("{}")
    dialog = ReviewHelpDialog(
        make_context(db, tmp_path),
        make_word(db),
        RATING_FORGOT,
        service=ReviewHelpService(fake, db.words),
    )
    dialog._reason_edit.setPlainText("只记得大概意思")

    dialog._skip()

    assert dialog.result() == QDialog.DialogCode.Accepted.value
    assert dialog.result_reason == "只记得大概意思"
    assert dialog.result_advice == ""
    assert fake.calls == 0
