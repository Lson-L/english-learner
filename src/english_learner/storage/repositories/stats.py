"""每日打卡与 AI 调用记录仓库。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from ...models.entities import (
    AICall,
    AIUsageByTask,
    AIUsageSummary,
    DailyStats,
    now_iso,
    today_str,
)
from .base import _BaseRepo


class StatsRepo(_BaseRepo):
    """每日统计：打卡、连续天数、到期词数。"""

    def bump(
        self,
        stat_date: str,
        *,
        article_read: int = 0,
        words_added: int = 0,
        reviews_done: int = 0,
    ) -> DailyStats:
        self._db.execute(
            """
            INSERT INTO daily_stats (stat_date, article_read, words_added, reviews_done)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stat_date) DO UPDATE SET
                article_read = daily_stats.article_read + excluded.article_read,
                words_added  = daily_stats.words_added + excluded.words_added,
                reviews_done = daily_stats.reviews_done + excluded.reviews_done
            """,
            (stat_date, int(article_read), int(words_added), int(reviews_done)),
        )
        return self.get(stat_date)

    def get(self, stat_date: str | None = None) -> DailyStats:
        day = stat_date or today_str()
        row = self._db.query_one("SELECT * FROM daily_stats WHERE stat_date = ?", (day,))
        if row is None:
            return DailyStats(stat_date=day)
        return DailyStats.from_row(row)

    def count_due(self, on_date: str | None = None) -> int:
        day = on_date or today_str()
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM words "
            "WHERE due_date IS NOT NULL AND due_date <= ? "
            "AND status <> 'mastered' AND in_vocabulary = 1",
            (day,),
        )
        return int(row["n"]) if row is not None else 0

    def list_all(self) -> list[DailyStats]:
        """全部每日统计（导出用），按日期升序。"""
        rows = self._db.query_all("SELECT * FROM daily_stats ORDER BY stat_date")
        return [DailyStats.from_row(row) for row in rows]

    def summarize_between(self, start_day: str, end_day: str) -> dict[str, int]:
        """日期闭区间内的打卡汇总：阅读篇数 / 新增生词 / 复习次数。"""
        row = self._db.query_one(
            """
            SELECT COALESCE(SUM(article_read), 0) AS articles,
                   COALESCE(SUM(words_added), 0) AS words,
                   COALESCE(SUM(reviews_done), 0) AS reviews
            FROM daily_stats WHERE stat_date BETWEEN ? AND ?
            """,
            (start_day, end_day),
        )
        if row is None:
            return {"articles": 0, "words": 0, "reviews": 0}
        return {
            "articles": int(row["articles"]),
            "words": int(row["words"]),
            "reviews": int(row["reviews"]),
        }

    def streak(self, today: date | None = None) -> int:
        """从今天（今天没学习则从昨天）往回数连续有活动的天数。"""
        current = today or date.today()
        rows = self._db.query_all(
            "SELECT stat_date FROM daily_stats "
            "WHERE article_read > 0 OR words_added > 0 OR reviews_done > 0"
        )
        active = {str(row["stat_date"]) for row in rows}
        if current.isoformat() not in active:
            current -= timedelta(days=1)
        streak = 0
        while current.isoformat() in active:
            streak += 1
            current -= timedelta(days=1)
        return streak


class AICallRepo(_BaseRepo):
    """AI 调用记录：只追加，不修改。

    `since` 是日期字符串（YYYY-MM-DD），用 `called_at >= since` 过滤；
    `called_at` 统一是本地 ISO 时间，字典序与时间序一致。
    """

    _SUMMARY_COLUMNS = """
        COUNT(*) AS calls,
        COALESCE(SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END), 0) AS failed_calls,
        COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
        COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
        COALESCE(SUM(total_tokens), 0) AS total_tokens,
        COALESCE(CAST(AVG(latency_ms) AS INTEGER), 0) AS avg_latency_ms
    """

    def insert(self, call: AICall) -> AICall:
        params = call.to_params()
        if not params["called_at"]:
            params["called_at"] = now_iso()
        columns = ", ".join(params)
        placeholders = ", ".join(f":{name}" for name in params)
        cursor = self._db.execute(
            f"INSERT INTO ai_calls ({columns}) VALUES ({placeholders})", params
        )
        return replace(call, id=int(cursor.lastrowid or 0), called_at=params["called_at"])

    def get_by_id(self, call_id: int) -> AICall | None:
        row = self._db.query_one("SELECT * FROM ai_calls WHERE id = ?", (int(call_id),))
        return AICall.from_row(row) if row is not None else None

    def list_recent(
        self,
        limit: int = 500,
        *,
        task: str | None = None,
        only_failed: bool = False,
        since: str | None = None,
    ) -> list[AICall]:
        """最近调用（按时间倒序）；limit 只影响展示，不影响统计。"""
        clauses, params = self._filters(task=task, only_failed=only_failed, since=since)
        sql = "SELECT * FROM ai_calls"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY called_at DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [AICall.from_row(row) for row in self._db.query_all(sql, tuple(params))]

    def summary(self, *, since: str | None = None) -> AIUsageSummary:
        """调用次数、失败数与 token 汇总；未返回 usage 的调用不计 token。"""
        clause, params = self._where(since=since)
        row = self._db.query_one(
            f"SELECT {self._SUMMARY_COLUMNS} FROM ai_calls{clause}", tuple(params)
        )
        return AIUsageSummary.from_row(row) if row is not None else AIUsageSummary()

    def by_task(self, *, since: str | None = None, limit: int = 50) -> list[AIUsageByTask]:
        """按任务聚合，调用次数多的在前。"""
        clause, params = self._where(since=since)
        params.append(max(1, int(limit)))
        rows = self._db.query_all(
            f"""
            SELECT task,
                   COUNT(*) AS calls,
                   COALESCE(SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END), 0) AS failed_calls,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(CAST(AVG(latency_ms) AS INTEGER), 0) AS avg_latency_ms
            FROM ai_calls{clause}
            GROUP BY task
            ORDER BY calls DESC, total_tokens DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [AIUsageByTask.from_row(row) for row in rows]

    def task_options(self) -> tuple[str, ...]:
        """出现过的任务名（供界面筛选下拉框）。"""
        rows = self._db.query_all(
            "SELECT DISTINCT task FROM ai_calls WHERE task <> '' ORDER BY task"
        )
        return tuple(str(row["task"]) for row in rows)

    def list_all(self) -> list[AICall]:
        """全部调用记录（导出用），按时间升序。"""
        rows = self._db.query_all("SELECT * FROM ai_calls ORDER BY called_at, id")
        return [AICall.from_row(row) for row in rows]

    def attempt_stats(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        task: str = "",
        task_prefix: str = "",
    ) -> tuple[int, int, int]:
        """某类任务的重试统计：`(首次尝试数, 重试尝试数, 失败尝试数)`。

        `ai_calls` 每次真实 HTTP 尝试一行，`attempt` 从 1 开始；业务层用
        “重试尝试 / 全部尝试”作为重试率，见 `services/metrics_service.py`。
        `since / until` 是日期上/下界（含当天），`until` 用于排除未来记录。
        `task` 精确匹配，`task_prefix` 前缀匹配（如“阅读检测”覆盖全部阶段）。
        """
        clauses: list[str] = []
        params: list[Any] = []
        if since:
            clauses.append("substr(called_at, 1, 10) >= ?")
            params.append(since)
        if until:
            clauses.append("substr(called_at, 1, 10) <= ?")
            params.append(until)
        if task:
            clauses.append("task = ?")
            params.append(task)
        if task_prefix:
            clauses.append("task LIKE ?")
            params.append(f"{task_prefix}%")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self._db.query_one(
            f"""
            SELECT COALESCE(SUM(CASE WHEN attempt <= 1 THEN 1 ELSE 0 END), 0) AS firsts,
                   COALESCE(SUM(CASE WHEN attempt > 1 THEN 1 ELSE 0 END), 0) AS retries,
                   COALESCE(SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END), 0) AS failed
            FROM ai_calls{where}
            """,
            tuple(params),
        )
        if row is None:
            return 0, 0, 0
        return int(row["firsts"]), int(row["retries"]), int(row["failed"])

    def count(self) -> int:
        row = self._db.query_one("SELECT COUNT(*) AS n FROM ai_calls")
        return int(row["n"]) if row is not None else 0

    def delete_all(self) -> int:
        """清空调用记录；返回删除行数。"""
        cursor = self._db.execute("DELETE FROM ai_calls")
        return int(cursor.rowcount or 0)

    @staticmethod
    def _where(*, since: str | None = None) -> tuple[str, list[Any]]:
        if since:
            return " WHERE called_at >= ?", [since]
        return "", []

    @classmethod
    def _filters(
        cls,
        *,
        task: str | None = None,
        only_failed: bool = False,
        since: str | None = None,
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if since:
            clauses.append("called_at >= ?")
            params.append(since)
        if task:
            clauses.append("task = ?")
            params.append(task)
        if only_failed:
            clauses.append("ok = 0")
        return clauses, params
