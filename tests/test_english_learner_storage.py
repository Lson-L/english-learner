"""存储层单测：建表幂等、CRUD 往返、唯一约束、级联删除、统计。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from english_learner.models.entities import (
    Article,
    Profile,
    QuizQuestion,
    ReadingQuiz,
    Word,
    now_iso,
)
from english_learner.storage.db import SCHEMA_VERSION, Database, DuplicateError


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def make_article(**overrides) -> Article:
    values = {
        "article_date": "2026-11-18",
        "title": "Small Habits",
        "content": "Small habits are powerful.\n\nThey add up over time.",
        "level": "B1",
        "topic": "psychology",
        "word_count": 9,
        "target_words": ("habits",),
        "created_at": now_iso(),
    }
    values.update(overrides)
    return Article(**values)


def make_word(**overrides) -> Word:
    values = {
        "lemma": "resilient",
        "display_form": "resilient",
        "phonetic": "/rɪˈzɪliənt/",
        "pos": "adj.",
        "meaning_cn": "有韧性的",
        "meaning_en": "able to recover quickly",
        "in_vocabulary": True,
        "due_date": "2026-11-18",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    values.update(overrides)
    return Word(**values)


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "data.db"
    first = Database(path)
    first.close()
    second = Database(path)

    row = second.query_one("PRAGMA user_version")
    assert row is not None
    assert int(row[0]) == SCHEMA_VERSION
    tables = {
        str(item["name"])
        for item in second.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "profile",
        "articles",
        "words",
        "word_forms",
        "highlights",
        "review_logs",
        "daily_stats",
        "reading_quizzes",
        "writing_attempts",
        "real_texts",
        "real_text_history",
        "content_sources",
    } <= tables
    second.close()


def test_migrate_v1_database_adds_new_columns(tmp_path: Path) -> None:
    """M3 之前的数据库要能原地升级，旧词条/复习记录不能丢。"""
    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lemma TEXT NOT NULL COLLATE NOCASE UNIQUE,
            display_form TEXT NOT NULL DEFAULT '',
            phonetic TEXT NOT NULL DEFAULT '',
            pos TEXT NOT NULL DEFAULT '',
            meaning_cn TEXT NOT NULL DEFAULT '',
            meaning_en TEXT NOT NULL DEFAULT '',
            example_en TEXT NOT NULL DEFAULT '',
            example_cn TEXT NOT NULL DEFAULT '',
            context_sentence TEXT NOT NULL DEFAULT '',
            article_id INTEGER,
            status TEXT NOT NULL DEFAULT 'new',
            ease REAL NOT NULL DEFAULT 2.5,
            interval_days INTEGER NOT NULL DEFAULT 0,
            repetitions INTEGER NOT NULL DEFAULT 0,
            due_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE review_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            prev_interval INTEGER NOT NULL,
            next_interval INTEGER NOT NULL,
            reviewed_at TEXT NOT NULL
        );
        INSERT INTO words (lemma, meaning_cn, created_at, updated_at)
            VALUES ('legacy', '旧词条', '2026-11-01T10:00:00', '2026-11-01T10:00:00');
        INSERT INTO review_logs (word_id, rating, prev_interval, next_interval, reviewed_at)
            VALUES (1, 3, 0, 1, '2026-11-01T10:00:00');
        PRAGMA user_version = 1;
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        word_columns = {str(row["name"]) for row in db.query_all("PRAGMA table_info(words)")}
        log_columns = {str(row["name"]) for row in db.query_all("PRAGMA table_info(review_logs)")}
        profile_columns = {str(row["name"]) for row in db.query_all("PRAGMA table_info(profile)")}
        quiz_columns = {
            str(row["name"]) for row in db.query_all("PRAGMA table_info(reading_quizzes)")
        }
        assert "mnemonic" in word_columns
        assert "in_vocabulary" in word_columns
        assert {"note", "advice", "answer_text", "verdict", "distance"} <= log_columns
        assert {"pending_level", "pending_count"} <= profile_columns
        assert {
            "error_types_json",
            "short_answer_text",
            "short_answer_score",
            "self_report",
            "recommended_level",
            "pending_level",
            "pending_count",
            "evidence_ratio",
        } <= quiz_columns

        word = db.words.get_by_lemma("legacy")
        assert word is not None and word.meaning_cn == "旧词条"
        assert word.mnemonic == ""
        # 旧版本查词即入本，升级时统一保留为“已加入”
        assert word.in_vocabulary is True

        assert word.id is not None
        logs = db.reviews.list_for_word(word.id)
        assert len(logs) == 1
        assert logs[0].note == "" and logs[0].advice == ""

        row = db.query_one("PRAGMA user_version")
        assert row is not None and int(row[0]) == SCHEMA_VERSION
        forms = db.query_one(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'word_forms'"
        )
        assert forms is not None
        quizzes = db.query_one(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'reading_quizzes'"
        )
        assert quizzes is not None
    finally:
        db.close()


def test_migrate_v4_database_adds_m39_columns(tmp_path: Path) -> None:
    """已存在 v4 检测数据时，原地加列不能丢题目/结果。"""
    path = tmp_path / "v4.db"
    questions = [
        {
            "prompt": f"Q{index}?",
            "options": ["a", "b", "c"],
            "answer_index": 0,
            "explanation": "",
            "skill": "detail",
            "difficulty": "B1",
            "kind": "choice",
        }
        for index in range(1, 6)
    ]
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cefr_level TEXT NOT NULL,
            vocab_estimate INTEGER NOT NULL DEFAULT 0,
            strengths_json TEXT NOT NULL DEFAULT '[]',
            weaknesses_json TEXT NOT NULL DEFAULT '[]',
            comment TEXT NOT NULL DEFAULT '',
            assessed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_date TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            level TEXT NOT NULL,
            topic TEXT NOT NULL DEFAULT '',
            word_count INTEGER NOT NULL DEFAULT 0,
            target_words_json TEXT NOT NULL DEFAULT '[]',
            read_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE reading_quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL UNIQUE,
            article_date TEXT NOT NULL,
            level TEXT NOT NULL,
            questions_json TEXT NOT NULL,
            answers_json TEXT NOT NULL DEFAULT '[]',
            explanations_json TEXT NOT NULL DEFAULT '[]',
            score INTEGER NOT NULL DEFAULT 0,
            previous_level TEXT NOT NULL DEFAULT '',
            result_level TEXT NOT NULL DEFAULT '',
            feedback TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
        INSERT INTO profile (
            id, cefr_level, vocab_estimate, strengths_json, weaknesses_json,
            comment, assessed_at, updated_at
        ) VALUES (1, 'B1', 3800, '[]', '[]', '旧画像', '2026-11-01T10:00:00',
                  '2026-11-01T10:00:00');
        INSERT INTO articles (
            article_date, title, content, level, topic, word_count, created_at
        ) VALUES ('2026-11-19', 'Old Article', 'Old content.', 'B1', 'test', 2,
                  '2026-11-19T08:00:00');
        """
    )
    conn.execute(
        """
        INSERT INTO reading_quizzes (
            article_id, article_date, level, questions_json, answers_json,
            explanations_json, score, previous_level, result_level, feedback,
            created_at, completed_at
        ) VALUES (1, '2026-11-19', 'B1', ?, '[0, 0, 0, 0, 0]', '[]', 5, 'B1',
                  'B2', '旧结果', '2026-11-19T09:00:00', '2026-11-19T09:10:00')
        """,
        (json.dumps(questions, ensure_ascii=False),),
    )
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        version_row = db.query_one("PRAGMA user_version")
        assert version_row is not None and int(version_row[0]) == SCHEMA_VERSION

        profile = db.profiles.get()
        assert profile is not None
        assert profile.cefr_level == "B1"
        assert profile.comment == "旧画像"
        assert profile.pending_level == ""
        assert profile.pending_count == 0

        quiz = db.quizzes.get_by_article(1)
        assert quiz is not None
        assert quiz.completed is True
        assert quiz.choice_total == 5
        assert quiz.short_total == 0
        assert quiz.total == 5
        assert quiz.score == 5
        assert quiz.result_level == "B2"
        assert quiz.short_answer_text == ""
        assert quiz.short_answer_score == 0
        assert quiz.error_types == ()
        assert quiz.evidence_ratio == 0

        columns = {str(row["name"]) for row in db.query_all("PRAGMA table_info(reading_quizzes)")}
        assert {"short_answer_text", "short_answer_score", "evidence_ratio"} <= columns
    finally:
        db.close()


