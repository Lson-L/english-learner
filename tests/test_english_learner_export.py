"""学习数据导出测试：JSON 全量快照与 CSV 分表。"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from english_learner.models.entities import (
    AICall,
    Article,
    EvidenceObservation,
    LevelEvidence,
    Profile,
    QuizQuestion,
    ReadingQuiz,
    WeeklyReview,
    Word,
    WritingAttempt,
    now_iso,
)
from english_learner.services.export_service import ExportService
from english_learner.storage.db import Database


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def make_service(db: Database) -> ExportService:
    return ExportService(
        db.profiles,
        db.articles,
        db.words,
        db.reviews,
        db.stats,
        db.quizzes,
        db.evidence,
        db.weekly_reviews,
        db.ai_calls,
        db.writing_attempts,
    )


def seed(db: Database) -> None:
    db.profiles.save(
        Profile(
            cefr_level="B1",
            vocab_estimate=3800,
            strengths=("阅读理解",),
            assessed_at="2026-11-18T10:00:00",
        )
    )
    article = db.articles.insert(
        Article(
            article_date="2026-11-19",
            title="Small Habits",
            content="Small habits are powerful.\n\nThey add up.",
            level="B1",
            topic="psychology",
            word_count=8,
            target_words=("habits", "powerful"),
            created_at=now_iso(),
        )
    )
    assert article.id is not None
    word = db.words.upsert(
        Word(
            lemma="resilient",
            display_form="resilient",
            meaning_cn="有韧性的",
            meaning_en="able to recover quickly",
            mnemonic="re-silient：像弹簧一样重新弹起来",
            article_id=article.id,
            in_vocabulary=True,
            due_date="2026-11-19",
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    assert word.id is not None
    db.words.remember_form("resilient", word.id)
    db.words.add_highlight(
        article_id=article.id,
        word_id=word.id,
        start_offset=0,
        end_offset=5,
        sentence="Small habits are powerful.",
    )
    db.reviews.log(
        word_id=word.id,
        rating=3,
        prev_interval=0,
        next_interval=1,
        note="和 resilient / resistant 混淆",
        advice="resilient 里有 sil，像弹簧",
        answer_text="resilent",
        verdict="near",
        distance=1,
    )
    db.stats.bump("2026-11-18", article_read=1)
    db.stats.bump("2026-11-19", words_added=1, reviews_done=1)
    db.ai_calls.insert(
        AICall(
            called_at="2026-11-19T20:05:00",
            task="查词",
            model="test-model",
            base_url="https://api.example.com/v1",
            prompt_tokens=120,
            completion_tokens=80,
            total_tokens=200,
            latency_ms=900,
        )
    )
    db.writing_attempts.replace_for_article(
        article.id,
        [
            WritingAttempt(
                article_id=article.id,
                article_date="2026-11-19",
                position=0,
                sentence_en="Small habits are powerful.",
                reference_cn="小习惯很有力量。",
                created_at=now_iso(),
            )
        ],
    )
    db.writing_attempts.save_grades(
        article.id,
        [
            (
                0,
                "Small habits are powerful.",
                {
                    "score": 2,
                    "edited": "Small habits are powerful.",
                    "reason": "很好",
                    "error_types": [],
                },
                2,
                "2026-11-19T21:30:00",
            )
        ],
    )
    db.evidence.insert(
        LevelEvidence(
            source="quiz",
            observed_at="2026-11-19T20:00:00",
            evidence_date="2026-11-19",
            article_level="B1",
            choice_correct=3,
            choice_total=5,
            short_score=1,
            short_total=2,
            weighted_points=2.0,
            evidence_ratio=0.4,
            error_types=("inference",),
            observations=(EvidenceObservation("B2", True, 1.0),),
            posterior=(0.05, 0.2, 0.6, 0.15),
            confidence=0.6,
        )
    )
    db.weekly_reviews.save(
        WeeklyReview(
            week_start="2026-11-16",
            summary="本周阅读稳定性提升。",
            suggested_level="B2",
            focus=("推断题",),
            quiz_count=1,
            review_count=1,
            words_added=1,
            confidence=0.7,
            created_at="2026-11-19T21:00:00",
        )
    )

    quiz = db.quizzes.insert(
        ReadingQuiz(
            article_id=article.id,
            article_date="2026-11-19",
            level="B1",
            questions=(
                QuizQuestion(
                    prompt="What is the main idea?",
                    options=("Small habits matter.", "Habits are hard.", "None of the above."),
                    answer_index=0,
                    explanation="第一段点明习惯的力量。",
                ),
                QuizQuestion(
                    prompt="What do habits do over time?",
                    options=("Add up.", "Disappear.", "Nothing."),
                    answer_index=0,
                ),
                QuizQuestion(
                    prompt="What is the tone?",
                    options=("Positive.", "Angry.", "Sad."),
                    answer_index=0,
                ),
                QuizQuestion(
                    prompt="Summarize the passage in 1-2 sentences.",
                    kind="short",
                    skill="writing",
                    difficulty="B1",
                    reference_answer="Small habits add up over time.",
                ),
            ),
            created_at=now_iso(),
        )
    )
    assert quiz.id is not None
    db.quizzes.save_result(
        quiz.id,
        answers=(0, 0, 1),
        explanations=("对了", "对了", "文章整体是积极的。", "短答基本达意。"),
        error_types=("none", "none", "inference", "short_answer"),
        short_answer_text="Small habits add up over time.",
        short_answer_score=1,
        self_report="hard",
        score=3,
        recommended_level="B2",
        previous_level="B1",
        result_level="B1",
        pending_level="B2",
        pending_count=1,
        feedback="细节理解不错，推断题需要再练。",
        evidence_ratio=0.6,
        completed_at="2026-11-19T20:00:00",
    )


def test_export_json_contains_all_tables(db: Database, tmp_path: Path) -> None:
    seed(db)

    result = make_service(db).export_json(tmp_path / "backup.json")

    path = result.files[0]
    assert path == tmp_path / "backup.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["app"] == "Daily English"
    assert data["version"]
    assert data["exported_at"]
    assert data["counts"] == {
        "profile": 1,
        "articles": 1,
        "words": 1,
        "word_forms": 1,
        "highlights": 1,
        "review_logs": 1,
        "daily_stats": 2,
        "reading_quizzes": 1,
        "level_evidence": 1,
        "weekly_reviews": 1,
        "ai_calls": 1,
        "writing_attempts": 1,
    }
    assert data["profile"]["cefr_level"] == "B1"
    assert data["profile"]["strengths"] == ["阅读理解"]
    assert data["articles"][0]["target_words"] == ["habits", "powerful"]
    assert data["words"][0]["lemma"] == "resilient"
    assert data["words"][0]["meaning_cn"] == "有韧性的"
    assert data["words"][0]["mnemonic"].startswith("re-silient")
    assert data["words"][0]["in_vocabulary"] is True
    assert data["word_forms"][0]["form"] == "resilient"
    assert data["highlights"][0]["start_offset"] == 0
    assert data["review_logs"][0]["rating"] == 3
    assert data["review_logs"][0]["note"] == "和 resilient / resistant 混淆"
    assert data["review_logs"][0]["advice"].startswith("resilient 里有 sil")
    assert data["review_logs"][0]["answer_text"] == "resilent"
    assert data["review_logs"][0]["verdict"] == "near"
    assert data["review_logs"][0]["distance"] == 1
    assert data["daily_stats"][0]["stat_date"] == "2026-11-18"
    assert data["reading_quizzes"][0]["score"] == 3
    assert data["reading_quizzes"][0]["total"] == 4
    assert data["reading_quizzes"][0]["recommended_level"] == "B2"
    assert data["reading_quizzes"][0]["pending_level"] == "B2"
    assert data["reading_quizzes"][0]["short_answer_score"] == 1
    assert data["reading_quizzes"][0]["questions"][0]["prompt"] == "What is the main idea?"
    assert data["level_evidence"][0]["confidence"] == pytest.approx(0.6)
    assert data["weekly_reviews"][0]["suggested_level"] == "B2"
    assert data["ai_calls"][0]["task"] == "查词"
    assert data["ai_calls"][0]["total_tokens"] == 200
    assert data["writing_attempts"][0]["score"] == 2
    assert "回译练习 1" in result.summary()
    assert "生词 1" in result.summary()
    assert "阅读检测 1" in result.summary()
    assert "等级证据 1" in result.summary()
    assert "每周复盘 1" in result.summary()
    assert "AI 调用记录 1" in result.summary()


def test_export_json_adds_suffix_and_overwrites_atomically(db: Database, tmp_path: Path) -> None:
    seed(db)
    service = make_service(db)

    first = service.export_json(tmp_path / "backup")
    second = service.export_json(tmp_path / "backup")

    assert first.files[0].name == "backup.json"
    assert second.files[0] == first.files[0]
    assert not (tmp_path / "backup.json.tmp").exists()


def test_export_csv_writes_one_file_per_table(db: Database, tmp_path: Path) -> None:
    seed(db)
    target = tmp_path / "csv"

    result = make_service(db).export_csv(target)

    assert {path.name for path in result.files} == {
        "profile.csv",
        "articles.csv",
        "words.csv",
        "word_forms.csv",
        "highlights.csv",
        "review_logs.csv",
        "daily_stats.csv",
        "reading_quizzes.csv",
        "level_evidence.csv",
        "weekly_reviews.csv",
        "ai_calls.csv",
        "writing_attempts.csv",
    }
    assert all(path.is_file() for path in result.files)

    words_text = (target / "words.csv").read_text(encoding="utf-8-sig")
    assert "有韧性的" in words_text
    rows = list(csv.DictReader(io.StringIO(words_text)))
    assert len(rows) == 1
    assert rows[0]["lemma"] == "resilient"
    assert rows[0]["status"] == "new"
    assert rows[0]["mnemonic"].startswith("re-silient")

    reviews_rows = list(
        csv.DictReader(io.StringIO((target / "review_logs.csv").read_text(encoding="utf-8-sig")))
    )
    assert reviews_rows[0]["note"] == "和 resilient / resistant 混淆"
    assert reviews_rows[0]["advice"].startswith("resilient 里有 sil")
    assert reviews_rows[0]["answer_text"] == "resilent"
    assert reviews_rows[0]["verdict"] == "near"
    assert reviews_rows[0]["distance"] == "1"

    articles_rows = list(
        csv.DictReader(io.StringIO((target / "articles.csv").read_text(encoding="utf-8-sig")))
    )
    assert articles_rows[0]["target_words"] == "habits; powerful"
    assert "Small habits" in articles_rows[0]["content"]

    profile_rows = list(
        csv.DictReader(io.StringIO((target / "profile.csv").read_text(encoding="utf-8-sig")))
    )
    assert profile_rows[0]["strengths"] == "阅读理解"

    ai_rows = list(
        csv.DictReader(io.StringIO((target / "ai_calls.csv").read_text(encoding="utf-8-sig")))
    )
    assert ai_rows[0]["task"] == "查词"
    assert ai_rows[0]["total_tokens"] == "200"

    quiz_rows = list(
        csv.DictReader(
            io.StringIO((target / "reading_quizzes.csv").read_text(encoding="utf-8-sig"))
        )
    )
    assert quiz_rows[0]["score"] == "3"
    assert quiz_rows[0]["total"] == "4"
    assert quiz_rows[0]["answers"] == "0; 0; 1"
    assert quiz_rows[0]["error_types"] == "none; none; inference; short_answer"
    assert quiz_rows[0]["short_answer_score"] == "1"
    assert quiz_rows[0]["self_report"] == "hard"
    assert quiz_rows[0]["recommended_level"] == "B2"
    assert quiz_rows[0]["pending_count"] == "1"
    assert "What is the main idea?" in quiz_rows[0]["questions"]
    assert quiz_rows[0]["feedback"] == "细节理解不错，推断题需要再练。"

    writing_rows = list(
        csv.DictReader(
            io.StringIO((target / "writing_attempts.csv").read_text(encoding="utf-8-sig"))
        )
    )
    assert writing_rows[0]["sentence_en"] == "Small habits are powerful."
    assert writing_rows[0]["reference_cn"] == "小习惯很有力量。"
    assert writing_rows[0]["score"] == "2"
    assert json.loads(writing_rows[0]["verdict"])["reason"] == "很好"


def test_export_empty_database(db: Database, tmp_path: Path) -> None:
    service = make_service(db)

    json_result = service.export_json(tmp_path / "empty.json")
    payload = json.loads(json_result.files[0].read_text(encoding="utf-8"))
    assert payload["profile"] is None
    assert set(payload["counts"].values()) == {0}

    csv_result = service.export_csv(tmp_path / "empty_csv")
    words_text = (tmp_path / "empty_csv" / "words.csv").read_text(encoding="utf-8-sig")
    assert words_text.strip().startswith("id,lemma")
    assert len(csv_result.files) == 12


def test_export_csv_directory_is_created(db: Database, tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    result = make_service(db).export_csv(nested)
    assert nested.is_dir()
    assert result.files


def test_export_articles_includes_readability_metrics(db: Database, tmp_path: Path) -> None:
    """M5.3：JSON 快照保留 metrics 对象，CSV 里写成 JSON 字符串。"""
    metrics = {"word_total": 8, "unknown_count": 0, "wordlist_size": 3000}
    db.articles.insert(
        Article(
            article_date="2026-11-20",
            title="Small Habits",
            content="Small habits are powerful.",
            level="B1",
            word_count=4,
            target_words=("habits",),
            metrics=metrics,
            created_at=now_iso(),
        )
    )
    service = make_service(db)

    result = service.export_json(tmp_path / "backup.json")
    data = json.loads(result.files[0].read_text(encoding="utf-8"))
    assert data["articles"][0]["metrics"] == metrics

    service.export_csv(tmp_path / "csv")
    rows = list(
        csv.DictReader(
            io.StringIO((tmp_path / "csv" / "articles.csv").read_text(encoding="utf-8-sig"))
        )
    )
    assert json.loads(rows[0]["metrics"]) == metrics
