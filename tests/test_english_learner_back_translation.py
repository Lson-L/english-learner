"""M5.4 回译练习：抽句、译文 / 批改解析、缓存出题与批量批改。"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from english_learner.models.entities import Article, WritingAttempt, now_iso
from english_learner.services.ai_client import AIError
from english_learner.services.back_translation import (
    InvalidBackTranslationError,
    normalize_error_types,
    parse_grades,
    parse_translations,
    pick_sentences,
    split_sentences,
)
from english_learner.services.back_translation_service import (
    TASK_GRADE,
    TASK_TRANSLATE,
    BackTranslationService,
)
from english_learner.services.learner_memory import LearnerMemoryService
from english_learner.services.prompts import (
    MEMORY_TASK_ARTICLE,
    build_back_translation_grade_messages,
    build_back_translation_translate_messages,
    build_learner_memory_block,
)
from english_learner.storage.db import Database

TODAY = "2026-11-20"
CONTENT = (
    "Resilient people recover from setbacks and keep moving toward their goals "
    "even when progress feels slow. "
    "The team worked late into the night to finish the project before the deadline. "
    "Small habits add up over time and quietly change our lives in ways we rarely notice. "
    "Rain stopped."
)


# ------------------------------------------------------------------ 桩模型
class FakeCompleter:
    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[str] = []
        self.messages: list[list[dict[str, str]]] = []

    def complete_json(self, messages, *, temperature=None, max_tokens=None, task="") -> str:
        self.calls.append(task)
        self.messages.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return str(reply)


def translation_payload(*values: str) -> str:
    return json.dumps({"translations": list(values)}, ensure_ascii=False)


def grading_payload(scores: tuple[int, ...] = (2, 0, 2)) -> str:
    items = [
        {
            "index": index,
            "score": score,
            "edited": f"Edited sentence {index}.",
            "reason": f"第 {index} 句的原因",
            "error_types": ["tense"] if score == 0 else [],
        }
        for index, score in enumerate(scores)
    ]
    return json.dumps({"items": items}, ensure_ascii=False)


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


def make_article(db: Database, **overrides) -> Article:
    values = {
        "article_date": TODAY,
        "title": "Small Habits",
        "content": CONTENT,
        "level": "B1",
        "word_count": len(CONTENT.split()),
        "target_words": ("resilient",),
        "created_at": now_iso(),
    }
    values.update(overrides)
    article = db.articles.insert(Article(**values))
    assert article.id is not None
    return article


def make_service(db: Database, completer: FakeCompleter) -> BackTranslationService:
    return BackTranslationService(completer, db.writing_attempts)


# ------------------------------------------------------------------ 切句
def test_split_sentences_handles_abbreviations_and_punctuation() -> None:
    sentences = split_sentences(
        "Small habits add up over time. They quietly change our lives! "
        "Do you agree? Dr. Chen thinks so. The U.S. team agrees. "
        "It costs 3.5 dollars. Use tools, e.g. a hammer."
    )

    assert sentences == (
        "Small habits add up over time.",
        "They quietly change our lives!",
        "Do you agree?",
        "Dr. Chen thinks so.",
        "The U.S. team agrees.",
        "It costs 3.5 dollars.",
        "Use tools, e.g. a hammer.",
    )


def test_split_sentences_returns_empty_for_blank_text() -> None:
    assert split_sentences("   \n\n ") == ()


def test_pick_sentences_prefers_target_words_and_skips_tiny_sentences() -> None:
    picked = pick_sentences(CONTENT, ("resilient",), limit=2)

    assert len(picked) == 2
    # 含目标词的长句优先；过短的 "Rain stopped." 被排除。
    assert picked[0].startswith("Resilient people")
    assert "Rain stopped." not in picked


def test_pick_sentences_keeps_article_order() -> None:
    picked = pick_sentences(CONTENT, limit=3)

    positions = [CONTENT.index(sentence) for sentence in picked]
    assert positions == sorted(positions)


def test_pick_sentences_drops_duplicates() -> None:
    content = (
        "Resilient people recover from setbacks and keep moving toward their goals. "
        "Resilient people recover from setbacks and keep moving toward their goals. "
        "The team worked late into the night to finish the project before the deadline."
    )
    picked = pick_sentences(content, limit=3)

    assert len(picked) == 2


# ------------------------------------------------------------- 译文解析
def test_parse_translations_accepts_fenced_json() -> None:
    text = '```json\n{"translations": ["一", "二", "三"]}\n```'
    assert parse_translations(text, 3) == ("一", "二", "三")


def test_parse_translations_accepts_object_items() -> None:
    text = json.dumps(
        {"translations": [{"translation": "一"}, {"translation_cn": "二"}, {"text": "三"}]},
        ensure_ascii=False,
    )
    assert parse_translations(text, 3) == ("一", "二", "三")


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"translations": ["一", "二"]}, "数量不符"),
        ({"translations": ["一", "", "三"]}, "空译文"),
        ({"translations": ["one", "two", "three"]}, "没有中文"),
        ({"items": []}, "translations"),
    ],
)
def test_parse_translations_rejects_bad_payloads(payload: dict, reason: str) -> None:
    with pytest.raises(InvalidBackTranslationError) as excinfo:
        parse_translations(json.dumps(payload), 3)
    assert reason in str(excinfo.value)


# ------------------------------------------------------------- 批改解析
def test_parse_grades_maps_by_index_and_clamps_scores() -> None:
    text = json.dumps(
        {
            "items": [
                {
                    "index": 1,
                    "score": 1,
                    "edited": "Fixed one.",
                    "reason": "时态不对",
                    "error_types": ["时态", "unknown"],
                },
                {
                    "index": 0,
                    "score": 2,
                    "edited": "Good.",
                    "reason": "很好",
                    "error_types": [],
                },
            ]
        },
        ensure_ascii=False,
    )

    grades = parse_grades(text, 2)

    assert grades[0].score == 2
    assert grades[1].score == 0
    assert grades[1].error_types == ("tense",)
    assert grades[1].reason == "时态不对"


@pytest.mark.parametrize(
    ("items", "reason"),
    [
        ([{"index": 0, "score": 2, "reason": "ok"}], "条数不符"),
        (
            [
                {"index": 0, "score": 2, "reason": "ok"},
                {"index": 0, "score": 2, "reason": "重复"},
            ],
            "重复",
        ),
        (
            [
                {"index": 0, "score": 2, "reason": "ok"},
                {"index": 2, "score": 2, "reason": "越界"},
            ],
            "越界",
        ),
        (
            [
                {"index": 0, "score": 2, "reason": "ok"},
                {"index": 1, "score": 2, "reason": ""},
            ],
            "缺少原因",
        ),
        (
            [
                {"index": 0, "score": 2, "reason": "ok"},
                {"index": 1, "reason": "漏了分数"},
            ],
            "缺少有效分数",
        ),
        (
            [
                {"index": 0, "score": 2, "reason": "ok"},
                {"index": 1, "score": "两分", "reason": "写成中文"},
            ],
            "缺少有效分数",
        ),
    ],
)
def test_parse_grades_rejects_bad_payloads(items: list[dict], reason: str) -> None:
    with pytest.raises(InvalidBackTranslationError) as excinfo:
        parse_grades(json.dumps({"items": items}, ensure_ascii=False), 2)
    assert reason in str(excinfo.value)


def test_normalize_error_types_supports_keys_labels_and_dedupe() -> None:
    assert normalize_error_types(["tense", "时态", "collocation", " BOGUS "]) == (
        "tense",
        "collocation",
    )
    assert normalize_error_types("冠词、介词") == ("article", "preposition")
    assert normalize_error_types(None) == ()


# ------------------------------------------------------------- 提示词
def test_translate_prompt_lists_sentences_in_order() -> None:
    messages = build_back_translation_translate_messages(
        ["First sentence.", "Second sentence.", "Third sentence."],
        "B1",
    )
    user = messages[-1]["content"]

    assert "CEFR B1" in user
    assert "1. First sentence." in user
    assert "3. Third sentence." in user
    assert '"translations"' in user


def test_grade_prompt_contains_pairs_and_binary_score() -> None:
    messages = build_back_translation_grade_messages(
        [
            ("Original one.", "中文一", "My one."),
            ("Original two.", "中文二", "My two."),
            ("Original three.", "中文三", "My three."),
        ],
        "B1",
    )
    user = messages[-1]["content"]

    assert "Original one." in user and "My one." in user
    assert "中文三" in user
    assert "只给 0 或 2" in user
    assert '"error_types"' in user


# ------------------------------------------------------------- 服务编排
def test_load_or_create_translates_once_then_caches(db: Database) -> None:
    article = make_article(db)
    completer = FakeCompleter(
        translation_payload("有韧性的人会从挫折中恢复。", "团队工作到深夜。", "小习惯会累积。")
    )
    service = make_service(db, completer)

    first = service.load_or_create(article)
    second = service.load_or_create(article)

    assert len(first) == 3
    assert first == second
    assert [item.reference_cn for item in first] == [
        "有韧性的人会从挫折中恢复。",
        "团队工作到深夜。",
        "小习惯会累积。",
    ]
    assert completer.calls == [TASK_TRANSLATE]
    assert first[0].sentence_en.startswith("Resilient people")


def test_load_or_create_retries_invalid_translation(db: Database) -> None:
    article = make_article(db)
    completer = FakeCompleter(
        '{"translations": ["只有一句"]}',
        translation_payload("一", "二", "三"),
    )
    service = make_service(db, completer)

    attempts = service.load_or_create(article)

    assert len(attempts) == 3
    assert completer.calls == [TASK_TRANSLATE, TASK_TRANSLATE]


def test_load_or_create_raises_after_two_bad_translations(db: Database) -> None:
    article = make_article(db)
    completer = FakeCompleter('{"translations": []}', '{"translations": []}')
    service = make_service(db, completer)

    with pytest.raises(AIError) as excinfo:
        service.load_or_create(article)
    assert "没有通过校验" in excinfo.value.message
    assert db.writing_attempts.list_for_article(article.id or 0) == []


def test_grade_writes_answers_verdicts_and_scores(db: Database) -> None:
    article = make_article(db)
    completer = FakeCompleter(translation_payload("一", "二", "三"))
    service = make_service(db, completer)
    attempts = service.load_or_create(article)

    completer.replies = [grading_payload((2, 0, 2))]
    graded = service.grade(
        article,
        ["Resilient people recover.", "Wrong sentence.", "Small habits add up."],
    )

    assert completer.calls[-1] == TASK_GRADE
    assert [item.score for item in graded] == [2, 0, 2]
    assert graded[1].answer_en == "Wrong sentence."
    assert graded[1].verdict is not None
    assert graded[1].verdict["error_types"] == ["tense"]
    assert all(item.graded for item in graded)
    assert [item.id for item in graded] == [item.id for item in attempts]

    stored = db.writing_attempts.list_for_article(article.id or 0)
    assert [item.score for item in stored] == [2, 0, 2]
    assert stored[0].graded_at is not None


def test_grade_rejects_blank_answers_without_ai_call(db: Database) -> None:
    article = make_article(db)
    completer = FakeCompleter(translation_payload("一", "二", "三"))
    service = make_service(db, completer)
    service.load_or_create(article)
    calls_before = len(completer.calls)

    with pytest.raises(ValueError):
        service.grade(article, ["One.", "", "Three."])

    assert len(completer.calls) == calls_before


def test_grade_keeps_answers_when_ai_fails_twice(db: Database) -> None:
    article = make_article(db)
    completer = FakeCompleter(translation_payload("一", "二", "三"))
    service = make_service(db, completer)
    service.load_or_create(article)

    completer.replies = ['{"items": []}', '{"items": []}']
    with pytest.raises(AIError):
        service.grade(article, ["One.", "Two.", "Three."])

    stored = db.writing_attempts.list_for_article(article.id or 0)
    assert all(not item.graded for item in stored)
    assert all(item.answer_en == "" for item in stored)


def test_replacing_article_cascades_writing_attempts(db: Database) -> None:
    article = make_article(db)
    completer = FakeCompleter(translation_payload("一", "二", "三"))
    make_service(db, completer).load_or_create(article)
    assert len(db.writing_attempts.list_for_article(article.id or 0)) == 3

    db.articles.replace_by_date(
        Article(
            article_date=TODAY,
            title="New Article",
            content="Fresh content with enough words to be valid.",
            level="B1",
            word_count=8,
            created_at=now_iso(),
        )
    )

    assert db.writing_attempts.list_for_article(article.id or 0) == []


# ------------------------------------------------------------- 学习者记忆
def test_learner_memory_collects_writing_error_types(db: Database) -> None:
    article = make_article(db)
    completer = FakeCompleter(translation_payload("一", "二", "三"))
    service = make_service(db, completer)
    service.load_or_create(article)
    completer.replies = [
        json.dumps(
            {
                "items": [
                    {
                        "index": 0,
                        "score": 0,
                        "edited": "A.",
                        "reason": "时态",
                        "error_types": ["tense"],
                    },
                    {
                        "index": 1,
                        "score": 0,
                        "edited": "B.",
                        "reason": "冠词",
                        "error_types": ["article"],
                    },
                    {
                        "index": 2,
                        "score": 2,
                        "edited": "C.",
                        "reason": "很好",
                        "error_types": [],
                    },
                ]
            },
            ensure_ascii=False,
        )
    ]
    graded = service.grade(article, ["One.", "Two.", "Three."])
    graded_at = (date(2026, 11, 20) - timedelta(days=1)).isoformat() + "T20:00:00"
    db.writing_attempts.save_grades(
        article.id or 0,
        [
            (
                item.position,
                item.answer_en,
                {"error_types": ["tense", "article"]} if item.position < 2 else {},
                0 if item.position < 2 else 2,
                graded_at,
            )
            for item in graded
        ],
    )

    memory_service = LearnerMemoryService(
        db.profiles,
        db.reviews,
        db.words,
        db.weekly_reviews,
        db.evidence,
        db.writing_attempts,
        today=lambda: "2026-11-20",
    )
    memory = memory_service.build()

    assert memory.writing_error_types == ("article", "tense")
    block = build_learner_memory_block(memory, MEMORY_TASK_ARTICLE)
    assert "英文写作薄弱：冠词、时态" in block


def test_writing_attempts_entity_roundtrip(db: Database) -> None:
    article = make_article(db)
    stored = db.writing_attempts.replace_for_article(
        article.id or 0,
        [
            WritingAttempt(
                article_id=article.id or 0,
                article_date=TODAY,
                position=0,
                sentence_en="Small habits add up.",
                reference_cn="小习惯会累积。",
                created_at=now_iso(),
            )
        ],
    )

    assert len(stored) == 1
    assert stored[0].sentence_en == "Small habits add up."
    assert stored[0].reference_cn == "小习惯会累积。"
    assert stored[0].graded is False
    assert stored[0].verdict is None
