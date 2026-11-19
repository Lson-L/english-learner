"""复习求助服务测试：AI 建议解析、记忆方法落库、失败兜底。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from english_learner.models.entities import Word, now_iso
from english_learner.services.ai_client import AIError
from english_learner.services.review_help_service import ReviewHelpService
from english_learner.storage.db import Database


class FakeCompleter:
    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls = 0

    def complete_json(self, messages, *, temperature=None, max_tokens=None, task="") -> str:
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


def make_word(**overrides) -> Word:
    values = {
        "lemma": "resilient",
        "display_form": "resilient",
        "pos": "adj.",
        "meaning_cn": "有韧性的",
        "meaning_en": "able to recover quickly",
        "example_en": "Children are resilient.",
        "context_sentence": "Resilient people keep going.",
        "status": "learning",
        "due_date": now_iso()[:10],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    values.update(overrides)
    return Word(**values)


def test_help_returns_advice_and_updates_mnemonic(db: Database) -> None:
    word = db.words.upsert(make_word())
    assert word.id is not None
    payload = {
        "advice": "你把它和 resistant 混淆了：resilient 强调恢复力，resistant 强调抵抗。",
        "mnemonic": "re-silient：被压弯后还能弹回来，就是 resilient",
    }
    fake = FakeCompleter(json.dumps(payload, ensure_ascii=False))
    service = ReviewHelpService(fake, db.words)

    result = service.help(word, 1, "总是和 resistant 搞混", "B1")

    assert fake.calls == 1
    assert result.advice.startswith("你把它和 resistant")
    assert result.mnemonic.startswith("re-silient")
    assert result.reason == "总是和 resistant 搞混"

    stored = db.words.get_by_id(word.id)
    assert stored is not None
    assert stored.mnemonic == result.mnemonic


def test_help_keeps_existing_mnemonic_when_reply_omits_it(db: Database) -> None:
    word = db.words.upsert(make_word(mnemonic="旧方法：re + silent"))
    fake = FakeCompleter(json.dumps({"advice": "多造一个自己的句子会更容易记住。", "mnemonic": ""}))
    service = ReviewHelpService(fake, db.words)

    result = service.help(word, 2, "只记得大概意思", "B1")

    assert result.mnemonic == "旧方法：re + silent"
    stored = db.words.get_by_id(word.id)
    assert stored is not None and stored.mnemonic == "旧方法：re + silent"


def test_help_rejects_missing_advice(db: Database) -> None:
    word = db.words.upsert(make_word())
    service = ReviewHelpService(FakeCompleter(json.dumps({"advice": ""})), db.words)

    with pytest.raises(AIError):
        service.help(word, 1, "没印象", "B1")


def test_help_rejects_invalid_rating(db: Database) -> None:
    word = db.words.upsert(make_word())
    service = ReviewHelpService(FakeCompleter(), db.words)

    with pytest.raises(ValueError):
        service.help(word, 3, "", "B1")
