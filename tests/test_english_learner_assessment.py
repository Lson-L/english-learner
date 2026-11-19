"""摸底测评的评分、解析容错与落库测试（全部 mock，无网络）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from english_learner.services.ai_client import AIError
from english_learner.services.assessment_bank import UNKNOWN_ANSWER, get_set
from english_learner.services.assessment_service import (
    AnswerSheet,
    AssessmentService,
    score_answers,
)
from english_learner.services.prompts import build_assessment_messages
from english_learner.storage.db import Database


class FakeCompleter:
    """按顺序返回预设回复；回复可以是异常，用于模拟调用失败。"""

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


def perfect_sheet() -> AnswerSheet:
    assessment_set = get_set("b")
    return AnswerSheet(
        comprehension=tuple(q.answer_index for q in assessment_set.comprehension),
        vocabulary=tuple(q.answer_index for q in assessment_set.vocabulary),
        translation="Small habits are easy to start.",
    )


def test_score_answers() -> None:
    assessment_set = get_set("b")
    score = score_answers(assessment_set, perfect_sheet())
    assert score.comprehension_correct == 5
    assert score.vocabulary_correct == 5
    assert score.total_correct == 10
    assert score.percent == 100.0
    assert "合计 10/10" in score.summary()

    empty = score_answers(assessment_set, AnswerSheet())
    assert empty.total_correct == 0
    assert empty.percent == 0.0


def test_unknown_answer_counts_as_wrong_and_notes_go_to_prompt() -> None:
    assessment_set = get_set("b")
    sheet = AnswerSheet(
        comprehension=(UNKNOWN_ANSWER,) * 5,
        vocabulary=(UNKNOWN_ANSWER,) * 5,
        comprehension_notes=("这题问的是什么？", "", "", "", ""),
        vocabulary_notes=("单词不认识",) * 5,
    )

    score = score_answers(assessment_set, sheet)
    # 主动选“不知道”按答错计分，但不会和“未作答”混为一谈
    assert score.total_correct == 0

    content = build_assessment_messages(assessment_set, sheet, score)[1]["content"]
    assert "用户答案：不知道" in content
    assert "用户补充：这题问的是什么？" in content
    assert "用户补充：单词不认识" in content
    # 空白补充不应出现在 Prompt 里
    assert content.count("用户补充：") == 6


def test_blank_answers_are_reported_as_unanswered() -> None:
    assessment_set = get_set("b")
    sheet = AnswerSheet()
    content = build_assessment_messages(
        assessment_set, sheet, score_answers(assessment_set, sheet)
    )[1]["content"]
    assert "用户答案：未作答" in content
    # 只有具体题目的补充说明才带缩进和冒号；评分说明里的“用户补充”不算
    assert "   用户补充：" not in content


def test_grade_parses_valid_json(db: Database) -> None:
    payload = {
        "cefr": "B1",
        "vocab_estimate": 3800,
        "strengths": ["阅读理解", "基础语法"],
        "weaknesses": ["学术词汇"],
        "comment": "整体 B1 偏上",
    }
    fake = FakeCompleter(json.dumps(payload, ensure_ascii=False))
    service = AssessmentService(fake, db.profiles)

    result = service.grade(get_set("b"), perfect_sheet())

    assert fake.calls == 1
    assert result.cefr == "B1"
    assert result.vocab_estimate == 3800
    assert result.strengths == ("阅读理解", "基础语法")
    assert result.weaknesses == ("学术词汇",)
    assert result.comment == "整体 B1 偏上"


def test_grade_accepts_fenced_json(db: Database) -> None:
    fake = FakeCompleter('```json\n{"cefr": "B2", "vocab_estimate": "5200"}\n```')
    result = AssessmentService(fake, db.profiles).grade(get_set("b"), perfect_sheet())
    assert result.cefr == "B2"
    assert result.vocab_estimate == 5200


def test_invalid_cefr_falls_back_to_level_hint(db: Database) -> None:
    fake = FakeCompleter('{"cefr": "Z9", "vocab_estimate": 100}')
    result = AssessmentService(fake, db.profiles).grade(get_set("b"), perfect_sheet())
    assert result.cefr == get_set("b").level_hint == "B1"


def test_garbage_reply_raises_ai_error(db: Database) -> None:
    fake = FakeCompleter("抱歉，我无法评分。")
    service = AssessmentService(fake, db.profiles)
    with pytest.raises(AIError) as exc_info:
        service.grade(get_set("b"), perfect_sheet())
    assert "无法解析" in exc_info.value.display_message()


def test_submit_saves_profile(db: Database) -> None:
    fake = FakeCompleter(
        '{"cefr": "B1", "vocab_estimate": 3800, "strengths": ["阅读"], "comment": "不错"}'
    )
    service = AssessmentService(fake, db.profiles)
    profile = service.submit(get_set("b"), perfect_sheet())

    stored = db.profiles.get()
    assert stored is not None
    assert stored.cefr_level == profile.cefr_level == "B1"
    assert stored.vocab_estimate == 3800
    assert stored.assessed_at


def test_manual_level_bypasses_ai(db: Database) -> None:
    fake = FakeCompleter()  # 不应被调用
    service = AssessmentService(fake, db.profiles)

    profile = service.save_manual("C1")

    assert fake.calls == 0
    assert profile.cefr_level == "C1"
    stored = db.profiles.get()
    assert stored is not None and stored.cefr_level == "C1"


def test_manual_invalid_level_rejected(db: Database) -> None:
    service = AssessmentService(FakeCompleter(), db.profiles)
    with pytest.raises(ValueError):
        service.save_manual("D9")
