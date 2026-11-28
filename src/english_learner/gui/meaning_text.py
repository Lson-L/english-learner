"""释义文本的界面格式化。

存储里保持单行（`1. ... 2. ...`），界面展示时按义项换成多行，编辑保存时
再压回单行。这样数据库、导出和搜索的口径不变，只是可读性更好。
"""

from __future__ import annotations

import re

# 匹配义项编号前的空白：`... 2. ...` → `...\n2. ...`
_SENSE_BREAK_RE = re.compile(r"\s+(?=\d+\.\s)")


def display_meaning_text(text: str) -> str:
    """把单行多义项释义拆成每项一行，供释义卡 / 输入框展示。"""
    return _SENSE_BREAK_RE.sub("\n", text or "")


def flatten_meaning_text(text: str) -> str:
    """把编辑框里的换行/多余空白压回单行，写库前调用。"""
    return " ".join((text or "").split())
