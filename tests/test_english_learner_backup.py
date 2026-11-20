"""数据库每日自动备份：服务层快照 / 清理策略 + 设置页入口（offscreen，无网络）。"""

from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QPushButton

from english_learner.gui.pages import settings as settings_module
from english_learner.gui.pages.settings import SettingsPage
from english_learner.models.entities import Word, now_iso
from english_learner.services.app_context import AppContext
from english_learner.services.backup_service import (
    backup_directory,
    create_daily_backup,
    prune_backups,
    run_startup_backup,
)
from english_learner.services.config_store import AIConfig, ConfigStore
from english_learner.storage.db import Database

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


def seed_word(db: Database, lemma: str = "alpha") -> None:
    db.words.upsert(
        Word(
            lemma=lemma,
            meaning_cn="第一个",
            in_vocabulary=True,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )


def read_word_lemmas(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [str(row[0]) for row in conn.execute("SELECT lemma FROM words ORDER BY lemma")]
    finally:
        conn.close()


# ---------------------------------------------------------------------- 服务层


def test_creates_daily_backup_with_data(tmp_path: Path) -> None:
    db = Database(tmp_path / "data.db")
    seed_word(db, "alpha")
    db.close()

    backup = create_daily_backup(db.path, today=date(2026, 1, 2))

    assert backup == tmp_path / "backups" / "data-20260102.db"
    assert backup is not None and backup.is_file()
    assert read_word_lemmas(backup) == ["alpha"]


def test_same_day_backup_is_skipped(tmp_path: Path) -> None:
    db = Database(tmp_path / "data.db")
    seed_word(db)
    db.close()

    first = create_daily_backup(db.path, today=date(2026, 3, 4))
    assert first is not None
    before = first.read_bytes()

    # 同一天再启动：不重复写文件，内容也不变。
    assert create_daily_backup(db.path, today=date(2026, 3, 4)) is None
    assert first.read_bytes() == before


def test_missing_database_returns_none_without_creating_dir(tmp_path: Path) -> None:
    assert create_daily_backup(tmp_path / "data.db") is None
    assert not (tmp_path / "backups").exists()


def test_backup_includes_wal_data_while_connection_open(tmp_path: Path) -> None:
    """连接不关闭时，最近写入还躺在 -wal 里；备份 API 必须把它带进去。"""
    db = Database(tmp_path / "data.db")
    seed_word(db, "walword")

    backup = create_daily_backup(db.path, today=date(2026, 5, 6))
    assert backup is not None
    db.close()

    # 旧实现直接复制 data.db 会漏掉这条记录，backup() 不会。
    assert read_word_lemmas(backup) == ["walword"]


def test_prune_keeps_newest_and_ignores_other_files(tmp_path: Path) -> None:
    directory = tmp_path / "backups"
    directory.mkdir()
    for day in range(1, 10):
        (directory / f"data-202601{day:02d}.db").write_bytes(b"x")
    notes = directory / "notes.txt"
    notes.write_text("用户自己放的文件", encoding="utf-8")
    odd = directory / "data-2026.db"
    odd.write_bytes(b"x")

    removed = prune_backups(directory, keep=7)

    remaining = sorted(item.name for item in directory.iterdir())
    assert [path.name for path in removed] == ["data-20260101.db", "data-20260102.db"]
    assert "data-20260103.db" in remaining
    assert "data-20260109.db" in remaining
    assert len([name for name in remaining if name.startswith("data-2026")]) == 8
    assert notes.exists() and odd.exists()


def test_startup_backup_swallows_errors(tmp_path: Path) -> None:
    broken = tmp_path / "data.db"
    broken.write_bytes(b"this is not a sqlite database")

    # 损坏的库不能让应用启动失败：返回 None，异常只进日志。
    assert run_startup_backup(broken) is None


def test_backup_directory_follows_database_path(tmp_path: Path) -> None:
    custom = tmp_path / "custom" / "data.db"
    assert backup_directory(custom) == tmp_path / "custom" / "backups"


# ---------------------------------------------------------------------- 设置页


def test_settings_page_opens_backup_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    context = AppContext(make_config(), store, db)
    page = SettingsPage(make_config(), store, context)

    captured: list[QUrl] = []

    class _DesktopServices:
        @staticmethod
        def openUrl(url: QUrl) -> bool:
            captured.append(url)
            return True

    monkeypatch.setattr(settings_module, "QDesktopServices", _DesktopServices)

    button = next(item for item in page.findChildren(QPushButton) if item.text() == "打开备份目录")
    button.click()

    assert captured, "点击后应调用 QDesktopServices.openUrl"
    assert Path(captured[0].toLocalFile()) == tmp_path / "backups"
    assert (tmp_path / "backups").is_dir()
