"""阅读检测仓库。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

from ...models.entities import (
    CEFR_LEVELS,
    QuizProduction,
    QuizQuestion,
    QuizSummary,
    ReadingQuiz,
    now_iso,
)
from .base import _BaseRepo


class QuizRepo(_BaseRepo):
    """阅读检测：一篇文章一套题，作答结果写回同一行。"""

    def get_by_article(self, article_id: int) -> ReadingQuiz | None:
        row = self._db.query_one(
            "SELECT * FROM reading_quizzes WHERE article_id = ?", (int(article_id),)
        )
        return ReadingQuiz.from_row(row) if row is not None else None

    def get_by_id(self, quiz_id: int) -> ReadingQuiz | None:
        row = self._db.query_one("SELECT * FROM reading_quizzes WHERE id = ?", (int(quiz_id),))
        return ReadingQuiz.from_row(row) if row is not None else None

    def save_self_report(self, quiz_id: int, self_report: str) -> None:
        """单独记录阅读自评：首次检测还没作答时也能写，重开不丢。"""
        self._db.execute(
            "UPDATE reading_quizzes SET self_report = ? WHERE id = ?",
            ((self_report or "").strip(), int(quiz_id)),
        )

    def insert(self, quiz: ReadingQuiz) -> ReadingQuiz:
        """插入一套题；同一篇文章重复插入时抛 DuplicateError。"""
        params = quiz.to_params()
        if not params["created_at"]:
            params["created_at"] = now_iso()
        columns = ", ".join(params)
        placeholders = ", ".join(f":{name}" for name in params)
        sql = f"INSERT INTO reading_quizzes ({columns}) VALUES ({placeholders})"
        try:
            cursor = self._db.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            from ..db import DuplicateError

            raise DuplicateError(f"文章 {quiz.article_id} 已经有一套阅读检测题") from exc
        return replace(quiz, id=int(cursor.lastrowid or 0), created_at=params["created_at"])

    def save_result(
        self,
        quiz_id: int,
        *,
        answers: tuple[int, ...],
        choice_notes: tuple[str, ...] = (),
        explanations: tuple[str, ...],
        score: int,
        previous_level: str,
        result_level: str,
        feedback: str,
        completed_at: str | None = None,
        error_types: tuple[str, ...] = (),
        short_answer_text: str = "",
        short_answer_score: int = 0,
        self_report: str = "",
        recommended_level: str = "",
        pending_level: str = "",
        pending_count: int = 0,
        evidence_ratio: float = 0.0,
        productions: tuple[QuizProduction, ...] = (),
        level_confidence: float = 0.0,
        posterior: tuple[float, ...] = (),
        practice_mode: bool = False,
    ) -> ReadingQuiz:
        """写回作答与讲评结果；只更新结果字段，题目本身不动。"""
        posterior_map = (
            dict(zip(CEFR_LEVELS, posterior, strict=True))
            if len(posterior) == len(CEFR_LEVELS)
            else {}
        )
        cursor = self._db.execute(
            """
            UPDATE reading_quizzes SET
                answers_json       = :answers_json,
                choice_notes_json  = :choice_notes_json,
                explanations_json  = :explanations_json,
                error_types_json   = :error_types_json,
                short_answer_text  = :short_answer_text,
                short_answer_score = :short_answer_score,
                self_report        = :self_report,
                score              = :score,
                recommended_level  = :recommended_level,
                previous_level     = :previous_level,
                result_level       = :result_level,
                pending_level      = :pending_level,
                pending_count      = :pending_count,
                feedback           = :feedback,
                evidence_ratio     = :evidence_ratio,
                productions_json   = :productions_json,
                level_confidence   = :level_confidence,
                posterior_json     = :posterior_json,
                practice_mode      = :practice_mode,
                completed_at       = :completed_at
            WHERE id = :id
            """,
            {
                "id": int(quiz_id),
                "answers_json": json.dumps(list(answers), ensure_ascii=False),
                "choice_notes_json": json.dumps(list(choice_notes), ensure_ascii=False),
                "explanations_json": json.dumps(list(explanations), ensure_ascii=False),
                "error_types_json": json.dumps(list(error_types), ensure_ascii=False),
                "short_answer_text": short_answer_text.strip(),
                "short_answer_score": int(short_answer_score),
                "self_report": self_report.strip(),
                "score": int(score),
                "recommended_level": recommended_level,
                "previous_level": previous_level,
                "result_level": result_level,
                "pending_level": pending_level,
                "pending_count": int(pending_count),
                "feedback": feedback.strip(),
                "evidence_ratio": float(evidence_ratio),
                "productions_json": json.dumps(
                    [item.to_dict() for item in productions], ensure_ascii=False
                ),
                "level_confidence": float(level_confidence),
                "posterior_json": json.dumps(posterior_map, ensure_ascii=False),
                "practice_mode": 1 if practice_mode else 0,
                "completed_at": completed_at or now_iso(),
            },
        )
        if cursor.rowcount == 0:
            raise ValueError(f"阅读检测 {quiz_id} 不存在，无法保存结果")
        stored = self.get_by_id(quiz_id)
        assert stored is not None
        return stored

    def append_questions(
        self,
        quiz_id: int,
        questions: tuple[QuizQuestion, ...],
        *,
        stage: int = 2,
    ) -> ReadingQuiz:
        """把第二阶段题目追加到同一行（自适应出题）。

        只允许未完成的检测追加；完成后再次调用直接返回原记录，保证幂等。
        """
        existing = self.get_by_id(quiz_id)
        if existing is None:
            raise ValueError(f"阅读检测 {quiz_id} 不存在，无法追加题目")
        if existing.completed:
            return existing
        merged = existing.questions + tuple(questions)
        cursor = self._db.execute(
            "UPDATE reading_quizzes SET questions_json = ?, stage = ? WHERE id = ?",
            (
                json.dumps([question.to_dict() for question in merged], ensure_ascii=False),
                int(stage),
                int(quiz_id),
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"阅读检测 {quiz_id} 不存在，无法追加题目")
        stored = self.get_by_id(quiz_id)
        assert stored is not None
        return stored

    def save_stage_answers(
        self,
        quiz_id: int,
        answers: tuple[int, ...],
        choice_notes: tuple[str, ...] = (),
    ) -> None:
        """第一轮作答先落库：第二阶段出题失败/关窗后重进不用重答。"""
        self._db.execute(
            """
            UPDATE reading_quizzes
               SET answers_json = ?, choice_notes_json = ?
             WHERE id = ? AND completed_at IS NULL
            """,
            (
                json.dumps(list(answers), ensure_ascii=False),
                json.dumps(list(choice_notes), ensure_ascii=False),
                int(quiz_id),
            ),
        )

    def list_recent_completed_with_titles(self, limit: int = 8) -> list[tuple[ReadingQuiz, str]]:
        """最近完成的检测 + 文章标题，用于讲评 prompt 的历史摘要。"""
        rows = self._db.query_all(
            """
            SELECT q.*, COALESCE(a.title, '') AS article_title
            FROM reading_quizzes AS q
            LEFT JOIN articles AS a ON a.id = q.article_id
            WHERE q.completed_at IS NOT NULL
            ORDER BY q.completed_at DESC, q.id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        return [(ReadingQuiz.from_row(row), str(row["article_title"] or "")) for row in rows]

    def list_recent_with_questions(
        self,
        limit: int = 4,
        *,
        exclude_article_id: int | None = None,
    ) -> list[ReadingQuiz]:
        """最近出过的题（不论是否完成），用于出题时避让重复问法。

        只读查询，不改缓存语义：已出题的文章仍然直接命中缓存，不会重新出题。
        """
        sql = "SELECT * FROM reading_quizzes"
        params: list[object] = []
        if exclude_article_id is not None:
            sql += " WHERE article_id != ?"
            params.append(int(exclude_article_id))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [ReadingQuiz.from_row(row) for row in self._db.query_all(sql, tuple(params))]

    def list_recent_formal_completed(self, limit: int = 8) -> list[ReadingQuiz]:
        """最近完成的**正式**检测（排除练习模式），按完成时间倒序。

        今日页进度与档内难度提示需要“最近一次正式检测”；如果先取最近 N 条
        再在内存里过滤，连续多天“正式检测 → 换一篇 → 练习检测”时可能被
        练习行挤满窗口，看不到真实后验。
        """
        rows = self._db.query_all(
            "SELECT * FROM reading_quizzes "
            "WHERE completed_at IS NOT NULL AND practice_mode = 0 "
            "ORDER BY completed_at DESC, id DESC LIMIT ?",
            (int(limit),),
        )
        return [ReadingQuiz.from_row(row) for row in rows]

    def list_completed_between(self, start_day: str, end_day: str) -> list[ReadingQuiz]:
        """按文章日期过滤的已完成检测，按日期升序（每周复盘 / 首页趋势）。"""
        rows = self._db.query_all(
            "SELECT * FROM reading_quizzes WHERE completed_at IS NOT NULL "
            "AND article_date BETWEEN ? AND ? ORDER BY article_date, id",
            (start_day, end_day),
        )
        return [ReadingQuiz.from_row(row) for row in rows]

    def list_completed_by_completion_between(
        self,
        start_day: str,
        end_day: str,
    ) -> list[ReadingQuiz]:
        """按**完成日期**过滤的已完成检测，按完成时间升序（M5.5 趋势用）。

        与 `list_completed_between` 的区别：跨天提交（周日文章、周一凌晨完成）
        算在完成那一天的窗口里；文章日期在窗口外但完成日期在窗口内也会计入。
        """
        rows = self._db.query_all(
            "SELECT * FROM reading_quizzes WHERE completed_at IS NOT NULL "
            "AND substr(completed_at, 1, 10) BETWEEN ? AND ? "
            "ORDER BY completed_at, id",
            (start_day, end_day),
        )
        return [ReadingQuiz.from_row(row) for row in rows]

    def count_completed_between(
        self,
        start_day: str,
        end_day: str,
        *,
        exclude_practice: bool = True,
    ) -> int:
        """完成日期闭区间内的检测数（默认排除练习模式，M5.5 计划完成度用）。

        按 `completed_at` 而不是 `article_date`：跨天提交应算在完成的那一天。
        """
        sql = (
            "SELECT COUNT(*) AS n FROM reading_quizzes "
            "WHERE completed_at IS NOT NULL "
            "AND substr(completed_at, 1, 10) BETWEEN ? AND ?"
        )
        if exclude_practice:
            sql += " AND practice_mode = 0"
        row = self._db.query_one(sql, (start_day, end_day))
        return int(row["n"]) if row is not None else 0

    def count_completed_on(self, day: str, *, exclude_id: int | None = None) -> int:
        """某天已完成的正式检测数；用于“同一天只调一次等级”。"""
        sql = (
            "SELECT COUNT(*) AS n FROM reading_quizzes "
            "WHERE article_date = ? AND completed_at IS NOT NULL AND practice_mode = 0"
        )
        params: list[object] = [day]
        if exclude_id is not None:
            sql += " AND id <> ?"
            params.append(int(exclude_id))
        row = self._db.query_one(sql, params)
        return int(row["n"]) if row is not None else 0

    def completed_article_ids(self) -> set[int]:
        """完成过检测的文章 id 集合（含练习模式）。

        统计页用它算“完成检测的阅读词数”这一严格口径；不读题目 JSON。
        """
        rows = self._db.query_all(
            "SELECT DISTINCT article_id FROM reading_quizzes WHERE completed_at IS NOT NULL"
        )
        return {int(row["article_id"]) for row in rows if row["article_id"] is not None}

    def list_formal_completed_light(self) -> list[QuizSummary]:
        """统计页趋势专用：只取等级 / 完成时间 / 得分 / 题目数。

        不再反序列化 `answers_json / explanations_json / productions_json`
        等大字段；题目数用 `questions_json` 的长度还原，与 `ReadingQuiz.total`
        同一口径（选择题 + 主动产出题）。
        """
        rows = self._db.query_all(
            "SELECT article_date, level, result_level, completed_at, score, questions_json "
            "FROM reading_quizzes WHERE completed_at IS NOT NULL AND practice_mode = 0 "
            "ORDER BY completed_at, id"
        )
        summaries: list[QuizSummary] = []
        for row in rows:
            try:
                total = len(json.loads(row["questions_json"] or "[]"))
            except (TypeError, ValueError):
                total = 0
            if total <= 0:
                continue
            summaries.append(
                QuizSummary(
                    article_date=str(row["article_date"] or ""),
                    level=str(row["level"] or ""),
                    result_level=str(row["result_level"] or ""),
                    completed_at=str(row["completed_at"] or ""),
                    score=int(row["score"] or 0),
                    total=total,
                )
            )
        return summaries

    def list_all(self) -> list[ReadingQuiz]:
        """全部阅读检测（导出用），按日期倒序。"""
        rows = self._db.query_all(
            "SELECT * FROM reading_quizzes ORDER BY article_date DESC, id DESC"
        )
        return [ReadingQuiz.from_row(row) for row in rows]
