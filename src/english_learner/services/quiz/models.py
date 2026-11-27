"""阅读检测的结果对象与异常。"""

from __future__ import annotations

from dataclasses import dataclass

from ...models.entities import AssessmentResult, Profile, ReadingQuiz
from ..level_estimator import LevelPosterior


class InvalidQuizError(ValueError):
    """AI 返回的题目不满足本地校验规则。"""


@dataclass(frozen=True)
class EvidenceSummary:
    """一次检测的本地证据摘要。

    `ratio` 是混合难度加权后的平均表现：答对高难度题加分更多，
    答错低难度题扣分更多；界面用它展示“偏强 / 中性 / 偏弱”。
    等级决策已改为后验模型，这个值仍保留用于解释与向后兼容。
    """

    ratio: float
    direction: str  # up / keep / down
    points: float
    max_points: float
    details: tuple[float, ...] = ()

    @property
    def label(self) -> str:
        if self.direction == "up":
            return "偏强"
        if self.direction == "down":
            return "偏弱"
        return "中性"


@dataclass(frozen=True)
class LevelDecision:
    """连续证据策略的输出：实际生效等级 + 待定状态。"""

    applied_level: str
    pending_level: str
    pending_count: int
    changed: bool
    waiting: bool


@dataclass(frozen=True)
class QuizOutcome:
    """一次“答题 + 讲评 + 调级”的完整结果，供界面展示。"""

    quiz: ReadingQuiz
    profile: Profile
    result: AssessmentResult
    previous_level: str
    recommended_level: str = ""
    pending_level: str = ""
    pending_count: int = 0
    evidence: EvidenceSummary | None = None
    posterior: LevelPosterior | None = None
    confidence: float = 0.0
    practice_mode: bool = False
    blind_level: str = ""

    @property
    def level_changed(self) -> bool:
        return self.profile.cefr_level != self.previous_level

    @property
    def waiting_for_upgrade(self) -> bool:
        return bool(self.pending_level and self.pending_count > 0)

    @property
    def score(self) -> int:
        return self.quiz.score

    @property
    def total(self) -> int:
        return self.quiz.total

    @property
    def percent(self) -> int:
        return self.quiz.percent
