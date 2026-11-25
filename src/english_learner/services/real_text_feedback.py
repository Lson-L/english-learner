"""真实文章反馈闭环（M7.2）：完成结果 → outcome → 下一次供给的间隔。

设计：
- 纯函数优先，方便单测；写库仍走 `RealTextRepo`；
- 判定优先级 **struggle > success > neutral**（M7_REAL_TEXT.md 第 7.1 节）；
- 这里只负责“完成时怎么记”，下一次供给的间隔表由
  `services.real_text_supply` 消费，不在两处重复实现。

Java 对照：≈ 一个无状态的 `RealTextFeedbackPolicy`，输入分数 + 自评，
输出枚举 outcome；持久化由 Repository 负责。
"""

from __future__ import annotations

from ..storage.repositories import RealTextRepo

OUTCOME_SUCCESS = "success"
OUTCOME_NEUTRAL = "neutral"
OUTCOME_STRUGGLE = "struggle"
OUTCOME_SKIP = "skip"
OUTCOME_ABANDON = "abandon"

OUTCOMES: frozenset[str] = frozenset(
    {OUTCOME_SUCCESS, OUTCOME_NEUTRAL, OUTCOME_STRUGGLE, OUTCOME_SKIP, OUTCOME_ABANDON}
)

# 完成判定的阈值（冻结，见 M7_REAL_TEXT.md 7.1）。
STRUGGLE_SCORE_MAX = 0.5
SUCCESS_SCORE_MIN = 0.7


def outcome_from_completion(score: float, self_report: str) -> str:
    """把完成分数 + 自评映射成 success / neutral / struggle。

    - `score < 0.5` 或自评「吃力」→ struggle（两个信号任一为负都算吃力）；
    - `score >= 0.7` 且自评「轻松 / 刚好」→ success；
    - 其余 → neutral（包括 0.5~0.7、以及高分但自评缺失的边界情况）。
    """
    try:
        value = float(score)
    except (TypeError, ValueError):
        value = 0.0
    report = str(self_report or "").strip().lower()
    if value < STRUGGLE_SCORE_MAX or report == "hard":
        return OUTCOME_STRUGGLE
    if value >= SUCCESS_SCORE_MIN and report in {"easy", "ok"}:
        return OUTCOME_SUCCESS
    return OUTCOME_NEUTRAL


def record_completion(
    repo: RealTextRepo,
    text_id: str,
    *,
    score: float,
    self_report: str,
    completed_at: str | None = None,
) -> str:
    """判定 outcome 并写回最近一次供给记录，返回 outcome。

    没有对应历史行时仓库会静默更新 0 行（不抛异常），这里同样不阻断
    检测结果落库——反馈是增强信息，不能把用户的答题结果搞丢。
    """
    outcome = outcome_from_completion(score, self_report)
    repo.record_completion(
        text_id,
        score=float(score),
        self_report=str(self_report or "").strip(),
        outcome=outcome,
        completed_at=completed_at,
    )
    return outcome


__all__ = [
    "OUTCOMES",
    "OUTCOME_ABANDON",
    "OUTCOME_NEUTRAL",
    "OUTCOME_SKIP",
    "OUTCOME_STRUGGLE",
    "OUTCOME_SUCCESS",
    "SUCCESS_SCORE_MIN",
    "STRUGGLE_SCORE_MAX",
    "outcome_from_completion",
    "record_completion",
]
