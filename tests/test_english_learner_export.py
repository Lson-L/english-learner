"""学习数据导出测试：JSON 全量快照与 CSV 分表。"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from english_learner.models.entities import Article, Profile, Word, now_iso
from english_learner.services.export_service import ExportService
from english_learner.storage.db import Database


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def make_service(db: Database) -> ExportService:
    return ExportService(db.profiles, db.articles, db.words, db.reviews, db.stats)


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
            article_id=article.id,
            due_date="2026-11-19",
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    assert word.id is not None
    db.words.add_highlight(
        article_id=article.id,
        word_id=word.id,
        start_offset=0,
        end_offset=5,
        sentence="Small habits are powerful.",
    )
    db.reviews.log(word_id=word.id, rating=3, prev_interval=0, next_interval=1)
    db.stats.bump("2026-11-18", article_read=1)
    db.stats.bump("2026-11-19", words_added=1, reviews_done=1)


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
        "highlights": 1,
        "review_logs": 1,
        "daily_stats": 2,
    }
    assert data["profile"]["cefr_level"] == "B1"
    assert data["profile"]["strengths"] == ["阅读理解"]
    assert data["articles"][0]["target_words"] == ["habits", "powerful"]
    assert data["words"][0]["lemma"] == "resilient"
    assert data["words"][0]["meaning_cn"] == "有韧性的"
    assert data["highlights"][0]["start_offset"] == 0
    assert data["review_logs"][0]["rating"] == 3
    assert data["daily_stats"][0]["stat_date"] == "2026-11-18"
    assert "生词 1" in result.summary()


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
        "highlights.csv",
        "review_logs.csv",
        "daily_stats.csv",
    }
    assert all(path.is_file() for path in result.files)

    words_text = (target / "words.csv").read_text(encoding="utf-8-sig")
    assert "有韧性的" in words_text
    rows = list(csv.DictReader(io.StringIO(words_text)))
    assert len(rows) == 1
    assert rows[0]["lemma"] == "resilient"
    assert rows[0]["status"] == "new"

    articles_rows = list(
        csv.DictReader(io.StringIO((target / "articles.csv").read_text(encoding="utf-8-sig")))
    )
    assert articles_rows[0]["target_words"] == "habits; powerful"
    assert "Small habits" in articles_rows[0]["content"]

    profile_rows = list(
        csv.DictReader(io.StringIO((target / "profile.csv").read_text(encoding="utf-8-sig")))
    )
    assert profile_rows[0]["strengths"] == "阅读理解"


def test_export_empty_database(db: Database, tmp_path: Path) -> None:
    service = make_service(db)

    json_result = service.export_json(tmp_path / "empty.json")
    payload = json.loads(json_result.files[0].read_text(encoding="utf-8"))
    assert payload["profile"] is None
    assert set(payload["counts"].values()) == {0}

    csv_result = service.export_csv(tmp_path / "empty_csv")
    words_text = (tmp_path / "empty_csv" / "words.csv").read_text(encoding="utf-8-sig")
    assert words_text.strip().startswith("id,lemma")
    assert len(csv_result.files) == 6


def test_export_csv_directory_is_created(db: Database, tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    result = make_service(db).export_csv(nested)
    assert nested.is_dir()
    assert result.files
