"""数据库整库导出 / 导入：校验、暂存、启动时替换（纯本地，无网络）。"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from english_learner.gui.pages import settings as settings_module
from english_learner.gui.pages.settings import SettingsPage
from english_learner.models.entities import Word, now_iso
from english_learner.services.app_context import AppContext
from english_learner.services.config_store import AIConfig, ConfigStore
from english_learner.services.database_transfer import (
    DatabaseTransferError,
    apply_pending_import,
    discard_pending_import,
    export_database,
    inspect_database,
    pending_import_path,
    stage_import,
)
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


def make_db(path: Path, lemma: str) -> Database:
    db = Database(path)
    db.words.upsert(
        Word(
            lemma=lemma,
            meaning_cn="释义",
            in_vocabulary=True,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    db.close()
    return db


def read_lemmas(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [str(row[0]) for row in conn.execute("SELECT lemma FROM words ORDER BY lemma")]
    finally:
        conn.close()


# ---------------------------------------------------------------------- 导出


def test_export_produces_importable_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "data.db"
    make_db(source, "homeword")

    target = export_database(tmp_path / "out" / "english-backup", source)

    assert target == tmp_path / "out" / "english-backup.db"
    assert read_lemmas(target) == ["homeword"]
    assert inspect_database(target).schema_version > 0


def test_export_refuses_to_overwrite_live_database(tmp_path: Path) -> None:
    source = tmp_path / "data.db"
    make_db(source, "alpha")

    with pytest.raises(DatabaseTransferError):
        export_database(source, source)


def test_export_without_database_raises(tmp_path: Path) -> None:
    with pytest.raises(DatabaseTransferError):
        export_database(tmp_path / "out.db", tmp_path / "missing.db")


# ---------------------------------------------------------------------- 校验


def test_inspect_rejects_non_sqlite_file(tmp_path: Path) -> None:
    broken = tmp_path / "data.db"
    broken.write_bytes(b"this is not a sqlite database")

    with pytest.raises(DatabaseTransferError):
        inspect_database(broken)


def test_inspect_rejects_foreign_sqlite_database(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign.db"
    conn = sqlite3.connect(str(foreign))
    conn.execute("CREATE TABLE foo (id INTEGER PRIMARY KEY)")
    conn.close()

    with pytest.raises(DatabaseTransferError):
        inspect_database(foreign)


def test_inspect_rejects_newer_schema(tmp_path: Path) -> None:
    database = tmp_path / "data.db"
    make_db(database, "alpha")
    conn = sqlite3.connect(str(database))
    conn.execute("PRAGMA user_version = 999")
    conn.close()

    with pytest.raises(DatabaseTransferError):
        inspect_database(database)


def test_inspect_accepts_older_schema(tmp_path: Path) -> None:
    database = tmp_path / "data.db"
    make_db(database, "alpha")
    conn = sqlite3.connect(str(database))
    conn.execute("PRAGMA user_version = 3")
    conn.close()

    # 旧版本库允许导入，启动时由 Database._apply_schema 原地迁移。
    assert inspect_database(database).schema_version == 3


# ---------------------------------------------------------------------- 导入


def test_stage_and_apply_replaces_database_with_backup(tmp_path: Path) -> None:
    source = tmp_path / "home" / "data.db"
    make_db(source, "homeword")
    snapshot = export_database(tmp_path / "home" / "export.db", source)

    local = tmp_path / "company" / "data.db"
    make_db(local, "companyword")

    staging = stage_import(snapshot, local)
    assert staging == pending_import_path(local)
    assert staging.is_file()
    # 暂存阶段不能动本机库。
    assert read_lemmas(local) == ["companyword"]

    result = apply_pending_import(local)

    assert result is not None
    assert read_lemmas(local) == ["homeword"]
    assert not staging.exists()
    assert result.safety_backup is not None
    assert read_lemmas(result.safety_backup) == ["companyword"]


def test_apply_removes_stale_wal_sidecars(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    make_db(source, "imported")
    local = tmp_path / "data.db"
    make_db(local, "local")
    # 模拟上次运行留下的日志文件：替换后必须清掉，否则会被当成新库的日志重放。
    for suffix in ("-wal", "-shm"):
        Path(str(local) + suffix).write_bytes(b"stale")

    stage_import(source, local)
    apply_pending_import(local)

    assert not Path(str(local) + "-wal").exists()
    assert not Path(str(local) + "-shm").exists()
    assert read_lemmas(local) == ["imported"]


def test_apply_without_staging_is_noop(tmp_path: Path) -> None:
    assert apply_pending_import(tmp_path / "data.db") is None


def test_discard_pending_import_removes_staging(tmp_path: Path) -> None:
    local = tmp_path / "data.db"
    make_db(local, "local")
    source = tmp_path / "source.db"
    make_db(source, "imported")
    staging = stage_import(source, local)

    assert discard_pending_import(local) is True
    assert not staging.exists()
    assert read_lemmas(local) == ["local"]
    # 没有暂存时再回退是安全的 no-op。
    assert discard_pending_import(local) is False


def test_apply_invalid_staging_keeps_local_data(tmp_path: Path) -> None:
    local = tmp_path / "data.db"
    make_db(local, "local")
    staging = pending_import_path(local)
    staging.write_bytes(b"not a database")

    with pytest.raises(DatabaseTransferError):
        apply_pending_import(local)

    assert read_lemmas(local) == ["local"]


def test_pre_import_backups_are_pruned(tmp_path: Path) -> None:
    local = tmp_path / "data.db"
    make_db(local, "local")
    backups = tmp_path / "backups"
    backups.mkdir()
    for day in range(1, 6):
        (backups / f"data-before-import-2026010{day}-000000.db").write_bytes(b"old")

    source = tmp_path / "source.db"
    make_db(source, "imported")
    stage_import(source, local)
    result = apply_pending_import(local)

    assert result is not None and result.safety_backup is not None
    remaining = sorted(item.name for item in backups.iterdir())
    assert len(remaining) == 3
    assert result.safety_backup.name in remaining


def test_stage_refuses_live_database(tmp_path: Path) -> None:
    local = tmp_path / "data.db"
    make_db(local, "local")

    with pytest.raises(DatabaseTransferError):
        stage_import(local, local)


# ---------------------------------------------------------------------- 设置页


def make_page(tmp_path: Path) -> SettingsPage:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    context = AppContext(make_config(), store, db)
    return SettingsPage(make_config(), store, context)


def find_button(page: SettingsPage, text: str) -> QPushButton:
    return next(item for item in page.findChildren(QPushButton) if item.text() == text)


class _FileDialogs:
    """替身：避免测试里真的弹系统文件对话框。"""

    chosen: str = ""

    @classmethod
    def getOpenFileName(cls, *args: object, **kwargs: object) -> tuple[str, str]:
        return (cls.chosen, "")

    @classmethod
    def getSaveFileName(cls, *args: object, **kwargs: object) -> tuple[str, str]:
        return (cls.chosen, "")


class _MessageBox:
    StandardButton = QMessageBox.StandardButton

    @staticmethod
    def question(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Yes


def test_settings_import_button_stages_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = make_page(tmp_path)
    source = tmp_path / "incoming.db"
    make_db(source, "from-other-machine")

    _FileDialogs.chosen = str(source)
    monkeypatch.setattr(settings_module, "QFileDialog", _FileDialogs)
    monkeypatch.setattr(settings_module, "QMessageBox", _MessageBox)
    monkeypatch.setattr(page, "_prompt_import_ready", lambda staging: "later")

    find_button(page, "导入数据库").click()

    staging = pending_import_path(tmp_path / "data.db")
    assert staging.is_file()
    assert "下次启动" in page._export_status.text()


def test_settings_import_rollback_discards_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = make_page(tmp_path)
    source = tmp_path / "incoming.db"
    make_db(source, "from-other-machine")

    _FileDialogs.chosen = str(source)
    monkeypatch.setattr(settings_module, "QFileDialog", _FileDialogs)
    monkeypatch.setattr(settings_module, "QMessageBox", _MessageBox)
    monkeypatch.setattr(page, "_prompt_import_ready", lambda staging: "rollback")

    find_button(page, "导入数据库").click()

    assert not pending_import_path(tmp_path / "data.db").exists()
    assert "已回退" in page._export_status.text()


def test_settings_import_restart_triggers_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = make_page(tmp_path)
    source = tmp_path / "incoming.db"
    make_db(source, "from-other-machine")

    _FileDialogs.chosen = str(source)
    monkeypatch.setattr(settings_module, "QFileDialog", _FileDialogs)
    monkeypatch.setattr(settings_module, "QMessageBox", _MessageBox)
    monkeypatch.setattr(page, "_prompt_import_ready", lambda staging: "restart")
    restarted: list[bool] = []
    monkeypatch.setattr(page, "_restart_after_import", lambda: restarted.append(True))

    find_button(page, "导入数据库").click()

    assert restarted == [True]
    # 重启前暂存必须保留，供新进程启动时应用。
    assert pending_import_path(tmp_path / "data.db").is_file()


def test_settings_export_button_writes_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = make_page(tmp_path)
    destination = tmp_path / "exported.db"

    _FileDialogs.chosen = str(destination)
    monkeypatch.setattr(settings_module, "QFileDialog", _FileDialogs)

    # 让页面真的写一份数据，确保导出的不是空库。
    database = Database(tmp_path / "data.db")
    database.words.upsert(
        Word(lemma="pageword", meaning_cn="释义", in_vocabulary=True, created_at=now_iso())
    )
    database.close()

    find_button(page, "导出数据库").click()

    assert destination.is_file()
    assert read_lemmas(destination) == ["pageword"]
    assert "已导出数据库" in page._export_status.text()
