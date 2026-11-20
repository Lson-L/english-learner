"""文章难词缓存与阅读后台预取的测试（全部 mock，无网络）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from english_learner.gui.pages.reader import ReaderPage  # noqa: E402
from english_learner.models.entities import Article, Word, now_iso, today_str  # noqa: E402
from english_learner.services.ai_client import AIError  # noqa: E402
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore, UiConfig  # noqa: E402
from english_learner.services.dictionary_service import DictionaryService  # noqa: E402
from english_learner.services.readability import select_lookup_words  # noqa: E402
from english_learner.storage.db import Database  # noqa: E402

_APP: QApplication | None = None

# 两个候选词：serendipity 不在内置词表里（优先级最高），resilient 是 C1（高于 B1）。
CONTENT = "Serendipity often rewards the prepared mind. Resilient people keep going."


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


class FakeCompleter:
    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.messages: list[list[dict[str, str]]] = []

    def complete_json(
        self, messages, *, temperature=None, max_tokens=None, timeout=None, task=""
    ) -> str:
        self.calls += 1
        self.messages.append(messages)
        self.last_timeout = timeout
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def prefetch_reply() -> str:
    return json.dumps(
        {
            "entries": [
                {
                    "word": "serendipity",
                    "lemma": "serendipity",
                    "phonetic": "/ˌserənˈdɪpəti/",
                    "pos": "n.",
                    "meaning_cn": "意外发现珍奇事物的运气",
                    "meaning_en": "the chance occurrence of happy discoveries",
                },
                {
                    "word": "resilient",
                    "lemma": "resilient",
                    "phonetic": "/rɪˈzɪliənt/",
                    "pos": "adj.",
                    "meaning_cn": "有韧性的",
                    "meaning_en": "able to recover quickly",
                },
            ]
        },
        ensure_ascii=False,
    )


# ------------------------------------------------------------------ 候选词
def test_select_lookup_words_prefers_unknown_and_above_level() -> None:
    wordlist = {"habit": "A2", "powerful": "B1", "resilient": "C1"}
    words = select_lookup_words(
        "Small habits are powerful. Resilient learners keep notes.",
        wordlist,
        level="B1",
    )

    assert "resilient" in words
    assert "habits" not in words  # A2 < B1
    assert "powerful" not in words  # B1 不超过用户等级


def test_select_lookup_words_skips_known_and_proper_nouns() -> None:
    words = select_lookup_words(
        "Anna met Serendipity in Paris. Serendipity is rare.",
        {},
        known_words={"anna"},
        level="B1",
    )

    # Anna 已掌握；Paris 只出现在句中大写 → 专有名词；serendipity 有小写形态。
    assert "serendipity" in words
    assert "anna" not in words
    assert "paris" not in words


# ------------------------------------------------------------------ 预取服务
def test_prefetch_caches_words_without_entering_vocabulary(db: Database) -> None:
    article = db.articles.insert(
        Article(
            article_date=today_str(),
            title="Serendipity",
            content=CONTENT,
            level="B1",
            word_count=len(CONTENT.split()),
            created_at=now_iso(),
        )
    )
    fake = FakeCompleter(prefetch_reply())
    service = DictionaryService(fake, db.words, db.stats)

    count = service.prefetch(CONTENT, level="B1", article_id=article.id)

    assert count == 2
    assert fake.calls == 1
    # 预取用更短的超时，避免关闭窗口时为后台请求干等两分钟。
    assert fake.last_timeout == 35.0
    cached = db.words.get_by_form("serendipity")
    assert cached is not None
    assert cached.meaning_cn == "意外发现珍奇事物的运气"
    assert cached.in_vocabulary is False
    assert cached.article_id == article.id
    assert cached.context_sentence.startswith("Serendipity often")

    # 之后双击该词直接命中缓存，不再调用 AI。
    no_ai = FakeCompleter()
    result = DictionaryService(no_ai, db.words).lookup(
        "Serendipity", "Serendipity often rewards the prepared mind."
    )
    assert result.cached is True
    assert no_ai.calls == 0


def test_prefetch_skips_when_everything_is_cached(db: Database) -> None:
    for lemma in ("serendipity", "resilient"):
        db.words.seed_cache(
            Word(lemma=lemma, meaning_cn="旧释义", created_at=now_iso(), updated_at=now_iso())
        )
    fake = FakeCompleter()  # 被调用就会 IndexError
    service = DictionaryService(fake, db.words, db.stats)

    assert service.prefetch(CONTENT, level="B1") == 0
    assert fake.calls == 0


def test_prefetch_ignores_ai_error_and_bad_json(db: Database) -> None:
    failing = DictionaryService(FakeCompleter(AIError("请求超时", "稍后再试")), db.words)
    assert failing.prefetch(CONTENT, level="B1") == 0

    garbled = DictionaryService(FakeCompleter("not json"), db.words)
    assert garbled.prefetch(CONTENT, level="B1") == 0


# ------------------------------------------------------------------ 阅读页接线
def make_context(db: Database, tmp_path: Path, *, prefetch: bool) -> AppContext:
    store = ConfigStore(tmp_path / "config.json")
    store.save_ui(UiConfig(prefetch_enabled=prefetch))
    return AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        store,
        db,
    )


def make_article(db: Database) -> Article:
    return db.articles.insert(
        Article(
            article_date=today_str(),
            title="Serendipity",
            content=CONTENT,
            level="B1",
            word_count=len(CONTENT.split()),
            created_at=now_iso(),
        )
    )


class FakeDictionaryService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int | None]] = []

    def prefetch(self, text: str, *, level: str = "B1", article_id: int | None = None) -> int:
        self.calls.append((text, level, article_id))
        return 0


def test_reader_starts_prefetch_after_loading_article(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_app()
    article = make_article(db)
    context = make_context(db, tmp_path, prefetch=True)
    service = FakeDictionaryService()
    monkeypatch.setattr(context, "dictionary_service", lambda: service)

    page = ReaderPage(context)
    page._load_article(article)
    worker = page._prefetch_worker
    assert worker is not None
    assert worker.wait(2000)

    assert service.calls == [(CONTENT, "B1", article.id)]
    page.shutdown()


def test_reader_respects_disabled_prefetch(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_app()
    article = make_article(db)
    context = make_context(db, tmp_path, prefetch=False)
    service = FakeDictionaryService()
    monkeypatch.setattr(context, "dictionary_service", lambda: service)

    page = ReaderPage(context)
    page._load_article(article)

    assert page._prefetch_worker is None
    assert service.calls == []
