"""仓储基类：所有 Repository 共享数据库连接。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..db import Database


class _BaseRepo:
    def __init__(self, db: Database) -> None:
        self._db = db
