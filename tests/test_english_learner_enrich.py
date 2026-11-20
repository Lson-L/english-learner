"""加入生词本后的后台释义补全（M6.3）测试（全部 mock，无网络）。"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from english_learner.gui.pages.reader import ReaderPage  # noqa: E402
from english_learner.models.entities import Article, Word, now_iso, today_str  # noqa: E402
from english_learner.services.ai_client import AIError  # noqa: E402
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore, UiConfig  # noqa: E402
from english_learner.services.dictionary_service import (  # noqa: E402
    DictionaryService,
    needs_definition,
)
from english_learner.services.prompts import build_dictionary_messages  # noqa: E402
from english_learner.storage.db import Database  # noqa: E402

_APP: QApplication | None = None

CONTENT = "Serendipity often rewards the prepared mind."
SENTENCE = "Serendipity often rewards the prepared mind."


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
        self.tasks: list[str] = []
        self.last_timeout: float | None = None

    def complete_json(
        self, messages, *, temperature=None, max_tokens=None, timeout=None, task=""
    ) -> str:
        self.calls += 1
        self.messages.append(messages)
        self.tasks.append(task)
        self.last_timeout = timeout
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def full_reply(*, lemma: str = "serendipity") -> str:
    return json.dumps(
        {
            "lemma": lemma,
            "display_form": "Serendipity",
            "phonetic": "/ˌserənˈdɪpəti/",
            "pos": "n.",
            "meaning_cn": "意外发现珍奇事物的运气（在本句中指“好运气”）",
            "meaning_en": "the chance occurrence of happy discoveries",
            "example_en": "Finding that cafe was pure serendipity.",
            "example_cn": "发现那家咖啡馆纯属意外之喜。",
            "cefr_band": "C1",
            "mnemonic": "serendipity：想象在旧书店随手一抽，正好翻到想找的那一页。",
        },
        ensure_ascii=False,
    )


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


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


def seed_short_word(db: Database, article_id: int | None = None) -> Word:
    """模拟文章 glossary / 后台预取写入的简释词条。"""
    stored = db.words.seed_cache(
        Word(
            lemma="serendipity",
            phonetic="/ˌserənˈdɪpəti/",
            pos="n.",
            meaning_cn="运气",
            article_id=article_id,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    assert stored is not None and stored.id is not None
    return stored


# ------------------------------------------------------------------ 判定与补全
def test_needs_definition_only_for_prefetched_entries() -> None:
    assert needs_definition(Word(lemma="luck", meaning_cn="运气")) is True
    assert needs_definition(Word(lemma="luck", example_en="Good luck!")) is False
    assert needs_definition(Word(lemma="luck", mnemonic="谐音“拉客”")) is False


def test_enrich_upgrades_short_entry_like_double_click(db: Database) -> None:
    article = make_article(db)
    word = seed_short_word(db, article.id)
    # 模拟已有复习进度：补全只能动释义，不能把 SRS 排期重置。
    db.words.mark_in_vocabulary(word.id, first_due_date="2026-12-01")
    word = db.words.get_by_id(word.id)
    assert word is not None
    db.words.save_schedule(
        replace(
            word,
            status="review",
            interval_days=6,
            repetitions=3,
            due_date="2026-12-01",
        )
    )

    fake = FakeCompleter(full_reply())
    service = DictionaryService(fake, db.words)

    updated = service.enrich_definition(word.id, SENTENCE, "B1")

    assert updated is not None
    assert updated.example_en == "Finding that cafe was pure serendipity."
    assert updated.mnemonic.startswith("serendipity：")
    assert updated.meaning_cn.startswith("意外发现")
    assert updated.cefr_band == "C1"
    assert updated.context_sentence == SENTENCE
    # 词条身份与学习状态保持不变。
    assert updated.lemma == "serendipity"
    assert updated.in_vocabulary is True
    assert updated.interval_days == 6
    assert updated.repetitions == 3
    assert updated.due_date == "2026-12-01"
    # 与双击查词共用同一套 prompt（学习者记忆为空时逐字节一致）。
    assert fake.calls == 1
    assert fake.tasks == ["查词补全"]
    assert fake.last_timeout == 45.0
    assert fake.messages[0] == build_dictionary_messages("serendipity", SENTENCE, "B1")


def test_enrich_skips_complete_entry_without_ai(db: Database) -> None:
    db.words.seed_cache(
        Word(
            lemma="luck",
            meaning_cn="运气",
            example_en="Good luck!",
            mnemonic="谐音“拉客”",
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    stored = db.words.get_by_lemma("luck")
    assert stored is not None and stored.id is not None
    fake = FakeCompleter()  # 被调用就会 IndexError

    assert DictionaryService(fake, db.words).enrich_definition(stored.id, SENTENCE) is None
    assert fake.calls == 0


def test_enrich_skips_missing_word(db: Database) -> None:
    fake = FakeCompleter()
    assert DictionaryService(fake, db.words).enrich_definition(9999, SENTENCE) is None
    assert fake.calls == 0


def test_enrich_keeps_existing_lemma_when_ai_disagrees(db: Database) -> None:
    article = make_article(db)
    stored = db.words.seed_cache(
        Word(
            lemma="runner",
            display_form="runners",
            meaning_cn="跑步者",
            article_id=article.id,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    assert stored is not None and stored.id is not None
    before = db.words.count(include_cache=True)

    fake = FakeCompleter(full_reply(lemma="run"))
    updated = DictionaryService(fake, db.words).enrich_definition(stored.id, SENTENCE)

    # AI 给了另一个原形也不改词条身份：高亮/词形映射都还指向原 id。
    assert updated is not None
    assert updated.lemma == "runner"
    assert updated.display_form == "runners"
    assert db.words.count(include_cache=True) == before


def test_enrich_propagates_ai_error(db: Database) -> None:
    article = make_article(db)
    word = seed_short_word(db, article.id)
    service = DictionaryService(FakeCompleter(AIError("请求超时", "稍后再试")), db.words)

    with pytest.raises(AIError):
        service.enrich_definition(word.id, SENTENCE)

    # 失败时保留原简释，不影响已加入生词本的状态。
    cached = db.words.get_by_id(word.id)
    assert cached is not None
    assert cached.meaning_cn == "运气"
    assert cached.in_vocabulary is False


# ------------------------------------------------------------------ 阅读页接线
def make_context(db: Database, tmp_path: Path) -> AppContext:
    store = ConfigStore(tmp_path / "config.json")
    store.save_ui(UiConfig(prefetch_enabled=False))
    return AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        store,
        db,
    )


def test_reader_enriches_in_background_after_adding(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_app()
    article = make_article(db)
    word = seed_short_word(db, article.id)
    context = make_context(db, tmp_path)
    fake = FakeCompleter(full_reply())
    service = DictionaryService(fake, db.words, db.stats)
    monkeypatch.setattr(context, "dictionary_service", lambda: service)

    page = ReaderPage(context)
    page._load_article(article)
    page._pending = ("serendipity", 0, len("Serendipity"), SENTENCE)
    page._current_word_id = word.id

    page._on_add_requested()

    worker = page._enrich_worker
    assert worker is not None
    assert worker.wait(3000)

    stored = db.words.get_by_id(word.id)
    assert stored is not None
    assert stored.in_vocabulary is True
    assert stored.example_en  # 后台补全的完整释义已经落库
    assert stored.mnemonic
    assert fake.tasks == ["查词补全"]
    page.shutdown()