def test_migrate_v5_database_adds_phase2_columns(tmp_path: Path) -> None:
    """v5（M3.9）数据库原地升级到 v6：新列/新表补齐，旧检测不回填、不阻塞。"""
    path = tmp_path / "v5.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cefr_level TEXT NOT NULL,
            vocab_estimate INTEGER NOT NULL DEFAULT 0,
            strengths_json TEXT NOT NULL DEFAULT '[]',
            weaknesses_json TEXT NOT NULL DEFAULT '[]',
            comment TEXT NOT NULL DEFAULT '',
            assessed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            pending_level TEXT NOT NULL DEFAULT '',
            pending_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lemma TEXT NOT NULL COLLATE NOCASE UNIQUE,
            display_form TEXT NOT NULL DEFAULT '',
            phonetic TEXT NOT NULL DEFAULT '',
            pos TEXT NOT NULL DEFAULT '',
            meaning_cn TEXT NOT NULL DEFAULT '',
            meaning_en TEXT NOT NULL DEFAULT '',
            example_en TEXT NOT NULL DEFAULT '',
            example_cn TEXT NOT NULL DEFAULT '',
            mnemonic TEXT NOT NULL DEFAULT '',
            context_sentence TEXT NOT NULL DEFAULT '',
            article_id INTEGER,
            in_vocabulary INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new',
            ease REAL NOT NULL DEFAULT 2.5,
            interval_days INTEGER NOT NULL DEFAULT 0,
            repetitions INTEGER NOT NULL DEFAULT 0,
            due_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE reading_quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL UNIQUE,
            article_date TEXT NOT NULL,
            level TEXT NOT NULL,
            questions_json TEXT NOT NULL,
            answers_json TEXT NOT NULL DEFAULT '[]',
            explanations_json TEXT NOT NULL DEFAULT '[]',
            error_types_json TEXT NOT NULL DEFAULT '[]',
            short_answer_text TEXT NOT NULL DEFAULT '',
            short_answer_score INTEGER NOT NULL DEFAULT 0,
            self_report TEXT NOT NULL DEFAULT '',
            score INTEGER NOT NULL DEFAULT 0,
            recommended_level TEXT NOT NULL DEFAULT '',
            previous_level TEXT NOT NULL DEFAULT '',
            result_level TEXT NOT NULL DEFAULT '',
            pending_level TEXT NOT NULL DEFAULT '',
            pending_count INTEGER NOT NULL DEFAULT 0,
            feedback TEXT NOT NULL DEFAULT '',
            evidence_ratio REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
        INSERT INTO profile (
            id, cefr_level, vocab_estimate, assessed_at, updated_at,
            pending_level, pending_count
        ) VALUES (1, 'B1', 3800, '2026-11-01T10:00:00', '2026-11-01T10:00:00', 'B2', 1);
        INSERT INTO words (lemma, meaning_cn, in_vocabulary, created_at, updated_at)
            VALUES ('legacy', '旧词条', 1, '2026-11-01T10:00:00', '2026-11-01T10:00:00');
        INSERT INTO reading_quizzes (
            article_id, article_date, level, questions_json, answers_json,
            score, previous_level, result_level, feedback, created_at, completed_at
        ) VALUES (1, '2026-11-19', 'B1', '[]', '[0,0,0]', 3, 'B1', 'B1',
                  '旧结果', '2026-11-19T09:00:00', '2026-11-19T09:10:00');
        PRAGMA user_version = 5;
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        version_row = db.query_one("PRAGMA user_version")
        assert version_row is not None and int(version_row[0]) == SCHEMA_VERSION

        profile = db.profiles.get()
        assert profile is not None
        assert profile.cefr_level == "B1"
        assert profile.pending_level == "B2"
        assert profile.level_confidence == 0
        assert profile.level_updated_reason == ""

        word = db.words.get_by_lemma("legacy")
        assert word is not None
        assert word.cefr_band == ""

        quiz = db.quizzes.get_by_article(1)
        assert quiz is not None
        assert quiz.completed is True
        assert quiz.stage == 2  # 旧检测默认视为完整卷
        assert quiz.productions == ()
        assert quiz.level_confidence == 0
        assert quiz.posterior == ()
        assert quiz.practice_mode is False

        tables = {
            str(item["name"])
            for item in db.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"level_evidence", "weekly_reviews"} <= tables
        # 旧检测不回填证据，避免把 M3.9 的旧结果当成新观测。
        assert db.evidence.count() == 0
    finally:
        db.close()


