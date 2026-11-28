"""查词多义项：解析、缓存版本与释义卡提示（全部 mock，无网络）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from english_learner.gui.widgets.word_panel import WordPanel  # noqa: E402
from english_learner.models.entities import Word, now_iso, today_str  # noqa: E402
from english_learner.services.dictionary_service import (  # noqa: E402
    DEFINITION_VERSION,
    DictionaryService,
    format_meanings,
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
        self.messages: list[list[dict[str, str]]] = []

    def complete_json(
        self, messages, *, temperature=None, max_tokens=None, timeout=None, task=""
    ) -> str:
        self.calls += 1
        self.messages.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def lie_reply() -> str:
    return json.dumps(
        {
            "lemma": "lie",
            "display_form": "lie",
            "phonetic": "/laɪ/",
            "pos": "v.",
            "meanings": [
                {
                    "pos": "v.",
                    "meaning_cn": "说谎",
                    "meaning_en": "to say something untrue",
                    "in_context": False,
                },
                {
                    "pos": "v.",
                    "meaning_cn": "躺；平卧",
                    "meaning_en": "to be in a flat position",
                    "in_context": True,
                },
                {
                    "pos": "n.",
                    "meaning_cn": "谎言",
                    "meaning_en": "a false statement",
                    "in_context": False,
                },
            ],
            "example_en": "The cat lay on the warm floor.",
            "example_cn": "猫躺在温暖的地板上。",
            "cefr_band": "A2",
            "mnemonic": "lie / lay / lain：躺下之后，谎话也躺了一地。",
        },
        ensure_ascii=False,
    )


# ------------------------------------------------------------------ 解析
def test_format_meanings_puts_context_first_and_numbers_senses() -> None:
    data = json.loads(lie_reply())

    meaning_cn, meaning_en = format_meanings(data)

    assert meaning_cn.startswith("1. v. 躺；平卧（本句义）")
    assert "2. v. 说谎" in meaning_cn
    assert "3. n. 谎言" in meaning_cn
    assert meaning_en.startswith("1. v. to be in a flat position (in this sentence)")


def test_format_meanings_single_sense_has_no_marker() -> None:
    data = {
        "meanings": [
            {
                "pos": "adj.",
                "meaning_cn": "有韧性的",
                "meaning_en": "able to recover quickly",
                "in_context": True,
            }
        ]
    }

    assert format_meanings(data) == ("adj. 有韧性的", "adj. able to recover quickly")


def test_format_meanings_accepts_string_boolean() -> None:
    data = {
        "meanings": [
            {"pos": "v.", "meaning_cn": "说谎", "in_context": "false"},
            {"pos": "v.", "meaning_cn": "躺；平卧", "in_context": "true"},
        ]
    }

    meaning_cn, _ = format_meanings(data)

    assert meaning_cn.startswith("1. v. 躺；平卧（本句义）")
    assert "2. v. 说谎" in meaning_cn


def test_legacy_circled_sense_marks_are_normalized_on_read(db: Database) -> None:
    """已经缓存成 ①② 的旧释义，读取时统一换成 1. 2.，避免字体回退忽大忽小。"""
    db.words.upsert(
        Word(
            lemma="back",
            meaning_cn="① n. 背部 ② adv. 回；向后",
            meaning_en="① the rear part ② toward the rear",
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )

    stored = db.words.get_by_lemma("back")

    assert stored is not None
    assert stored.meaning_cn == "1. n. 背部 2. adv. 回；向后"
    assert stored.meaning_en == "1. the rear part 2. toward the rear"


def test_format_meanings_falls_back_to_flat_fields() -> None:
    assert format_meanings({"meaning_cn": "河岸", "meaning_en": "land beside a river"}) == (
        "河岸",
        "land beside a river",
    )


# ------------------------------------------------------------------ 缓存
def test_lookup_stores_multiple_senses_and_hits_cache(db: Database) -> None:
    fake = FakeCompleter(lie_reply())
    service = DictionaryService(fake, db.words)

    first = service.lookup("lie", "The town lies by the river.", None, "B1")

    assert fake.calls == 1
    assert first.cached is False
    assert "2. v. 说谎" in first.word.meaning_cn
    assert first.word.definition_version == DEFINITION_VERSION
    stored = db.words.get_by_lemma("lie")
    assert stored is not None and stored.definition_version == DEFINITION_VERSION

    second = DictionaryService(FakeCompleter(), db.words).lookup(
        "lie", "The town lies by the river.", None, "B1"
    )
    assert second.cached is True
    assert second.definition_stale is False
    assert "2. v. 说谎" in second.word.meaning_cn


def test_old_cache_only_entry_is_refreshed_once(db: Database) -> None:
    db.words.upsert(
        Word(
            lemma="lie",
            meaning_cn="位于",
            in_vocabulary=False,
            created_at=now_iso(),
            updated_at=now_iso(),
            due_date=today_str(),
        )
    )
    fake = FakeCompleter(lie_reply())

    result = DictionaryService(fake, db.words).lookup(
        "lie", "The town lies by the river.", None, "B1"
    )

    assert fake.calls == 1
    assert result.cached is False
    assert "（本句义）" in result.word.meaning_cn
    assert result.word.definition_version == DEFINITION_VERSION


def test_vocabulary_word_keeps_edits_but_is_marked_stale(db: Database) -> None:
    db.words.upsert(
        Word(
            lemma="lie",
            meaning_cn="位于（我手动改过）",
            in_vocabulary=True,
            created_at=now_iso(),
            updated_at=now_iso(),
            due_date=today_str(),
        )
    )
    fake = FakeCompleter()  # 被调用就会 IndexError

    result = DictionaryService(fake, db.words).lookup(
        "lie", "The town lies by the river.", None, "B1"
    )

    assert fake.calls == 0
    assert result.cached is True
    assert result.definition_stale is True
    assert result.word.meaning_cn == "位于（我手动改过）"


# ------------------------------------------------------------------ 释义卡
def test_word_panel_puts_each_sense_on_its_own_line() -> None:
    get_app()
    panel = WordPanel()
    word = Word(
        lemma="back",
        meaning_cn="1. n. 背部；后背（本句义） 2. adv. 回；向后 3. v. 支持；赞成",
        in_vocabulary=True,
    )

    panel.show_definition(word, "Back home.", cached=True)

    assert panel._meaning_cn.text() == (
        "1. n. 背部；后背（本句义）\n2. adv. 回；向后\n3. v. 支持；赞成"
    )


def test_word_panel_shows_refresh_button_for_stale_definition() -> None:
    get_app()
    panel = WordPanel()
    word = Word(lemma="lie", meaning_cn="位于", in_vocabulary=True)

    panel.show_definition(word, "The town lies by the river.", cached=True, definition_stale=True)

    assert not panel._refresh_button.isHidden()
    assert "旧版查词" in panel._status.text()

    panel.show_definition(word, "The town lies by the river.", cached=True)
    assert panel._refresh_button.isHidden()
