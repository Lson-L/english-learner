"""设置页数据导出入口测试（offscreen，本地文件，无网络）。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

from english_learner.gui.pages.settings import SettingsPage  # noqa: E402
from english_learner.models.entities import Word, now_iso, today_str  # noqa: E402
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore  # noqa: E402
from english_learner.services.export_service import ExportResult  # noqa: E402
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


def make_page(tmp_path: Path) -> tuple[SettingsPage, AppContext, Database]:
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    context = AppContext(make_config(), store, db)
    page = SettingsPage(make_config(), store, context)
    return page, context, db


def test_export_buttons_need_database_context(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())

    page = SettingsPage(make_config(), store)

    assert page._export_json_button.isEnabled() is False
    assert page._export_csv_button.isEnabled() is False
    assert "未连接" in page._export_status.text()
    assert page.shutdown() is None


def test_export_buttons_enabled_with_context(tmp_path: Path) -> None:
    get_app()
    page, _context, db = make_page(tmp_path)

    assert page._export_json_button.isEnabled() is True
    assert page._export_csv_button.isEnabled() is True
    db.close()


def test_export_json_button_writes_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    get_app()
    page, _context, db = make_page(tmp_path)
    db.words.upsert(
        Word(
            lemma="resilient",
            meaning_cn="有韧性的",
            due_date=today_str(),
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    target = tmp_path / "ui-export.json"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(target), "JSON 文件 (*.json)"),
    )

    page._export_json()
    worker = page._worker
    assert worker is not None
    assert worker.wait(5000) is True

    get_app().processEvents()

    assert target.is_file()
    assert "导出完成" in page._export_status.text()
    assert "生词 1" in page._export_status.text()
    assert page._export_json_button.isEnabled() is True
    assert page._worker is None
    db.close()


def test_export_result_message_and_error_fallback(tmp_path: Path) -> None:
    get_app()
    page, _context, db = make_page(tmp_path)

    result = ExportResult(
        files=(tmp_path / "backup.json",),
        counts={
            "profile": 1,
            "articles": 2,
            "words": 3,
            "highlights": 0,
            "review_logs": 5,
            "daily_stats": 2,
        },
    )
    page._on_export_succeeded(result)
    assert "导出完成" in page._export_status.text()
    assert "文章 2" in page._export_status.text()

    page._on_export_succeeded(object())
    assert "意外数据" in page._export_status.text()

    page._on_export_failed("磁盘已满")
    assert page._export_status.text() == "磁盘已满"

    page._on_export_cancelled()
    assert "已取消" in page._export_status.text()
    db.close()
