"""划词查词与阅读器 UI 的测试（offscreen，无网络）。"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from english_learner.gui.pages.reader import ReaderPage  # noqa: E402
from english_learner.gui.regenerate_dialog import RegenerateDialog  # noqa: E402
from english_learner.gui.widgets import ArticleView  # noqa: E402
from english_learner.gui.workers import TaskWorker  # noqa: E402
from english_learner.models.entities import (  # noqa: E402
    Article,
    Highlight,
    Profile,
    Word,
    now_iso,
    today_str,
)
from english_learner.services.ai_client import AIError  # noqa: E402
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore  # noqa: E402
from english_learner.services.dictionary_service import (  # noqa: E402
    DictionaryService,
    extract_sentence,
    normalize_lemma,
)
from english_learner.storage.db import Database  # noqa: E402

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


class FakeCompleter:
    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls = 0

    def complete_json(self, messages, *, temperature=None, max_tokens=None) -> str:
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def make_article(**overrides) -> Article:
    values = {
        "article_date": today_str(),
        "title": "Small Habits",
        "content": "Small habits are powerful. Resilient people keep going.",
        "level": "B1",
        "word_count": 8,
        "created_at": now_iso(),
    }
    values.update(overrides)
    return Article(**values)


def make_word(**overrides) -> Word:
    values = {
        "lemma": "habits",
        "display_form": "habits",
        "meaning_cn": "习惯",
        "status": "new",
        "due_date": today_str(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    values.update(overrides)
    return Word(**values)


# ---------------------------------------------------------------- 纯函数
def test_normalize_lemma() -> None:
    assert normalize_lemma("  Hello, ") == "hello"
    assert normalize_lemma("(resilient)") == "resilient"
    assert normalize_lemma("don't") == "don't"
    assert normalize_lemma("well-known") == "well-known"
    assert normalize_lemma("123") == ""
    assert normalize_lemma("中文") == ""
    assert normalize_lemma("") == ""
    assert normalize_lemma("hello world") == ""


def test_extract_sentence() -> None:
    text = "Anna waited. Resilient people recover fast! Last one."
    start = text.index("Resilient")
    assert extract_sentence(text, start, start + 9) == "Resilient people recover fast!"

    start = text.index("Anna")
    assert extract_sentence(text, start, start + 4) == "Anna waited."

    multiline = "First line here\nSecond line here"
    assert extract_sentence(multiline, 6, 10) == "First line here"


def test_extract_sentence_truncates_long_text() -> None:
    text = "word " * 100 + "target" + " more" * 100
    start = text.index("target")
    sentence = extract_sentence(text, start, start + 6, max_len=120)
    assert len(sentence) <= 120
    assert "target" in sentence


# ---------------------------------------------------------------- 查词服务
def test_lookup_hits_cache_without_ai(db: Database) -> None:
    stored = db.words.upsert(make_word())
    fake = FakeCompleter()  # 没有预设回复，被调用就会抛 IndexError
    service = DictionaryService(fake, db.words, db.stats)

    result = service.lookup("Habits,", "Small habits are powerful.")

    assert result.cached is True
    assert result.word.id == stored.id
    assert fake.calls == 0


def test_lookup_new_word_calls_ai_and_stores(db: Database) -> None:
    payload = {
        "lemma": "resilient",
        "display_form": "resilient",
        "phonetic": "/rɪˈzɪliənt/",
        "pos": "adj.",
        "meaning_cn": "有韧性的",
        "meaning_en": "able to recover quickly",
        "example_en": "Children are resilient.",
        "example_cn": "孩子很有韧性。",
    }
    fake = FakeCompleter(json.dumps(payload, ensure_ascii=False))
    service = DictionaryService(fake, db.words, db.stats)

    result = service.lookup("Resilient", "Resilient people keep going.", None, "B1")

    assert fake.calls == 1
    assert result.cached is False
    assert result.word.meaning_cn == "有韧性的"
    assert result.word.id is not None
    stored = db.words.get_by_lemma("resilient")
    assert stored is not None
    assert stored.due_date == today_str()
    assert stored.context_sentence == "Resilient people keep going."


def test_lookup_rejects_non_word(db: Database) -> None:
    service = DictionaryService(FakeCompleter(), db.words, db.stats)
    with pytest.raises(AIError):
        service.lookup("12345")


def test_add_to_vocabulary_is_idempotent(db: Database) -> None:
    article = db.articles.insert(make_article())
    word = db.words.upsert(make_word())
    assert article.id is not None and word.id is not None
    service = DictionaryService(FakeCompleter(), db.words, db.stats)

    first = service.add_to_vocabulary(
        word,
        article_id=article.id,
        start_offset=0,
        end_offset=5,
        sentence="Small habits are powerful.",
    )
    second = service.add_to_vocabulary(
        word,
        article_id=article.id,
        start_offset=0,
        end_offset=5,
    )

    assert first is True
    assert second is False
    assert len(db.words.list_highlights(article.id)) == 1
    assert db.stats.get(today_str()).words_added == 1


# ------------------------------------------------------------------ 阅读器
def test_article_view_renders_highlights(db: Database) -> None:
    get_app()
    view = ArticleView()
    article = make_article()
    view.set_article(article)
    assert view.toPlainText() == article.content

    view.set_highlights([Highlight(article_id=1, word_id=1, start_offset=0, end_offset=5)])
    assert view.highlight_count() == 1

    view.set_highlights([])
    assert view.highlight_count() == 0


def test_reader_page_loads_cached_article_without_worker(db: Database) -> None:
    get_app()
    article = db.articles.insert(make_article())
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )

    page = ReaderPage(context)
    page.on_activated()

    assert page._worker is None
    assert page._view.toPlainText() == article.content
    assert "Small Habits" in page._title_label.text()

    stored = db.articles.get_by_date(today_str())
    assert stored is not None and stored.read_at is not None
    assert db.stats.get(today_str()).article_read == 1


def test_reader_feedback_button_needs_profile_and_article(db: Database) -> None:
    get_app()
    db.articles.insert(make_article())
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )

    page = ReaderPage(context)
    page.on_activated()
    assert page._feedback_button.isEnabled() is False

    db.profiles.save(Profile(cefr_level="B1"))
    page.refresh()
    assert page._feedback_button.isEnabled() is True


def test_regenerate_dialog_returns_input_or_empty() -> None:
    get_app()
    dialog = RegenerateDialog()
    assert dialog.request_text() == ""

    dialog._request_edit.setPlainText("  写一篇关于深海生物的文章  ")
    assert dialog.request_text() == "写一篇关于深海生物的文章"


def test_reader_shutdown_returns_worker_that_ignores_cancel(db: Database) -> None:
    """关闭页面时先等宽限期；任务卡住时把 worker 交回主窗口兜底等待。"""
    get_app()
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )
    page = ReaderPage(context)

    started = threading.Event()
    release = threading.Event()

    def task() -> str:
        started.set()
        release.wait(3)
        return "done"

    worker = TaskWorker(task)
    worker.start()
    assert started.wait(2.0) is True

    page._worker = worker
    stuck = page.shutdown(timeout_ms=100)

    assert stuck is worker
    assert worker.isRunning() is True

    release.set()
    assert worker.wait(3000) is True


def test_reader_page_without_profile_does_not_generate(db: Database) -> None:
    get_app()
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )

    page = ReaderPage(context)
    page.on_activated()

    assert page._worker is None
    assert not page._generate_button.isHidden()
    assert "测评" in page._status.text()