def test_migrate_v7_database_adds_review_evidence_columns(tmp_path: Path) -> None:
    """v7 旧库升级到 v8：review_logs 加证据列，旧复习记录不丢、不回填。"""
    path = tmp_path / "v7.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE review_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            prev_interval INTEGER NOT NULL,
            next_interval INTEGER NOT NULL,
            reviewed_at TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            advice TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO review_logs (word_id, rating, prev_interval, next_interval, reviewed_at, note)
            VALUES (1, 2, 0, 1, '2026-11-19T10:00:00', '旧记录');
        PRAGMA user_version = 7;
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        version_row = db.query_one("PRAGMA user_version")
        assert version_row is not None and int(version_row[0]) == SCHEMA_VERSION
        columns = {str(row["name"]) for row in db.query_all("PRAGMA table_info(review_logs)")}
        assert {"answer_text", "verdict", "distance"} <= columns

        word = db.words.upsert(make_word())
        assert word.id is not None
        legacy = db.reviews.list_for_word(1)
        assert len(legacy) == 1
        assert legacy[0].note == "旧记录"
        assert (legacy[0].answer_text, legacy[0].verdict, legacy[0].distance) == ("", "", 0)

        logged = db.reviews.log(
            word_id=word.id,
            rating=2,
            prev_interval=0,
            next_interval=1,
            answer_text="resilent",
            verdict="near",
            distance=1,
        )
        assert logged.answer_text == "resilent"
        assert logged.verdict == "near"
        assert logged.distance == 1
        assert db.reviews.list_for_word(word.id)[0].verdict == "near"
    finally:
        db.close()


def test_level_evidence_and_weekly_review_roundtrip(db: Database) -> None:
    from english_learner.models.entities import (
        BandReviewStat,
        EvidenceObservation,
        LevelEvidence,
        WeeklyReview,
    )

    article = db.articles.insert(make_article())
    assert article.id is not None
    quiz = db.quizzes.insert(
        ReadingQuiz(
            article_id=article.id,
            article_date=article.article_date,
            level="B1",
            questions=(QuizQuestion(prompt="Q1?", options=("a", "b"), answer_index=1),),
            stage=1,
            created_at=now_iso(),
        )
    )
    assert quiz.id is not None
    evidence = db.evidence.insert(
        LevelEvidence(
            source="quiz",
            observed_at="2026-11-19T20:00:00",
            evidence_date="2026-11-19",
            article_level="B1",
            quiz_id=quiz.id,
            choice_correct=1,
            choice_total=1,
            observations=(
                EvidenceObservation("B2", True, 0.5, "blind"),
                EvidenceObservation("B1", True, 1.0),
            ),
            posterior=(0.05, 0.2, 0.6, 0.15),
            confidence=0.6,
        )
    )

    assert evidence.id is not None
    loaded = db.evidence.list_recent(1)[0]
    assert loaded.quiz_id == quiz.id
    assert loaded.evidence_date == "2026-11-19"
    assert loaded.observations[0] == EvidenceObservation("B2", True, 0.5, "blind")
    assert loaded.posterior_map["B2"] == pytest.approx(0.6)
    assert db.evidence.count_on("2026-11-19") == 1
    assert db.evidence.count_on("2026-11-20") == 0

    review = db.weekly_reviews.save(
        WeeklyReview(
            week_start="2026-11-16",
            summary="本周稳定。",
            suggested_level="B2",
            focus=("推断题",),
            quiz_count=1,
            review_count=3,
            words_added=2,
            confidence=0.7,
            created_at="2026-11-19T21:00:00",
        )
    )
    assert review.id is not None
    assert db.weekly_reviews.get_by_week("2026-11-16") == review
    assert db.weekly_reviews.list_recent(1) == [review]

    applied = db.weekly_reviews.mark_applied("2026-11-16", "B2", "2026-11-19T22:00:00")
    assert applied is not None and applied.applied is True
    assert applied.applied_level == "B2"

    # 删除文章会级联删检测，但证据保留（quiz_id 置空）。
    db.articles.delete_by_date(article.article_date)
    kept = db.evidence.list_recent(1)
    assert len(kept) == 1 and kept[0].quiz_id is None
    assert isinstance(BandReviewStat("B2", 2, 1), BandReviewStat)


