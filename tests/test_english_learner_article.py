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
from english_learner.services.learner_memory import LearnerMemory
from english_learner.services.prompts import build_article_messages
from english_learner.services.readability import ReadabilityThresholds
from english_learner.storage.db import Database

# 12 个词；重复 6 次 = 72 词，3 段 = 216 词，落在 200~400 的校验区间内。
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


def test_target_word_accepts_common_inflections() -> None:
    """目标词用了派生/屈折形式时不应误判为“没写目标词”。"""
    payload = make_payload(target_words=["resilient", "study", "make"])
    sentence = (
        "Resilience helps people recover, and students who studied hard "
        "keep making progress every day."
    )
    payload["paragraphs"] = [" ".join([sentence] * 6) for _ in range(3)]

    draft = parse_article_draft(json.dumps(payload, ensure_ascii=False), "B1")

    assert draft.target_words == ("resilient", "study", "make")


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


def test_article_uses_learner_memory_instead_of_profile_focus(db: Database) -> None:
    """有记忆时用记忆块替代画像里的一行薄弱点，并带上上周计划。"""
    profile = db.profiles.save(Profile(cefr_level="B1", weaknesses=("学术词汇",)))
    memory = LearnerMemory(
        level="B1",
        weaknesses=("学术词汇",),
        error_types=("inference",),
        focus=("推断题", "B2 高频词"),
    )
    fake = FakeCompleter(payload_text())
    service = ArticleService(
        fake,
        db.articles,
        db.words,
        memory_provider=lambda: memory,
    )

    service.get_or_create_today(profile, today="2026-11-18")

    prompt = fake.messages[0][1]["content"]
    assert "【学习者记忆】" in prompt
    assert "测评薄弱点：学术词汇" in prompt
    assert "近 7 天主要错因：推断" in prompt
    assert "上周复盘给学习者定的重点是“推断题、B2 高频词”" in prompt
    # 记忆非空时不再重复注入画像里的旧薄弱点行。
    assert "学习者近期的薄弱点是" not in prompt


def test_article_without_memory_keeps_profile_focus(db: Database) -> None:
    """没有记忆（或记忆为空）时保持 M5.2 之前的 prompt 行为。"""
    profile = db.profiles.save(Profile(cefr_level="B1", weaknesses=("学术词汇",)))
    fake = FakeCompleter(payload_text())
    service = make_service(db, fake)

    service.get_or_create_today(profile, today="2026-11-18")

    prompt = fake.messages[0][1]["content"]
    assert "学习者近期的薄弱点是“学术词汇”" in prompt
    assert "【学习者记忆】" not in prompt
    assert "上周复盘给学习者定的重点" not in prompt


# ------------------------------------------------------- M5.3 本地可读性指标
READABILITY_WORDLIST = {
    "small": "A1",
    "habit": "A1",
    "are": "A1",
    "easy": "A1",
    "to": "A1",
    "start": "A1",
    "and": "A1",
    "repeat": "A2",
    "every": "A1",
    "day": "A1",
}
# 12 个正文里完全没有的“生词”，用来构造一篇明显超阈值的文章。
HARD_SENTENCE = "Zorb quix flarn pleek stoop vlim brunt shree glim trove blen krast."


