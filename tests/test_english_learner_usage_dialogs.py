"""AI 用量弹窗、日志查看器与设置页入口的 offscreen 测试（无网络）。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox  # noqa: E402

from english_learner.gui.ai_usage_dialog import AIUsageDialog  # noqa: E402
from english_learner.gui.log_viewer_dialog import LogViewerDialog  # noqa: E402
from english_learner.gui.pages.settings import SettingsPage  # noqa: E402
from english_learner.models.entities import AICall, now_iso  # noqa: E402
from english_learner.services.app_context import AppContext  # noqa: E402
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


def seed_calls(db: Database) -> None:
    db.ai_calls.insert(
        AICall(
            called_at=now_iso(),
            task="查词",
            model="test-model",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            latency_ms=500,
        )
    )
    db.ai_calls.insert(
        AICall(
            called_at=now_iso(),
            task="每日文章",
            model="test-model",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            latency_ms=1500,
        )
    )
    db.ai_calls.insert(
        AICall(
            called_at="2020-01-01T08:00:00",
            task="查词",
            model="old-model",
            ok=False,
            error_kind="auth",
            error_message="API Key 无效",
            latency_ms=100,
        )
    )


# ------------------------------------------------------------- 用量弹窗
def test_usage_dialog_summary_and_filters(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    seed_calls(db)

    dialog = AIUsageDialog(db.ai_calls)

    assert dialog._metric_labels["calls"].text() == "2"
    assert dialog._metric_labels["total_tokens"].text() == "450"
    assert dialog._detail_table.rowCount() == 2
    assert dialog._task_table.rowCount() == 2

    dialog._failed_check.setChecked(True)
    assert dialog._detail_table.rowCount() == 0
    assert "只看失败" in dialog._status.text() or "还没有" in dialog._status.text()

    dialog._failed_check.setChecked(False)
    dialog._range_combo.setCurrentIndex(3)  # 全部
    assert dialog._metric_labels["calls"].text() == "3"
    assert dialog._detail_table.rowCount() == 3

    dialog._task_combo.setCurrentIndex(dialog._task_combo.findData("查词"))
    assert dialog._detail_table.rowCount() == 2

    dialog.close()
    db.close()


def test_usage_dialog_clear_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    seed_calls(db)
    dialog = AIUsageDialog(db.ai_calls)

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    dialog._clear()

    assert db.ai_calls.count() == 0
    assert "已清空" in dialog._status.text()
    dialog.close()
    db.close()


def test_usage_dialog_empty_state(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    dialog = AIUsageDialog(db.ai_calls)

    assert dialog._metric_labels["calls"].text() == "0"
    assert "还没有调用记录" in dialog._status.text()
    dialog.close()
    db.close()


def test_usage_dialog_metrics_readable_at_min_height(tmp_path: Path) -> None:
    """回归：窗口压到最小高度时，汇总数字不能被压成几像素高的细条。"""
    get_app()
    db = Database(tmp_path / "data.db")
    dialog = AIUsageDialog(db.ai_calls)
    dialog.resize(1020, 620)
    dialog.show()
    get_app().processEvents()

    labels = dialog.findChildren(QLabel, "usageValue")
    assert len(labels) == 6
    assert min(label.height() for label in labels) >= 20
    # offscreen 环境没有字体库，这里只确认说明文字有实际高度、没有被压成 0。
    assert dialog._summary_note.height() > 0

    dialog.close()
    db.close()


# ----------------------------------------------------------- 日志查看器
def test_log_viewer_shows_and_filters(tmp_path: Path) -> None:
    get_app()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-11-19 [INFO] AI 调用完成 json=True\n2026-11-19 [ERROR] AI 调用失败 超时\n",
        encoding="utf-8",
    )

    dialog = LogViewerDialog(log_dir)

    assert dialog._file_combo.count() == 1
    assert "AI 调用完成" in dialog._view.toPlainText()
    assert "共 2 行" in dialog._status.text()

    dialog._search_edit.setText("error")
    assert "AI 调用失败" in dialog._view.toPlainText()
    assert "AI 调用完成" not in dialog._view.toPlainText()
    assert "筛选" in dialog._status.text()

    dialog._auto_check.setChecked(True)
    dialog._auto_check.setChecked(False)
    dialog.close()


def test_log_viewer_empty_directory(tmp_path: Path) -> None:
    get_app()
    dialog = LogViewerDialog(tmp_path / "missing")

    assert dialog._file_combo.count() == 0
    assert dialog._view.isEnabled() is False
    assert "还没有日志文件" in dialog._status.text()
    dialog.close()


# --------------------------------------------------------------- 设置页
def test_settings_page_usage_summary_and_entry(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    seed_calls(db)
    context = AppContext(make_config(), store, db)

    page = SettingsPage(make_config(), store, context)

    text = page._usage_summary.text()
    assert "今日 2 次调用" in text
    assert "450 tokens" in text
    assert page._usage_button.isEnabled() is True
    assert page._logs_button.isEnabled() is True

    db.close()


def test_settings_page_without_context_disables_usage(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())

    page = SettingsPage(make_config(), store)

    assert page._usage_button.isEnabled() is False
    assert "未连接数据库" in page._usage_summary.text()
    assert page.shutdown() is None
