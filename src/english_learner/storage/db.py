"""SQLite 连接、建表与迁移。

Java 对照：
- Database ≈ DataSource + 一次性的 SchemaInitializer；
- `repositories.py` ≈ 一组 MyBatis Mapper（手写 SQL，不开 ORM）。

线程模型（重要）：
- 应用里 AI 调用跑在 TaskWorker 子线程，子线程可能写文章 / 生词，
  所以连接用 `check_same_thread=False` 打开；
- 所有访问都经过本类的锁（RLock），业务层不要自己拿 connection 直接跑 SQL；
- 自动提交模式（isolation_level=None），需要原子性时用 `transaction()` 显式包一层。

迁移：
- 版本号存在 `PRAGMA user_version` 里，当前 SCHEMA_VERSION = 1；
- 建表语句全部是 `CREATE ... IF NOT EXISTS`，重复初始化不会报错。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# 默认路径与配置文件同目录（%APPDATA%/EnglishLearner/data.db），
# 这里只借用 config_store 的目录解析，不引入任何界面依赖。
from ..services.config_store import config_dir
from .repositories import ArticleRepo, ProfileRepo, ReviewRepo, StatsRepo, WordRepo

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS profile (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    cefr_level      TEXT    NOT NULL,
    vocab_estimate  INTEGER NOT NULL DEFAULT 0,
    strengths_json  TEXT    NOT NULL DEFAULT '[]',
    weaknesses_json TEXT    NOT NULL DEFAULT '[]',
    comment         TEXT    NOT NULL DEFAULT '',
    assessed_at     TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    article_date     TEXT    NOT NULL UNIQUE,
    title            TEXT    NOT NULL,
    content          TEXT    NOT NULL,
    level            TEXT    NOT NULL,
    topic            TEXT    NOT NULL DEFAULT '',
    word_count       INTEGER NOT NULL DEFAULT 0,
    target_words_json TEXT   NOT NULL DEFAULT '[]',
    read_at          TEXT,
    created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS words (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lemma           TEXT    NOT NULL COLLATE NOCASE UNIQUE,
    display_form    TEXT    NOT NULL DEFAULT '',
    phonetic        TEXT    NOT NULL DEFAULT '',
    pos             TEXT    NOT NULL DEFAULT '',
    meaning_cn      TEXT    NOT NULL DEFAULT '',
    meaning_en      TEXT    NOT NULL DEFAULT '',
    example_en      TEXT    NOT NULL DEFAULT '',
    example_cn      TEXT    NOT NULL DEFAULT '',
    context_sentence TEXT   NOT NULL DEFAULT '',
    article_id      INTEGER REFERENCES articles(id) ON DELETE SET NULL,
    status          TEXT    NOT NULL DEFAULT 'new',
    ease            REAL    NOT NULL DEFAULT 2.5,
    interval_days   INTEGER NOT NULL DEFAULT 0,
    repetitions     INTEGER NOT NULL DEFAULT 0,
    due_date        TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS highlights (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id   INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    word_id      INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    start_offset INTEGER NOT NULL,
    end_offset   INTEGER NOT NULL,
    sentence     TEXT    NOT NULL DEFAULT '',
    UNIQUE (article_id, start_offset, end_offset)
);

CREATE TABLE IF NOT EXISTS review_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id       INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    rating        INTEGER NOT NULL,
    prev_interval INTEGER NOT NULL,
    next_interval INTEGER NOT NULL,
    reviewed_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_stats (
    stat_date    TEXT PRIMARY KEY,
    article_read INTEGER NOT NULL DEFAULT 0,
    words_added  INTEGER NOT NULL DEFAULT 0,
    reviews_done INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_words_due ON words(due_date);
CREATE INDEX IF NOT EXISTS idx_highlights_article ON highlights(article_id);
CREATE INDEX IF NOT EXISTS idx_review_logs_word ON review_logs(word_id);
"""


class StorageError(RuntimeError):
    """存储层错误：数据库版本不兼容等。"""


class DuplicateError(StorageError):
    """唯一约束冲突（如同一天重复插入文章）。业务层可以据此回读已有记录。"""


class Database:
    """一个进程共用一个 SQLite 连接；仓库对象从这里取。"""

    def __init__(self, path: Path | str | None = None, *, initialize: bool = True) -> None:
        self.path: Path = Path(path) if path is not None else config_dir() / "data.db"
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._profiles: ProfileRepo | None = None
        self._articles: ArticleRepo | None = None
        self._words: WordRepo | None = None
        self._reviews: ReviewRepo | None = None
        self._stats: StatsRepo | None = None
        if initialize:
            self.connect()

    # ------------------------------------------------------------ 连接管理
    def connect(self) -> sqlite3.Connection:
        """建立连接并确保表结构存在（幂等）。"""
        with self._lock:
            if self._conn is None:
                if str(self.path) != ":memory:":
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(self.path), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                # 自动提交：默认每条语句立即生效，事务由 transaction() 显式控制。
                conn.isolation_level = None
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA busy_timeout = 5000")
                self._conn = conn
                self._apply_schema(conn)
            return self._conn

    def _apply_schema(self, conn: sqlite3.Connection) -> None:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise StorageError(
                f"数据库版本 {version} 高于本程序支持的 {SCHEMA_VERSION}，"
                "请升级 Daily English 后再打开。"
            )
        conn.executescript(SCHEMA_SQL)
        if version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------ SQL 辅助
    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.connect().execute(sql, params)

    def executemany(self, sql: str, rows: Any) -> sqlite3.Cursor:
        with self._lock:
            return self.connect().executemany(sql, rows)

    def query_one(self, sql: str, params: Any = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.connect().execute(sql, params).fetchone()

    def query_all(self, sql: str, params: Any = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.connect().execute(sql, params).fetchall()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """显式事务，保证多条写语句要么全成功要么全回滚。

        注意：持有同一把 RLock，**不要嵌套使用**（SQLite 不支持嵌套 BEGIN）。
        """
        with self._lock:
            conn = self.connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    # ------------------------------------------------------------ 仓库访问
    @property
    def profiles(self) -> ProfileRepo:
        if self._profiles is None:
            self._profiles = ProfileRepo(self)
        return self._profiles

    @property
    def articles(self) -> ArticleRepo:
        if self._articles is None:
            self._articles = ArticleRepo(self)
        return self._articles

    @property
    def words(self) -> WordRepo:
        if self._words is None:
            self._words = WordRepo(self)
        return self._words

    @property
    def reviews(self) -> ReviewRepo:
        if self._reviews is None:
            self._reviews = ReviewRepo(self)
        return self._reviews

    @property
    def stats(self) -> StatsRepo:
        if self._stats is None:
            self._stats = StatsRepo(self)
        return self._stats
