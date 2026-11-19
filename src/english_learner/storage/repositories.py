"""纯 SQL 仓库：每个表一个 Repository。

Java 对照：
- 本文件 ≈ MyBatis 的 Mapper 接口 + XML，只是 SQL 以 Python 字符串形式内联；
- Repository 不写业务判断，只做“对象 ⇄ 行”的搬运；
- 事务边界由 Database.transaction() 提供，业务层负责组合多个仓库调用。

命名约定：
- `get_*` / `list_*` / `count_*` 只读；
- `insert_*` / `save_*` / `upsert_*` / `bump_*` 写入；
- 查不到时返回 None 或空列表，不抛异常；违反唯一约束抛 DuplicateError。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from ..models.entities import (
    CEFR_LEVELS,
    AICall,
    AIUsageByTask,
    AIUsageSummary,
    Article,
    BandReviewStat,
    DailyStats,
    Highlight,
    LevelEvidence,
    Profile,
    QuizProduction,
    QuizQuestion,
    ReadingQuiz,
    ReviewLog,
    WeeklyReview,
    Word,
    WordForm,
    normalize_cefr_level,
    now_iso,
    today_str,
)

if TYPE_CHECKING:  # 仅用于类型标注，避免 db.py ↔ repositories.py 循环导入
    from .db import Database


class _BaseRepo:
    def __init__(self, db: Database) -> None:
        self._db = db


class ProfileRepo(_BaseRepo):
    """profile 表只有一行（id = 1）。"""

    _SAVE_SQL = """
        INSERT INTO profile (
            id, cefr_level, vocab_estimate, strengths_json, weaknesses_json,
            comment, assessed_at, updated_at, pending_level, pending_count,
            level_confidence, level_updated_reason
        ) VALUES (
            1, :cefr_level, :vocab_estimate, :strengths_json, :weaknesses_json,
            :comment, :assessed_at, :updated_at, :pending_level, :pending_count,
            :level_confidence, :level_updated_reason
        )
        ON CONFLICT(id) DO UPDATE SET
            cefr_level      = excluded.cefr_level,
            vocab_estimate  = excluded.vocab_estimate,
            strengths_json  = excluded.strengths_json,
            weaknesses_json = excluded.weaknesses_json,
            comment         = excluded.comment,
            assessed_at     = excluded.assessed_at,
            updated_at      = excluded.updated_at,
            pending_level   = excluded.pending_level,
            pending_count   = excluded.pending_count,
            level_confidence = excluded.level_confidence,
            level_updated_reason = excluded.level_updated_reason
    """

    def get(self) -> Profile | None:
        row = self._db.query_one("SELECT * FROM profile WHERE id = 1")
        return Profile.from_row(row) if row is not None else None

    def save(self, profile: Profile) -> Profile:
        params = profile.to_params()
        if not params["assessed_at"]:
            params["assessed_at"] = now_iso()
        if not params["updated_at"]:
            params["updated_at"] = params["assessed_at"]
        self._db.execute(self._SAVE_SQL, params)
        stored = self.get()
        assert stored is not None  # 刚写完必然能读回
        return stored


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
            from .db import DuplicateError

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


class WordRepo(_BaseRepo):
    """生词本 + 查词缓存 + 正文高亮。"""

    _UPSERT_SQL = """
        INSERT INTO words (
            lemma, display_form, phonetic, pos, meaning_cn, meaning_en,
            example_en, example_cn, mnemonic, context_sentence, article_id,
            in_vocabulary, cefr_band, status, ease, interval_days, repetitions, due_date,
            created_at, updated_at
        ) VALUES (
            :lemma, :display_form, :phonetic, :pos, :meaning_cn, :meaning_en,
            :example_en, :example_cn, :mnemonic, :context_sentence, :article_id,
            :in_vocabulary, :cefr_band, :status, :ease, :interval_days, :repetitions, :due_date,
            :created_at, :updated_at
        )
        ON CONFLICT(lemma) DO UPDATE SET
            -- 保留第一次记录的文章形态，避免同一原形的不同词形来回覆盖
            display_form     = CASE
                WHEN words.display_form = '' THEN excluded.display_form
                ELSE words.display_form
            END,
            phonetic         = excluded.phonetic,
            pos              = excluded.pos,
            meaning_cn       = excluded.meaning_cn,
            meaning_en       = excluded.meaning_en,
            example_en       = excluded.example_en,
            example_cn       = excluded.example_cn,
            mnemonic         = CASE
                WHEN excluded.mnemonic <> '' THEN excluded.mnemonic
                ELSE words.mnemonic
            END,
            context_sentence = CASE
                WHEN excluded.context_sentence <> '' THEN excluded.context_sentence
                ELSE words.context_sentence
            END,
            article_id       = COALESCE(words.article_id, excluded.article_id),
            -- 已加入生词本的词不会因为再次查词被降级回缓存
            in_vocabulary    = CASE
                WHEN words.in_vocabulary = 1 THEN 1
                ELSE excluded.in_vocabulary
            END,
            -- 词档是后来才有的字段：旧词条为空时补上新查到的值，非空则保留。
            cefr_band        = CASE
                WHEN excluded.cefr_band <> '' THEN excluded.cefr_band
                ELSE words.cefr_band
            END,
            updated_at       = excluded.updated_at
    """

    def get_by_lemma(self, lemma: str) -> Word | None:
        row = self._db.query_one("SELECT * FROM words WHERE lemma = ?", (lemma,))
        return Word.from_row(row) if row is not None else None

    def get_by_id(self, word_id: int) -> Word | None:
        row = self._db.query_one("SELECT * FROM words WHERE id = ?", (word_id,))
        return Word.from_row(row) if row is not None else None

    def get_by_form(self, form: str) -> Word | None:
        """按“文章中出现的词形”查缓存（如 went → go）。"""
        value = (form or "").strip().lower()
        if not value:
            return None
        row = self._db.query_one(
            """
            SELECT w.* FROM words AS w
            JOIN word_forms AS f ON f.word_id = w.id
            WHERE f.form = ?
            """,
            (value,),
        )
        return Word.from_row(row) if row is not None else None

    def remember_form(self, form: str, word_id: int) -> None:
        """记录词形 → 原形词条的映射，供下一次查词直接命中缓存。"""
        value = (form or "").strip().lower()
        if not value:
            return
        self._db.execute(
            """
            INSERT INTO word_forms (form, word_id, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(form) DO UPDATE SET word_id = excluded.word_id
            """,
            (value, int(word_id), now_iso()),
        )

    def upsert(self, word: Word) -> Word:
        """按 lemma（大小写不敏感）插入或更新释义，SRS 字段保持原值。"""
        params = word.to_params()
        if not params["created_at"]:
            params["created_at"] = now_iso()
        if not params["updated_at"]:
            params["updated_at"] = params["created_at"]
        with self._db.transaction() as conn:
            conn.execute(self._UPSERT_SQL, params)
            row = conn.execute("SELECT * FROM words WHERE lemma = ?", (word.lemma,)).fetchone()
        assert row is not None
        return Word.from_row(row)

    def add_highlight(
        self,
        *,
        article_id: int,
        word_id: int,
        start_offset: int,
        end_offset: int,
        sentence: str = "",
    ) -> bool:
        """插入高亮；同一位置重复添加返回 False（幂等）。"""
        cursor = self._db.execute(
            """
            INSERT OR IGNORE INTO highlights
                (article_id, word_id, start_offset, end_offset, sentence)
            VALUES (?, ?, ?, ?, ?)
            """,
            (article_id, word_id, int(start_offset), int(end_offset), sentence),
        )
        return cursor.rowcount > 0

    def list_all_forms(self) -> list[WordForm]:
        """全部词形缓存（导出用）。"""
        rows = self._db.query_all("SELECT * FROM word_forms ORDER BY form")
        return [WordForm.from_row(row) for row in rows]

    def list_all_highlights(self) -> list[Highlight]:
        """全部高亮（导出用）。"""
        rows = self._db.query_all("SELECT * FROM highlights ORDER BY article_id, start_offset, id")
        return [Highlight.from_row(row) for row in rows]

    def list_highlights(self, article_id: int) -> list[Highlight]:
        rows = self._db.query_all(
            "SELECT * FROM highlights WHERE article_id = ? ORDER BY start_offset",
            (article_id,),
        )
        return [Highlight.from_row(row) for row in rows]

    def list_highlighted_words(self, article_id: int) -> list[Word]:
        """某篇文章里标注过的词，按首次出现位置排序。"""
        rows = self._db.query_all(
            """
            SELECT w.* FROM words AS w
            JOIN highlights AS h ON h.word_id = w.id
            WHERE h.article_id = ?
            GROUP BY w.id
            ORDER BY MIN(h.start_offset)
            """,
            (article_id,),
        )
        return [Word.from_row(row) for row in rows]

    def list_created_on(self, day: str) -> list[Word]:
        """某天加入生词本的词（created_at 是 ISO 时间，取前 10 位比较日期）。"""
        rows = self._db.query_all(
            "SELECT * FROM words WHERE substr(created_at, 1, 10) = ? "
            "AND in_vocabulary = 1 ORDER BY id",
            (day,),
        )
        return [Word.from_row(row) for row in rows]

    def save_schedule(self, word: Word) -> Word:
        """写回 SM-2 调度结果（状态、易度、间隔、次数、到期日）。

        释义字段不动：复习只改变调度，不覆盖用户可能手动改过的释义。
        """
        if word.id is None:
            raise ValueError("save_schedule 需要带 id 的词条")
        cursor = self._db.execute(
            """
            UPDATE words SET
                status = :status,
                ease = :ease,
                interval_days = :interval_days,
                repetitions = :repetitions,
                due_date = :due_date,
                updated_at = :updated_at
            WHERE id = :id
            """,
            {
                "id": int(word.id),
                "status": word.status,
                "ease": float(word.ease),
                "interval_days": int(word.interval_days),
                "repetitions": int(word.repetitions),
                "due_date": word.due_date,
                "updated_at": word.updated_at or now_iso(),
            },
        )
        if cursor.rowcount == 0:
            raise ValueError(f"词条 {word.id} 不存在，无法保存复习进度")
        stored = self.get_by_id(word.id)
        assert stored is not None
        return stored

    def update_meaning(
        self,
        word_id: int,
        *,
        meaning_cn: str,
        meaning_en: str = "",
    ) -> Word | None:
        """生词本里手动改释义；返回更新后的词条，词条不存在时返回 None。"""
        cursor = self._db.execute(
            "UPDATE words SET meaning_cn = ?, meaning_en = ?, updated_at = ? WHERE id = ?",
            (meaning_cn.strip(), meaning_en.strip(), now_iso(), int(word_id)),
        )
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(int(word_id))

    def update_mnemonic(self, word_id: int, mnemonic: str) -> Word | None:
        """写入 / 更新 AI 生成的记忆方法。"""
        cursor = self._db.execute(
            "UPDATE words SET mnemonic = ?, updated_at = ? WHERE id = ?",
            (mnemonic.strip(), now_iso(), int(word_id)),
        )
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(int(word_id))

    def mark_in_vocabulary(self, word_id: int) -> Word | None:
        """把词条从“查词缓存”升级为“已加入生词本”。"""
        cursor = self._db.execute(
            "UPDATE words SET in_vocabulary = 1, updated_at = ? WHERE id = ?",
            (now_iso(), int(word_id)),
        )
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(int(word_id))

    def delete(self, word_id: int) -> bool:
        """从生词本删除词条；相关高亮与复习记录由外键 ON DELETE CASCADE 清理。"""
        cursor = self._db.execute("DELETE FROM words WHERE id = ?", (int(word_id),))
        return cursor.rowcount > 0

    def list_all(
        self,
        status: str | None = None,
        *,
        include_cache: bool = False,
    ) -> list[Word]:
        """默认只列已加入生词本的词；`include_cache=True` 用于导出全量缓存。"""
        clauses: list[str] = []
        params: list[object] = []
        if not include_cache:
            clauses.append("in_vocabulary = 1")
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db.query_all(
            f"SELECT * FROM words{where} ORDER BY created_at DESC, id DESC",
            params,
        )
        return [Word.from_row(row) for row in rows]

    def list_due(self, on_date: str | None = None, limit: int | None = None) -> list[Word]:
        """到期（due_date <= 指定日期）且已加入生词本、未掌握的词的集合。"""
        day = on_date or today_str()
        sql = (
            "SELECT * FROM words WHERE due_date IS NOT NULL AND due_date <= ? "
            "AND status <> 'mastered' AND in_vocabulary = 1 ORDER BY due_date, id"
        )
        params: list[object] = [day]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [Word.from_row(row) for row in self._db.query_all(sql, params)]

    def count(self, status: str | None = None, *, include_cache: bool = False) -> int:
        clauses: list[str] = []
        params: list[object] = []
        if not include_cache:
            clauses.append("in_vocabulary = 1")
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self._db.query_one(f"SELECT COUNT(*) AS n FROM words{where}", params)
        return int(row["n"]) if row is not None else 0

    def count_unmastered(self) -> int:
        """未掌握且已加入生词本的词数（旧版首页指标，保留兼容）。"""
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM words WHERE status <> 'mastered' AND in_vocabulary = 1"
        )
        return int(row["n"]) if row is not None else 0

    def count_by_band(self) -> dict[str, int]:
        """生词本里各 CEFR 词档的数量（只统计已标注词档的词）。"""
        rows = self._db.query_all(
            "SELECT cefr_band, COUNT(*) AS n FROM words "
            "WHERE in_vocabulary = 1 AND cefr_band <> '' GROUP BY cefr_band"
        )
        return {str(row["cefr_band"]): int(row["n"]) for row in rows}


class ReviewRepo(_BaseRepo):
    """复习历史（review_logs）：只追加，不修改。"""

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
    ) -> ReviewLog:
        timestamp = reviewed_at or now_iso()
        cursor = self._db.execute(
            """
            INSERT INTO review_logs
                (word_id, rating, prev_interval, next_interval, reviewed_at, note, advice)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(word_id),
                int(rating),
                int(prev_interval),
                int(next_interval),
                timestamp,
                note.strip(),
                advice.strip(),
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
        )

    def list_for_word(self, word_id: int, limit: int = 20) -> list[ReviewLog]:
        rows = self._db.query_all(
            "SELECT * FROM review_logs WHERE word_id = ? "
            "ORDER BY reviewed_at DESC, id DESC LIMIT ?",
            (int(word_id), int(limit)),
        )
        return [ReviewLog.from_row(row) for row in rows]

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
            from .db import DuplicateError

            raise DuplicateError(f"文章 {quiz.article_id} 已经有一套阅读检测题") from exc
        return replace(quiz, id=int(cursor.lastrowid or 0), created_at=params["created_at"])

    def save_result(
        self,
        quiz_id: int,
        *,
        answers: tuple[int, ...],
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

    def save_stage_answers(self, quiz_id: int, answers: tuple[int, ...]) -> None:
        """第一轮作答先落库：第二阶段出题失败/关窗后重进不用重答。"""
        self._db.execute(
            "UPDATE reading_quizzes SET answers_json = ? WHERE id = ? AND completed_at IS NULL",
            (json.dumps(list(answers), ensure_ascii=False), int(quiz_id)),
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

    def list_completed_between(self, start_day: str, end_day: str) -> list[ReadingQuiz]:
        """日期闭区间内完成的检测，按日期升序（每周复盘 / 首页趋势）。"""
        rows = self._db.query_all(
            "SELECT * FROM reading_quizzes WHERE completed_at IS NOT NULL "
            "AND article_date BETWEEN ? AND ? ORDER BY article_date, id",
            (start_day, end_day),
        )
        return [ReadingQuiz.from_row(row) for row in rows]

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

    def list_all(self) -> list[ReadingQuiz]:
        """全部阅读检测（导出用），按日期倒序。"""
        rows = self._db.query_all(
            "SELECT * FROM reading_quizzes ORDER BY article_date DESC, id DESC"
        )
        return [ReadingQuiz.from_row(row) for row in rows]


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
