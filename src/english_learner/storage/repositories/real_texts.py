"""真实文章库与来源仓库。"""

from __future__ import annotations

from datetime import date, timedelta

from ...models.entities import (
    ContentSource,
    RealText,
    RealTextHistory,
    now_iso,
)
from .base import _BaseRepo


class RealTextRepo(_BaseRepo):
    """真实文章库 + 供给历史。

    文章库由构建期 `corpus.json` 种子写入；历史记录每次上架、完成、换回或弃读。
    业务判断（选哪篇、间隔多久）在 `services` 层，这里只做行 ↔ 对象搬运。
    """

    _UPSERT_SQL = """
        INSERT INTO real_texts (
            id, title, content, level, level_source, source, source_url,
            license, license_url, published, topics_json, word_count, excerpt,
            attribution, questions_json, fetched_at, content_hash,
            questions_source, corpus_version, created_at
        ) VALUES (
            :id, :title, :content, :level, :level_source, :source, :source_url,
            :license, :license_url, :published, :topics_json, :word_count, :excerpt,
            :attribution, :questions_json, :fetched_at, :content_hash,
            :questions_source, :corpus_version, :created_at
        )
        ON CONFLICT(id) DO UPDATE SET
            title            = excluded.title,
            content          = excluded.content,
            level            = excluded.level,
            level_source     = excluded.level_source,
            source           = excluded.source,
            source_url       = excluded.source_url,
            license          = excluded.license,
            license_url      = excluded.license_url,
            published        = excluded.published,
            topics_json      = excluded.topics_json,
            word_count       = excluded.word_count,
            excerpt          = excluded.excerpt,
            attribution      = excluded.attribution,
            questions_json   = excluded.questions_json,
            fetched_at       = excluded.fetched_at,
            content_hash     = excluded.content_hash,
            questions_source = excluded.questions_source,
            corpus_version   = excluded.corpus_version
    """

    _HISTORY_INSERT_SQL = """
        INSERT INTO real_text_history (
            text_id, served_on, article_id, level, word_count, status, score,
            self_report, outcome, completed_at, created_at, updated_at
        ) VALUES (
            :text_id, :served_on, :article_id, :level, :word_count, :status, :score,
            :self_report, :outcome, :completed_at, :created_at, :updated_at
        )
    """

    # ------------------------------------------------------------ 文章库
    def upsert(self, text: RealText) -> RealText:
        params = text.to_params()
        if not params["created_at"]:
            params["created_at"] = now_iso()
        self._db.execute(self._UPSERT_SQL, params)
        stored = self.get(text.id)
        assert stored is not None  # 刚写完必然能读回
        return stored

    def get(self, text_id: str) -> RealText | None:
        row = self._db.query_one("SELECT * FROM real_texts WHERE id = ?", (text_id,))
        return RealText.from_row(row) if row is not None else None

    def list_all(self) -> list[RealText]:
        rows = self._db.query_all("SELECT * FROM real_texts ORDER BY level, id")
        return [RealText.from_row(row) for row in rows]

    def list_unread(self) -> list[RealText]:
        """从未上架过的文章；served / completed / abandoned 都算已用。"""
        rows = self._db.query_all(
            "SELECT * FROM real_texts WHERE id NOT IN "
            "(SELECT text_id FROM real_text_history) ORDER BY level, id"
        )
        return [RealText.from_row(row) for row in rows]

    def max_corpus_version(self) -> int:
        row = self._db.query_one(
            "SELECT COALESCE(MAX(corpus_version), 0) AS version FROM real_texts"
        )
        return int(row["version"]) if row is not None else 0

    # ---------------------------------------------------------- 供给历史
    def mark_served(
        self,
        text: RealText,
        *,
        served_on: str,
        article_id: int | None,
    ) -> RealTextHistory:
        stamp = now_iso()
        history = RealTextHistory(
            text_id=text.id,
            served_on=served_on,
            article_id=article_id,
            level=text.level,
            word_count=text.word_count,
            status="served",
            created_at=stamp,
            updated_at=stamp,
        )
        params = history.to_params()
        cursor = self._db.execute(self._HISTORY_INSERT_SQL, params)
        stored = self._history_by_id(int(cursor.lastrowid or 0))
        assert stored is not None
        return stored

    def record_completion(
        self,
        text_id: str,
        *,
        score: float,
        self_report: str,
        outcome: str,
        completed_at: str | None = None,
    ) -> None:
        stamp = now_iso()
        self._db.execute(
            """
            UPDATE real_text_history
               SET status = 'completed', score = ?, self_report = ?, outcome = ?,
                   completed_at = ?, updated_at = ?
             WHERE id = (
                 SELECT id FROM real_text_history
                  WHERE text_id = ? ORDER BY id DESC LIMIT 1
             )
            """,
            (float(score), self_report, outcome, completed_at or stamp, stamp, text_id),
        )

    def mark_overridden(self, text_id: str) -> None:
        stamp = now_iso()
        self._db.execute(
            """
            UPDATE real_text_history
               SET status = 'overridden', outcome = 'skip', updated_at = ?
             WHERE id = (
                 SELECT id FROM real_text_history
                  WHERE text_id = ? ORDER BY id DESC LIMIT 1
             )
            """,
            (stamp, text_id),
        )

    def expire_abandoned(self, today: str, *, days: int = 7) -> int:
        """把超期未完成的 served 记录结算为 abandoned。"""
        cutoff = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
        cursor = self._db.execute(
            """
            UPDATE real_text_history
               SET status = 'abandoned', outcome = 'abandon', updated_at = ?
             WHERE status = 'served' AND served_on <= ?
            """,
            (now_iso(), cutoff),
        )
        return int(cursor.rowcount)

    def served_ids(self) -> set[str]:
        rows = self._db.query_all("SELECT DISTINCT text_id FROM real_text_history")
        return {str(row["text_id"]) for row in rows}

    def list_history(self) -> list[RealTextHistory]:
        rows = self._db.query_all("SELECT * FROM real_text_history ORDER BY id")
        return [RealTextHistory.from_row(row) for row in rows]

    def last_history(self) -> RealTextHistory | None:
        row = self._db.query_one("SELECT * FROM real_text_history ORDER BY id DESC LIMIT 1")
        return RealTextHistory.from_row(row) if row is not None else None

    def history_for(self, text_id: str) -> list[RealTextHistory]:
        rows = self._db.query_all(
            "SELECT * FROM real_text_history WHERE text_id = ? ORDER BY id",
            (text_id,),
        )
        return [RealTextHistory.from_row(row) for row in rows]

    def _history_by_id(self, history_id: int) -> RealTextHistory | None:
        row = self._db.query_one("SELECT * FROM real_text_history WHERE id = ?", (history_id,))
        return RealTextHistory.from_row(row) if row is not None else None


