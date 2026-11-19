"""每日文章生成、校验、重试与当天缓存的测试（全部 mock，无网络）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from english_learner.models.entities import Article, Profile, Word, now_iso
from english_learner.services.ai_client import AIError
from english_learner.services.article_service import (
    ArticleService,
    InvalidArticleError,
    parse_article_draft,
)
from english_learner.services.prompts import build_article_messages
from english_learner.storage.db import Database

# 12 个词；重复 6 次 = 72 词，3 段 = 216 词，落在 200~400 的校验区间内。
BASE_SENTENCE = "Small habits are easy to start and easy to repeat every day."


class FakeCompleter:
    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.messages: list[list[dict[str, str]]] = []

    def complete_json(self, messages, *, temperature=None, max_tokens=None) -> str:
        self.calls += 1
        self.messages.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_payload(
    *,
    paragraph_count: int = 3,
    repeats: int = 6,
    title: str = "Small Habits",
    level: str = "B1",
    target_words: list[str] | None = None,
) -> dict:
    paragraphs = [" ".join([BASE_SENTENCE] * repeats) for _ in range(paragraph_count)]
    return {
        "title": title,
        "level": level,
        "topic": "psychology",
        "paragraphs": paragraphs,
        "target_words": ["habits"] if target_words is None else target_words,
        "glossary": [{"word": "habits", "meaning_cn": "习惯"}],
    }


def payload_text(**overrides) -> str:
    return json.dumps(make_payload(**overrides), ensure_ascii=False)


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


@pytest.fixture
def profile() -> Profile:
    return Profile(cefr_level="B1")


def make_service(db: Database, fake: FakeCompleter) -> ArticleService:
    return ArticleService(fake, db.articles, db.words)


def test_parse_valid_draft() -> None:
    draft = parse_article_draft(payload_text(), "B1")
    assert draft.title == "Small Habits"
    assert draft.level == "B1"
    assert len(draft.paragraphs) == 3
    assert draft.word_count == 216
    assert draft.target_words == ("habits",)
    assert draft.glossary == (("habits", "习惯"),)
    assert draft.content == "\n\n".join(draft.paragraphs)


def test_parse_accepts_fenced_json() -> None:
    text = "```json\n" + payload_text() + "\n```"
    assert parse_article_draft(text, "B1").title == "Small Habits"


def test_too_few_paragraphs_rejected() -> None:
    text = json.dumps(make_payload(paragraph_count=2, repeats=9), ensure_ascii=False)
    with pytest.raises(InvalidArticleError, match="段落"):
        parse_article_draft(text, "B1")


def test_word_count_out_of_range_rejected() -> None:
    text = json.dumps(make_payload(repeats=3), ensure_ascii=False)  # 108 词
    with pytest.raises(InvalidArticleError, match="词数"):
        parse_article_draft(text, "B1")


def test_missing_target_word_rejected() -> None:
    text = json.dumps(make_payload(target_words=["resilient"]), ensure_ascii=False)
    with pytest.raises(InvalidArticleError, match="目标词"):
        parse_article_draft(text, "B1")


def test_missing_target_word_allowed_in_soft_mode() -> None:
    text = json.dumps(make_payload(target_words=["resilient"]), ensure_ascii=False)
    draft = parse_article_draft(text, "B1", require_targets=False)
    assert draft.target_words == ("resilient",)


def test_wrong_level_rejected() -> None:
    text = payload_text(level="C1")
    with pytest.raises(InvalidArticleError, match="等级"):
        parse_article_draft(text, "B1")


def test_service_retries_once_then_succeeds(db: Database, profile: Profile) -> None:
    fake = FakeCompleter("这不是 JSON", payload_text())
    draft = make_service(db, fake).generate_article("B1")

    assert fake.calls == 2
    assert draft.title == "Small Habits"


def test_service_fails_after_two_bad_replies(db: Database) -> None:
    fake = FakeCompleter("垃圾", "还是垃圾")
    with pytest.raises(AIError) as exc_info:
        make_service(db, fake).generate_article("B1")
    assert fake.calls == 2
    assert "没有通过校验" in exc_info.value.display_message()


def test_get_or_create_today_caches(db: Database, profile: Profile) -> None:
    fake = FakeCompleter(payload_text())
    service = make_service(db, fake)

    first = service.get_or_create_today(profile, today="2026-11-18")
    second = service.get_or_create_today(profile, today="2026-11-18")

    assert fake.calls == 1
    assert first.id == second.id
    assert first.article_date == "2026-11-18"
    assert db.articles.get_by_date("2026-11-18") == first


def test_due_words_are_sent_to_prompt(db: Database, profile: Profile) -> None:
    db.words.upsert(
        Word(
            lemma="resilient",
            in_vocabulary=True,
            due_date="2026-11-18",
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    fake = FakeCompleter(payload_text())
    make_service(db, fake).get_or_create_today(profile, today="2026-11-18")

    user_message = fake.messages[0][1]["content"]
    assert "resilient" in user_message


def test_regenerate_replaces_today_article(db: Database, profile: Profile) -> None:
    fake = FakeCompleter(payload_text(title="First"), payload_text(title="Second"))
    service = make_service(db, fake)

    first = service.get_or_create_today(profile, today="2026-11-18")
    second = service.regenerate_today(profile, today="2026-11-18")

    assert fake.calls == 2
    assert first.title == "First"
    assert second.title == "Second"
    assert second.article_date == "2026-11-18"
    assert second.id != first.id
    assert len(db.articles.list_recent()) == 1


def test_regenerate_changes_topic_and_avoids_old_title(
    db: Database, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """“换一篇”不能只重发同一份 prompt：主题要换，旧标题要明确排除。"""
    monkeypatch.setattr(ArticleService, "pick_angle", staticmethod(lambda: "从一件日常小事切入"))
    fake = FakeCompleter(payload_text(title="First"), payload_text(title="Second"))
    service = make_service(db, fake)

    service.get_or_create_today(profile, today="2026-11-18")
    service.regenerate_today(profile, today="2026-11-18")

    # 2026-11-18 的轮换主题是 history。
    first_prompt = fake.messages[0][1]["content"]
    second_prompt = fake.messages[1][1]["content"]
    assert "主题：history" in first_prompt
    assert "主题：history" not in second_prompt
    assert "First" in second_prompt
    assert "切入角度" in second_prompt


def test_retry_uses_a_different_angle(
    db: Database, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    angles = iter(["角度 A", "角度 B"])
    monkeypatch.setattr(ArticleService, "pick_angle", staticmethod(lambda: next(angles)))
    fake = FakeCompleter("这不是 JSON", payload_text())

    make_service(db, fake).generate_article("B1")

    first_prompt = fake.messages[0][1]["content"]
    second_prompt = fake.messages[1][1]["content"]
    assert "角度 A" in first_prompt and "角度 B" not in first_prompt
    assert "角度 B" in second_prompt and "角度 A" not in second_prompt


def test_regenerate_follows_user_request(db: Database, profile: Profile) -> None:
    """填了输入框就按用户方向选题：不加默认主题、不加随机角度。"""
    fake = FakeCompleter(payload_text(title="First"), payload_text(title="Second"))
    service = make_service(db, fake)

    service.get_or_create_today(profile, today="2026-11-18")
    service.regenerate_today(
        profile,
        today="2026-11-18",
        user_request="写一篇关于深海生物的文章",
    )

    prompt = fake.messages[1][1]["content"]
    assert "写一篇关于深海生物的文章" in prompt
    assert "主题：history" not in prompt
    assert "切入角度" not in prompt


def test_regenerate_with_request_includes_current_article(db: Database, profile: Profile) -> None:
    """用户写了换一篇要求时，要把当前文章一起发给模型，便于理解指代。"""
    fake = FakeCompleter(payload_text(title="First"), payload_text(title="Second"))
    service = make_service(db, fake)

    service.get_or_create_today(profile, today="2026-11-18")
    service.regenerate_today(
        profile,
        today="2026-11-18",
        user_request="沿用这篇的叙事方式，但换成深海主题",
    )

    prompt = fake.messages[1][1]["content"]
    assert "【当前文章（仅作参考）】" in prompt
    assert "标题：First" in prompt
    assert BASE_SENTENCE in prompt
    assert "不要逐句改写" in prompt
    # 用户要的是“按这篇参考”，不能再把当前标题列进“禁止重复”，避免指令冲突。
    assert "下面这些文章已经写过了" not in prompt


def test_regenerate_without_request_omits_current_article(db: Database, profile: Profile) -> None:
    """没写要求时不带当前文章，避免模型被原文带偏、也节省 token。"""
    fake = FakeCompleter(payload_text(title="First"), payload_text(title="Second"))
    service = make_service(db, fake)

    service.get_or_create_today(profile, today="2026-11-18")
    service.regenerate_today(profile, today="2026-11-18")

    prompt = fake.messages[1][1]["content"]
    assert "【当前文章（仅作参考）】" not in prompt
    assert BASE_SENTENCE not in prompt
    # 仍会为了避免重复而列出旧标题
    assert "First" in prompt


def test_prompt_ignores_current_article_without_request(db: Database) -> None:
    """底层 prompt 也做兜底：没有 user_request 就不渲染当前文章。"""
    article = Article(
        article_date="2026-11-18",
        title="First",
        content=BASE_SENTENCE,
        level="B1",
        word_count=12,
        created_at=now_iso(),
    )

    messages = build_article_messages("B1", [], "psychology", current_article=article)
    assert "【当前文章（仅作参考）】" not in messages[1]["content"]


def test_regenerate_failure_keeps_old_article(db: Database, profile: Profile) -> None:
    fake = FakeCompleter(payload_text(), "垃圾", "还是垃圾")
    service = make_service(db, fake)
    first = service.get_or_create_today(profile, today="2026-11-18")

    with pytest.raises(AIError):
        service.regenerate_today(profile, today="2026-11-18")

    stored = db.articles.get_by_date("2026-11-18")
    assert stored is not None
    assert stored.id == first.id
    assert stored.title == first.title
