"""SQLite 连接、建表与迁移。

Java 对照：
- Database ≈ DataSource + 一次性的 SchemaInitializer；
- `repositories/` ≈ 一组 MyBatis Mapper（手写 SQL，不开 ORM）。

线程模型（重要）：
- 应用里 AI 调用跑在 TaskWorker 子线程，子线程可能写文章 / 生词，
  所以连接用 `check_same_thread=False` 打开；
- 所有访问都经过本类的锁（RLock），业务层不要自己拿 connection 直接跑 SQL；
- 自动提交模式（isolation_level=None），需要原子性时用 `transaction()` 显式包一层。

迁移：
- 版本号存在 `PRAGMA user_version` 里，当前 SCHEMA_VERSION = 10；
- 建表语句全部是 `CREATE ... IF NOT EXISTS`，重复初始化不会报错；
- v1 → v2：words 增加 mnemonic，review_logs 增加 note/advice，新增 word_forms
  词形缓存表；用 `ALTER TABLE ... ADD COLUMN` 原地升级，旧数据不丢；
- v2 → v3：words 增加 in_vocabulary，把升级前的词全部标记为已加入生词本
  （无法区分当时是“查过”还是“真正加入”，宁可保留也不误删）；
- v3 → v4：新增 reading_quizzes 阅读检测表（题目缓存 + 作答结果），
  新表由 CREATE TABLE IF NOT EXISTS 自动补齐，不需要 ALTER 旧表；
- v4 → v5：阅读检测第一阶段（M3.9）：profile 增加待定等级/连续证据计数；
  reading_quizzes 增加短答、错因、自评、建议等级、证据强度等列；
- v5 → v6：Phase 2 / 3：profile 增加等级置信度与调整原因；words 增加 CEFR 词档；
  reading_quizzes 增加多道产出题、后验、置信度、练习模式与阶段；
  新增 level_evidence（历史证据回放）与 weekly_reviews（每周复盘）两张表。
  旧检测结果不回填证据、不阻塞启动；
- v6 → v7：新增 ai_calls（AI 调用记录），只记用量元数据，不保存提示词和回复；
- v7 → v8：review_logs 增加 answer_text / verdict / distance（M5.1 复习证据），
  只加列，旧记录保持空串 / 0，不参与统计；
  articles 增加 metrics_json（M5.3 本地可读性指标，M5.1 发布时还没有这一列，
  所以这一列不放在 `version < 8` 分支里，而是幂等补齐，见 `_apply_schema`）；
- v8 内新增 writing_attempts（M5.4 回译练习）：新表由 `CREATE TABLE IF NOT EXISTS`
  自动补齐，不需要再升版本号，旧库启动后即可用；
- v8 内新增 reading_quizzes.choice_notes_json（阅读检测“不知道”补充说明）：
  与 articles.metrics_json 一样用幂等 `_add_column` 补齐，不升版本号；
- v8 → v9（M7）：articles 增加 source / source_url / source_ref（文章来源）；
  review_logs 增加 mode（语境复习，M7.4 使用）；新增 real_texts（真实文章库）、
  real_text_history（供给历史）、content_sources（内置在线源，M7.7 使用）三张表。
  新表由 `CREATE TABLE IF NOT EXISTS` 自动补齐，旧库只加列、不重建表。
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
from .repositories import (
    AICallRepo,
    ArticleRepo,
    ContentSourceRepo,
    LevelEvidenceRepo,
    ProfileRepo,
    QuizRepo,
    RealTextRepo,
    ReviewRepo,
    StatsRepo,
    WeeklyReviewRepo,
    WordRepo,
    WritingAttemptRepo,
)

SCHEMA_VERSION = 10

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS profile (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    cefr_level      TEXT    NOT NULL,
    vocab_estimate  INTEGER NOT NULL DEFAULT 0,
    strengths_json  TEXT    NOT NULL DEFAULT '[]',
    weaknesses_json TEXT    NOT NULL DEFAULT '[]',
    comment         TEXT    NOT NULL DEFAULT '',
    assessed_at     TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    -- 阅读检测连续证据：pending_level 非空表示“建议升级但还在观察”
    pending_level   TEXT    NOT NULL DEFAULT '',
    pending_count   INTEGER NOT NULL DEFAULT 0,
    -- Phase 2：等级估计的置信度与最近一次调整原因
    level_confidence REAL   NOT NULL DEFAULT 0,
    level_updated_reason TEXT NOT NULL DEFAULT ''
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
    -- M5.3 本地可读性指标（生词比例 / 句长 / 目标词覆盖），空串表示旧文章没有指标
    metrics_json     TEXT    NOT NULL DEFAULT '',
    -- M7 文章来源：generated（AI 生成）/ real（真实文章）/ imported（以后）
    source           TEXT    NOT NULL DEFAULT 'generated',
    source_url       TEXT    NOT NULL DEFAULT '',
    source_ref       TEXT    NOT NULL DEFAULT '',
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
    mnemonic        TEXT    NOT NULL DEFAULT '',
    context_sentence TEXT   NOT NULL DEFAULT '',
    article_id      INTEGER REFERENCES articles(id) ON DELETE SET NULL,
    -- 0 = 只是查词缓存；1 = 用户点过“加入生词本”
    in_vocabulary   INTEGER NOT NULL DEFAULT 0,
    -- AI 查词时顺带标注的 CEFR 词档；旧词条为空
    cefr_band       TEXT    NOT NULL DEFAULT '',
    -- 释义结构版本：0 = 旧版单语境义；2 = 多义项释义
    definition_version INTEGER NOT NULL DEFAULT 0,
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
    reviewed_at   TEXT    NOT NULL,
    note          TEXT    NOT NULL DEFAULT '',
    advice        TEXT    NOT NULL DEFAULT '',
    -- M5.1 拼写证据：用户输入、判定（correct/near/wrong/skipped）、编辑距离
    answer_text   TEXT    NOT NULL DEFAULT '',
    verdict       TEXT    NOT NULL DEFAULT '',
    distance      INTEGER NOT NULL DEFAULT 0,
    -- M7.4 语境复习：normal / context（v9 先建列，旧库由 _add_column 补齐）
    mode          TEXT    NOT NULL DEFAULT ''
);

-- 词形缓存：went → go。查询命中这里就不用再调用 AI 判断原形。
CREATE TABLE IF NOT EXISTS word_forms (
    form       TEXT    NOT NULL COLLATE NOCASE PRIMARY KEY,
    word_id    INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL
);

-- 阅读检测：一篇文章一套题；article_id 唯一，文章替换时级联删除。
-- completed_at 为空表示只出了题，用户还没提交答案。
CREATE TABLE IF NOT EXISTS reading_quizzes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id        INTEGER NOT NULL UNIQUE REFERENCES articles(id) ON DELETE CASCADE,
    article_date      TEXT    NOT NULL,
    level             TEXT    NOT NULL,
    questions_json    TEXT    NOT NULL,
    answers_json      TEXT    NOT NULL DEFAULT '[]',
    -- 与选择题一一对应：选「不知道」时用户填写的补充说明（其余位置为空串）
    choice_notes_json TEXT    NOT NULL DEFAULT '[]',
    explanations_json TEXT    NOT NULL DEFAULT '[]',
    error_types_json  TEXT    NOT NULL DEFAULT '[]',
    short_answer_text TEXT    NOT NULL DEFAULT '',
    short_answer_score INTEGER NOT NULL DEFAULT 0,
    self_report       TEXT    NOT NULL DEFAULT '',
    score             INTEGER NOT NULL DEFAULT 0,
    recommended_level TEXT    NOT NULL DEFAULT '',
    previous_level    TEXT    NOT NULL DEFAULT '',
    result_level      TEXT    NOT NULL DEFAULT '',
    pending_level     TEXT    NOT NULL DEFAULT '',
    pending_count     INTEGER NOT NULL DEFAULT 0,
    feedback          TEXT    NOT NULL DEFAULT '',
    evidence_ratio    REAL    NOT NULL DEFAULT 0,
    -- Phase 2 / 3：多道产出题、本地后验、置信度、练习模式与出题阶段
    productions_json  TEXT    NOT NULL DEFAULT '[]',
    level_confidence  REAL    NOT NULL DEFAULT 0,
    posterior_json    TEXT    NOT NULL DEFAULT '{}',
    practice_mode     INTEGER NOT NULL DEFAULT 0,
    stage             INTEGER NOT NULL DEFAULT 2,
    created_at        TEXT    NOT NULL,
    completed_at      TEXT
);

-- 回译练习：一篇文章最多 3 句，首次打开写入题目，提交后回写答案与批改。
-- 只作练习反馈，不参与等级与 SRS；文章被替换时级联删除。
CREATE TABLE IF NOT EXISTS writing_attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id    INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    article_date  TEXT    NOT NULL,
    position      INTEGER NOT NULL DEFAULT 0,
    sentence_en   TEXT    NOT NULL,
    reference_cn  TEXT    NOT NULL DEFAULT '',
    answer_en     TEXT    NOT NULL DEFAULT '',
    -- 单句批改 JSON：edited / reason / error_types
    verdict_json  TEXT    NOT NULL DEFAULT '',
    score         INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL,
    graded_at     TEXT,
    UNIQUE (article_id, position)
);

-- 等级证据：每次检测的题目级观测 + 后验，供后续检测回放历史。
-- quiz_id 允许为空并 SET NULL：文章被替换导致检测级联删除时，证据仍然保留。
CREATE TABLE IF NOT EXISTS level_evidence (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    source                TEXT    NOT NULL,
    observed_at           TEXT    NOT NULL,
    evidence_date         TEXT    NOT NULL DEFAULT '',
    article_level         TEXT    NOT NULL DEFAULT '',
    quiz_id               INTEGER REFERENCES reading_quizzes(id) ON DELETE SET NULL,
    choice_correct        INTEGER NOT NULL DEFAULT 0,
    choice_total          INTEGER NOT NULL DEFAULT 0,
    short_score           INTEGER NOT NULL DEFAULT 0,
    short_total           INTEGER NOT NULL DEFAULT 0,
    weighted_points       REAL    NOT NULL DEFAULT 0,
    evidence_ratio        REAL    NOT NULL DEFAULT 0,
    error_types_json      TEXT    NOT NULL DEFAULT '[]',
    observations_json     TEXT    NOT NULL DEFAULT '[]',
    posterior_json        TEXT    NOT NULL DEFAULT '{}',
    confidence            REAL    NOT NULL DEFAULT 0
);

-- 每周复盘：同一周只存一条；AI 只给建议，用户确认后写 applied_*。
CREATE TABLE IF NOT EXISTS weekly_reviews (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start       TEXT    NOT NULL UNIQUE,
    summary          TEXT    NOT NULL DEFAULT '',
    suggested_level  TEXT    NOT NULL DEFAULT '',
    focus_json       TEXT    NOT NULL DEFAULT '[]',
    quiz_count       INTEGER NOT NULL DEFAULT 0,
    review_count     INTEGER NOT NULL DEFAULT 0,
    words_added      INTEGER NOT NULL DEFAULT 0,
    confidence       REAL    NOT NULL DEFAULT 0,
    applied_level    TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL,
    applied_at       TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS daily_stats (
    stat_date    TEXT PRIMARY KEY,
    article_read INTEGER NOT NULL DEFAULT 0,
    words_added  INTEGER NOT NULL DEFAULT 0,
    reviews_done INTEGER NOT NULL DEFAULT 0
);

-- M7 真实文章库：构建期 corpus.json 种子写入；运行期只读这里。
CREATE TABLE IF NOT EXISTS real_texts (
    id               TEXT    PRIMARY KEY,
    title            TEXT    NOT NULL,
    content          TEXT    NOT NULL,
    level            TEXT    NOT NULL,
    level_source     TEXT    NOT NULL DEFAULT '',
    source           TEXT    NOT NULL DEFAULT '',
    source_url       TEXT    NOT NULL DEFAULT '',
    license          TEXT    NOT NULL DEFAULT '',
    license_url      TEXT    NOT NULL DEFAULT '',
    published        TEXT    NOT NULL DEFAULT '',
    topics_json      TEXT    NOT NULL DEFAULT '[]',
    word_count       INTEGER NOT NULL DEFAULT 0,
    excerpt          INTEGER NOT NULL DEFAULT 0,
    attribution      TEXT    NOT NULL DEFAULT '',
    questions_json   TEXT    NOT NULL DEFAULT '[]',
    fetched_at       TEXT    NOT NULL DEFAULT '',
    content_hash     TEXT    NOT NULL DEFAULT '',
    questions_source TEXT    NOT NULL DEFAULT 'bundled',
    corpus_version   INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT    NOT NULL
);

-- 真实文章供给历史：每次上架一行，完成 / 换回 / 弃读都回写这里。
CREATE TABLE IF NOT EXISTS real_text_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    text_id      TEXT    NOT NULL REFERENCES real_texts(id) ON DELETE CASCADE,
    served_on    TEXT    NOT NULL,
    article_id   INTEGER,
    level        TEXT    NOT NULL DEFAULT '',
    word_count   INTEGER NOT NULL DEFAULT 0,
    status       TEXT    NOT NULL DEFAULT 'served',
    score        REAL    NOT NULL DEFAULT 0,
    self_report  TEXT    NOT NULL DEFAULT '',
    outcome      TEXT    NOT NULL DEFAULT '',
    completed_at TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

-- M7.7 内置在线源：只存 URL 与抓取状态，正文成功抓取后写 real_texts。
CREATE TABLE IF NOT EXISTS content_sources (
    id              TEXT    PRIMARY KEY,
    title           TEXT    NOT NULL DEFAULT '',
    url             TEXT    NOT NULL,
    level           TEXT    NOT NULL DEFAULT '',
    license         TEXT    NOT NULL DEFAULT '',
    source          TEXT    NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_attempt_at TEXT    NOT NULL DEFAULT '',
    last_error      TEXT    NOT NULL DEFAULT '',
    failure_count   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL
);

-- AI 调用记录：每次真实 HTTP 尝试一行（重试/降级会产生多行）。
-- 只保存用量元数据，不保存提示词与模型回复；tokens 允许为空（网关不返回 usage）。
CREATE TABLE IF NOT EXISTS ai_calls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    called_at         TEXT    NOT NULL,
    task              TEXT    NOT NULL DEFAULT '',
    model             TEXT    NOT NULL DEFAULT '',
    base_url          TEXT    NOT NULL DEFAULT '',
    json_mode         INTEGER NOT NULL DEFAULT 0,
    attempt           INTEGER NOT NULL DEFAULT 1,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    ok                INTEGER NOT NULL DEFAULT 1,
    error_kind        TEXT    NOT NULL DEFAULT '',
    error_message     TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_words_due ON words(due_date);
CREATE INDEX IF NOT EXISTS idx_highlights_article ON highlights(article_id);
CREATE INDEX IF NOT EXISTS idx_review_logs_word ON review_logs(word_id);
CREATE INDEX IF NOT EXISTS idx_word_forms_word ON word_forms(word_id);
CREATE INDEX IF NOT EXISTS idx_reading_quizzes_date ON reading_quizzes(article_date);
CREATE INDEX IF NOT EXISTS idx_reading_quizzes_completed ON reading_quizzes(completed_at);
CREATE INDEX IF NOT EXISTS idx_level_evidence_time ON level_evidence(observed_at);
CREATE INDEX IF NOT EXISTS idx_level_evidence_quiz ON level_evidence(quiz_id);
CREATE INDEX IF NOT EXISTS idx_writing_attempts_article ON writing_attempts(article_id);
CREATE INDEX IF NOT EXISTS idx_writing_attempts_time ON writing_attempts(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_calls_time ON ai_calls(called_at);
CREATE INDEX IF NOT EXISTS idx_ai_calls_task ON ai_calls(task);
CREATE INDEX IF NOT EXISTS idx_real_text_history_text ON real_text_history(text_id);
CREATE INDEX IF NOT EXISTS idx_real_text_history_served ON real_text_history(served_on);
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
        self._quizzes: QuizRepo | None = None
        self._evidence: LevelEvidenceRepo | None = None
        self._weekly: WeeklyReviewRepo | None = None
        self._ai_calls: AICallRepo | None = None
        self._writing: WritingAttemptRepo | None = None
        self._real_texts: RealTextRepo | None = None
        self._content_sources: ContentSourceRepo | None = None
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

        # 新库：CREATE TABLE 直接带上全部列；旧库：下面按版本逐列补齐。
        conn.executescript(SCHEMA_SQL)

        if version < 2:
            self._add_column(conn, "words", "mnemonic", "TEXT NOT NULL DEFAULT ''")
            self._add_column(conn, "review_logs", "note", "TEXT NOT NULL DEFAULT ''")
            self._add_column(conn, "review_logs", "advice", "TEXT NOT NULL DEFAULT ''")

        if version < 3:
            self._add_column(conn, "words", "in_vocabulary", "INTEGER NOT NULL DEFAULT 0")
            # 旧版本"查词即入生词本"，无法区分是否手动加入；保留全部旧词，避免误删。
            conn.execute("UPDATE words SET in_vocabulary = 1 WHERE in_vocabulary = 0")

        if version < 5:
            self._add_column(conn, "profile", "pending_level", "TEXT NOT NULL DEFAULT ''")
            self._add_column(conn, "profile", "pending_count", "INTEGER NOT NULL DEFAULT 0")
            self._add_column(
                conn, "reading_quizzes", "error_types_json", "TEXT NOT NULL DEFAULT '[]'"
            )
            self._add_column(
                conn, "reading_quizzes", "short_answer_text", "TEXT NOT NULL DEFAULT ''"
            )
            self._add_column(
                conn, "reading_quizzes", "short_answer_score", "INTEGER NOT NULL DEFAULT 0"
            )
            self._add_column(conn, "reading_quizzes", "self_report", "TEXT NOT NULL DEFAULT ''")
            self._add_column(
                conn, "reading_quizzes", "recommended_level", "TEXT NOT NULL DEFAULT ''"
            )
            self._add_column(conn, "reading_quizzes", "pending_level", "TEXT NOT NULL DEFAULT ''")
            self._add_column(conn, "reading_quizzes", "pending_count", "INTEGER NOT NULL DEFAULT 0")
            self._add_column(conn, "reading_quizzes", "evidence_ratio", "REAL NOT NULL DEFAULT 0")

        if version < 6:
            self._add_column(conn, "profile", "level_confidence", "REAL NOT NULL DEFAULT 0")
            self._add_column(conn, "profile", "level_updated_reason", "TEXT NOT NULL DEFAULT ''")
            self._add_column(conn, "words", "cefr_band", "TEXT NOT NULL DEFAULT ''")
            self._add_column(
                conn, "reading_quizzes", "productions_json", "TEXT NOT NULL DEFAULT '[]'"
            )
            self._add_column(conn, "reading_quizzes", "level_confidence", "REAL NOT NULL DEFAULT 0")
            self._add_column(
                conn, "reading_quizzes", "posterior_json", "TEXT NOT NULL DEFAULT '{}'"
            )
            self._add_column(conn, "reading_quizzes", "practice_mode", "INTEGER NOT NULL DEFAULT 0")
            self._add_column(conn, "reading_quizzes", "stage", "INTEGER NOT NULL DEFAULT 2")

        if version < 8:
            self._add_column(conn, "review_logs", "answer_text", "TEXT NOT NULL DEFAULT ''")
            self._add_column(conn, "review_logs", "verdict", "TEXT NOT NULL DEFAULT ''")
            self._add_column(conn, "review_logs", "distance", "INTEGER NOT NULL DEFAULT 0")

        if version < 9:
            # M7：旧文章统一视为 AI 生成；新表由上面的 CREATE TABLE IF NOT EXISTS 补齐。
            self._add_column(conn, "articles", "source", "TEXT NOT NULL DEFAULT 'generated'")
            self._add_column(conn, "articles", "source_url", "TEXT NOT NULL DEFAULT ''")
            self._add_column(conn, "articles", "source_ref", "TEXT NOT NULL DEFAULT ''")
            self._add_column(conn, "review_logs", "mode", "TEXT NOT NULL DEFAULT ''")

        if version < 10:
            # 查词支持多义项：旧词条保留原释义，version=0 让查词时按需重查。
            self._add_column(conn, "words", "definition_version", "INTEGER NOT NULL DEFAULT 0")

        # articles.metrics_json 属于 v8 的 M5.3 增量，但 M5.1 已经把 v8 发布出去了
        # （当时还没有这一列），所以不能放进 `version < 8` 分支，否则已经是 v8 的
        # 旧库永远补不上。_add_column 自己用 PRAGMA table_info 判断，重复启动幂等。
        self._add_column(conn, "articles", "metrics_json", "TEXT NOT NULL DEFAULT ''")
        # 同理：阅读检测的补充说明也是 v8 内的幂等增量，旧库启动后补列即可。
        self._add_column(conn, "reading_quizzes", "choice_notes_json", "TEXT NOT NULL DEFAULT '[]'")

        # words.cefr_band 是 v6 才加的列，索引必须等 ALTER TABLE 之后再建。
        conn.execute("CREATE INDEX IF NOT EXISTS idx_words_band ON words(cefr_band)")

        if version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        """幂等加列：新库的全新表已经有该列，这里会跳过。"""
        existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

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

    @property
    def quizzes(self) -> QuizRepo:
        if self._quizzes is None:
            self._quizzes = QuizRepo(self)
        return self._quizzes

    @property
    def evidence(self) -> LevelEvidenceRepo:
        if self._evidence is None:
            self._evidence = LevelEvidenceRepo(self)
        return self._evidence

    @property
    def weekly_reviews(self) -> WeeklyReviewRepo:
        if self._weekly is None:
            self._weekly = WeeklyReviewRepo(self)
        return self._weekly

    @property
    def ai_calls(self) -> AICallRepo:
        if self._ai_calls is None:
            self._ai_calls = AICallRepo(self)
        return self._ai_calls

    @property
    def writing_attempts(self) -> WritingAttemptRepo:
        if self._writing is None:
            self._writing = WritingAttemptRepo(self)
        return self._writing

    @property
    def real_texts(self) -> RealTextRepo:
        if self._real_texts is None:
            self._real_texts = RealTextRepo(self)
        return self._real_texts

    @property
    def content_sources(self) -> ContentSourceRepo:
        if self._content_sources is None:
            self._content_sources = ContentSourceRepo(self)
        return self._content_sources