def test_cache_only_word_is_not_in_vocabulary(db: Database) -> None:
    """双击查词只写缓存：不出现在生词本/复习队列，显式加入后才算。"""
    word = db.words.upsert(make_word(lemma="cacheonly", in_vocabulary=False))
    assert word.id is not None

    assert db.words.count() == 0
    assert db.words.count(include_cache=True) == 1
    assert db.words.list_all() == []
    assert db.words.list_due("2026-11-18") == []
    assert db.stats.count_due("2026-11-18") == 0

    marked = db.words.mark_in_vocabulary(word.id)
    assert marked is not None and marked.in_vocabulary is True
    assert db.words.count() == 1
    assert [item.lemma for item in db.words.list_due("2026-11-18")] == ["cacheonly"]
    assert db.stats.count_due("2026-11-18") == 1


def test_word_form_cache_mnemonic_and_cascade(db: Database) -> None:
    word = db.words.upsert(make_word(lemma="go", display_form="went"))
    assert word.id is not None

    db.words.remember_form("Went", word.id)
    cached = db.words.get_by_form("went")
    assert cached is not None and cached.id == word.id
    assert [form.form for form in db.words.list_all_forms()] == ["went"]

    updated = db.words.update_mnemonic(word.id, "go-went-gone：出门又走远")
    assert updated is not None
    assert updated.mnemonic == "go-went-gone：出门又走远"

    assert db.words.update_mnemonic(9999, "x") is None
    assert db.words.delete(word.id) is True
    assert db.words.get_by_form("went") is None


def test_quiz_roundtrip_result_and_cascade(db: Database) -> None:
    article = db.articles.insert(make_article())
    assert article.id is not None
    questions = (
        QuizQuestion(prompt="Q1?", options=("a", "b", "c"), answer_index=1, explanation="解析"),
        QuizQuestion(prompt="Q2?", options=("a", "b", "c"), answer_index=2),
        QuizQuestion(prompt="Q3?", options=("a", "b", "c"), answer_index=0),
        QuizQuestion(
            prompt="Summarize it.",
            kind="short",
            skill="writing",
            difficulty="B1",
            reference_answer="A reference.",
        ),
    )
    stored = db.quizzes.insert(
        ReadingQuiz(
            article_id=article.id,
            article_date=article.article_date,
            level="B1",
            questions=questions,
            created_at=now_iso(),
        )
    )

    assert stored.id is not None
    assert stored.completed is False
    loaded = db.quizzes.get_by_article(article.id)
    assert loaded == stored
    assert [question.prompt for question in loaded.questions] == [  # type: ignore[union-attr]
        "Q1?",
        "Q2?",
        "Q3?",
        "Summarize it.",
    ]

    with pytest.raises(DuplicateError):
        db.quizzes.insert(stored)

    completed = db.quizzes.save_result(
        stored.id,
        answers=(1, 2, 0),
        explanations=("对了", "", "再看原文", "短答反馈"),
        error_types=("none", "vocabulary", "none", "short_answer"),
        short_answer_text="Habits add up over time.",
        short_answer_score=1,
        self_report="hard",
        score=3,
        recommended_level="B2",
        previous_level="B1",
        result_level="B1",
        pending_level="B2",
        pending_count=1,
        feedback="细节题需要再练。",
        evidence_ratio=0.5,
        completed_at="2026-11-19T20:00:00",
    )
    assert completed.completed is True
    assert completed.score == 3
    assert completed.total == 4
    assert completed.percent == 75
    assert completed.previous_level == "B1"
    assert completed.result_level == "B1"
    assert completed.recommended_level == "B2"
    assert completed.level_changed is False
    assert completed.waiting_for_upgrade is True
    assert completed.pending_level == "B2"
    assert completed.pending_count == 1
    assert completed.answers == (1, 2, 0)
    assert completed.explanations == ("对了", "", "再看原文", "短答反馈")
    assert completed.error_types == ("none", "vocabulary", "none", "short_answer")
    assert completed.short_answer_text == "Habits add up over time."
    assert completed.short_answer_score == 1
    assert completed.self_report == "hard"
    assert completed.evidence_ratio == pytest.approx(0.5)
    assert db.quizzes.list_all() == [completed]

    # 文章被替换/删除时，题目随外键级联清理
    db.articles.delete_by_date(article.article_date)
    assert db.quizzes.get_by_article(article.id) is None


