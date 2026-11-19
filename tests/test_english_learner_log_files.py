"""运行日志读取工具测试：尾部截断、轮转文件排序与关键字筛选。"""

from __future__ import annotations

import os
from pathlib import Path

from english_learner.services.log_files import (
    filter_log_text,
    list_log_files,
    read_log_tail,
)


def test_read_log_tail_returns_full_small_file(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(b"first\nsecond\n")

    assert read_log_tail(path) == "first\nsecond\n"


def test_read_log_tail_drops_partial_first_line(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(("x" * 200 + "\nhello\n").encode("utf-8"))

    text = read_log_tail(path, max_bytes=10)

    assert text == "hello\n"
    assert "x" not in text


def test_read_log_tail_handles_missing_file(tmp_path: Path) -> None:
    assert read_log_tail(tmp_path / "missing.log") == ""


def test_read_log_tail_replaces_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(b"\xff\xfeabc\n")

    text = read_log_tail(path)

    assert "abc" in text
    assert "�" in text


def test_list_log_files_sorted_by_mtime(tmp_path: Path) -> None:
    older = tmp_path / "app.log.1"
    newer = tmp_path / "app.log"
    older.write_bytes(b"old")
    newer.write_bytes(b"new")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_100, 1_700_000_100))

    files = list_log_files(tmp_path)

    assert [info.name for info in files] == ["app.log", "app.log.1"]
    assert files[0].size_text == "3 B"
    assert files[0].modified_at


def test_list_log_files_missing_directory(tmp_path: Path) -> None:
    assert list_log_files(tmp_path / "nope") == ()


def test_filter_log_text_is_case_insensitive_and_line_based() -> None:
    text = "2026-11-19 [INFO] AI 调用完成\n2026-11-19 [ERROR] 连接失败\n堆栈续行"

    assert filter_log_text(text, "ai 调用完成") == "2026-11-19 [INFO] AI 调用完成"
    assert filter_log_text(text, "ERROR") == "2026-11-19 [ERROR] 连接失败"
    assert filter_log_text(text, "  ") == text
    assert filter_log_text(text, "不存在") == ""
