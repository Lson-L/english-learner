"""M5.2 学习者记忆：本地派生摘要、按任务过滤、prompt 注入与空数据兼容。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from english_learner.models.entities import (
    LevelEvidence,
    Profile,
    WeeklyReview,
    Word,
    now_iso,
)
from english_learner.services.learner_memory import (
    LearnerMemory,
    LearnerMemoryService,
    load_memory,
)
from english_learner.services.prompts import (
    MAX_MEMORY_CHARS,
    MEMORY_TASK_ARTICLE,
    MEMORY_TASK_DICTIONARY,
    MEMORY_TASK_REVIEW_HELP,
    build_article_messages,
    build_dictionary_messages,
    build_learner_memory_block,
    build_review_help_messages,
    build_weekly_review_messages,
)
from english_learner.storage.db import Database

ON = date(2026, 11, 19)  # 周四；本周一 = 2026-11-16


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def make_memory_service(db: Database) -> LearnerMemoryService:
    return LearnerMemoryService(
        db.profiles,
        db.reviews,
        db.words,
        db.weekly_reviews,
        db.evidence,
        today=lambda: ON.isoformat(),
    )


def seed_word(db: Database, lemma: str, *, due_date: str = "2026-11-18") -> Word:
    return db.words.upsert(
        Word(
            lemma=lemma,
            meaning_cn=f"{lemma} 的释义",
            in_vocabulary=True,
            due_date=due_date,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )


def seed_memory_data(db: Database) -> None:
    db.profiles.save(
        Profile(
            cefr_level="B1",
            level_confidence=0.62,
            level_updated_reason="连续两次检测上调",
            weaknesses=("学术词汇", "长难句"),
        )
    )
    resilient = seed_word(db, "resilient")
    ambiguous = seed_word(db, "ambiguous")
    environment = seed_word(db, "environment")
    mastered = seed_word(db, "obsolete", due_date="2026-10-01")
    assert resilient.id and ambiguous.id and environment.id and mastered.id
    db.words.save_schedule(
        Word(
            id=mastered.id,
            lemma=mastered.lemma,
            meaning_cn=mastered.meaning_cn,
            in_vocabulary=True,
            status="mastered",
            interval_days=40,
            due_date="2026-10-01",
        )
    )

    # 反复忘记：resilient / ambiguous 各 2 次；environment 只有 1 次，不该入选。
    for word_id in (resilient.id, resilient.id, ambiguous.id, ambiguous.id, environment.id):
        db.reviews.log(
            word_id=word_id,
            rating=1,
            prev_interval=1,
            next_interval=1,
            reviewed_at="2026-11-18T20:00:00",
        )
    # 拼写证据：resilient 错 2 次、ambiguous 差一点 1 次。
    for word_id, verdict in (
        (resilient.id, "wrong"),
        (resilient.id, "wrong"),
        (ambiguous.id, "near"),
    ):
        db.reviews.log(
            word_id=word_id,
            rating=3,
            prev_interval=1,
            next_interval=6,
            reviewed_at="2026-11-18T21:00:00",
            verdict=verdict,
        )

    # 近 7 天错因：inference 2 次、detail / vocabulary 各 1 次（按字典序取 detail）。
    db.evidence.insert(
        LevelEvidence(
            source="quiz",
            observed_at="2026-11-18T20:00:00",
            evidence_date="2026-11-18",
            error_types=("inference", "vocabulary"),
        )
    )
    db.evidence.insert(
        LevelEvidence(
            source="quiz",
            observed_at="2026-11-17T20:00:00",
            evidence_date="2026-11-17",
            error_types=("inference", "detail"),
        )
    )
    # 窗口外的证据不参与统计。
    db.evidence.insert(
        LevelEvidence(
            source="quiz",
            observed_at="2026-10-01T20:00:00",
            evidence_date="2026-10-01",
            error_types=("grammar",),
        )
    )

    db.weekly_reviews.save(
        WeeklyReview(
            week_start="2026-11-09",
            summary="上周复盘",
            focus=("推断题", "B2 高频词"),
            created_at="2026-11-15T20:00:00",
        )
    )
    # 本周记录即使存在，也不能被当成“上周计划”。
    db.weekly_reviews.save(
        WeeklyReview(
            week_start="2026-11-16",
            summary="本周复盘",
            focus=("不该出现",),
            created_at="2026-11-19T20:00:00",
        )
    )


# ---------------------------------------------------------------- 汇总口径
def test_empty_database_returns_empty_memory_and_blocks(db: Database) -> None:
    memory = make_memory_service(db).build(on=ON)

    assert memory.is_empty
    for task in (
        MEMORY_TASK_ARTICLE,
        MEMORY_TASK_DICTIONARY,
        MEMORY_TASK_REVIEW_HELP,
    ):
        assert build_learner_memory_block(memory, task) == ""


def test_build_collects_profile_evidence_words_and_plan(db: Database) -> None:
    seed_memory_data(db)

    memory = make_memory_service(db).build(on=ON)

    assert memory.level == "B1"
    assert memory.confidence == pytest.approx(0.62)
    assert memory.level_reason == "连续两次检测上调"
    assert memory.weaknesses == ("学术词汇", "长难句")
    assert memory.error_types == ("inference", "detail")
    assert memory.forgotten_words == ("ambiguous", "resilient")
    assert memory.misspelled_words == ("resilient", "ambiguous")
    # 到期积压：resilient / ambiguous / environment；已掌握的 obsolete 不算。
    assert memory.due_backlog == 3
    assert memory.focus == ("推断题", "B2 高频词")


def test_error_window_covers_today_and_previous_six_days(db: Database) -> None:
    """“近 7 天”= 今天 + 之前 6 天；第 7 天前的证据不能混进来。"""
    db.profiles.save(Profile(cefr_level="B1"))
    db.evidence.insert(
        LevelEvidence(
            source="quiz",
            observed_at="2026-11-13T20:00:00",  # day - 6，应计入
            error_types=("grammar",),
        )
    )
    db.evidence.insert(
        LevelEvidence(
            source="quiz",
            observed_at="2026-11-12T20:00:00",  # day - 7，不应计入
            error_types=("main_idea",),
        )
    )

    memory = make_memory_service(db).build(on=ON)

    assert memory.error_types == ("grammar",)


def test_error_window_excludes_future_dates(db: Database) -> None:
    db.profiles.save(Profile(cefr_level="B1"))
    db.evidence.insert(
        LevelEvidence(
            source="quiz",
            observed_at="2026-11-20T00:30:00",  # 明天（系统时钟回拨场景）
            error_types=("main_idea",),
        )
    )
    db.evidence.insert(
        LevelEvidence(
            source="quiz",
            observed_at="2026-11-19T20:00:00",
            error_types=("grammar",),
        )
    )

    memory = make_memory_service(db).build(on=ON)

    assert memory.error_types == ("grammar",)


def test_error_window_crosses_year_boundary(db: Database) -> None:
    db.profiles.save(Profile(cefr_level="B1"))
    db.evidence.insert(
        LevelEvidence(
            source="quiz",
            observed_at="2025-12-27T20:00:00",  # 2026-01-02 的 day-6，应计入
            error_types=("grammar",),
        )
    )
    db.evidence.insert(
        LevelEvidence(
            source="quiz",
            observed_at="2025-12-26T20:00:00",  # day-7，不应计入
            error_types=("main_idea",),
        )
    )

    memory = make_memory_service(db).build(on=date(2026, 1, 2))

    assert memory.error_types == ("grammar",)


def test_error_types_ignore_non_skill_values(db: Database) -> None:
    db.profiles.save(Profile(cefr_level="B1"))
    db.evidence.insert(
        LevelEvidence(
            source="quiz",
            observed_at="2026-11-18T20:00:00",
            error_types=("none", "unknown", "short_answer", "rewrite", "inference"),
        )
    )

    memory = make_memory_service(db).build(on=ON)

    assert memory.error_types == ("inference",)


def test_block_filters_fields_by_task(db: Database) -> None:
    seed_memory_data(db)
    memory = make_memory_service(db).build(on=ON)

    article = build_learner_memory_block(memory, MEMORY_TASK_ARTICLE)
    dictionary = build_learner_memory_block(memory, MEMORY_TASK_DICTIONARY)
    review_help = build_learner_memory_block(memory, MEMORY_TASK_REVIEW_HELP)

    assert "测评薄弱点：学术词汇、长难句" in article
    assert "到期未复习：3 个词" in article
    assert "反复忘记" in article
    # 上周计划走文章 prompt 的 learner_plan，不在记忆块里重复渲染。
    assert "上周计划重点" not in article

    # 查词只需要等级与错因，不需要积压和计划。
    assert "近 7 天主要错因：推断、细节" in dictionary
    assert "到期未复习" not in dictionary
    assert "上周计划重点" not in dictionary

    # 复习求助需要反复忘记 / 拼写薄弱与计划，不需要积压。
    assert "反复忘记：ambiguous、resilient" in review_help
    assert "拼写薄弱：resilient、ambiguous" in review_help
    assert "上周计划重点：推断题、B2 高频词" in review_help
    assert "到期未复习" not in review_help


def test_block_is_truncated_to_char_budget() -> None:
    memory = LearnerMemory(
        level="C1",
        confidence=1.0,
        level_reason="很长的调级原因" * 30,
        weaknesses=tuple("很长的测评薄弱点" * 3 for _ in range(3)),
        error_types=("inference", "vocabulary"),
        forgotten_words=tuple(f"averylongforgottenword{index}" for index in range(5)),
        misspelled_words=tuple(f"averylongmisspelledword{index}" for index in range(5)),
        due_backlog=99,
    )

    block = build_learner_memory_block(memory, MEMORY_TASK_ARTICLE)

    assert block.startswith("【学习者记忆】")
    assert len(block) <= MAX_MEMORY_CHARS
    assert block.endswith("…")


def test_weaknesses_only_memory_renders_for_article_task() -> None:
    """只有测评薄弱点时：文章能看到，查词 / 求助因字段过滤返回空串。"""
    memory = LearnerMemory(weaknesses=("学术词汇", "长难句"))

    assert memory.is_empty is False
    assert "测评薄弱点：学术词汇、长难句" in build_learner_memory_block(memory, MEMORY_TASK_ARTICLE)
    assert build_learner_memory_block(memory, MEMORY_TASK_DICTIONARY) == ""
    assert build_learner_memory_block(memory, MEMORY_TASK_REVIEW_HELP) == ""


def test_load_memory_swallows_provider_errors() -> None:
    def broken() -> LearnerMemory:
        raise RuntimeError("db closed")

    assert load_memory(broken).is_empty
    assert load_memory(None).is_empty


# ---------------------------------------------------------------- prompt 注入
def make_word() -> Word:
    return Word(
        lemma="resilient",
        display_form="resilient",
        meaning_cn="有韧性的",
        created_at=now_iso(),
        updated_at=now_iso(),
    )


def test_prompts_are_byte_identical_when_memory_is_empty() -> None:
    profile = Profile(cefr_level="B1")
    word = make_word()

    assert build_article_messages("B1", ["habits"], "psychology") == build_article_messages(
        "B1",
        ["habits"],
        "psychology",
        learner_memory="",
        learner_plan="",
    )
    assert build_dictionary_messages("resilient", "Be resilient.", "B1") == (
        build_dictionary_messages("resilient", "Be resilient.", "B1", learner_memory="")
    )
    assert build_review_help_messages(word, 1, "忘了", "B1") == (
        build_review_help_messages(word, 1, "忘了", "B1", learner_memory="")
    )
    assert build_weekly_review_messages(
        profile, week_start="2026-11-16", week_end="2026-11-19"
    ) == build_weekly_review_messages(
        profile,
        week_start="2026-11-16",
        week_end="2026-11-19",
        previous_focus=(),
    )


def test_article_prompt_contains_memory_and_plan(db: Database) -> None:
    seed_memory_data(db)
    memory = make_memory_service(db).build(on=ON)
    block = build_learner_memory_block(memory, MEMORY_TASK_ARTICLE)

    messages = build_article_messages(
        "B1",
        ["habits"],
        "psychology",
        learner_memory=block,
        learner_plan="、".join(memory.focus),
    )
    prompt = messages[-1]["content"]

    assert "【学习者记忆】" in prompt
    assert "测评薄弱点：学术词汇、长难句" in prompt
    assert "近 7 天主要错因：推断、细节" in prompt
    assert "上周复盘给学习者定的重点是“推断题、B2 高频词”" in prompt
    assert "以上是学习者的长期记忆" in prompt


def test_dictionary_and_review_help_prompts_accept_memory() -> None:
    memory = LearnerMemory(
        level="B1",
        error_types=("inference",),
        forgotten_words=("resilient",),
        misspelled_words=("environment",),
        focus=("推断题",),
    )

    dictionary_prompt = build_dictionary_messages(
        "resilient",
        "Be resilient.",
        "B1",
        learner_memory=build_learner_memory_block(memory, MEMORY_TASK_DICTIONARY),
    )[-1]["content"]
    help_prompt = build_review_help_messages(
        make_word(),
        1,
        "忘了",
        "B1",
        learner_memory=build_learner_memory_block(memory, MEMORY_TASK_REVIEW_HELP),
    )[-1]["content"]

    assert "【学习者记忆】" in dictionary_prompt
    assert "近 7 天主要错因：推断" in dictionary_prompt
    assert "【学习者记忆】" in help_prompt
    assert "反复忘记：resilient" in help_prompt


def test_weekly_review_prompt_contains_previous_plan() -> None:
    profile = Profile(cefr_level="B1")

    prompt = build_weekly_review_messages(
        profile,
        week_start="2026-11-16",
        week_end="2026-11-19",
        previous_focus=("推断题", "B2 高频词"),
    )[-1]["content"]

    assert "【上周计划重点】推断题、B2 高频词" in prompt
    assert "上周计划完成度" in prompt
