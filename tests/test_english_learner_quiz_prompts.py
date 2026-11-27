"""阅读检测出题多样性：题型池、随机角度、最近题干避让与 prompt 结构。"""

from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path

import pytest

from english_learner.models.entities import Article, QuizQuestion, ReadingQuiz, now_iso
from english_learner.services.prompts import (
    QUIZ_BOUNDARY_TYPE_KEYS,
    QUIZ_QUESTION_TYPES,
    QUIZ_STAGE_ONE_BUCKETS,
    build_quiz_stage_one_messages,
    build_quiz_stage_two_messages,
)
from english_learner.services.reading_quiz_service import ReadingQuizService
from english_learner.storage.db import Database

TODAY = "2026-11-27"
ARTICLE = Article(
    article_date=TODAY,
    title="The Quiet Power of Small Habits",
    content="Small habits are easy to start and easy to repeat. They add up over time.",
    level="B1",
    topic="psychology",
    word_count=18,
    created_at=now_iso(),
)


class StubCompleter:
    """只用于抽样/prompt 单测，发起 AI 调用直接失败。"""

    def complete_json(self, messages, *, temperature=None, max_tokens=None, task=""):
        raise AssertionError("本测试不发起 AI 调用")


class CapturingCompleter:
    """返回固定 JSON，并记录收到的 messages，用于断言 prompt 内容。"""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.messages: list[list[dict[str, str]]] = []

    def complete_json(self, messages, *, temperature=None, max_tokens=None, task=""):
        self.messages.append(messages)
        return self.reply


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def make_service(db: Database, seed: int = 0) -> ReadingQuizService:
    return ReadingQuizService(
        StubCompleter(),
        db.quizzes,
        db.profiles,
        shuffle_options=False,
        rng=random.Random(seed),
    )


def type_by_key(key: str):
    return next(item for item in QUIZ_QUESTION_TYPES if item.key == key)


def stage_two_payload() -> str:
    questions: list[dict[str, object]] = [
        {
            "kind": "choice",
            "prompt": f"Boundary question {index}?",
            "options": ["A", "B", "C", "D"],
            "answer_index": 1,
            "skill": "inference",
            "difficulty": "B2",
            "explanation": "解析",
        }
        for index in (1, 2)
    ]
    questions.append(
        {
            "kind": "short",
            "prompt": '"Small habits add up over time."',
            "skill": "translation",
            "difficulty": "B1",
            "reference_answer": "小习惯会随着时间不断累积。",
            "explanation": "评分要点：add up 译为累积。",
        }
    )
    questions.append(
        {
            "kind": "short",
            "prompt": '"They change our lives in ways we rarely notice."',
            "skill": "translation",
            "difficulty": "B2",
            "reference_answer": "它们以我们很少察觉的方式改变生活。",
            "explanation": "评分要点：in ways 表示方式。",
        }
    )
    return json.dumps({"questions": questions}, ensure_ascii=False)


# ------------------------------------------------------------------ 题型目录
def test_question_type_catalog_is_unique_and_maps_to_existing_skills() -> None:
    keys = [item.key for item in QUIZ_QUESTION_TYPES]
    assert len(keys) == len(set(keys))
    assert {item.skill for item in QUIZ_QUESTION_TYPES} <= {
        "main_idea",
        "detail",
        "inference",
        "vocabulary",
    }
    buckets = {item.bucket for item in QUIZ_QUESTION_TYPES}
    assert set(QUIZ_STAGE_ONE_BUCKETS) <= buckets
    assert set(QUIZ_BOUNDARY_TYPE_KEYS) <= set(keys)


def test_stage_one_plan_covers_all_three_buckets(db: Database) -> None:
    types = make_service(db, seed=3)._pick_stage_one_types()
    assert len(types) == len(QUIZ_STAGE_ONE_BUCKETS)
    assert {item.bucket for item in types} == set(QUIZ_STAGE_ONE_BUCKETS)
    assert len({item.key for item in types}) == len(types)


def test_stage_one_plan_varies_across_seeds(db: Database) -> None:
    combos = {
        tuple(item.key for item in make_service(db, seed=seed)._pick_stage_one_types())
        for seed in range(20)
    }
    assert len(combos) > 5


def test_boundary_type_plan_uses_boundary_pool(db: Database) -> None:
    types = make_service(db, seed=5)._pick_boundary_types(2)
    assert len(types) == 2
    assert {item.key for item in types} <= set(QUIZ_BOUNDARY_TYPE_KEYS)
    assert len({item.key for item in types}) == 2


