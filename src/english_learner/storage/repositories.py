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

import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from typing import TYPE_CHECKING

from ..models.entities import (
    Article,
    DailyStats,
    Highlight,
    Profile,
    ReviewLog,
    Word,
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
            comment, assessed_at, updated_at
        ) VALUES (
            1, :cefr_level, :vocab_estimate, :strengths_json, :weaknesses_json,
            :comment, :assessed_at, :updated_at
        )
        ON CONFLICT(id) DO UPDATE SET
            cefr_level      = excluded.cefr_level,
            vocab_estimate  = excluded.vocab_estimate,
            strengths_json  = excluded.strengths_json,
            weaknesses_json = excluded.weaknesses_json,
            comment         = excluded.comment,
            assessed_at     = excluded.assessed_at,
            updated_at      = excluded.updated_at
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
            example_en, example_cn, context_sentence, article_id, status,
            ease, interval_days, repetitions, due_date, created_at, updated_at
        ) VALUES (
            :lemma, :display_form, :phonetic, :pos, :meaning_cn, :meaning_en,
            :example_en, :example_cn, :context_sentence, :article_id, :status,
            :ease, :interval_days, :repetitions, :due_date, :created_at, :updated_at
        )
        ON CONFLICT(lemma) DO UPDATE SET
            display_form     = excluded.display_form,
            phonetic         = excluded.phonetic,
            pos              = excluded.pos,
            meaning_cn       = excluded.meaning_cn,
            meaning_en       = excluded.meaning_en,
            example_en       = excluded.example_en,
            example_cn       = excluded.example_cn,
            context_sentence = CASE
                WHEN excluded.context_sentence <> '' THEN excluded.context_sentence
                ELSE words.context_sentence
            END,
            article_id       = COALESCE(words.article_id, excluded.article_id),
            updated_at       = excluded.updated_at
    """

    def get_by_lemma(self, lemma: str) -> Word | None:
        row = self._db.query_one("SELECT * FROM words WHERE lemma = ?", (lemma,))
        return Word.from_row(row) if row is not None else None

    def get_by_id(self, word_id: int) -> Word | None:
        row = self._db.query_one("SELECT * FROM words WHERE id = ?", (word_id,))
        return Word.from_row(row) if row is not None else None

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
        """某天查过 / 新加入的词（created_at 是 ISO 时间，取前 10 位比较日期）。"""
        rows = self._db.query_all(
            "SELECT * FROM words WHERE substr(created_at, 1, 10) = ? ORDER BY id",
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

    def delete(self, word_id: int) -> bool:
        """从生词本删除词条；相关高亮与复习记录由外键 ON DELETE CASCADE 清理。"""
        cursor = self._db.execute("DELETE FROM words WHERE id = ?", (int(word_id),))
        return cursor.rowcount > 0

    def list_all(self, status: str | None = None) -> list[Word]:
        if status is None:
            rows = self._db.query_all("SELECT * FROM words ORDER BY created_at DESC, id DESC")
        else:
            rows = self._db.query_all(
                "SELECT * FROM words WHERE status = ? ORDER BY created_at DESC, id DESC",
                (status,),
            )
        return [Word.from_row(row) for row in rows]

    def list_due(self, on_date: str | None = None, limit: int | None = None) -> list[Word]:
        """到期（due_date <= 指定日期）且未掌握的词，按到期时间升序。"""
        day = on_date or today_str()
        sql = (
            "SELECT * FROM words WHERE due_date IS NOT NULL AND due_date <= ? "
            "AND status <> 'mastered' ORDER BY due_date, id"
        )
        params: list[object] = [day]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [Word.from_row(row) for row in self._db.query_all(sql, params)]

    def count(self, status: str | None = None) -> int:
        if status is None:
            row = self._db.query_one("SELECT COUNT(*) AS n FROM words")
        else:
            row = self._db.query_one("SELECT COUNT(*) AS n FROM words WHERE status = ?", (status,))
        return int(row["n"]) if row is not None else 0

    def count_unmastered(self) -> int:
        """M1 首页的“今日待复习”先用“未掌握”近似，M2 改为按 due_date 统计。"""
        row = self._db.query_one("SELECT COUNT(*) AS n FROM words WHERE status <> 'mastered'")
        return int(row["n"]) if row is not None else 0


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
    ) -> ReviewLog:
        timestamp = reviewed_at or now_iso()
        cursor = self._db.execute(
            """
            INSERT INTO review_logs (word_id, rating, prev_interval, next_interval, reviewed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(word_id), int(rating), int(prev_interval), int(next_interval), timestamp),
        )
        return ReviewLog(
            id=int(cursor.lastrowid or 0),
            word_id=int(word_id),
            rating=int(rating),
            prev_interval=int(prev_interval),
            next_interval=int(next_interval),
            reviewed_at=timestamp,
        )

    def list_for_word(self, word_id: int, limit: int = 20) -> list[ReviewLog]:
        rows = self._db.query_all(
            "SELECT * FROM review_logs WHERE word_id = ? "
            "ORDER BY reviewed_at DESC, id DESC LIMIT ?",
            (int(word_id), int(limit)),
        )
        return [ReviewLog.from_row(row) for row in rows]

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
            "WHERE due_date IS NOT NULL AND due_date <= ? AND status <> 'mastered'",
            (day,),
        )
        return int(row["n"]) if row is not None else 0

    def list_all(self) -> list[DailyStats]:
        """全部每日统计（导出用），按日期升序。"""
        rows = self._db.query_all("SELECT * FROM daily_stats ORDER BY stat_date")
        return [DailyStats.from_row(row) for row in rows]

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