def hard_payload_text(**overrides) -> str:
    paragraphs = [" ".join([HARD_SENTENCE] * 6) for _ in range(3)]
    payload = {
        "title": "Hard Article",
        "level": "B1",
        "topic": "science",
        "paragraphs": paragraphs,
        "target_words": [],
        "glossary": [],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def make_readability_service(
    db: Database,
    fake: FakeCompleter,
    *,
    thresholds: ReadabilityThresholds | None = None,
) -> ArticleService:
    return ArticleService(
        fake,
        db.articles,
        db.words,
        readability=thresholds,
        wordlist_provider=lambda: READABILITY_WORDLIST,
    )


def test_generated_article_records_readability_metrics(db: Database, profile: Profile) -> None:
    fake = FakeCompleter(payload_text())
    service = make_readability_service(db, fake)

    article = service.get_or_create_today(profile, today="2026-11-20")

    assert article.metrics is not None
    assert article.metrics["word_total"] > 0
    assert article.metrics["wordlist_size"] == len(READABILITY_WORDLIST)
    assert article.metrics["target_total"] == 1
    assert article.metrics["target_found"] == 1
    # 入库后按 JSON 还原，字段不丢。
    stored = db.articles.get_by_date("2026-11-20")
    assert stored is not None
    assert stored.metrics == article.metrics


def test_readability_metrics_can_be_disabled(db: Database, profile: Profile) -> None:
    fake = FakeCompleter(payload_text())
    service = make_readability_service(db, fake, thresholds=ReadabilityThresholds(enabled=False))

    article = service.get_or_create_today(profile, today="2026-11-20")

    assert article.metrics is None
    stored = db.articles.get_by_date("2026-11-20")
    assert stored is not None and stored.metrics is None


def test_hard_article_is_recorded_but_not_retried_by_default(
    db: Database, profile: Profile
) -> None:
    """默认只记录 / 告警：不因为指标难看就多花一次调用。"""
    fake = FakeCompleter(hard_payload_text())
    service = make_readability_service(db, fake)

    article = service.get_or_create_today(profile, today="2026-11-20")

    assert fake.calls == 1
    assert article.title == "Hard Article"
    assert article.metrics is not None
    assert article.metrics["unknown_count"] == article.metrics["word_total"]


def test_retry_enabled_rewrites_article_that_is_too_hard(db: Database, profile: Profile) -> None:
    fake = FakeCompleter(hard_payload_text(), payload_text())
    service = make_readability_service(
        db,
        fake,
        thresholds=ReadabilityThresholds(retry_enabled=True),
    )

    article = service.get_or_create_today(profile, today="2026-11-20")

    assert fake.calls == 2
    assert article.title == "Small Habits"
    # 重试走现有 repair_note 通道，把“偏难”原因喂回模型。
    assert "偏难" in fake.messages[1][-1]["content"]
    assert article.metrics is not None
    assert article.metrics["unknown_count"] == 0


def test_known_user_words_count_as_known(db: Database, profile: Profile) -> None:
    """生词本里已加入 / 间隔够长的词，不再算生词。"""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO words (lemma, in_vocabulary, status, interval_days, "
            "created_at, updated_at) VALUES ('zorb', 1, 'learning', 0, ?, ?)",
            (now_iso(), now_iso()),
        )
        conn.execute(
            "INSERT INTO words (lemma, in_vocabulary, status, interval_days, "
            "created_at, updated_at) VALUES ('quix', 0, 'mastered', 45, ?, ?)",
            (now_iso(), now_iso()),
        )
    known = db.words.list_known_lemmas()
    assert "zorb" in known and "quix" in known

    fake = FakeCompleter(hard_payload_text())
    service = make_readability_service(db, fake)
    article = service.get_or_create_today(profile, today="2026-11-20")

    assert article.metrics is not None
    # 每段 6 句 × 12 词，其中每句固定出现 zorb / quix 两个已知词。
    assert article.metrics["unknown_count"] < article.metrics["word_total"]


def test_readability_retry_failure_keeps_first_article(db: Database, profile: Profile) -> None:
    """P2 回归：第二次生成校验失败时，不能把第一篇已通过校验的文章丢掉。"""
    fake = FakeCompleter(hard_payload_text(), "not json")
    service = make_readability_service(
        db,
        fake,
        thresholds=ReadabilityThresholds(retry_enabled=True),
    )

    article = service.get_or_create_today(profile, today="2026-11-20")

    assert fake.calls == 2
    assert article.title == "Hard Article"
    assert article.metrics is not None


def test_readability_retry_ai_error_keeps_first_article(db: Database, profile: Profile) -> None:
    """第二次生成网络失败时同样保留第一篇，不抛给界面。"""
    fake = FakeCompleter(hard_payload_text(), AIError("网络错误"))
    service = make_readability_service(
        db,
        fake,
        thresholds=ReadabilityThresholds(retry_enabled=True),
    )

    article = service.get_or_create_today(profile, today="2026-11-20")

    assert article.title == "Hard Article"
    assert article.metrics is not None
