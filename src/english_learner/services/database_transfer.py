"""数据库快照导出 / 导入：在两台电脑之间整体搬运学习数据。

为什么是"整库覆盖"而不是逐表合并：两台机器各自会生成不同文章、不同检测
记录和不同的 SM-2 排期，按主键合并无法判断该信谁，稍有不慎就会把复习计划
改坏。因此这里只做快照搬运，导入前自动备份本机数据，用户自己决定何时覆盖。

导入分两步，避免在应用运行中替换正在使用的 SQLite 文件：
1. 设置页选择文件 → 校验 → 暂存为 `data.import.db`；
2. 下次启动、建立任何连接之前 → 备份本机库 → 删除旧 -wal/-shm → 原子替换。

Java 对照：≈ 一个 DatabaseMigrationTool，校验 + 暂存 + 启动时 apply。
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..storage.db import SCHEMA_VERSION
from .backup_service import (
    backup_directory,
    copy_database_snapshot,
    default_database_path,
)

logger = logging.getLogger(__name__)

IMPORT_STAGING_NAME = "data.import.db"
# 没有这三张表就不是本应用的库；旧版本库缺新表没关系，启动迁移会补齐。
REQUIRED_TABLES = frozenset({"profile", "articles", "words"})
_PRE_IMPORT_PREFIX = "data-before-import-"
_PRE_IMPORT_SUFFIX = ".db"
_PRE_IMPORT_KEEP = 3


class DatabaseTransferError(RuntimeError):
    """导入 / 导出失败；message 直接展示给用户。"""


@dataclass(frozen=True)
class DatabaseInfo:
    """校验通过后的数据库摘要。"""

    path: Path
    schema_version: int
    tables: tuple[str, ...]


@dataclass(frozen=True)
class ImportResult:
    """一次启动时导入的结果。"""

    database: Path
    safety_backup: Path | None


def pending_import_path(db_path: Path | str | None = None) -> Path:
    """暂存文件与数据库同目录，保证替换是同一分区的原子操作。"""
    source = Path(db_path) if db_path is not None else default_database_path()
    return source.with_name(IMPORT_STAGING_NAME)


def inspect_database(path: Path | str) -> DatabaseInfo:
    """确认文件是本应用的 SQLite 库且版本不比当前程序新。"""
    target = Path(path)
    if not target.is_file():
        raise DatabaseTransferError("文件不存在。")

    try:
        conn = sqlite3.connect(str(target))
        try:
            tables = tuple(
                sorted(
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    )
                )
            )
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            check = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise DatabaseTransferError("不是有效的 SQLite 数据库文件。") from exc

    if not check or str(check[0]).lower() != "ok":
        raise DatabaseTransferError("数据库完整性检查未通过，文件可能已损坏。")
    if version > SCHEMA_VERSION:
        raise DatabaseTransferError(
            f"该数据库来自更新版本（schema v{version}，本程序支持 v{SCHEMA_VERSION}），"
            "请先升级应用。"
        )
    missing = REQUIRED_TABLES - set(tables)
    if missing:
        raise DatabaseTransferError("这不像是 Daily English 的数据库（缺少核心表）。")
    return DatabaseInfo(path=target, schema_version=version, tables=tables)


def export_database(destination: Path | str, db_path: Path | str | None = None) -> Path:
    """把当前数据库导出成可直接拿走 / 导入的快照文件。"""
    source = Path(db_path) if db_path is not None else default_database_path()
    if not source.is_file():
        raise DatabaseTransferError("本机还没有数据库，先正常使用一次再导出。")

    target = Path(destination)
    if target.suffix.lower() != ".db":
        target = target.with_suffix(".db")
    if target.resolve() == source.resolve():
        raise DatabaseTransferError("不能覆盖正在使用的数据库文件，请换一个文件名。")

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    try:
        copy_database_snapshot(source, temp)
        temp.replace(target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    logger.info("数据库已导出：%s", target)
    return target


def stage_import(source: Path | str, db_path: Path | str | None = None) -> Path:
    """校验外部数据库并暂存；真正替换发生在下次启动。"""
    candidate = Path(source)
    info = inspect_database(candidate)

    database = Path(db_path) if db_path is not None else default_database_path()
    staging = pending_import_path(database)
    staging.parent.mkdir(parents=True, exist_ok=True)
    if candidate.resolve() in {database.resolve(), staging.resolve()}:
        raise DatabaseTransferError("不能导入当前正在使用的数据库文件。")

    # 仍然走 SQLite 备份 API 复制：即使用户选的是带 -wal 的活库，也能拿到一致快照。
    temp = staging.with_name(staging.name + ".tmp")
    try:
        copy_database_snapshot(candidate, temp)
        temp.replace(staging)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    logger.info("数据库已暂存待导入：%s（schema v%s）", staging, info.schema_version)
    return staging


def discard_pending_import(db_path: Path | str | None = None) -> bool:
    """放弃已暂存的导入（用户在弹窗里选「回退」）。

    此时本机数据库从未被替换，删除暂存文件即可；返回是否真的删掉了暂存。
    """
    staging = pending_import_path(db_path)
    staging.with_name(staging.name + ".tmp").unlink(missing_ok=True)
    if not staging.exists():
        return False
    staging.unlink()
    logger.info("已放弃待导入数据库：%s", staging)
    return True


def apply_pending_import(
    db_path: Path | str | None = None, *, keep_backups: int = _PRE_IMPORT_KEEP
) -> ImportResult | None:
    """启动时调用：把暂存库替换成当前库，替换前先备份。没有暂存返回 None。"""
    database = Path(db_path) if db_path is not None else default_database_path()
    staging = pending_import_path(database)
    if not staging.is_file():
        return None

    info = inspect_database(staging)
    safety = _backup_before_import(database, keep=keep_backups) if database.is_file() else None

    # 旧的 -wal / -shm 属于被替换掉的库，绝不能留给新文件：
    # SQLite 会把它们当成新库的日志重放，轻则报错，重则写坏数据。
    for suffix in ("-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)

    _replace_with_retry(staging, database)
    logger.info(
        "已导入数据库：%s（schema v%s，导入前备份：%s）",
        database,
        info.schema_version,
        safety,
    )
    return ImportResult(database=database, safety_backup=safety)


def _replace_with_retry(
    source: Path, target: Path, *, attempts: int = 10, delay: float = 0.2
) -> None:
    """Windows 上文件被短暂占用时 `replace` 会报 PermissionError。

    「立即重启」时旧进程可能还有后台线程没退干净，这里最多等约 2 秒再放弃；
    失败时暂存文件仍在，下次启动会重试，不会丢数据。
    """
    for attempt in range(attempts):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def _backup_before_import(database: Path, *, keep: int) -> Path:
    """导入前把本机库另存一份，命名带时间戳，和每日备份分开保留。"""
    directory = backup_directory(database)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = directory / f"{_PRE_IMPORT_PREFIX}{stamp}{_PRE_IMPORT_SUFFIX}"
    temp = target.with_name(target.name + ".tmp")
    try:
        copy_database_snapshot(database, temp)
        temp.replace(target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    _prune_pre_import(directory, keep=keep)
    return target


def _prune_pre_import(directory: Path, *, keep: int) -> None:
    files = sorted(
        item
        for item in directory.iterdir()
        if item.is_file()
        and item.name.startswith(_PRE_IMPORT_PREFIX)
        and item.name.endswith(_PRE_IMPORT_SUFFIX)
    )
    limit = max(keep, 1)
    for item in files[:-limit]:
        try:
            item.unlink()
        except OSError:
            logger.warning("删除旧的导入前备份失败：%s", item, exc_info=True)
