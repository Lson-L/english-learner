"""学习效果度量（M5.5）：全部本地计算，0 次 AI 调用。

设计：
- **只读现有数据**：`review_logs / daily_stats / reading_quizzes /
  weekly_reviews / ai_calls / writing_attempts`，不建表、不新增 AI 调用；
- **口径固定在本文与 `AI_TASKS.md`**，避免以后统计漂移；
- **不用“AI 自评质量”当指标**：所有数字都能从本地记录复算；
- 数据不足时返回 `None`（界面显示 “—”），而不是用 0 误导。

统计口径（近 N 天，默认 30 天，含今天）：

- 生词首次通过率：窗口内发生“首次复习”的词中，首次评分 >= 3（认识 / 太简单）
  的比例；每词只看第一次记录；
- 词回炉率：窗口内复习过的词中，至少有一次评分 <= 2（忘记 / 模糊）的比例，
  按词去重；
- 计划完成率：仅看“上周复盘”的 `focus`，本周能找到对应学习行为的条目 /
  能映射到行为的条目；无法映射的条目不进分母；
- 文章 / 检测重试率：`ai_calls` 中 `attempt > 1` 的行数 / 该任务全部尝试数；
  文章按任务 `每日文章`，检测按前缀 `阅读检测`；
- 同等级得分趋势：窗口内完成的非练习检测按文章等级分组；最近 3 次平均分 −
  更早 3 次平均分（百分点），差值在 ±3 内视为持平。

Java 对照：≈ 一个只读的 `MetricsQueryService`，组合多个 Mapper 的聚合 SQL；
它不写库、不调外部服务，可以在 UI 线程直接执行。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from ..models.entities import CEFR_LEVELS, normalize_cefr_level, today_str
from ..storage.repositories import (
    AICallRepo,
    QuizRepo,
    ReviewRepo,
    StatsRepo,
    WeeklyReviewRepo,
    WritingAttemptRepo,
)
from .srs import RATING_FORGOT, RATING_KNOWN, RATING_VAGUE

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 30
# 首次复习评到这个分数（认识 / 太简单）算“通过”。
PASS_RATING = RATING_KNOWN
# 评到这两档（忘记 / 模糊）算一次“回炉”。
WEAK_RATINGS: tuple[int, ...] = (RATING_FORGOT, RATING_VAGUE)
# 趋势对比窗口：最近 3 次 vs 更早 3 次。
TREND_SAMPLE = 3
# 得分差在 ±3 个百分点内视为持平，避免小样本抖动被读成趋势。
TREND_FLAT_POINTS = 3.0

ARTICLE_TASK = "每日文章"
QUIZ_TASK_PREFIX = "阅读检测"

# focus 条目 → 本周可观测的学习行为（任一行为发生即视为完成）。
# 按子串匹配，中英文都支持；无法映射的条目（如听力 / 口语）不进入分母。
PLAN_KEYWORD_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("阅读", "文章", "朗读", "read"), ("reading",)),
    (("复习", "回顾", "review"), ("review",)),
    (("单词", "词汇", "生词", "vocab", "word"), ("vocabulary",)),
    (("检测", "自测", "测试", "quiz", "test"), ("quiz",)),
    (("写作", "回译", "翻译", "造句", "writing", "translation"), ("writing",)),
    (("题",), ("quiz",)),
    (("词",), ("review", "vocabulary")),
)


@dataclass(frozen=True)
class RateMetric:
    """一个“分子 / 分母”型指标；分母为 0 时 `rate` 返回 None（界面显示 —）。"""

    total: int = 0
    hit: int = 0

    @property
    def rate(self) -> float | None:
        return self.hit / self.total if self.total else None


@dataclass(frozen=True)
class RetryMetric:
    """某类任务的重试情况：重试率 = 重试尝试 / 全部尝试。"""

    attempts: int = 0
    retries: int = 0
    failures: int = 0

    @property
    def rate(self) -> float | None:
        return self.retries / self.attempts if self.attempts else None


@dataclass(frozen=True)
class PlanCompletion:
    """上周复盘计划在本周的完成情况；`planned == 0` 时 `rate` 为 None。"""

    plan_week: str = ""
    measured_week: str = ""
    focus: tuple[str, ...] = ()
    planned: int = 0
    completed: int = 0

    @property
    def rate(self) -> float | None:
        return self.completed / self.planned if self.planned else None


@dataclass(frozen=True)
class QuizPoint:
    """一次检测的得分点（按文章等级归组）。"""

    article_date: str
    level: str
    score: int
    total: int

    @property
    def percent(self) -> int:
        return round(self.score * 100 / self.total) if self.total else 0


@dataclass(frozen=True)
class QuizTrend:
    """同一文章等级的得分趋势；样本不足时 `delta` 为 None。"""

    level: str
    points: tuple[QuizPoint, ...] = ()
    recent_avg: float | None = None
    previous_avg: float | None = None

    @property
    def latest_percent(self) -> int:
        return self.points[-1].percent if self.points else 0

    @property
    def delta(self) -> float | None:
        if self.recent_avg is None or self.previous_avg is None:
            return None
        return self.recent_avg - self.previous_avg

    @property
    def direction(self) -> str:
        """`up / down / flat / unknown`；差值在 ±3 个百分点内算持平。"""
        delta = self.delta
        if delta is None:
            return "unknown"
        # 严格比较：差值正好等于 ±3 时仍算持平（与文档“±3 内”一致）。
        if delta > TREND_FLAT_POINTS:
            return "up"
        if delta < -TREND_FLAT_POINTS:
            return "down"
        return "flat"


@dataclass(frozen=True)
class MetricsSnapshot:
    """一次学习效果快照；界面只读这个对象，不直接碰仓库。"""

    window_days: int = DEFAULT_WINDOW_DAYS
    window_start: str = ""
    window_end: str = ""
    first_pass: RateMetric = RateMetric()
    relearn: RateMetric = RateMetric()
    plan: PlanCompletion | None = None
    article_retry: RetryMetric = RetryMetric()
    quiz_retry: RetryMetric = RetryMetric()
    quiz_trends: tuple[QuizTrend, ...] = ()

    @property
    def has_data(self) -> bool:
        return bool(
            self.first_pass.total
            or self.relearn.total
            or (self.plan is not None and self.plan.planned > 0)
            or self.article_retry.attempts
            or self.quiz_retry.attempts
            or self.quiz_trends
        )


def plan_categories(text: str) -> frozenset[str]:
    """把一条复盘 focus 映射到学习行为类别；无法映射时返回空集合。"""
    lowered = (text or "").strip().lower()
    if not lowered:
        return frozenset()
    categories: set[str] = set()
    for keywords, groups in PLAN_KEYWORD_GROUPS:
        if any(keyword in lowered for keyword in keywords):
            categories.update(groups)
    return frozenset(categories)


class MetricsService:
    """汇总本地学习效果指标；所有方法只读 SQL，可离线运行。"""

    def __init__(
        self,
        review_repo: ReviewRepo,
        stats_repo: StatsRepo,
        quiz_repo: QuizRepo,
        weekly_repo: WeeklyReviewRepo,
        ai_call_repo: AICallRepo,
        writing_repo: WritingAttemptRepo | None = None,
        today: Callable[[], str] = today_str,
    ) -> None:
        self._reviews = review_repo
        self._stats = stats_repo
        self._quizzes = quiz_repo
        self._weekly = weekly_repo
        self._ai_calls = ai_call_repo
        self._writing = writing_repo
        self._today = today

    def build(
        self,
        *,
        on: date | None = None,
        window_days: int = DEFAULT_WINDOW_DAYS,
    ) -> MetricsSnapshot:
        """汇总近 `window_days` 天（含今天）的指标；空库也能正常返回。"""
        day = on or self._parse_today()
        days = max(1, int(window_days))
        start = (day - timedelta(days=days - 1)).isoformat()
        end = day.isoformat()

        first_total, first_passed = self._reviews.first_review_stats(
            start, end, pass_rating=PASS_RATING
        )
        relearn_total, relearn_hit = self._reviews.relearn_stats(
            start, end, weak_ratings=WEAK_RATINGS
        )
        article = self._ai_calls.attempt_stats(since=start, until=end, task=ARTICLE_TASK)
        quiz = self._ai_calls.attempt_stats(since=start, until=end, task_prefix=QUIZ_TASK_PREFIX)

        return MetricsSnapshot(
            window_days=days,
            window_start=start,
            window_end=end,
            first_pass=RateMetric(total=first_total, hit=first_passed),
            relearn=RateMetric(total=relearn_total, hit=relearn_hit),
            plan=self._plan_completion(day),
            article_retry=self._retry_metric(article),
            quiz_retry=self._retry_metric(quiz),
            quiz_trends=self._quiz_trends(start, end),
        )

    # ------------------------------------------------------------------ 内部
    def _parse_today(self) -> date:
        try:
            return date.fromisoformat(self._today())
        except ValueError:
            return date.today()

    @staticmethod
    def _retry_metric(stats: tuple[int, int, int]) -> RetryMetric:
        firsts, retries, failures = stats
        return RetryMetric(attempts=firsts + retries, retries=retries, failures=failures)

    def _plan_completion(self, day: date) -> PlanCompletion | None:
        """只跟踪“上周复盘”的 focus；更早的计划不再计算，避免陈旧数据误导。"""
        current_week = (day - timedelta(days=day.weekday())).isoformat()
        last_week = (day - timedelta(days=day.weekday() + 7)).isoformat()
        previous = self._weekly.get_by_week(last_week)
        if previous is None or not previous.focus:
            return None

        activity = self._week_activity(current_week, day.isoformat())
        planned = 0
        completed = 0
        for item in previous.focus:
            categories = plan_categories(item)
            if not categories:
                continue
            planned += 1
            if any(activity.get(category, False) for category in categories):
                completed += 1
        if planned == 0:
            # focus 全部无法映射（如只写了“练习听力”）：没有可评估的计划。
            return None
        return PlanCompletion(
            plan_week=last_week,
            measured_week=current_week,
            focus=previous.focus,
            planned=planned,
            completed=completed,
        )

    def _week_activity(self, start_day: str, end_day: str) -> dict[str, bool]:
        """本周各类学习行为是否发生过；用于计划完成度判定。"""
        stats = self._stats.summarize_between(start_day, end_day)
        return {
            "reading": stats.get("articles", 0) > 0,
            "review": stats.get("reviews", 0) > 0,
            "vocabulary": stats.get("words", 0) > 0,
            "quiz": self._quizzes.count_completed_between(start_day, end_day) > 0,
            "writing": (
                self._writing is not None
                and self._writing.count_graded_between(start_day, end_day) > 0
            ),
        }

    def _quiz_trends(self, start_day: str, end_day: str) -> tuple[QuizTrend, ...]:
        """窗口内完成的非练习检测，按文章等级分组后计算趋势。

        窗口与排序都用**完成时间**：跨天提交算在完成那一天，不会因为
        `article_date` 在窗口外或排序错位而漏算。
        """
        grouped: dict[str, list[QuizPoint]] = {}
        for quiz in self._quizzes.list_completed_by_completion_between(start_day, end_day):
            if quiz.practice_mode or quiz.total <= 0:
                continue
            level = normalize_cefr_level(quiz.level, quiz.level or "?")
            grouped.setdefault(level, []).append(
                QuizPoint(
                    article_date=quiz.article_date,
                    level=level,
                    score=int(quiz.score),
                    total=int(quiz.total),
                )
            )

        order = {level: index for index, level in enumerate(CEFR_LEVELS)}
        trends = [self._make_trend(level, points) for level, points in grouped.items()]
        trends.sort(key=lambda trend: order.get(trend.level, len(CEFR_LEVELS)))
        return tuple(trends)

    @staticmethod
    def _make_trend(level: str, points: list[QuizPoint]) -> QuizTrend:
        recent = points[-TREND_SAMPLE:]
        previous = points[-TREND_SAMPLE * 2 : -TREND_SAMPLE]
        recent_avg = sum(point.percent for point in recent) / len(recent) if recent else None
        previous_avg = (
            sum(point.percent for point in previous) / len(previous) if previous else None
        )
        return QuizTrend(
            level=level,
            points=tuple(points),
            recent_avg=recent_avg,
            previous_avg=previous_avg,
        )


__all__ = [
    "ARTICLE_TASK",
    "DEFAULT_WINDOW_DAYS",
    "MetricsService",
    "MetricsSnapshot",
    "PASS_RATING",
    "PLAN_KEYWORD_GROUPS",
    "PlanCompletion",
    "QUIZ_TASK_PREFIX",
    "QuizPoint",
    "QuizTrend",
    "RateMetric",
    "RetryMetric",
    "TREND_FLAT_POINTS",
    "TREND_SAMPLE",
    "WEAK_RATINGS",
    "plan_categories",
]