# ------------------------------------------------------------------ prompt 结构
def test_stage_one_prompt_lists_types_angle_and_avoid_stems() -> None:
    types = (type_by_key("main_idea"), type_by_key("detail_locate"), type_by_key("inference"))
    messages = build_quiz_stage_one_messages(
        ARTICLE,
        "B1",
        question_types=types,
        angle="围绕文中的一处转折或对比",
        avoid_stems=["According to paragraph 1, why do small habits last?"],
    )
    user = messages[-1]["content"]
    assert "【主旨】" in user
    assert "【细节定位】" in user
    assert "【推断】" in user
    assert "围绕文中的一处转折或对比" in user
    assert "According to paragraph 1, why do small habits last?" in user
    assert "不要再用相同或近似的问法" in user


def test_stage_one_prompt_uses_placeholder_example() -> None:
    user = build_quiz_stage_one_messages(ARTICLE, "B1")[-1]["content"]
    assert '"prompt": "<英文题干>"' in user
    assert '"skill": "<该题型对应的 skill>"' in user
    assert "What is the main idea of the passage?" not in user
    assert '"skill": "detail"' not in user


def test_stage_two_prompt_uses_boundary_types_and_placeholder_example() -> None:
    types = (type_by_key("attitude"), type_by_key("reference"))
    messages = build_quiz_stage_two_messages(
        ARTICLE,
        "B1",
        "B2",
        question_types=types,
        angle="围绕开头与结尾的呼应",
    )
    user = messages[-1]["content"]
    assert "【作者态度】" in user
    assert "【指代】" in user
    assert "围绕开头与结尾的呼应" in user
    assert '"prompt": "\\"<英文原句>\\""' in user
    assert "Small habits add up over time." not in user


# ------------------------------------------------------------------ 题干避让
def test_avoid_stems_reads_recent_quizzes_and_excludes_current(db: Database) -> None:
    article = db.articles.insert(ARTICLE)
    other = db.articles.insert(
        Article(
            article_date="2026-11-26",
            title="Another article",
            content="Another passage for the quiz.",
            level="B1",
            topic="science",
            word_count=6,
            created_at=now_iso(),
        )
    )
    question = QuizQuestion(
        prompt="  According to the passage, why?  ",
        options=("A", "B", "C", "D"),
        answer_index=0,
        skill="detail",
        difficulty="B1",
    )
    db.quizzes.insert(
        ReadingQuiz(
            article_id=other.id,
            article_date="2026-11-26",
            level="B1",
            questions=(question,),
            created_at=now_iso(),
        )
    )
    db.quizzes.insert(
        ReadingQuiz(
            article_id=article.id,
            article_date=TODAY,
            level="B1",
            questions=(replace(question, prompt="Current article stem?"),),
            created_at=now_iso(),
        )
    )

    stems = make_service(db)._avoid_stems(article.id)
    assert stems == ("According to the passage, why?",)


def test_avoid_stems_dedupes_after_truncation(db: Database) -> None:
    """两条长题干前 100 字符相同时，只保留一条（先截断再去重）。"""
    article = db.articles.insert(ARTICLE)
    other = db.articles.insert(
        Article(
            article_date="2026-11-26",
            title="Another article",
            content="Another passage for the quiz.",
            level="B1",
            topic="science",
            word_count=6,
            created_at=now_iso(),
        )
    )
    prefix = "A" * 100
    questions = tuple(
        QuizQuestion(
            prompt=f"{prefix} {suffix}",
            options=("A", "B", "C", "D"),
            answer_index=0,
            skill="detail",
            difficulty="B1",
        )
        for suffix in ("first tail", "second tail")
    )
    db.quizzes.insert(
        ReadingQuiz(
            article_id=other.id,
            article_date="2026-11-26",
            level="B1",
            questions=questions,
            created_at=now_iso(),
        )
    )

    assert make_service(db)._avoid_stems(article.id) == (prefix,)


def test_stage_two_puts_stage_one_stems_into_avoid_list(db: Database) -> None:
    completer = CapturingCompleter(stage_two_payload())
    service = ReadingQuizService(
        completer,
        db.quizzes,
        db.profiles,
        shuffle_options=False,
        rng=random.Random(1),
    )
    previous = (
        QuizQuestion(
            prompt="What is the main idea of the passage?",
            options=("A", "B", "C", "D"),
            answer_index=0,
            skill="main_idea",
            difficulty="B1",
        ),
    )

    questions = service.generate_stage_two(ARTICLE, "B1", "B2", previous=previous)

    assert len(questions) == 4
    stage_two_prompts = [
        messages[-1]["content"]
        for messages in completer.messages
        if "边界探测题" in messages[-1]["content"]
    ]
    assert stage_two_prompts
    assert "What is the main idea of the passage?" in stage_two_prompts[0]
