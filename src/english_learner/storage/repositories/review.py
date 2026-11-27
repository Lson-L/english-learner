"""复习日志仓库。"""

from __future__ import annotations

from collections.abc import Iterable

from ...models.entities import (
    BandReviewStat,
    ReviewLog,
    normalize_cefr_level,
    now_iso,
)
from .base import _BaseRepo


class ReviewRepo(_BaseRepo):
    """复习历史（review_logs）：只追加；界面“撤销上一张”是唯一删除入口。"""

    def log(
        self,
        *,
        word_id: int,
        rating: int,
        prev_interval: int,
        next_interval: int,
        reviewed_at: str | None = None,
        note: str = "",
        advice: str = "",
        answer_text: str = "",
        verdict: str = "",
        distance: int = 0,
        mode: str = "",
    ) -> ReviewLog:
        timestamp = reviewed_at or now_iso()
        cursor = self._db.execute(
            """
            INSERT INTO review_logs
                (word_id, rating, prev_interval, next_interval, reviewed_at, note, advice,
                 answer_text, verdict, distance, mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(word_id),
                int(rating),
                int(prev_interval),
                int(next_interval),
                timestamp,
                note.strip(),
                advice.strip(),
                answer_text.strip(),
                verdict.strip(),
                max(0, int(distance)),
                mode.strip(),
            ),
        )
        return ReviewLog(
            id=int(cursor.lastrowid or 0),
            word_id=int(word_id),
            rating=int(rating),
            prev_interval=int(prev_interval),
            next_interval=int(next_interval),
            reviewed_at=timestamp,
            note=note.strip(),
            advice=advice.strip(),
            answer_text=answer_text.strip(),
            verdict=verdict.strip(),
            distance=max(0, int(distance)),
            mode=mode.strip(),
        )

    def delete(self, log_id: int) -> bool:
        """删除一条复习记录（仅供“撤销上一张”回滚刚写入的日志）。"""
        cursor = self._db.execute("DELETE FROM review_logs WHERE id = ?", (int(log_id),))
        return cursor.rowcount > 0

    def list_for_word(self, word_id: int, limit: int = 20) -> list[ReviewLog]:
        rows = self._db.query_all(
            "SELECT * FROM review_logs WHERE word_id = ? "
            "ORDER BY reviewed_at DESC, id DESC LIMIT ?",
            (int(word_id), int(limit)),
        )
        return [ReviewLog.from_row(row) for row in rows]

    def last_reviews(self, word_ids: Iterable[int]) -> dict[int, tuple[int, str]]:
        """批量取每个词最近一次复习的 `(rating, verdict)`。

        复习页排卡时一次查完整个会话（最多 200 个词），避免 N+1 查询。
        `id` 自增，用 `MAX(id)` 取最新一条；没有历史的词不出现在结果里。
        """
        ids = sorted({int(word_id) for word_id in word_ids if int(word_id) > 0})
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        rows = self._db.query_all(
            f"""
            SELECT word_id, rating, verdict
            FROM review_logs
            WHERE id IN (
                SELECT MAX(id)
                FROM review_logs
                WHERE word_id IN ({placeholders})
                GROUP BY word_id
            )
            """,
            tuple(ids),
        )
        return {
            int(row["word_id"]): (int(row["rating"]), str(row["verdict"] or "")) for row in rows
        }

    # 复习求助记录：有用户原因或 AI 建议才算一条，过滤放在 SQL 里，
    # 避免“先取 N 条再过滤”导致更早的求助被漏掉、省略计数偏小。
    _HELP_LOG_FILTER = "word_id = ? AND (TRIM(note) <> '' OR TRIM(advice) <> '')"

    def list_help_for_word(self, word_id: int, limit: int = 5) -> list[ReviewLog]:
        """某词的复习求助记录，最新在前。"""
        rows = self._db.query_all(
            f"SELECT * FROM review_logs WHERE {self._HELP_LOG_FILTER} "
            "ORDER BY reviewed_at DESC, id DESC LIMIT ?",
            (int(word_id), int(limit)),
        )
        return [ReviewLog.from_row(row) for row in rows]

    def count_help_for_word(self, word_id: int) -> int:
        """某词带原因或建议的求助记录总数（用于“更早的 N 次”）。"""
        row = self._db.query_one(
            f"SELECT COUNT(*) AS n FROM review_logs WHERE {self._HELP_LOG_FILTER}",
            (int(word_id),),
        )
        return int(row["n"]) if row is not None else 0

    def list_recent(self, limit: int = 50) -> list[ReviewLog]:
        rows = self._db.query_all(
            "SELECT * FROM review_logs ORDER BY reviewed_at DESC, id DESC LIMIT ?",
            (int(limit),),
        )
        return [ReviewLog.from_row(row) for row in rows]

    def list_all(self) -> list[ReviewLog]:
        """全部复习历史（导出用），按时间升序。"""
        rows = self._db.query_all("SELECT * FROM review_logs ORDER BY reviewed_at, id")
        return [ReviewLog.from_row(row) for row in rows]

    def count_for_date(self, day: str) -> int:
        """某天复习次数（reviewed_at 是 ISO 时间，取前 10 位比较日期）。"""
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM review_logs WHERE substr(reviewed_at, 1, 10) = ?",
            (day,),
        )
        return int(row["n"]) if row is not None else 0

    def count_between(self, start_day: str, end_day: str) -> int:
        """日期闭区间内的复习次数（用于每周复盘）。"""
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM review_logs "
            "WHERE substr(reviewed_at, 1, 10) BETWEEN ? AND ?",
            (start_day, end_day),
        )
        return int(row["n"]) if row is not None else 0

    def list_band_stats(
        self,
        start_day: str | None = None,
        end_day: str | None = None,
    ) -> list[BandReviewStat]:
        """按生词的 CEFR 词档聚合复习表现。

        不传日期时统计全部历史（等级估计的弱证据）；
        每周复盘传入本周一~今天，只看本周复习表现。
        """
        clauses = ["w.cefr_band <> ''", "w.in_vocabulary = 1"]
        params: list[object] = []
        if start_day is not None:
            clauses.append("substr(r.reviewed_at, 1, 10) >= ?")
            params.append(start_day)
        if end_day is not None:
            clauses.append("substr(r.reviewed_at, 1, 10) <= ?")
            params.append(end_day)
        rows = self._db.query_all(
            f"""
            SELECT w.cefr_band AS band,
                   COUNT(*) AS total,
                   SUM(CASE WHEN r.rating >= 3 THEN 1 ELSE 0 END) AS known
            FROM review_logs AS r
            JOIN words AS w ON w.id = r.word_id
            WHERE {" AND ".join(clauses)}
            GROUP BY w.cefr_band
            ORDER BY w.cefr_band
            """,
            params,
        )
        return [
            BandReviewStat(
                band=normalize_cefr_level(row["band"]),
                total=int(row["total"]),
                known=int(row["known"] or 0),
            )
            for row in rows
            if str(row["band"] or "").strip()
        ]

    def first_review_stats(
        self,
        start_day: str,
        end_day: str,
        *,
        pass_rating: int = 3,
    ) -> tuple[int, int]:
        """窗口内发生“首次复习”的词数与首次即通过的词数（M5.5）。

        口径：每个词按 `reviewed_at, id` 取第一条记录作为“首次复习”，
        只有首次复习日期落在 `[start_day, end_day]` 才计入；同一词后续复习
        不重复计入。上界用于排除系统时钟异常 / 手工写入的未来记录。
        返回 `(首次复习词数, 首次评分 >= pass_rating 的词数)`。
        """
        row = self._db.query_one(
            """
            WITH first_logs AS (
                SELECT word_id, rating, reviewed_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY word_id ORDER BY reviewed_at, id
                       ) AS rn
                FROM review_logs
            )
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN rating >= ? THEN 1 ELSE 0 END), 0) AS passed
            FROM first_logs
            WHERE rn = 1
              AND substr(reviewed_at, 1, 10) BETWEEN ? AND ?
            """,
            (int(pass_rating), start_day, end_day),
        )
        if row is None:
            return 0, 0
        return int(row["total"]), int(row["passed"])

    def relearn_stats(
        self,
        start_day: str,
        end_day: str,
        *,
        weak_ratings: tuple[int, ...] = (1, 2),
    ) -> tuple[int, int]:
        """窗口内复习过的词数与其中“回炉”过的词数（M5.5）。

        “回炉”= 窗口内至少有一次评分落在 `weak_ratings`（默认忘记 / 模糊）。
        返回 `(复习过的词数, 回炉词数)`，按词去重。
        """
        values = tuple(int(item) for item in weak_ratings)
        if not values:
            return 0, 0
        placeholders = ", ".join("?" for _ in values)
        row = self._db.query_one(
            f"""
            SELECT COUNT(DISTINCT word_id) AS total,
                   COUNT(DISTINCT CASE WHEN rating IN ({placeholders}) THEN word_id END) AS weak
            FROM review_logs
            WHERE substr(reviewed_at, 1, 10) BETWEEN ? AND ?
            """,
            (*values, start_day, end_day),
        )
        if row is None:
            return 0, 0
        return int(row["total"]), int(row["weak"])

    def list_forgotten_words(
        self,
        *,
        limit: int = 5,
        min_count: int = 2,
        rating: int = 1,
    ) -> list[tuple[str, int]]:
        """反复被评“忘记”的词（按次数降序）。

        只统计已加入生词本的词；`min_count` 过滤偶发失误，
        返回值是 `(lemma, 次数)`，供学习者记忆摘要使用。
        """
        rows = self._db.query_all(
            """
            SELECT w.lemma AS lemma, COUNT(*) AS n
            FROM review_logs AS r
            JOIN words AS w ON w.id = r.word_id
            WHERE r.rating = ?
              AND w.in_vocabulary = 1
              AND TRIM(w.lemma) <> ''
            GROUP BY w.lemma
            HAVING COUNT(*) >= ?
            ORDER BY n DESC, w.lemma
            LIMIT ?
            """,
            (int(rating), max(1, int(min_count)), max(1, int(limit))),
        )
        return [(str(row["lemma"]), int(row["n"])) for row in rows]

    def list_misspelled_words(
        self,
        *,
        limit: int = 5,
        verdicts: tuple[str, ...] = ("wrong", "near"),
    ) -> list[tuple[str, int]]:
        """拼写判分错误最多的词（按次数降序）。

        verdicts 由调用方传入（M5.1 的 `VERDICT_WRONG / VERDICT_NEAR`），
        避免在 SQL 里散落字符串、与业务常量各改一半。
        """
        values = tuple(item.strip() for item in verdicts if item and item.strip())
        if not values:
            return []
        placeholders = ", ".join("?" for _ in values)
        rows = self._db.query_all(
            f"""
            SELECT w.lemma AS lemma, COUNT(*) AS n
            FROM review_logs AS r
            JOIN words AS w ON w.id = r.word_id
            WHERE r.verdict IN ({placeholders})
              AND w.in_vocabulary = 1
              AND TRIM(w.lemma) <> ''
            GROUP BY w.lemma
            ORDER BY n DESC, w.lemma
            LIMIT ?
            """,
            (*values, max(1, int(limit))),
        )
        return [(str(row["lemma"]), int(row["n"])) for row in rows]
