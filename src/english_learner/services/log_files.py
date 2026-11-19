"""运行日志文件的读取工具（纯 Python，不依赖 PySide6）。

日志由 `app.setup_logging()` 写到 `%APPDATA%/EnglishLearner/logs/app.log`，
按 1 MB 轮转、最多保留 4 个文件（app.log / app.log.1 ~ app.log.3）。
界面上只看最近一段内容即可，所以这里只实现“尾部读取 + 关键字筛选”：

- 只读文件末尾 `MAX_TAIL_BYTES`，避免用户日志很大时卡住 UI；
- 从中间截断时丢掉第一行（可能是半行），并按 utf-8 容错解码；
- 轮转文件按修改时间倒序排列，最新的排在最前面。

Java 对照：≈ 一个 `LogFileReader` 工具类，`Files.newBufferedReader` +
`RandomAccessFile.seek`，不涉及业务。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config_store import log_dir

# 单次最多读取 256 KB：日志是给人扫一眼的，不是完整归档。
MAX_TAIL_BYTES = 256 * 1024


@dataclass(frozen=True)
class LogFileInfo:
    """一个日志文件的基本信息，用于下拉框展示。"""

    path: Path
    size_bytes: int
    modified_at: str

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size_text(self) -> str:
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        if self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes / (1024 * 1024):.2f} MB"


def log_directory() -> Path:
    """当前应用的日志目录（不保证存在）。"""
    return log_dir()


def list_log_files(directory: Path | None = None) -> tuple[LogFileInfo, ...]:
    """列出日志文件（含轮转备份），最新的在前。目录不存在时返回空元组。"""
    base = Path(directory) if directory is not None else log_directory()
    if not base.is_dir():
        return ()

    entries: list[tuple[float, LogFileInfo]] = []
    for path in base.glob("app.log*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append(
            (
                stat.st_mtime,
                LogFileInfo(
                    path=path,
                    size_bytes=int(stat.st_size),
                    modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                ),
            )
        )
    entries.sort(key=lambda item: item[0], reverse=True)
    return tuple(info for _mtime, info in entries)


def read_log_tail(path: Path | str, max_bytes: int = MAX_TAIL_BYTES) -> str:
    """读取日志末尾内容；文件不存在或不可读时返回空字符串。

    `max_bytes<=0` 视为不限制（调用方一般不会这么做，仅方便测试）。
    """
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError:
        return ""

    start = max(0, size - max_bytes) if max_bytes > 0 else 0
    try:
        with target.open("rb") as handle:
            handle.seek(start)
            data = handle.read()
    except OSError:
        return ""

    text = data.decode("utf-8", errors="replace")
    if start > 0:
        # 从文件中间开始时，第一行大概率是半行，直接丢掉。
        newline = text.find("\n")
        if newline >= 0:
            text = text[newline + 1 :]
    return text


def filter_log_text(text: str, keyword: str) -> str:
    """按关键字做不区分大小写的逐行筛选；空关键字返回原文。

    逐行筛选会丢掉多行异常堆栈的上下文，界面里已注明“按行筛选”。
    """
    needle = keyword.strip().lower()
    if not needle:
        return text
    return "\n".join(line for line in text.splitlines() if needle in line.lower())
