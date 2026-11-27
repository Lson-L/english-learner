"""阅读检测：本地证据打分与等级策略。"""

from __future__ import annotations

from collections.abc import Sequence

from ...models.entities import CEFR_LEVELS, ReadingQuiz, normalize_cefr_level
from ..level_estimator import LevelPosterior
from ..prompts import QuizHistoryEntry
from .constants import (
    _SELF_REPORT_ADJUST,
    EVIDENCE_DOWN_THRESHOLD,
    EVIDENCE_STRONG_THRESHOLD,
    EVIDENCE_UP_THRESHOLD,
    LEVEL_ORDER,
    UPGRADE_EVIDENCE_REQUIRED,
)
from .models import EvidenceSummary, LevelDecision


def score_quiz_evidence(
    level: str,
    quiz: ReadingQuiz,
    answers: tuple[int, ...],
    *,
    short_answer_score: int = 0,
    production_scores: tuple[int, ...] | None = None,
    production_scored: Sequence[bool] | None = None,
    self_report: str = "",
) -> EvidenceSummary:
    """把“答对/答错 + 题目难度 + 产出题 + 自评”折算成本地证据。

    单题权重：
    - 同档答对 +1，答错 -1；
    - 每高一档，答对额外 +0.5，答错少扣 0.5；
    - 每低一档，答对少加 0.5，答错多扣 0.5；
    - 自评“轻松/吃力”只做 ±0.25 的弱修正。
    """
    current_index = _level_index(normalize_cefr_level(level))
    if current_index is None:
        current_index = 1
    scores = list(production_scores) if production_scores is not None else [short_answer_score]
    scored_flags = (
        list(production_scored) if production_scored is not None else [True] * len(scores)
    )
    points = 0.0
    details: list[float] = []

    for position, question in enumerate(quiz.choice_questions):
        chosen = answers[position] if position < len(answers) else -1
        delta = _difficulty_delta(question.difficulty, current_index)
        correct = chosen == question.answer_index
        item_points = (1.0 + 0.5 * delta) if correct else (-1.0 + 0.5 * delta)
        details.append(item_points)
        points += item_points

    items = len(quiz.choice_questions)
    for position, question in enumerate(quiz.production_questions):
        if position < len(scored_flags) and not scored_flags[position]:
            # AI 漏评的产出题不计入证据，避免把格式问题算成答错。
            continue
        score = scores[position] if position < len(scores) else 0
        delta = _difficulty_delta(question.difficulty, current_index)
        correct = int(score) >= 1
        item_points = (1.0 + 0.5 * delta) if correct else (-1.0 + 0.5 * delta)
        details.append(item_points)
        points += item_points
        items += 1

    points += _SELF_REPORT_ADJUST.get((self_report or "").strip().lower(), 0.0)
    max_points = float(max(1, items))
    ratio = max(-1.0, min(1.0, points / max_points))
    if ratio >= EVIDENCE_UP_THRESHOLD:
        direction = "up"
    elif ratio <= EVIDENCE_DOWN_THRESHOLD:
        direction = "down"
    else:
        direction = "keep"
    return EvidenceSummary(
        ratio=ratio,
        direction=direction,
        points=points,
        max_points=max_points,
        details=tuple(details),
    )


def decide_recommended_level(
    previous: str,
    ai_suggested: str,
    evidence: EvidenceSummary,
) -> str:
    """旧版“AI 建议 + 证据”组合规则（保留给回放/测试）。

    Phase 2 起实际决策由 `level_estimator.estimate_level()` 完成；
    这个函数仍可用于解释旧数据，不参与新检测。
    """
    previous = previous.strip().upper()
    previous_index = _level_index(previous)
    if previous_index is None:
        return previous

    ai_index = _level_index(normalize_cefr_level(ai_suggested))
    if ai_index is None:
        ai_index = previous_index
    ai_direction = _sign(ai_index - previous_index)
    evidence_direction = {"up": 1, "down": -1, "keep": 0}.get(evidence.direction, 0)

    if ai_direction == 0:
        direction = evidence_direction
    elif evidence_direction == 0:
        direction = ai_direction
    elif ai_direction == evidence_direction:
        direction = ai_direction
    else:
        direction = evidence_direction if abs(evidence.ratio) >= EVIDENCE_STRONG_THRESHOLD else 0

    target = max(0, min(len(LEVEL_ORDER) - 1, previous_index + direction))
    return LEVEL_ORDER[target]