class ContentSourceRepo(_BaseRepo):
    """内置在线源（M7.7）。M7.0 只建表，抓取逻辑后续再接。"""

    _UPSERT_SQL = """
        INSERT INTO content_sources (
            id, title, url, level, license, source, enabled,
            last_attempt_at, last_error, failure_count, created_at
        ) VALUES (
            :id, :title, :url, :level, :license, :source, :enabled,
            :last_attempt_at, :last_error, :failure_count, :created_at
        )
        ON CONFLICT(id) DO UPDATE SET
            title           = excluded.title,
            url             = excluded.url,
            level           = excluded.level,
            license         = excluded.license,
            source          = excluded.source,
            enabled         = excluded.enabled,
            last_attempt_at = excluded.last_attempt_at,
            last_error      = excluded.last_error,
            failure_count   = excluded.failure_count
    """

    def upsert(self, source: ContentSource) -> ContentSource:
        params = source.to_params()
        if not params["created_at"]:
            params["created_at"] = now_iso()
        self._db.execute(self._UPSERT_SQL, params)
        stored = self.get(source.id)
        assert stored is not None
        return stored

    def get(self, source_id: str) -> ContentSource | None:
        row = self._db.query_one("SELECT * FROM content_sources WHERE id = ?", (source_id,))
        return ContentSource.from_row(row) if row is not None else None

    def list_all(self) -> list[ContentSource]:
        rows = self._db.query_all("SELECT * FROM content_sources ORDER BY id")
        return [ContentSource.from_row(row) for row in rows]