def test_profile_roundtrip_and_overwrite(db: Database) -> None:
    profile = Profile(
        cefr_level="B1",
        vocab_estimate=3800,
        strengths=("阅读理解", "基础语法"),
        weaknesses=("学术词汇",),
        comment="整体 B1 偏上",
        assessed_at="2026-11-18T10:00:00",
        pending_level="B2",
        pending_count=1,
    )
    stored = db.profiles.save(profile)

    assert stored.updated_at  # 空值会被自动补上
    loaded = db.profiles.get()
    assert loaded == stored
    assert loaded is not None
    assert loaded.strengths == ("阅读理解", "基础语法")
    assert loaded.pending_level == "B2"
    assert loaded.pending_count == 1

    db.profiles.save(Profile(cefr_level="B2", assessed_at="2026-11-19T10:00:00"))
    updated = db.profiles.get()
    assert updated is not None
    assert updated.cefr_level == "B2"
    row = db.query_one("SELECT COUNT(*) AS n FROM profile")
    assert row is not None and int(row["n"]) == 1


def test_article_insert_get_and_duplicate(db: Database) -> None:
    stored = db.articles.insert(make_article())
    assert stored.id is not None
    assert stored.target_words == ("habits",)

    assert db.articles.get_by_date("2026-11-18") == stored
    assert db.articles.get_by_id(stored.id) == stored
    assert [item.id for item in db.articles.list_recent()] == [stored.id]

    with pytest.raises(DuplicateError):
        db.articles.insert(make_article(title="另一篇"))

    assert db.articles.delete_by_date("2026-11-18") is True
    assert db.articles.get_by_date("2026-11-18") is None
    assert db.articles.delete_by_date("2026-11-18") is False


def test_word_upsert_is_case_insensitive(db: Database) -> None:
    stored = db.words.upsert(make_word(lemma="Resilient"))
    assert stored.id is not None

    again = db.words.upsert(make_word(lemma="resilient", meaning_cn="更新的释义"))
    assert again.id == stored.id
    assert again.meaning_cn == "更新的释义"

    assert db.words.count() == 1
    assert db.words.get_by_lemma("RESILIENT") is not None
    assert db.words.count_unmastered() == 1


def test_highlight_is_idempotent_and_cascades(db: Database) -> None:
    article = db.articles.insert(make_article())
    word = db.words.upsert(make_word(article_id=article.id))
    assert article.id is not None and word.id is not None

    inserted = db.words.add_highlight(
        article_id=article.id,
        word_id=word.id,
        start_offset=0,
        end_offset=5,
        sentence="Small habits are powerful.",
    )
    assert inserted is True
    duplicate = db.words.add_highlight(
        article_id=article.id,
        word_id=word.id,
        start_offset=0,
        end_offset=5,
    )
    assert duplicate is False
    assert len(db.words.list_highlights(article.id)) == 1

    db.articles.delete_by_date("2026-11-18")
    assert db.words.list_highlights(article.id) == []
    # 生词本不依赖某篇文章
    assert db.words.get_by_id(word.id) is not None
    assert db.words.get_by_id(word.id).article_id is None  # type: ignore[union-attr]


def test_replace_by_date_clears_highlights_and_keeps_words(db: Database) -> None:
    old = db.articles.insert(make_article())
    word = db.words.upsert(make_word(article_id=old.id))
    assert old.id is not None and word.id is not None
    db.words.add_highlight(
        article_id=old.id,
        word_id=word.id,
        start_offset=0,
        end_offset=5,
    )

    new = db.articles.replace_by_date(
        make_article(title="New One", content="A brand new article.", word_count=4)
    )

    assert new.id is not None and new.id != old.id
    stored = db.articles.get_by_date("2026-11-18")
    assert stored is not None and stored.title == "New One"
    assert db.words.list_highlights(old.id) == []
    assert db.words.get_by_id(word.id) is not None


def test_list_highlighted_words_and_created_on(db: Database) -> None:
    article = db.articles.insert(make_article())
    first = db.words.upsert(
        make_word(lemma="alpha", created_at="2026-11-18T09:00:00", updated_at="2026-11-18T09:00:00")
    )
    second = db.words.upsert(
        make_word(lemma="beta", created_at="2026-11-18T10:00:00", updated_at="2026-11-18T10:00:00")
    )
    db.words.upsert(
        make_word(lemma="old", created_at="2026-11-17T10:00:00", updated_at="2026-11-17T10:00:00")
    )
    assert article.id is not None and first.id is not None and second.id is not None
    # beta 先标在高亮位置前，验证按偏移而不是按插入顺序返回
    db.words.add_highlight(article_id=article.id, word_id=second.id, start_offset=0, end_offset=4)
    db.words.add_highlight(article_id=article.id, word_id=first.id, start_offset=10, end_offset=15)

    assert [word.lemma for word in db.words.list_highlighted_words(article.id)] == [
        "beta",
        "alpha",
    ]
    assert [word.lemma for word in db.words.list_created_on("2026-11-18")] == ["alpha", "beta"]


def test_word_update_meaning_and_delete_cascade(db: Database) -> None:
    article = db.articles.insert(make_article())
    word = db.words.upsert(make_word(article_id=article.id))
    assert article.id is not None and word.id is not None
    db.words.add_highlight(
        article_id=article.id,
        word_id=word.id,
        start_offset=0,
        end_offset=5,
    )
    db.reviews.log(word_id=word.id, rating=3, prev_interval=0, next_interval=1)

    updated = db.words.update_meaning(word.id, meaning_cn="新的释义", meaning_en="a new meaning")
    assert updated is not None
    assert updated.meaning_cn == "新的释义"
    assert updated.meaning_en == "a new meaning"
    assert db.words.update_meaning(9999, meaning_cn="x") is None

    assert db.words.delete(word.id) is True
    assert db.words.get_by_id(word.id) is None
    assert db.words.list_highlights(article.id) == []
    assert db.reviews.list_for_word(word.id) == []
    assert db.words.delete(word.id) is False


