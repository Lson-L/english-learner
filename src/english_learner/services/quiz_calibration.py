"""阅读检测的本地统计：难度 / 技能正确率与历史摘要。

Phase 3 的“题目质量校准”不需要另建题库表：
- 每套题完成后的作答已经在 `reading_quizzes` 里；
- 这里用纯函数按 `difficulty / skill` 聚合正确率；
- 结果会写进讲评 prompt（让 AI 知道用户最近在哪个难度/技能上稳定），
  也会出现在每周复盘里。

注意：每篇文章的题目都是 AI 现出的，题目本身不重复；因此这里校准的是
“难度标签的表现”，不是同一道题的重复作答率。

Java 对照：≈ 一个无状态的 `QuizStatsCalculator`。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..models.entities import CEFR_LEVELS, ReadingQuiz, normalize_cefr_level


@dataclass(frozen=True)
class DifficultyStat:
    """某个难度标签下的正确率。"""

    level: str
    correct: int
    total: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass(frozen=True)
class SkillStat:
    """某个技能标签（main_idea / inference / writing…）下的正确率。"""

    skill: str
    correct: int
    total: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass(frozen=True)
class QuestionOutcome:
    """一道题的实际对错，供历史摘要使用。

    `scored=False` 表示 AI 漏评了这道产出题，统计时应跳过，
    不能把格式问题算成学生答错。
    """

    index: int
    skill: str
    difficulty: str
    kind: str
    correct: bool
    scored: bool = True


def question_outcomes(quiz: ReadingQuiz) -> tuple[QuestionOutcome, ...]:
    """把一套已完成检测还原成逐题对错（选择题 + 主动产出题）。"""
    if not quiz.completed:
        return ()

    outcomes: list[QuestionOutcome] = []
    choice_position = 0
    production_position = 0
    productions = quiz.production_answers
    for index, question in enumerate(quiz.questions, start=1):
        if question.is_short:
            production = (
                productions[production_position] if production_position < len(productions) else None
            )
            production_position += 1
            score = production.score if production is not None else 0
            scored = production.scored if production is not None else True
            outcomes.append(
                QuestionOutcome(
                    index=index,
                    skill=question.skill or "writing",
                    difficulty=normalize_cefr_level(question.difficulty, quiz.level),
                    kind="production",
                    correct=score >= 1,
                    scored=scored,
                )
            )
            continue

        chosen = quiz.answers[choice_position] if choice_position < len(quiz.answers) else -1
        choice_position += 1
        outcomes.append(
            QuestionOutcome(
                index=index,
                skill=question.skill or "unknown",
                difficulty=normalize_cefr_level(question.difficulty, quiz.level),
                kind="choice",
                correct=chosen == question.answer_index,
            )
        )
    return tuple(outcomes)


def difficulty_stats(quizzes: Sequence[ReadingQuiz]) -> tuple[DifficultyStat, ...]:
    """按难度聚合正确率，按 CEFR 顺序返回，方便 prompt 稳定展示。"""
    totals: dict[str, list[int]] = {}
    for quiz in quizzes:
        for outcome in question_outcomes(quiz):
            if not outcome.scored:
                continue
            bucket = totals.setdefault(outcome.difficulty, [0, 0])
            bucket[1] += 1
            if outcome.correct:
                bucket[0] += 1

    ordered = [level for level in CEFR_LEVELS if level in totals]
    ordered += sorted(level for level in totals if level not in CEFR_LEVELS)
    return tuple(
        DifficultyStat(level=level, correct=totals[level][0], total=totals[level][1])
        for level in ordered
    )


def skill_stats(quizzes: Sequence[ReadingQuiz]) -> tuple[SkillStat, ...]:
    """按技能聚合正确率，正确率低的技能排前面（复盘更关注薄弱点）。"""
    totals: dict[str, list[int]] = {}
    for quiz in quizzes:
        for outcome in question_outcomes(quiz):
            if not outcome.scored:
                continue
            bucket = totals.setdefault(outcome.skill, [0, 0])
            bucket[1] += 1
            if outcome.correct:
                bucket[0] += 1

    stats = [
        SkillStat(skill=skill, correct=values[0], total=values[1])
        for skill, values in totals.items()
    ]
    stats.sort(key=lambda item: (item.accuracy, -item.total, item.skill))
    return tuple(stats)


def summarizing_error_types(quizzes: Sequence[ReadingQuiz]) -> tuple[tuple[str, int], ...]:
    """统计错因出现次数（只统计非 none 的），次数多的排前面。"""
    counts: dict[str, int] = {}
    for quiz in quizzes:
        for error_type in quiz.error_types:
            value = (error_type or "").strip()
            if not value or value == "none":
                continue
            counts[value] = counts.get(value, 0) + 1
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


__all__ = [
    "DifficultyStat",
    "QuestionOutcome",
    "SkillStat",
    "difficulty_stats",
    "question_outcomes",
    "skill_stats",
    "summarizing_error_types",
]