def apply_level_policy(
    current: str,
    recommended: str,
    pending_level: str = "",
    pending_count: int = 0,
) -> LevelDecision:
    """连续证据策略：向上要连续两次，向下立即生效。

    例：当前 B1，第一次建议 B2 → 保持 B1，记录 pending=B2 / count=1；
    第二次仍建议 B2 → 真正升到 B2。若中间一次建议保持 B1，则清空待定。
    """
    current = current.strip().upper()
    recommended = recommended.strip().upper()
    current_index = _level_index(current)
    recommended_index = _level_index(recommended)
    if current_index is None or recommended_index is None:
        return LevelDecision(current, "", 0, False, False)

    if recommended_index == current_index:
        return LevelDecision(current, "", 0, False, False)

    if recommended_index < current_index:
        return LevelDecision(recommended, "", 0, True, False)

    count = pending_count + 1 if pending_level.strip().upper() == recommended else 1
    if count >= UPGRADE_EVIDENCE_REQUIRED:
        return LevelDecision(recommended, "", 0, True, False)
    return LevelDecision(current, recommended, count, False, True)


def choose_boundary_level(level: str, correct: int, total: int) -> str:
    """第一轮正确率决定第二轮往哪边探测：>= 2/3 升一档，否则降一档。"""
    current = normalize_cefr_level(level, "B1")
    index = CEFR_LEVELS.index(current)
    if total <= 0:
        return current
    if correct * 3 >= total * 2:
        target = min(len(CEFR_LEVELS) - 1, index + 1)
    else:
        target = max(0, index - 1)
    return CEFR_LEVELS[target]


def _difficulty_delta(difficulty: str, current_index: int) -> int:
    index = _level_index(normalize_cefr_level(difficulty))
    if index is None:
        return 0
    return max(-2, min(2, index - current_index))


def _level_index(level: str) -> int | None:
    try:
        return LEVEL_ORDER.index(level)
    except ValueError:
        return None


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def history_entries(
    recent: list[tuple[ReadingQuiz, str]],
) -> tuple[QuizHistoryEntry, ...]:
    entries: list[QuizHistoryEntry] = []
    for quiz, title in recent:
        scored_productions = [item for item in quiz.production_answers if item.scored]
        production_score = sum(item.score for item in scored_productions)
        production_total = len(scored_productions)
        entries.append(
            QuizHistoryEntry(
                date=quiz.article_date,
                title=title or f"#{quiz.article_id}",
                level=quiz.level,
                score=quiz.choice_correct + production_score,
                total=quiz.choice_total + production_total,
                choice_correct=quiz.choice_correct,
                choice_total=quiz.choice_total,
                production_score=production_score,
                production_total=production_total,
                difficulties=tuple(
                    normalize_cefr_level(question.difficulty, quiz.level)
                    for question in quiz.questions
                ),
                error_types=quiz.error_types,
            )
        )
    return tuple(entries)


def _level_comment(
    previous: str,
    decision: LevelDecision,
    recommended: str,
    score: int,
    total: int,
    *,
    confidence: float = 0.0,
    practice_mode: bool = False,
    unscored: int = 0,
) -> str:
    if practice_mode:
        text = (
            f"检测得分 {score}/{total}；今天已经完成过一次正式检测，"
            f"本次只练手，等级保持 {decision.applied_level}。"
        )
    elif decision.waiting:
        text = (
            f"检测得分 {score}/{total}，本地后验建议升到 {recommended}"
            f"（置信度 {confidence:.2f}）；这是第 "
            f"{decision.pending_count}/{UPGRADE_EVIDENCE_REQUIRED} 次向上证据，"
            f"等级暂时保持 {decision.applied_level}。"
        )
    elif decision.changed:
        text = (
            f"检测得分 {score}/{total}，等级从 {previous} 调整为 "
            f"{decision.applied_level}（置信度 {confidence:.2f}）。"
        )
    else:
        text = (
            f"检测得分 {score}/{total}，等级保持 {decision.applied_level}"
            f"（置信度 {confidence:.2f}）。"
        )
    if unscored:
        text += f" 有 {unscored} 道产出题 AI 未给分，未计入等级证据。"
    return text


def _level_reason(
    decision: LevelDecision,
    posterior: LevelPosterior,
    *,
    practice_mode: bool,
) -> str:
    if practice_mode:
        return "今日已完成一次正式检测，本次练习不调整等级。"
    if decision.changed:
        return f"阅读检测后验：{decision.applied_level} （置信度 {posterior.confidence:.2f}）"
    if decision.waiting:
        return (
            f"后验建议升到 {decision.pending_level}，"
            f"等待连续证据（{decision.pending_count}/{UPGRADE_EVIDENCE_REQUIRED}）"
        )
    return f"阅读检测后验保持 {decision.applied_level}（置信度 {posterior.confidence:.2f}）"