def test_review_repo_logs_recent_and_count(db: Database) -> None:
    word = db.words.upsert(make_word())
    assert word.id is not None

    first = db.reviews.log(
        word_id=word.id,
        rating=3,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-18T09:00:00",
    )
    second = db.reviews.log(
        word_id=word.id,
        rating=4,
        prev_interval=1,
        next_interval=19,
        reviewed_at="2026-11-19T09:00:00",
    )

    assert first.id is not None and second.id is not None
    logs = db.reviews.list_for_word(word.id)
    assert [log.rating for log in logs] == [4, 3]  # 最新的在前
    assert db.reviews.list_recent(1) == [second]
    assert db.reviews.count_for_date("2026-11-18") == 1
    assert db.reviews.count_for_date("2026-11-19") == 1
    assert db.reviews.count_for_date("2026-11-20") == 0


def test_review_repo_help_logs_filter_order_and_limit(db: Database) -> None:
    """求助记录只保留有原因/建议的条目，按时间倒序，limit 与并列时间稳定。"""
    word = db.words.upsert(make_word())
    assert word.id is not None

    db.reviews.log(
        word_id=word.id,
        rating=3,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-19T10:00:00",
    )
    note_only = db.reviews.log(
        word_id=word.id,
        rating=2,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-19T09:00:00",
        note="只记得大概意思",
    )
    advice_only = db.reviews.log(
        word_id=word.id,
        rating=1,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-19T11:00:00",
        advice="拆词根记",
    )
    # note/advice 全为空白：log() 会 strip 成空串，等同于普通记录。
    db.reviews.log(
        word_id=word.id,
        rating=1,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-19T12:00:00",
        note="   ",
        advice="  ",
    )
    # 相同时间戳：靠 id DESC 保证后写入的排前面。
    tie_first = db.reviews.log(
        word_id=word.id,
        rating=2,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-19T13:00:00",
        note="并列 A",
    )
    tie_second = db.reviews.log(
        word_id=word.id,
        rating=2,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-19T13:00:00",
        note="并列 B",
    )

    assert db.reviews.count_help_for_word(word.id) == 4
    logs = db.reviews.list_help_for_word(word.id)
    assert [log.id for log in logs] == [
        tie_second.id,
        tie_first.id,
        advice_only.id,
        note_only.id,
    ]

    limited = db.reviews.list_help_for_word(word.id, limit=2)
    assert [log.id for log in limited] == [tie_second.id, tie_first.id]

    assert db.reviews.list_help_for_word(9999) == []
    assert db.reviews.count_help_for_word(9999) == 0


def test_review_band_stats_and_stats_summary(db: Database) -> None:
    easier = db.words.upsert(make_word(lemma="easyword", cefr_band="A2"))
    harder = db.words.upsert(make_word(lemma="hardword", cefr_band="C1"))
    unbanded = db.words.upsert(make_word(lemma="unbanded", cefr_band=""))
    assert easier.id is not None and harder.id is not None and unbanded.id is not None

    db.reviews.log(
        word_id=easier.id,
        rating=4,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-19T09:00:00",
    )
    db.reviews.log(
        word_id=easier.id,
        rating=3,
        prev_interval=1,
        next_interval=6,
        reviewed_at="2026-11-19T10:00:00",
    )
    db.reviews.log(
        word_id=harder.id,
        rating=1,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-19T11:00:00",
    )
    db.reviews.log(
        word_id=unbanded.id,
        rating=4,
        prev_interval=0,
        next_interval=1,
        reviewed_at="2026-11-19T12:00:00",
    )

    stats = {item.band: item for item in db.reviews.list_band_stats()}
    assert stats["A2"].total == 2 and stats["A2"].known == 2
    assert stats["C1"].total == 1 and stats["C1"].known == 0
    assert "unbanded" not in {item.band for item in db.reviews.list_band_stats()}
    weekly = {item.band: item for item in db.reviews.list_band_stats("2026-11-19", "2026-11-19")}
    assert weekly["A2"].total == 2 and weekly["C1"].total == 1
    assert db.reviews.list_band_stats("2026-11-20", "2026-11-21") == []
    assert db.reviews.count_between("2026-11-19", "2026-11-19") == 4
    assert db.reviews.count_between("2026-11-20", "2026-11-21") == 0

    db.stats.bump("2026-11-18", article_read=1, words_added=2, reviews_done=3)
    db.stats.bump("2026-11-19", article_read=1, words_added=1, reviews_done=2)
    summary = db.stats.summarize_between("2026-11-16", "2026-11-22")
    assert summary == {"articles": 2, "words": 3, "reviews": 5}
    assert db.stats.summarize_between("2026-11-01", "2026-11-02") == {
        "articles": 0,
        "words": 0,
        "reviews": 0,
    }


def test_save_schedule_only_touches_scheduling_fields(db: Database) -> None:
    word = db.words.upsert(make_word(meaning_cn="原释义"))
    assert word.id is not None

    updated = db.words.save_schedule(
        replace(
            word,
            status="learning",
            ease=2.7,
            interval_days=6,
            repetitions=2,
            due_date="2026-11-25",
            updated_at="2026-11-19T12:00:00",
        )
    )

    assert updated.meaning_cn == "原释义"
    assert updated.status == "learning"
    assert updated.ease == pytest.approx(2.7)
    assert updated.interval_days == 6
    assert updated.repetitions == 2
    assert updated.due_date == "2026-11-25"
    assert updated.updated_at == "2026-11-19T12:00:00"


