"""每日文章仓库。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from ...models.entities import (
    Article,
    now_iso,
)
from .base import _BaseRepo


class ArticleRepo(_BaseRepo):
    """每日文章。article_date 唯一，一天最多一篇。"""

    def get_by_date(self, article_date: str) -> Article | None:
        row = self._db.query_one("SELECT * FROM articles WHERE article_date = ?", (article_date,))
        return Article.from_row(row) if row is not None else None

    def get_by_id(self, article_id: int) -> Article | None:
        row = self._db.query_one("SELECT * FROM articles WHERE id = ?", (article_id,))
        return Article.from_row(row) if row is not None else None

    def insert(self, article: Article) -> Article:
        """插入文章；同一天已存在时抛 DuplicateError（由业务层回读缓存）。"""
        params = article.to_params()
        if not params["created_at"]:
            params["created_at"] = now_iso()
        columns = ", ".join(params)
        placeholders = ", ".join(f":{name}" for name in params)
        sql = f"INSERT INTO articles ({columns}) VALUES ({placeholders})"
        try:
            cursor = self._db.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            from ..db import DuplicateError

            raise DuplicateError(f"日期 {article.article_date} 的文章已存在") from exc
        return replace(article, id=int(cursor.lastrowid or 0), created_at=params["created_at"])

    def replace_by_date(self, article: Article) -> Article:
        """原子替换某天的文章：删旧文（级联清理高亮）+ 插新文放在同一事务里。

        这样即使插入失败也不会把当天文章弄丢；成功后返回带新 id 的 Article。
        """
        params = article.to_params()
        if not params["created_at"]:
            params["created_at"] = now_iso()
        columns = ", ".join(params)
        placeholders = ", ".join(f":{name}" for name in params)
        sql = f"INSERT INTO articles ({columns}) VALUES ({placeholders})"
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM articles WHERE article_date = ?", (article.article_date,))
            cursor = conn.execute(sql, params)
        return replace(article, id=int(cursor.lastrowid or 0), created_at=params["created_at"])

    def delete_by_date(self, article_date: str) -> bool:
        """删除某天的文章；highlights 由外键 ON DELETE CASCADE 自动清理。"""
        cursor = self._db.execute("DELETE FROM articles WHERE article_date = ?", (article_date,))
        return cursor.rowcount > 0

    def mark_read(self, article_id: int, read_at: str | None = None) -> bool:
        """首次打开时标记已读；已经是已读状态返回 False（避免重复打卡）。"""
        cursor = self._db.execute(
            "UPDATE articles SET read_at = ? WHERE id = ? AND read_at IS NULL",
            (read_at or now_iso(), article_id),
        )
        return cursor.rowcount > 0

    def list_recent(self, limit: int = 30) -> list[Article]:
        rows = self._db.query_all(
            "SELECT * FROM articles ORDER BY article_date DESC LIMIT ?", (int(limit),)
        )
        return [Article.from_row(row) for row in rows]

    def list_all(self) -> list[Article]:
        """全部文章（导出用），按日期倒序。"""
        rows = self._db.query_all("SELECT * FROM articles ORDER BY article_date DESC, id DESC")
        return [Article.from_row(row) for row in rows]

    def list_reading_volume(self) -> list[tuple[int, int, str | None, str]]:
        """统计页阅读剂量专用：只取 id / 词数 / 阅读时间 / 来源。

        之前的实现用 `list_all()`，会把每篇文章的正文一起读进内存再逐条累加；
        文章攒多之后每次切到统计页都要加载全部正文。这里只投影四列。
        """
        rows = self._db.query_all(
            "SELECT id, word_count, read_at, source FROM articles ORDER BY article_date"
        )
        return [
            (
                int(row["id"]),
                int(row["word_count"] or 0),
                str(row["read_at"] or "") or None,
                str(row["source"] or ""),
            )
            for row in rows
        ]
