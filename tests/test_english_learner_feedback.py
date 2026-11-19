"""阅读反馈调级的测试：重新定级、软约束重写文章、失败不动原数据。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from english_learner.models.entities import Article, Profile, Word, now_iso
from english_learner.services.ai_client import AIError
from english_learner.services.level_feedback_service import (
    LevelAdjustment,
    LevelFeedbackService,
    clamp_reassessed_level,
)
from english_learner.storage.db import Database

TODAY = "2026-11-18"
BASE_SENTENCE = "Small habits are easy to start and easy to repeat every day."


class FakeCompleter:
    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.messages: list[list[dict[str, str]]] = []

    def complete_json(self, messages, *, temperature=None, max_tokens=None, task="") -> str:
        self.calls += 1
        self.messages.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def article_payload(level: str = "A2", title: str = "An Easier Read") -> str:
    paragraphs = [" ".join([BASE_SENTENCE] * 6) for _ in range(3)]
    return json.dumps(
        {
            "title": title,
            "level": level,
            "topic": "psychology",
            "paragraphs": paragraphs,
            "target_words": [],
            "glossary": [],
        },
        ensure_ascii=False,
    )


def reassess_payload(**overrides) -> str:
    data = {
        "cefr": "A2",
        "vocab_estimate": 3000,
        "strengths": ["基本阅读理解"],
        "weaknesses": ["词汇量不足"],
        "comment": "生词偏多，先下调一档建立信心。",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


@pytest.fixture
def profile(db: Database) -> Profile:
    return db.profiles.save(
        Profile(
            cefr_level="B1",
            vocab_estimate=3800,
            strengths=("阅读理解",),
            weaknesses=("学术词汇",),
            assessed_at="2026-11-01T10:00:00",
        )
    )


@pytest.fixture
def article(db: Database) -> Article:
    return db.articles.insert(
        Article(
            article_date=TODAY,
            title="Too Hard",
            content="Small habits are powerful. Resilient people keep going.",
            level="B1",
            topic="psychology",
            word_count=8,
            created_at=now_iso(),
        )
    )


def add_word(db: Database, article: Article, lemma: str, created_at: str) -> Word:
    word = db.words.upsert(
        Word(
            lemma=lemma,
            display_form=lemma,
            meaning_cn="释义",
            in_vocabulary=True,
            due_date=TODAY,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    assert article.id is not None and word.id is not None
    db.words.add_highlight(
        article_id=article.id,
        word_id=word.id,
        start_offset=0,
        end_offset=len(lemma),
        sentence="Small habits are powerful.",
    )
    return word


def make_service(db: Database, fake: FakeCompleter) -> LevelFeedbackService:
    return LevelFeedbackService(fake, db.profiles, db.articles, db.words)


def test_clamp_reassessed_level_limits_step_and_direction() -> None:
    assert clamp_reassessed_level("B1", "A2", "太难了") == "A2"
    assert clamp_reassessed_level("C1", "A2", "太难了") == "B2"
    assert clamp_reassessed_level("B1", "C1", "太难了") == "A2"
    assert clamp_reassessed_level("B1", "C1", "太简单了") == "B2"
    assert clamp_reassessed_level("B1", "A2", "太简单了") == "B2"
    assert clamp_reassessed_level("B1", "Z9", "太难了") == "B1"


def test_collect_today_words_prefers_highlighted(db: Database, article: Article) -> None:
    add_word(db, article, "resilient", f"{TODAY}T09:00:00")
    db.words.upsert(
        Word(
            lemma="looked-up",
            in_vocabulary=True,
            created_at=f"{TODAY}T10:00:00",
            updated_at=f"{TODAY}T10:00:00",
        )
    )
    db.words.upsert(
        Word(
            lemma="yesterday",
            created_at="2026-11-17T10:00:00",
            updated_at="2026-11-17T10:00:00",
        )
    )

    service = make_service(db, FakeCompleter())
    words = service.collect_today_words(article.id, TODAY)

    assert [word.lemma for word in words] == ["resilient", "looked-up"]


def test_adjust_lowers_level_and_replaces_article(
    db: Database, profile: Profile, article: Article
) -> None:
    add_word(db, article, "resilient", f"{TODAY}T09:00:00")
    fake = FakeCompleter(reassess_payload(), article_payload())
    service = make_service(db, fake)

    outcome = service.adjust("生词太多，读得很吃力", today=TODAY)

    assert isinstance(outcome, LevelAdjustment)
    assert outcome.previous_level == "B1"
    assert outcome.profile.cefr_level == "A2"
    assert outcome.level_changed is True
    assert outcome.article.level == "A2"
    assert outcome.article.article_date == TODAY
    assert outcome.target_words == ("resilient",)
    assert fake.calls == 2

    # 旧文章被替换、旧高亮被级联清除，生词本保留
    assert outcome.article.id != article.id
    assert db.articles.list_recent()[0].id == outcome.article.id
    assert db.words.get_by_lemma("resilient") is not None
    assert db.words.list_highlights(article.id) == []

    stored_profile = db.profiles.get()
    assert stored_profile is not None
    assert stored_profile.cefr_level == "A2"
    assert stored_profile.vocab_estimate == 3000

    # 反馈与今日生词都进了调级 prompt；文章 prompt 带上了软约束词
    assess_prompt = fake.messages[0][1]["content"]
    assert "生词太多" in assess_prompt
    assert "resilient" in assess_prompt
    article_prompt = fake.messages[1][1]["content"]
    assert "resilient" in article_prompt
    assert "流畅" in article_prompt
    assert "psychology" in article_prompt
    assert "生词太多" in article_prompt  # 阅读感受也用于控制生词密度


def test_soft_target_words_are_not_required(
    db: Database, profile: Profile, article: Article
) -> None:
    add_word(db, article, "resilient", f"{TODAY}T09:00:00")
    # 正文里没有 resilient，但软约束下不应判失败
    fake = FakeCompleter(reassess_payload(), article_payload())
    outcome = make_service(db, fake).adjust("有点难", today=TODAY)

    assert outcome.article.level == "A2"


def test_same_level_still_regenerates(db: Database, profile: Profile, article: Article) -> None:
    fake = FakeCompleter(reassess_payload(cefr="B1"), article_payload(level="B1"))
    outcome = make_service(db, fake).adjust("难度刚好", today=TODAY)

    assert outcome.level_changed is False
    assert outcome.profile.cefr_level == "B1"
    assert outcome.article.level == "B1"
    assert outcome.article.id != article.id


def test_missing_fields_keep_old_profile(db: Database, profile: Profile, article: Article) -> None:
    fake = FakeCompleter('{"cefr": "A2"}', article_payload())
    outcome = make_service(db, fake).adjust("太难了", today=TODAY)

    assert outcome.profile.vocab_estimate == 3800  # 沿用旧词汇量
    assert outcome.profile.strengths == ("阅读理解",)
    assert outcome.profile.weaknesses == ("学术词汇",)


def test_invalid_level_falls_back_to_current(
    db: Database, profile: Profile, article: Article
) -> None:
    fake = FakeCompleter(reassess_payload(cefr="Z9"), article_payload(level="B1"))
    outcome = make_service(db, fake).adjust("太难了", today=TODAY)

    assert outcome.profile.cefr_level == "B1"
    assert outcome.level_changed is False


def test_a1_is_mapped_to_lowest_supported_level(
    db: Database, profile: Profile, article: Article
) -> None:
    fake = FakeCompleter(reassess_payload(cefr="A1"), article_payload(level="A2"))
    outcome = make_service(db, fake).adjust("太难了", today=TODAY)

    assert outcome.profile.cefr_level == "A2"
    assert outcome.level_changed is True


def test_bad_reassess_json_changes_nothing(
    db: Database, profile: Profile, article: Article
) -> None:
    add_word(db, article, "resilient", f"{TODAY}T09:00:00")
    fake = FakeCompleter("抱歉，我无法判断。")
    service = make_service(db, fake)

    with pytest.raises(AIError):
        service.adjust("太难了", today=TODAY)

    assert fake.calls == 1
    stored = db.articles.get_by_date(TODAY)
    assert stored is not None and stored.id == article.id
    assert db.profiles.get().cefr_level == "B1"  # type: ignore[union-attr]
    assert len(db.words.list_highlights(article.id)) == 1


def test_article_generation_failure_keeps_old_article(
    db: Database, profile: Profile, article: Article
) -> None:
    fake = FakeCompleter(reassess_payload(), "垃圾", "还是垃圾")
    service = make_service(db, fake)

    with pytest.raises(AIError):
        service.adjust("太难了", today=TODAY)

    stored = db.articles.get_by_date(TODAY)
    assert stored is not None and stored.id == article.id
    assert db.profiles.get().cefr_level == "B1"  # type: ignore[union-attr]


def test_blank_feedback_rejected_before_ai(
    db: Database, profile: Profile, article: Article
) -> None:
    fake = FakeCompleter()
    with pytest.raises(AIError):
        make_service(db, fake).adjust("   ", today=TODAY)
    assert fake.calls == 0


def test_adjust_requires_profile_and_article(db: Database) -> None:
    fake = FakeCompleter()
    service = make_service(db, fake)

    with pytest.raises(AIError, match="测评"):
        service.adjust("太难了", today=TODAY)

    db.profiles.save(Profile(cefr_level="B1"))
    with pytest.raises(AIError, match="文章"):
        service.adjust("太难了", today=TODAY)

    assert fake.calls == 0


def test_no_profile_data_is_lost_when_article_missing_words(
    db: Database, profile: Profile, article: Article
) -> None:
    # 今日没有生词也应能正常调级
    fake = FakeCompleter(reassess_payload(), article_payload())
    outcome = make_service(db, fake).adjust("稍微有点难", today=TODAY)
    assert outcome.target_words == ()
    assert outcome.article.level == "A2"