def test_stats_bump_streak_and_due(db: Database) -> None:
    db.stats.bump("2026-11-15", article_read=1)
    db.stats.bump("2026-11-16", words_added=2)
    stats = db.stats.bump("2026-11-17", reviews_done=1)
    assert stats.reviews_done == 1

    db.stats.bump("2026-11-17", reviews_done=1)
    assert db.stats.get("2026-11-17").reviews_done == 2

    assert db.stats.streak(date(2026, 11, 17)) == 3
    assert db.stats.streak(date(2026, 11, 18)) == 3  # 今天没学，从昨天数
    assert db.stats.streak(date(2026, 11, 20)) == 0

    db.words.upsert(make_word(lemma="due", due_date="2026-11-16"))
    db.words.upsert(make_word(lemma="future", due_date="2026-12-01"))
    db.words.upsert(make_word(lemma="mastered", due_date="2026-11-01", status="mastered"))
    assert db.stats.count_due("2026-11-17") == 1
    assert [item.lemma for item in db.words.list_due("2026-11-17")] == ["due"]


# --------------------------------------------------- M5.3 可读性指标（v8 增量）
def test_article_metrics_json_roundtrip(db: Database) -> None:
    metrics = {"word_total": 120, "unknown_count": 6, "wordlist_size": 3000}
    article = db.articles.insert(make_article(metrics=metrics))

    stored = db.articles.get_by_date(article.article_date)
    assert stored is not None
    assert stored.metrics == metrics

    replaced = db.articles.replace_by_date(
        make_article(article_date="2026-11-19", metrics={"word_total": 200})
    )
    assert replaced.metrics == {"word_total": 200}


def test_article_without_metrics_reads_as_none(db: Database) -> None:
    article = db.articles.insert(make_article())
    stored = db.articles.get_by_id(int(article.id or 0))
    assert stored is not None
    assert stored.metrics is None


def test_migrate_v8_database_adds_article_metrics_column(tmp_path: Path) -> None:
    """M5.1 发布的 v8 旧库没有 articles.metrics_json，升级时幂等补列且旧文章不丢。"""
    path = tmp_path / "v8.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_date TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            level TEXT NOT NULL,
            topic TEXT NOT NULL DEFAULT '',
            word_count INTEGER NOT NULL DEFAULT 0,
            target_words_json TEXT NOT NULL DEFAULT '[]',
            read_at TEXT,
            created_at TEXT NOT NULL
        );
        INSERT INTO articles
            (article_date, title, content, level, topic, word_count, target_words_json, created_at)
            VALUES ('2026-11-19', '旧文章', 'Old content here.', 'B1', 'science', 3, '[]',
                    '2026-11-19T08:00:00');
        PRAGMA user_version = 8;
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        columns = {str(row["name"]) for row in db.query_all("PRAGMA table_info(articles)")}
        assert "metrics_json" in columns

        legacy = db.articles.get_by_date("2026-11-19")
        assert legacy is not None
        assert legacy.title == "旧文章"
        assert legacy.metrics is None

        db.articles.insert(make_article(article_date="2026-11-20", metrics={"word_total": 9}))
        added = db.articles.get_by_date("2026-11-20")
        assert added is not None and added.metrics == {"word_total": 9}
    finally:
        db.close()


