"""回译 / 写作记录仓库。"""

from __future__ import annotations

import json
from typing import Any

from ...models.entities import (
    WritingAttempt,
    now_iso,
)
from .base import _BaseRepo


class WritingAttemptRepo(_BaseRepo):
    """回译练习（writing_attempts）：一篇文章最多 3 句，先写题目后回填批改。"""

    _INSERT_SQL = """
        INSERT INTO writing_attempts (
            article_id, article_date, position, sentence_en, reference_cn,
            answer_en, verdict_json, score, created_at, graded_at
        ) VALUES (
            :article_id, :article_date, :position, :sentence_en, :reference_cn,
            :answer_en, :verdict_json, :score, :created_at, :graded_at
        )
    """

    def list_for_article(self, article_id: int) -> list[WritingAttempt]:
        rows = self._db.query_all(
            "SELECT * FROM writing_attempts WHERE article_id = ? ORDER BY position",
            (int(article_id),),
        )
        return [WritingAttempt.from_row(row) for row in rows]

    def replace_for_article(
        self,
        article_id: int,
        attempts: list[WritingAttempt] | tuple[WritingAttempt, ...],
    ) -> list[WritingAttempt]:
        """覆盖某篇文章的练习题（删除旧行 + 插入新行在同一事务里）。

        只应在“首次出题”时使用；已经提交过批改的记录由 `save_grades` 回写，
        不会被这里覆盖（服务层先检查缓存）。
        """
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM writing_attempts WHERE article_id = ?", (int(article_id),))
            for attempt in attempts:
                params = attempt.to_params()
                if not params["created_at"]:
                    params["created_at"] = now_iso()
                conn.execute(self._INSERT_SQL, params)
        return self.list_for_article(article_id)

    def save_grades(
        self,
        article_id: int,
        rows: list[tuple[int, str, dict[str, Any] | None, int, str]],
    ) -> list[WritingAttempt]:
        """批量回写批改结果：rows = (position, answer_en, verdict, score, graded_at)。"""
        with self._db.transaction() as conn:
            for position, answer_en, verdict, score, graded_at in rows:
                conn.execute(
                    """
                    UPDATE writing_attempts
                       SET answer_en = ?, verdict_json = ?, score = ?, graded_at = ?
                     WHERE article_id = ? AND position = ?
                    """,
                    (
                        answer_en.strip(),
                        json.dumps(verdict, ensure_ascii=False) if verdict else "",
                        int(score),
                        graded_at,
                        int(article_id),
                        int(position),
                    ),
                )
        return self.list_for_article(article_id)

    def count_graded_between(self, start_day: str, end_day: str) -> int:
        """日期闭区间内已提交批改的回译句子数（M5.5 计划完成度用）。"""
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM writing_attempts "
            "WHERE graded_at IS NOT NULL AND substr(graded_at, 1, 10) BETWEEN ? AND ?",
            (start_day, end_day),
        )
        return int(row["n"]) if row is not None else 0

    def list_recent(self, limit: int = 20) -> list[WritingAttempt]:
        """最近的回译记录（学习者记忆统计错因用），新 → 旧。"""
        rows = self._db.query_all(
            "SELECT * FROM writing_attempts "
            "ORDER BY COALESCE(graded_at, created_at) DESC, id DESC LIMIT ?",
            (int(limit),),
        )
        return [WritingAttempt.from_row(row) for row in rows]

    def list_all(self) -> list[WritingAttempt]:
        """全部回译记录（导出用），按文章日期与位置排序。"""
        rows = self._db.query_all(
            "SELECT * FROM writing_attempts ORDER BY article_date, article_id, position"
        )
        return [WritingAttempt.from_row(row) for row in rows]
