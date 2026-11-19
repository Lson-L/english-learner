"""本地等级后验估计（Phase 2）。

为什么不用“答对几道就加减一档”：
- 单次检测只有 5~7 个信号，噪声很大；
- 同一道题对不同等级的人难度不同，简单的二值信号会浪费“题目难度”信息。

这里用一个极简的 Rasch / 逻辑斯蒂模型：

    P(correct | L, d) = 1 / (1 + exp(k * (index(d) - index(L))))

- `L` 是候选等级（A2/B1/B2/C1），`d` 是题目难度；
- 同档题答对概率 0.5，高两档题答对概率约 0.11，低两档约 0.89；
- 对每道题按 `correct / incorrect` 计算似然，乘上以当前等级为中心的
  先验，得到四个候选等级的后验概率；
- 只在后验峰值（在“最多跳一档”的窗口内）置信度 >= 0.6 时才建议改级，
  升级仍由调用方的连续证据策略决定是否立即生效。

本模块是纯函数：不碰数据库、不碰界面、不发网络请求，方便单测和回放。

Java 对照：
- `LevelPosterior` ≈ 一个不可变的 DTO；
- `estimate_level()` ≈ 无状态的静态工具方法，类似 `ScoreCalculator`。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from math import exp, log

from ..models.entities import (
    CEFR_LEVELS,
    BandReviewStat,
    EvidenceObservation,
    LevelEvidence,
    ReadingQuiz,
    normalize_cefr_level,
)

# 难度斜率：越大表示“高两档题”越难，答对它的证据越强。
DIFFICULTY_SLOPE = 1.1
# 后验置信度门槛：低于这个值只展示、不改等级。
CONFIDENCE_THRESHOLD = 0.6
# 单次检测最多建议跳一档，避免偶发全对把用户直接带到 C1。
MAX_LEVEL_STEP = 1
# 先验：当前等级权重 1.0，每远一档乘 0.4。
PRIOR_DISTANCE_WEIGHT = 0.4
# 历史证据只回放最近 5 次，且越久权重越低，避免半年前的表现一直压着现在的判断。
HISTORY_LIMIT = 5
HISTORY_DECAY = 0.75
# 复习词档证据：至少 4 次复习才计入，单次最多 0.8 权重。
REVIEW_BAND_MIN_REVIEWS = 4
REVIEW_BAND_WEIGHT_PER_REVIEW = 0.04
REVIEW_BAND_WEIGHT_CAP = 0.8
REVIEW_BAND_GOOD_ACCURACY = 0.6

# 概率裁剪：避免极端难度题出现 0/1 的概率导致一条证据“封死”其它等级。
_MIN_PROBABILITY = 0.02
_MAX_PROBABILITY = 0.98


def _level_index(level: str) -> int | None:
    try:
        return CEFR_LEVELS.index(level)
    except ValueError:
        return None


def _normalized_index(level: str, fallback: int) -> int:
    index = _level_index(normalize_cefr_level(level))
    return fallback if index is None else index


def _prior(current_index: int) -> tuple[float, ...]:
    """以当前等级为中心的离散先验，保证没有证据时不会乱跳。"""
    raw = [PRIOR_DISTANCE_WEIGHT ** abs(index - current_index) for index in range(len(CEFR_LEVELS))]
    total = sum(raw)
    return tuple(value / total for value in raw)


def _correct_probability(candidate_index: int, difficulty_index: int) -> float:
    probability = 1.0 / (1.0 + exp(DIFFICULTY_SLOPE * (difficulty_index - candidate_index)))
    return max(_MIN_PROBABILITY, min(_MAX_PROBABILITY, probability))


def _log_likelihood(
    candidate_index: int,
    observations: Sequence[EvidenceObservation],
    default_difficulty: int,
) -> float:
    total = 0.0
    for observation in observations:
        weight = max(0.0, min(2.0, float(observation.weight)))
        if weight <= 0:
            continue
        difficulty_index = _normalized_index(observation.difficulty, default_difficulty)
        probability = _correct_probability(candidate_index, difficulty_index)
        if not observation.correct:
            probability = 1.0 - probability
        # 概率已裁剪到 [0.02, 0.98]，log 不会出现 -inf。
        total += weight * log(probability)
    return total


def _normalize(values: Sequence[float]) -> tuple[float, ...]:
    total = sum(values)
    if total <= 0:
        return tuple(1.0 / len(values) for _ in values)
    return tuple(value / total for value in values)


def estimate_level(
    current_level: str,
    observations: Sequence[EvidenceObservation] = (),
    *,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> LevelPosterior:
    """根据当前等级 + 观测，返回四档后验、MAP 与建议等级。

    实现细节：
    - 先验以 `current_level` 为中心；观测按 `difficulty` 计算似然；
    - 后验在全四档上归一化，供界面展示分布；
    - 建议等级只在“当前等级 ±1”窗口内选，避免跨级跳变；
    - 窗口内峰值概率低于 `confidence_threshold` 时建议保持当前等级。
    """
    current = normalize_cefr_level(current_level)
    if not current:
        current = "B1"
    current_index = CEFR_LEVELS.index(current)

    prior = _prior(current_index)
    log_posterior = [
        log(prior[index]) + _log_likelihood(index, observations, current_index)
        for index in range(len(CEFR_LEVELS))
    ]
    # 减去最大值再 exp，数值上更稳。
    peak = max(log_posterior)
    posterior = _normalize([exp(value - peak) for value in log_posterior])

    map_index = max(range(len(posterior)), key=lambda index: posterior[index])
    map_level = CEFR_LEVELS[map_index]

    low = max(0, current_index - MAX_LEVEL_STEP)
    high = min(len(CEFR_LEVELS) - 1, current_index + MAX_LEVEL_STEP)
    window_indices = list(range(low, high + 1))
    window_mass = sum(posterior[index] for index in window_indices) or 1.0
    window_probabilities = {index: posterior[index] / window_mass for index in window_indices}
    window_map = max(window_indices, key=lambda index: window_probabilities[index])
    confidence = window_probabilities[window_map]

    if window_map == current_index or confidence >= confidence_threshold:
        recommended = CEFR_LEVELS[window_map]
    else:
        recommended = current

    effective = sum(
        max(0.0, min(2.0, float(item.weight))) for item in observations if item.weight > 0
    )
    return LevelPosterior(
        probabilities=posterior,
        map_level=map_level,
        confidence=confidence,
        recommended_level=recommended,
        effective_observations=effective,
        observation_count=len(observations),
    )


def observations_from_quiz(
    quiz: ReadingQuiz,
    answers: Sequence[int],
    production_scores: Sequence[int] = (),
    *,
    production_scored: Sequence[bool] = (),
) -> tuple[EvidenceObservation, ...]:
    """把一次检测的作答折算成题目级观测（选择题 + 主动产出题）。

    `production_scored` 里为 False 的产出题（AI 漏评分）不产生观测，
    避免把格式问题当成学生答错，污染等级后验。
    """
    default_level = normalize_cefr_level(quiz.level, "B1")
    observations: list[EvidenceObservation] = []

    for position, question in enumerate(quiz.choice_questions):
        chosen = answers[position] if position < len(answers) else -1
        observations.append(
            EvidenceObservation(
                difficulty=normalize_cefr_level(question.difficulty, default_level),
                correct=chosen == question.answer_index,
                weight=1.0,
                source="quiz",
            )
        )

    for position, question in enumerate(quiz.production_questions):
        if position < len(production_scored) and not production_scored[position]:
            continue
        score = production_scores[position] if position < len(production_scores) else 0
        observations.append(
            EvidenceObservation(
                difficulty=normalize_cefr_level(question.difficulty, default_level),
                correct=int(score) >= 1,
                weight=1.0,
                source="production",
            )
        )
    return tuple(observations)


def observations_from_self_report(
    self_report: str,
    level: str,
) -> tuple[EvidenceObservation, ...]:
    """自评只是弱证据：轻松/吃力各 0.25 权重，刚好不产生观测。"""
    value = (self_report or "").strip().lower()
    difficulty = normalize_cefr_level(level, "B1")
    if value in {"easy", "轻松"}:
        return (EvidenceObservation(difficulty, True, 0.25, "self_report"),)
    if value in {"hard", "吃力"}:
        return (EvidenceObservation(difficulty, False, 0.25, "self_report"),)
    return ()


def replay_evidence(
    evidence: Sequence[LevelEvidence],
    *,
    limit: int = HISTORY_LIMIT,
    decay: float = HISTORY_DECAY,
) -> tuple[EvidenceObservation, ...]:
    """回放最近几次检测的题目级观测，按时间衰减权重。

    传入顺序必须是“最新在前”（`LevelEvidenceRepo.list_recent` 的返回顺序）。
    """
    replayed: list[EvidenceObservation] = []
    for age, item in enumerate(list(evidence)[:limit]):
        factor = decay**age
        if factor <= 0:
            continue
        for observation in item.observations:
            weight = observation.weight * factor
            if weight < 0.01:
                continue
            replayed.append(
                replace(
                    observation,
                    weight=round(weight, 3),
                    source=f"history:{observation.source}",
                )
            )
    return tuple(replayed)


def review_band_observations(
    stats: Sequence[BandReviewStat],
) -> tuple[EvidenceObservation, ...]:
    """把“各词档复习正确率”变成弱证据。

    例：B2 词档复习了 20 次、认识 80% → 一条 difficulty=B2、correct=True、
    weight=0.8 的观测。它不能单独改变等级，但会和其他信号一起推动后验。
    """
    observations: list[EvidenceObservation] = []
    for stat in stats:
        band = normalize_cefr_level(stat.band)
        if not band or stat.total < REVIEW_BAND_MIN_REVIEWS:
            continue
        weight = min(REVIEW_BAND_WEIGHT_CAP, stat.total * REVIEW_BAND_WEIGHT_PER_REVIEW)
        observations.append(
            EvidenceObservation(
                difficulty=band,
                correct=stat.accuracy >= REVIEW_BAND_GOOD_ACCURACY,
                weight=weight,
                source="review",
            )
        )
    return tuple(observations)


def observations_from_blind_level(
    level: str,
    *,
    current_level: str = "",
    weight: float = 0.5,
) -> tuple[EvidenceObservation, ...]:
    """把 AI 盲评等级折成一条弱观测（单侧证据）。

    盲评模型不看当前等级，只根据文章与作答给建议。把它编码成“用户在盲评
    等级难度的题上答对”会有一个方向问题：如果盲评**低于**当前等级（B1 用户
    被判 A2），A2 题答对对 B1 来说本来就更可能，反而把后验往上推。

    因此按方向编码：
    - 盲评高于当前 → `(盲评等级, correct=True)`，向上证据；
    - 盲评低于当前 → `(盲评等级, correct=False)`，向下证据；
    - 与当前相同 → 不产生观测。
    """
    normalized = normalize_cefr_level(level)
    if not normalized or weight <= 0:
        return ()
    current = normalize_cefr_level(current_level)
    if not current:
        # 旧调用方没传当前等级：保持历史上“建议即正向”的行为。
        return (EvidenceObservation(normalized, True, weight, "blind"),)

    current_index = CEFR_LEVELS.index(current)
    blind_index = CEFR_LEVELS.index(normalized)
    if blind_index == current_index:
        return ()
    return (
        EvidenceObservation(
            difficulty=normalized,
            correct=blind_index > current_index,
            weight=weight,
            source="blind",
        ),
    )


def posterior_to_tuple(posterior: LevelPosterior) -> tuple[float, ...]:
    return tuple(round(value, 4) for value in posterior.probabilities)


@dataclass(frozen=True)
class LevelPosterior:
    """一次后验估计的结果（不可变）。"""

    probabilities: tuple[float, ...]
    map_level: str
    confidence: float
    recommended_level: str
    effective_observations: float = 0.0
    observation_count: int = 0

    def probability(self, level: str) -> float:
        index = _level_index(level.strip().upper())
        return self.probabilities[index] if index is not None else 0.0

    @property
    def probability_map(self) -> dict[str, float]:
        return dict(zip(CEFR_LEVELS, self.probabilities, strict=True))


__all__ = [
    "CONFIDENCE_THRESHOLD",
    "DIFFICULTY_SLOPE",
    "HISTORY_DECAY",
    "HISTORY_LIMIT",
    "MAX_LEVEL_STEP",
    "LevelPosterior",
    "estimate_level",
    "observations_from_blind_level",
    "observations_from_quiz",
    "observations_from_self_report",
    "posterior_to_tuple",
    "replay_evidence",
    "review_band_observations",
]
