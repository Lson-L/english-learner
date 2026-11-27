"""生词、词形与高亮仓库。"""

from __future__ import annotations

from datetime import date, timedelta

from ...models.entities import (
    Highlight,
    Word,
    WordForm,
    now_iso,
    today_str,
)
from .base import _BaseRepo


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

    # 预取 / 文章难词只补缓存，绝不覆盖已有词条：已有的完整释义（例句、记忆法、
    # SRS 状态、in_vocabulary）都比预生成的短释义更权威。
    _SEED_CACHE_SQL = """
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
        ON CONFLICT(lemma) DO NOTHING
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

    def seed_cache(self, word: Word) -> Word | None:
        """写入“预生成释义”缓存；同原形已存在时原样保留旧词条。

        用于文章 glossary 与阅读后台预取：只补空缺，不覆盖用户已有的
        完整释义、SRS 进度与生词本状态。
        """
        params = word.to_params()
        if not params["created_at"]:
            params["created_at"] = now_iso()
        if not params["updated_at"]:
            params["updated_at"] = params["created_at"]
        with self._db.transaction() as conn:
            conn.execute(self._SEED_CACHE_SQL, params)
            row = conn.execute("SELECT * FROM words WHERE lemma = ?", (word.lemma,)).fetchone()
        return Word.from_row(row) if row is not None else None

    def cached_forms(self) -> set[str]:
        """全部已知词形与原形（小写）：预取时用来跳过已有缓存的词。"""
        rows = self._db.query_all(
            "SELECT lemma AS value FROM words UNION SELECT form AS value FROM word_forms"
        )
        return {str(row["value"]).strip().lower() for row in rows if str(row["value"]).strip()}

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

    def update_definition(self, word_id: int, word: Word) -> Word | None:
        """按 id 更新释义类字段（音标 / 词性 / 释义 / 例句 / 记忆法 / 词档 / 上下文）。

        后台补全预生成简释专用（M6.3）：不改 lemma（高亮与 word_forms 都指向它）、
        display_form、in_vocabulary 与 SRS 排期，也不新建词条；空的新值不覆盖旧值。
        """
        cursor = self._db.execute(
            """
            UPDATE words SET
                phonetic = CASE WHEN :phonetic <> '' THEN :phonetic ELSE phonetic END,
                pos = CASE WHEN :pos <> '' THEN :pos ELSE pos END,
                meaning_cn = CASE
                    WHEN :meaning_cn <> '' THEN :meaning_cn
                    ELSE meaning_cn
                END,
                meaning_en = CASE
                    WHEN :meaning_en <> '' THEN :meaning_en
                    ELSE meaning_en
                END,
                example_en = CASE
                    WHEN :example_en <> '' THEN :example_en
                    ELSE example_en
                END,
                example_cn = CASE
                    WHEN :example_cn <> '' THEN :example_cn
                    ELSE example_cn
                END,
                -- 记忆方法只在原本为空时补：用户可能已在生词本里手动生成过
                mnemonic = CASE
                    WHEN mnemonic = '' AND :mnemonic <> '' THEN :mnemonic
                    ELSE mnemonic
                END,
                cefr_band = CASE WHEN :cefr_band <> '' THEN :cefr_band ELSE cefr_band END,
                context_sentence = CASE
                    WHEN :context_sentence <> '' THEN :context_sentence
                    ELSE context_sentence
                END,
                article_id = COALESCE(article_id, :article_id),
                updated_at = :updated_at
            WHERE id = :id
            """,
            {
                "id": int(word_id),
                "phonetic": (word.phonetic or "").strip(),
                "pos": (word.pos or "").strip(),
                "meaning_cn": (word.meaning_cn or "").strip(),
                "meaning_en": (word.meaning_en or "").strip(),
                "example_en": (word.example_en or "").strip(),
                "example_cn": (word.example_cn or "").strip(),
                "mnemonic": (word.mnemonic or "").strip(),
                "cefr_band": (word.cefr_band or "").strip(),
                "context_sentence": (word.context_sentence or "").strip(),
                "article_id": word.article_id,
                "updated_at": now_iso(),
            },
        )
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(int(word_id))

    def mark_in_vocabulary(self, word_id: int, *, first_due_date: str | None = None) -> Word | None:
        """把词条从“查词缓存”升级为“已加入生词本”。

        `first_due_date` 只在 0 → 1 的第一次入本时写入，用于把首次复习放到
        明天；重复加入（`in_vocabulary` 已经是 1）不能重置已经推进的 SRS 排期。
        查词缓存可能放了很多天，若沿用它当时的 `due_date`，一入本就会变成逾期。
        """
        current = self.get_by_id(int(word_id))
        if current is None:
            return None
        if not current.in_vocabulary and first_due_date:
            self._db.execute(
                "UPDATE words SET in_vocabulary = 1, due_date = ?, updated_at = ? WHERE id = ?",
                (first_due_date, now_iso(), int(word_id)),
            )
        else:
            self._db.execute(
                "UPDATE words SET in_vocabulary = 1, updated_at = ? WHERE id = ?",
                (now_iso(), int(word_id)),
            )
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

    def list_known_lemmas(self, min_interval_days: int = 30) -> tuple[str, ...]:
        """用户已掌握的词（M5.3 可读性指标用）。

        口径：加入生词本（`in_vocabulary = 1`）或复习间隔达到阈值（默认 30 天）。
        只返回原形，不返回释义，避免把大对象带进文章生成流程。
        阈值由调用方传入，storage 层不引用 services 的常量。
        """
        rows = self._db.query_all(
            "SELECT lemma FROM words WHERE in_vocabulary = 1 OR interval_days >= ?",
            (int(min_interval_days),),
        )
        return tuple(str(row["lemma"]).strip().lower() for row in rows if str(row["lemma"]).strip())

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

    def count_due(self, on_date: str | None = None) -> int:
        """到期未复习的词数（与 `list_due` 同一口径，用于积压压力提示）。"""
        day = on_date or today_str()
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM words WHERE due_date IS NOT NULL AND due_date <= ? "
            "AND status <> 'mastered' AND in_vocabulary = 1",
            (day,),
        )
        return int(row["n"]) if row is not None else 0

    def count_by_band(self) -> dict[str, int]:
        """生词本里各 CEFR 词档的数量（只统计已标注词档的词）。"""
        rows = self._db.query_all(
            "SELECT cefr_band, COUNT(*) AS n FROM words "
            "WHERE in_vocabulary = 1 AND cefr_band <> '' GROUP BY cefr_band"
        )
        return {str(row["cefr_band"]): int(row["n"]) for row in rows}

    def count_overdue(self, on_date: str | None = None) -> int:
        """已过期未复习的词数（不含今天到期；用于区分“逾期”和“今日待复习”）。"""
        day = on_date or today_str()
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM words WHERE due_date IS NOT NULL AND due_date < ? "
            "AND status <> 'mastered' AND in_vocabulary = 1",
            (day,),
        )
        return int(row["n"]) if row is not None else 0

    def count_stalled(self, on_date: str | None = None, *, days: int = 7) -> int:
        """停滞词：学习中且到期日早于 `days` 天前仍未复习。

        只用于统计页提示，不参与调度；`days` 小于 1 时按 1 处理。
        """
        day = on_date or today_str()
        try:
            cutoff = (date.fromisoformat(day) - timedelta(days=max(1, int(days)))).isoformat()
        except ValueError:
            cutoff = day
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM words WHERE in_vocabulary = 1 AND status = 'learning' "
            "AND due_date IS NOT NULL AND due_date < ?",
            (cutoff,),
        )
        return int(row["n"]) if row is not None else 0

    def due_counts(self, start_day: str, end_day: str) -> dict[str, int]:
        """按到期日统计未掌握词数，供“未来 7 天复习负载”使用。"""
        rows = self._db.query_all(
            "SELECT due_date, COUNT(*) AS n FROM words "
            "WHERE in_vocabulary = 1 AND status <> 'mastered' AND due_date IS NOT NULL "
            "AND due_date BETWEEN ? AND ? GROUP BY due_date",
            (start_day, end_day),
        )
        return {str(row["due_date"]): int(row["n"]) for row in rows}