def test_v8_database_without_writing_attempts_gets_new_table(tmp_path: Path) -> None:
    """M5.4 在 v8 上新增 writing_attempts：旧库启动时由 CREATE IF NOT EXISTS 补齐。"""
    path = tmp_path / "data.db"
    db = Database(path)
    db.execute("DROP TABLE writing_attempts")
    db.close()

    reopened = Database(path)

    assert int(reopened.query_one("PRAGMA user_version")[0]) == SCHEMA_VERSION
    tables = {
        str(item["name"])
        for item in reopened.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "writing_attempts" in tables
    reopened.close()


def test_list_known_lemmas_uses_vocabulary_and_interval(db: Database) -> None:
    db.words.upsert(make_word(lemma="added"))
    db.words.upsert(make_word(lemma="mature", in_vocabulary=False, interval_days=45))
    db.words.upsert(make_word(lemma="fresh", in_vocabulary=False, interval_days=3))

    known = db.words.list_known_lemmas()
    assert "added" in known
    assert "mature" in known
    assert "fresh" not in known


def test_review_last_reviews_returns_latest_per_word(db: Database) -> None:
    """复习页排卡要一次拿到每个词最近一次 (rating, verdict)，避免 N+1 查询。"""
    alpha = db.words.upsert(make_word(lemma="alpha"))
    beta = db.words.upsert(make_word(lemma="beta"))
    assert alpha.id is not None and beta.id is not None

    db.reviews.log(word_id=alpha.id, rating=2, prev_interval=0, next_interval=1, verdict="near")
    db.reviews.log(word_id=alpha.id, rating=3, prev_interval=1, next_interval=6, verdict="correct")
    db.reviews.log(word_id=beta.id, rating=4, prev_interval=6, next_interval=10)

    assert db.reviews.last_reviews([alpha.id, beta.id, 999]) == {
        alpha.id: (3, "correct"),
        beta.id: (4, ""),
    }
    assert db.reviews.last_reviews([]) == {}


def test_migrate_v8_database_adds_quiz_choice_notes_column(tmp_path: Path) -> None:
    """阅读检测的补充说明是 v8 内幂等增量：旧库启动后补列，不升版本号。"""
    path = tmp_path / "data.db"
    db = Database(path)
    db.execute("ALTER TABLE reading_quizzes DROP COLUMN choice_notes_json")
    db.close()

    reopened = Database(path)
    try:
        rows = reopened.query_all("PRAGMA table_info(reading_quizzes)")
        columns = {str(row["name"]) for row in rows}
        assert "choice_notes_json" in columns
        assert int(reopened.query_one("PRAGMA user_version")["user_version"]) == SCHEMA_VERSION
    finally:
        reopened.close()


def test_article_source_columns_roundtrip(db: Database) -> None:
    """M7：文章来源三列要能完整往返，旧代码不传时默认 generated。"""
    article = db.articles.insert(
        make_article(
            source="real",
            source_url="https://example.com/story",
            source_ref="gutenberg-the-gift-of-the-magi",
        )
    )
    assert article.id is not None
    loaded = db.articles.get_by_date(article.article_date)
    assert loaded is not None
    assert loaded.source == "real"
    assert loaded.source_url == "https://example.com/story"
    assert loaded.source_ref == "gutenberg-the-gift-of-the-magi"

    default = db.articles.insert(make_article(article_date="2026-11-20"))
    assert default.source == "generated"


def test_migrate_v9_database_adds_word_definition_version(tmp_path: Path) -> None:
    """v9 旧库升级到 v10：words 加 definition_version，旧词条保持 0（按需重查）。"""
    path = tmp_path / "v9.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lemma TEXT NOT NULL COLLATE NOCASE UNIQUE,
            display_form TEXT NOT NULL DEFAULT '',
            phonetic TEXT NOT NULL DEFAULT '',
            pos TEXT NOT NULL DEFAULT '',
            meaning_cn TEXT NOT NULL DEFAULT '',
            meaning_en TEXT NOT NULL DEFAULT '',
            example_en TEXT NOT NULL DEFAULT '',
            example_cn TEXT NOT NULL DEFAULT '',
            mnemonic TEXT NOT NULL DEFAULT '',
            context_sentence TEXT NOT NULL DEFAULT '',
            article_id INTEGER,
            in_vocabulary INTEGER NOT NULL DEFAULT 0,
            cefr_band TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            ease REAL NOT NULL DEFAULT 2.5,
            interval_days INTEGER NOT NULL DEFAULT 0,
            repetitions INTEGER NOT NULL DEFAULT 0,
            due_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO words (lemma, meaning_cn, in_vocabulary, created_at, updated_at)
            VALUES ('lie', '位于', 0, '2026-11-27T08:00:00', '2026-11-27T08:00:00');
        PRAGMA user_version = 9;
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        columns = {str(row["name"]) for row in db.query_all("PRAGMA table_info(words)")}
        assert "definition_version" in columns
        old = db.words.get_by_lemma("lie")
        assert old is not None
        assert old.definition_version == 0
        assert old.meaning_cn == "位于"
    finally:
        db.close()


def test_migrate_v8_database_adds_real_text_tables_and_source_columns(tmp_path: Path) -> None:
    """v8 旧库升级到 v9：加来源列 / mode，建三张新表，旧文章默认 generated。"""
    path = tmp_path / "v8.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_date TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            level TEXT NOT NULL,
            topic TEXT NOT NULL DEFAULT '',
            word_count INTEGER NOT NULL DEFAULT 0,
            target_words_json TEXT NOT NULL DEFAULT '[]',
            metrics_json TEXT NOT NULL DEFAULT '',
            read_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE review_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            prev_interval INTEGER NOT NULL,
            next_interval INTEGER NOT NULL,
            reviewed_at TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            advice TEXT NOT NULL DEFAULT '',
            answer_text TEXT NOT NULL DEFAULT '',
            verdict TEXT NOT NULL DEFAULT '',
            distance INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO articles (article_date, title, content, level, created_at)
            VALUES ('2026-11-19', 'Old', 'Old content.', 'B1', '2026-11-19T08:00:00');
        PRAGMA user_version = 8;
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        article_columns = {str(row["name"]) for row in db.query_all("PRAGMA table_info(articles)")}
        assert {"source", "source_url", "source_ref"} <= article_columns
        log_columns = {str(row["name"]) for row in db.query_all("PRAGMA table_info(review_logs)")}
        assert "mode" in log_columns
        tables = {
            str(item["name"])
            for item in db.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"real_texts", "real_text_history", "content_sources"} <= tables

        old = db.articles.get_by_date("2026-11-19")
        assert old is not None
        assert old.source == "generated"
        assert old.source_url == ""
        assert old.source_ref == ""
    finally:
        db.close()


def test_review_log_mode_roundtrip(db: Database) -> None:
    """M7.4：review_logs.mode 要能落库并按词读回。"""
    word = db.words.upsert(
        Word(
            lemma="contextual",
            in_vocabulary=True,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    assert word.id is not None

    db.reviews.log(
        word_id=word.id,
        rating=3,
        prev_interval=0,
        next_interval=1,
        mode="context",
    )
    db.reviews.log(
        word_id=word.id,
        rating=2,
        prev_interval=1,
        next_interval=2,
        mode="spelling",
    )

    logs = db.reviews.list_for_word(word.id)
    assert {log.mode for log in logs} == {"context", "spelling"}
