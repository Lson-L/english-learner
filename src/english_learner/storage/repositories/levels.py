"""等级证据与每周复盘仓库。"""

from __future__ import annotations

from ...models.entities import (
    LevelEvidence,
    WeeklyReview,
    now_iso,
)
from .base import _BaseRepo


class LevelEvidenceRepo(_BaseRepo):
    """等级证据：只追加，不修改；供后验回放与导出。"""

    def insert(self, evidence: LevelEvidence) -> LevelEvidence:
        params = evidence.to_params()
        if not params["observed_at"]:
            params["observed_at"] = now_iso()
        columns = ", ".join(params)
        placeholders = ", ".join(f":{name}" for name in params)
        cursor = self._db.execute(
            f"INSERT INTO level_evidence ({columns}) VALUES ({placeholders})",
            params,
        )
        row = self._db.query_one("SELECT * FROM level_evidence WHERE id = ?", (cursor.lastrowid,))
        assert row is not None
        return LevelEvidence.from_row(row)

    def list_recent(self, limit: int = 10) -> list[LevelEvidence]:
        rows = self._db.query_all(
            "SELECT * FROM level_evidence ORDER BY observed_at DESC, id DESC LIMIT ?",
            (int(limit),),
        )
        return [LevelEvidence.from_row(row) for row in rows]

    def list_between(self, start_day: str, end_day: str) -> list[LevelEvidence]:
        rows = self._db.query_all(
            "SELECT * FROM level_evidence "
            "WHERE COALESCE(NULLIF(evidence_date, ''), substr(observed_at, 1, 10)) "
            "BETWEEN ? AND ? ORDER BY observed_at, id",
            (start_day, end_day),
        )
        return [LevelEvidence.from_row(row) for row in rows]

    def list_all(self) -> list[LevelEvidence]:
        rows = self._db.query_all("SELECT * FROM level_evidence ORDER BY observed_at, id")
        return [LevelEvidence.from_row(row) for row in rows]

    def count(self) -> int:
        row = self._db.query_one("SELECT COUNT(*) AS n FROM level_evidence")
        return int(row["n"]) if row is not None else 0

    def count_on(self, day: str, *, exclude_quiz_id: int | None = None) -> int:
        """某天产生过多少条等级证据（用于“同一天只做一次正式检测”）。

        证据表不随文章替换删除，所以即使当天换过文章，也能识别出“今天已经
        做过一次正式检测”，新检测只能进入练习模式。
        """
        sql = (
            "SELECT COUNT(*) AS n FROM level_evidence "
            "WHERE COALESCE(NULLIF(evidence_date, ''), substr(observed_at, 1, 10)) = ?"
        )
        params: list[object] = [day]
        if exclude_quiz_id is not None:
            sql += " AND (quiz_id IS NULL OR quiz_id <> ?)"
            params.append(int(exclude_quiz_id))
        row = self._db.query_one(sql, params)
        return int(row["n"]) if row is not None else 0


class WeeklyReviewRepo(_BaseRepo):
    """每周复盘：一周一条；AI 建议与用户确认结果分开保存。"""

    _SAVE_SQL = """
        INSERT INTO weekly_reviews (
            week_start, summary, suggested_level, focus_json, quiz_count,
            review_count, words_added, confidence, applied_level, created_at, applied_at
        ) VALUES (
            :week_start, :summary, :suggested_level, :focus_json, :quiz_count,
            :review_count, :words_added, :confidence, :applied_level, :created_at, :applied_at
        )
        ON CONFLICT(week_start) DO UPDATE SET
            summary         = excluded.summary,
            suggested_level = excluded.suggested_level,
            focus_json      = excluded.focus_json,
            quiz_count      = excluded.quiz_count,
            review_count    = excluded.review_count,
            words_added     = excluded.words_added,
            confidence      = excluded.confidence,
            created_at      = excluded.created_at
    """

    def get_by_week(self, week_start: str) -> WeeklyReview | None:
        row = self._db.query_one("SELECT * FROM weekly_reviews WHERE week_start = ?", (week_start,))
        return WeeklyReview.from_row(row) if row is not None else None

    def save(self, review: WeeklyReview) -> WeeklyReview:
        params = review.to_params()
        if not params["created_at"]:
            params["created_at"] = now_iso()
        self._db.execute(self._SAVE_SQL, params)
        stored = self.get_by_week(review.week_start)
        assert stored is not None
        return stored

    def mark_applied(
        self,
        week_start: str,
        applied_level: str,
        applied_at: str | None = None,
    ) -> WeeklyReview | None:
        cursor = self._db.execute(
            "UPDATE weekly_reviews SET applied_level = ?, applied_at = ? WHERE week_start = ?",
            (applied_level, applied_at or now_iso(), week_start),
        )
        if cursor.rowcount == 0:
            return None
        return self.get_by_week(week_start)

    def list_recent(self, limit: int = 4) -> list[WeeklyReview]:
        rows = self._db.query_all(
            "SELECT * FROM weekly_reviews ORDER BY week_start DESC LIMIT ?", (int(limit),)
        )
        return [WeeklyReview.from_row(row) for row in rows]

    def list_all(self) -> list[WeeklyReview]:
        rows = self._db.query_all("SELECT * FROM weekly_reviews ORDER BY week_start")
        return [WeeklyReview.from_row(row) for row in rows]
