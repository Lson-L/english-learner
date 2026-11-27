"""阅读检测的错因与薄弱点（M8）。

为什么要单独一层：
- `quiz_calibration` 已经有难度 / 技能 / 错因的纯函数，但它只接收 quiz 列表；
- 统计页需要“近 N 天完成的正式检测”，这层负责取数与过滤（练习模式、
  未完成、没有题目的行都不进统计），再把纯函数的结果包装成 UI 快照；
- 不写库、不发 AI、不依赖 PySide6，可以直接单测。

口径：
- 只统计 `completed_at` 在窗口内、`practice_mode = 0`、`total > 0` 的检测；
- 错因来自 `reading_quizzes.error_types`，`none` 表示该题答对，不计入；
- 难度是题目自带的 CEFR 标签，不是文章等级；
- “不知道”比例 = 选择题里选 `UNKNOWN_ANSWER` 的次数 / 选择题总数。

Java 对照：≈ 一个只读的 `QuizInsightsQueryService`，内部复用
`QuizStatsCalculator` 的纯函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..models.entities import today_str
from ..storage.repositories import QuizRepo
from .quiz_calibration import (
    DifficultyStat,
    SkillStat,
    difficulty_stats,
    skill_stats,
    summarizing_error_types,
)
from .reading_quiz_service import UNKNOWN_ANSWER

DEFAULT_WINDOW_DAYS = 30
# 错因 / 技能标签：统计页展示用；services 不依赖 PySide6，文案常量放这里。
ERROR_TYPE_LABELS: dict[str, str] = {
    "vocabulary": "词汇不理解",
    "inference": "推断偏差",
    "detail": "细节没抓住",
    "main_idea": "主旨把握不准",
    "grammar": "语法 / 句子结构",
    "translation": "英译中理解偏差",
    "short_answer": "表达不够准确",
    "rewrite": "改写不准确",
    "careless": "粗心",
    "unknown": "原因不明",
}
ERROR_TYPE_LABELS_EN: dict[str, str] = {
    "vocabulary": "Vocabulary",
    "inference": "Inference",
    "detail": "Details",
    "main_idea": "Main idea",
    "grammar": "Grammar",
    "translation": "Translation",
    "short_answer": "Short answer",
    "rewrite": "Rewrite",
    "careless": "Careless",
    "unknown": "Unclear",
}
SKILL_LABELS: dict[str, str] = {
    "main_idea": "主旨",
    "detail": "细节",
    "inference": "推断",
    "vocabulary": "词汇",
    "grammar": "语法",
    "translation": "英译中",
    "writing": "表达",
    "rewrite": "改写",
    "unknown": "未标注",
}
SKILL_LABELS_EN: dict[str, str] = {
    "main_idea": "Main idea",
    "detail": "Detail",
    "inference": "Inference",
    "vocabulary": "Vocabulary",
    "grammar": "Grammar",
    "translation": "Translation",
    "writing": "Writing",
    "rewrite": "Rewrite",
    "unknown": "Unlabeled",
}


@dataclass(frozen=True)
class QuizInsights:
    """窗口内的错因 / 技能 / 难度快照；界面只读这个对象。"""

    window_days: int = DEFAULT_WINDOW_DAYS
    window_start: str = ""
    window_end: str = ""
    quizzes: int = 0
    errors: tuple[tuple[str, int], ...] = ()
    skills: tuple[SkillStat, ...] = ()
    difficulties: tuple[DifficultyStat, ...] = ()
    unknown_choices: int = 0
    choice_total: int = 0

    @property
    def unknown_rate(self) -> float | None:
        return self.unknown_choices / self.choice_total if self.choice_total else None

    @property
    def has_data(self) -> bool:
        return self.quizzes > 0


class QuizInsightsService:
    """按窗口汇总错因与薄弱点；只读 SQL、0 次 AI 调用。"""

    def __init__(self, quiz_repo: QuizRepo, *, today=today_str) -> None:
        self._quizzes = quiz_repo
        self._today = today

    def build(
        self,
        *,
        on: date | None = None,
        window_days: int = DEFAULT_WINDOW_DAYS,
    ) -> QuizInsights:
        day = on or self._parse_today()
        days = max(1, int(window_days))
        start = (day - timedelta(days=days - 1)).isoformat()
        end = day.isoformat()

        quizzes = [
            quiz
            for quiz in self._quizzes.list_completed_by_completion_between(start, end)
            if not quiz.practice_mode and quiz.total > 0
        ]
        unknown_choices = 0
        choice_total = 0
        for quiz in quizzes:
            for question, chosen in zip(quiz.choice_questions, quiz.answers, strict=False):
                if question.answer_index < 0:
                    continue
                # -1 是“未作答”，不进分母；-2（不知道）算有效作答。
                if chosen < 0 and chosen != UNKNOWN_ANSWER:
                    continue
                choice_total += 1
                if chosen == UNKNOWN_ANSWER:
                    unknown_choices += 1

        return QuizInsights(
            window_days=days,
            window_start=start,
            window_end=end,
            quizzes=len(quizzes),
            errors=summarizing_error_types(quizzes),
            skills=skill_stats(quizzes),
            difficulties=difficulty_stats(quizzes),
            unknown_choices=unknown_choices,
            choice_total=choice_total,
        )

    def _parse_today(self) -> date:
        try:
            return date.fromisoformat(self._today())
        except ValueError:
            return date.today()


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "ERROR_TYPE_LABELS",
    "ERROR_TYPE_LABELS_EN",
    "QuizInsights",
    "QuizInsightsService",
    "SKILL_LABELS",
    "SKILL_LABELS_EN",
]
