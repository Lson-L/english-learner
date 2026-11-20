"""学习数据库的本地每日备份。

设计：
- 每次启动时、在 SQLite 连接与 schema 迁移**之前**调用 `run_startup_backup()`；
- 用 `sqlite3.Connection.backup()` 做在线快照：它走 SQLite 的备份 API，
  能正确包含 WAL 里尚未合并的已提交数据，比直接复制文件安全；
- 文件名 `data-YYYYMMDD.db`，同一天只备份一次；只保留最近 7 份；
- 备份失败只记日志，绝不阻塞应用启动。

Java 对照：≈ 启动时跑一次的 BackupRunner，调用数据库自带的备份 API，
失败吞掉异常并记 warn，主流程照常往下走。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date
from pathlib import Path

from .config_store import config_dir

logger = logging.getLogger(__name__)

BACKUP_DIR_NAME = "backups"
BACKUP_KEEP = 7
_PREFIX = "data-"
_SUFFIX = ".db"
# 只认 data-YYYYMMDD.db，避免误删用户自己放进 backups/ 的其它文件。
_NAME_PATTERN = re.compile(rf"^{re.escape(_PREFIX)}(\d{{8}}){re.escape(_SUFFIX)}$")


def default_database_path() -> Path:
    """与 `Database()` 的默认路径保持一致（%APPDATA%/EnglishLearner/data.db）。"""
    return config_dir() / "data.db"


def backup_directory(db_path: Path | str | None = None) -> Path:
    """备份目录固定放在数据库同级的 backups/ 下，方便连同数据库一起拷走。"""
    source = Path(db_path) if db_path is not None else default_database_path()
    return source.parent / BACKUP_DIR_NAME


def create_daily_backup(
    db_path: Path | str | None = None,
    *,
    keep: int = BACKUP_KEEP,
    today: date | None = None,
) -> Path | None:
    """生成当天的备份并清理旧文件。

    返回新建的备份路径；数据库文件不存在或当天已有备份时返回 None。
    """
    source = Path(db_path) if db_path is not None else default_database_path()
    if not source.is_file():
        return None

    directory = backup_directory(source)
    directory.mkdir(parents=True, exist_ok=True)

    stamp = (today or date.today()).strftime("%Y%m%d")
    target = directory / f"{_PREFIX}{stamp}{_SUFFIX}"
    created: Path | None = None
    if not target.exists():
        # 先写 .tmp 再原子替换：复制中途失败不会留下半截的 data-YYYYMMDD.db。
        temp = target.with_name(target.name + ".tmp")
        try:
            copy_database_snapshot(source, temp)
            temp.replace(target)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        created = target
        logger.info("数据库已备份：%s", target)

    prune_backups(directory, keep=keep)
    return created


def prune_backups(directory: Path | str, *, keep: int = BACKUP_KEEP) -> list[Path]:
    """只保留最近 keep 份 `data-YYYYMMDD.db`，返回被删除的文件列表。"""
    directory = Path(directory)
    if not directory.is_dir():
        return []

    backups = sorted(
        item for item in directory.iterdir() if item.is_file() and _NAME_PATTERN.match(item.name)
    )
    limit = max(keep, 1)
    removed: list[Path] = []
    for item in backups[:-limit]:
        try:
            item.unlink()
        except OSError:
            logger.warning("删除旧备份失败：%s", item, exc_info=True)
            continue
        removed.append(item)
    if removed:
        logger.info("清理旧备份 %d 份（保留最近 %d 份）", len(removed), limit)
    return removed


def run_startup_backup(
    db_path: Path | str | None = None, *, keep: int = BACKUP_KEEP
) -> Path | None:
    """启动时调用；任何异常都只记日志，不影响应用继续使用。"""
    try:
        return create_daily_backup(db_path, keep=keep)
    except Exception:  # noqa: BLE001 - 备份失败不允许阻塞启动
        logger.warning("启动自动备份失败，本次跳过", exc_info=True)
        return None


def copy_database_snapshot(source: Path | str, target: Path | str) -> None:
    """用 SQLite 备份 API 生成一致快照（包含 WAL 中的已提交数据）。

    导出 / 导入也复用它：直接复制文件可能拿到半截事务，还会漏掉 -wal。
    """
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
