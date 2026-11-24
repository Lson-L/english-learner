"""学习统计（M7）：把本地记录聚合成“看得见的积累”。

设计：
- **只读现有表**：`daily_stats / words / reading_quizzes`，不建新表、不发 AI 请求；
- 聚合全部在服务层完成，界面只读 `StatsOverview`，保证 `services/` 可单测；
- 热力图级别、成就阈值固定在本文，避免以后统计口径漂移；
- 没有数据时返回全 0 快照，由界面显示引导文案，不用假数据撑场面。

统计口径（与今日页 / 设置页保持一致）：

- 打卡日 = `daily_stats` 当天三项计数任意一项大于 0；
- 连续天数 = 从今天（今天没学则从昨天）往前连续有打卡的天数；
- 最长连续 = 全部历史里最长的连续打卡区间；
- 累计生词 = `words` 中 `in_vocabulary = 1` 的词数（查词缓存不算）；
- 累计复习 / 阅读 = `daily_stats` 对应计数求和（撤销复习的负值按 0 处理）；
- 检测趋势 = 已完成且非练习的阅读检测，按结果等级分组，每组取最近 8 次。

Java 对照：≈ 一个只读的 `StatsOverviewService`，把多个 Mapper 的聚合结果
组装成一个 DTO；它不写库，可以在 UI 线程直接执行。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from ..models.entities import (
    CEFR_LEVELS,
    DailyStats,
    ReadingQuiz,
    normalize_cefr_level,
    today_str,
)
from ..storage.repositories import QuizRepo, StatsRepo, WordRepo

# 热力图展示半年：26 周 × 7 天。
HEATMAP_WEEKS = 26
# 累计曲线窗口；更早的活动计入起点基线，曲线仍然从真实累计值开始。
CUMULATIVE_DAYS = 90
# 每日活动柱状图窗口。
ACTIVITY_DAYS = 30
# 检测趋势最多画 4 条等级线（分类色只有 4 个槽位）。
TREND_LEVEL_LIMIT = 4
# 每个等级最多取最近 8 次检测，避免旧数据把折线挤成一团。
TREND_POINT_LIMIT = 8

# 热力图分档：当天活动总量 → 颜色级别（0 表示没学习）。
HEAT_LEVELS: tuple[tuple[int, int], ...] = ((2, 1), (5, 2), (10, 3))


def heat_level(total: int) -> int:
    """把当天活动量映射到 0~4 的热力级别。"""
    if total <= 0:
        return 0
    for upper, level in HEAT_LEVELS:
        if total <= upper:
            return level
    return 4


def nice_max(value: int) -> int:
    """Y 轴上界取 1 / 2 / 5 × 10^n，让刻度落在整数上。"""
    if value <= 0:
        return 1
    exponent = 10 ** int(math.floor(math.log10(value)))
    for multiplier in (1, 2, 5, 10):
        candidate = multiplier * exponent
        if value <= candidate:
            return int(candidate)
    return int(value)


@dataclass(frozen=True)
class ActivityDay:
    """一天的学习量；热力图与近 30 天柱状图共用这个结构。"""

    day: str
    articles: int = 0
    words: int = 0
    reviews: int = 0
    future: bool = False

    @property
    def total(self) -> int:
        return max(0, self.articles) + max(0, self.words) + max(0, self.reviews)

    @property
    def level(self) -> int:
        return heat_level(self.total)


@dataclass(frozen=True)
class CumulativePoint:
    """某一天的累计值；词 / 阅读 / 复习三条曲线共用一组点。"""

    day: str
    words: int = 0
    articles: int = 0
    reviews: int = 0

    def value(self, metric: str) -> int:
        return int(getattr(self, metric, 0))


@dataclass(frozen=True)
class TrendPoint:
    """一次检测的得分点（百分比）。"""

    day: str
    percent: int


@dataclass(frozen=True)
class QuizSeries:
    """同一文章等级的得分折线。"""

    level: str
    points: tuple[TrendPoint, ...] = ()

    @property
    def latest_percent(self) -> int:
        return self.points[-1].percent if self.points else 0


@dataclass(frozen=True)
class WordStatusSplit:
    """生词本按 SRS 状态拆分；三段之和等于 total。"""

    total: int = 0
    new: int = 0
    learning: int = 0
    mastered: int = 0

    @property
    def mastered_rate(self) -> float | None:
        return self.mastered / self.total if self.total else None


@dataclass(frozen=True)
class Achievement:
    """一个成就：当前值达到目标即解锁。"""

    key: str
    title: str
    description: str
    current: int
    target: int

    @property
    def unlocked(self) -> bool:
        return self.current >= self.target

    @property
    def progress(self) -> float:
        if self.target <= 0:
            return 1.0
        return min(1.0, self.current / self.target)


@dataclass(frozen=True)
class StatsOverview:
    """一次统计快照；界面只读这个对象，不自己拼 SQL。"""

    today: str = ""
    current_streak: int = 0
    longest_streak: int = 0
    active_days: int = 0
    total_words: int = 0
    total_reviews: int = 0
    total_articles: int = 0
    total_quizzes: int = 0
    heatmap_days: tuple[ActivityDay, ...] = ()
    cumulative: tuple[CumulativePoint, ...] = ()
    daily: tuple[ActivityDay, ...] = ()
    word_status: WordStatusSplit = WordStatusSplit()
    quiz_series: tuple[QuizSeries, ...] = ()
    achievements: tuple[Achievement, ...] = ()

    @property
    def unlocked_achievements(self) -> int:
        return sum(1 for item in self.achievements if item.unlocked)

    @property
    def heatmap_active_days(self) -> int:
        return sum(1 for item in self.heatmap_days if item.total > 0)

    @property
    def has_data(self) -> bool:
        return bool(self.active_days or self.total_words or self.total_quizzes)


class StatsOverviewService:
    """汇总统计页需要的全部数据；只读 SQL、可离线、0 次 AI 调用。"""

    def __init__(
        self,
        stats_repo: StatsRepo,
        word_repo: WordRepo,
        quiz_repo: QuizRepo,
        today=today_str,
    ) -> None:
        self._stats = stats_repo
        self._words = word_repo
        self._quizzes = quiz_repo
        self._today = today

    def build(self, *, on: date | None = None) -> StatsOverview:
        """汇总全部统计；空库也能返回结构完整的快照。"""
        day = on or self._parse_today()
        rows = self._stats.list_all()
        by_day = {row.stat_date: row for row in rows}
        active_dates = self._active_dates(rows)

        total_words = self._words.count()
        learning = self._words.count(status="learning")
        mastered = self._words.count(status="mastered")
        word_status = WordStatusSplit(
            total=total_words,
            # 未知状态（如旧数据）统一并入“新词”，保证三段之和等于总数。
            new=max(0, total_words - learning - mastered),
            learning=learning,
            mastered=mastered,
        )

        quizzes = self._completed_quizzes()
        achievements = self._achievements(
            total_articles=self._sum(rows, "article_read"),
            total_reviews=self._sum(rows, "reviews_done"),
            total_words=total_words,
            total_quizzes=len(quizzes),
            longest_streak=self._longest_streak(active_dates),
        )

        return StatsOverview(
            today=day.isoformat(),
            current_streak=self._current_streak(active_dates, day),
            longest_streak=self._longest_streak(active_dates),
            active_days=len(active_dates),
            total_words=total_words,
            total_reviews=self._sum(rows, "reviews_done"),
            total_articles=self._sum(rows, "article_read"),
            total_quizzes=len(quizzes),
            heatmap_days=self._heatmap(day, by_day),
            cumulative=self._cumulative(day, rows, by_day),
            daily=self._daily(day, by_day),
            word_status=word_status,
            quiz_series=self._quiz_series(quizzes),
            achievements=achievements,
        )

    # ------------------------------------------------------------------ 内部
    def _parse_today(self) -> date:
        try:
            return date.fromisoformat(self._today())
        except ValueError:
            return date.today()

    @staticmethod
    def _sum(rows: list[DailyStats], field: str) -> int:
        """对某个计数字段求和；撤销复习产生的负值不抵消历史成果。"""
        return sum(max(0, int(getattr(row, field, 0))) for row in rows)

    @staticmethod
    def _active_dates(rows: list[DailyStats]) -> set[date]:
        dates: set[date] = set()
        for row in rows:
            if not row.is_active:
                continue
            try:
                dates.add(date.fromisoformat(row.stat_date))
            except ValueError:
                continue
        return dates

    @staticmethod
    def _current_streak(active: set[date], today: date) -> int:
        cursor = today if today in active else today - timedelta(days=1)
        streak = 0
        while cursor in active:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    @staticmethod
    def _longest_streak(active: set[date]) -> int:
        longest = 0
        current = 0
        previous: date | None = None
        for day in sorted(active):
            if previous is not None and (day - previous).days == 1:
                current += 1
            else:
                current = 1
            longest = max(longest, current)
            previous = day
        return longest

    @staticmethod
    def _activity_day(day: date, by_day: dict[str, DailyStats], *, future: bool) -> ActivityDay:
        row = by_day.get(day.isoformat())
        if row is None:
            return ActivityDay(day=day.isoformat(), future=future)
        return ActivityDay(
            day=day.isoformat(),
            articles=max(0, row.article_read),
            words=max(0, row.words_added),
            reviews=max(0, row.reviews_done),
            future=future,
        )

    def _heatmap(self, today: date, by_day: dict[str, DailyStats]) -> tuple[ActivityDay, ...]:
        """从本周周日往前推 26 周，周一为每列第一格。"""
        end = today + timedelta(days=6 - today.weekday())
        start = end - timedelta(days=HEATMAP_WEEKS * 7 - 1)
        return tuple(
            self._activity_day(
                start + timedelta(days=offset),
                by_day,
                future=start + timedelta(days=offset) > today,
            )
            for offset in range(HEATMAP_WEEKS * 7)
        )

    def _daily(self, today: date, by_day: dict[str, DailyStats]) -> tuple[ActivityDay, ...]:
        start = today - timedelta(days=ACTIVITY_DAYS - 1)
        return tuple(
            self._activity_day(start + timedelta(days=offset), by_day, future=False)
            for offset in range(ACTIVITY_DAYS)
        )

    def _cumulative(
        self,
        today: date,
        rows: list[DailyStats],
        by_day: dict[str, DailyStats],
    ) -> tuple[CumulativePoint, ...]:
        """窗口开始前的活动先折成基线，曲线第一点就是真实累计值。"""
        start = today - timedelta(days=CUMULATIVE_DAYS - 1)
        start_text = start.isoformat()
        baseline = {"words": 0, "articles": 0, "reviews": 0}
        for row in rows:
            if row.stat_date >= start_text:
                continue
            baseline["words"] += max(0, row.words_added)
            baseline["articles"] += max(0, row.article_read)
            baseline["reviews"] += max(0, row.reviews_done)

        running = dict(baseline)
        points: list[CumulativePoint] = []
        for offset in range(CUMULATIVE_DAYS):
            day_text = (start + timedelta(days=offset)).isoformat()
            row = by_day.get(day_text)
            if row is not None:
                running["words"] += max(0, row.words_added)
                running["articles"] += max(0, row.article_read)
                running["reviews"] += max(0, row.reviews_done)
            points.append(
                CumulativePoint(
                    day=day_text,
                    words=running["words"],
                    articles=running["articles"],
                    reviews=running["reviews"],
                )
            )
        return tuple(points)

    def _completed_quizzes(self) -> list[ReadingQuiz]:
        """已完成、非练习、有题目的检测；按完成时间升序。"""
        quizzes = [
            quiz
            for quiz in self._quizzes.list_all()
            if quiz.completed and not quiz.practice_mode and quiz.total > 0
        ]
        quizzes.sort(key=self._quiz_time)
        return quizzes

    @staticmethod
    def _quiz_time(quiz: ReadingQuiz) -> str:
        return str(quiz.completed_at or quiz.article_date or "")

    def _quiz_series(self, quizzes: list[ReadingQuiz]) -> tuple[QuizSeries, ...]:
        grouped: dict[str, list[TrendPoint]] = {}
        for quiz in quizzes:
            level = normalize_cefr_level(quiz.result_level or quiz.level, quiz.level or "?")
            grouped.setdefault(level, []).append(
                TrendPoint(
                    day=self._quiz_time(quiz)[:10],
                    percent=quiz.percent,
                )
            )

        order = {level: index for index, level in enumerate(CEFR_LEVELS)}
        levels = sorted(grouped, key=lambda level: order.get(level, len(CEFR_LEVELS)))
        return tuple(
            QuizSeries(level=level, points=tuple(grouped[level][-TREND_POINT_LIMIT:]))
            for level in levels[:TREND_LEVEL_LIMIT]
        )

    @staticmethod
    def _achievements(
        *,
        total_articles: int,
        total_reviews: int,
        total_words: int,
        total_quizzes: int,
        longest_streak: int,
    ) -> tuple[Achievement, ...]:
        return (
            Achievement("first_read", "初次开卷", "完成第 1 篇阅读", total_articles, 1),
            Achievement("streak_7", "一周不断", "连续学习 7 天", longest_streak, 7),
            Achievement("streak_30", "月度坚持", "连续学习 30 天", longest_streak, 30),
            Achievement("streak_100", "百日筑基", "连续学习 100 天", longest_streak, 100),
            Achievement("words_50", "词汇萌芽", "累计生词 50 个", total_words, 50),
            Achievement("words_200", "词汇成林", "累计生词 200 个", total_words, 200),
            Achievement("words_500", "词汇沃土", "累计生词 500 个", total_words, 500),
            Achievement("reviews_100", "复习百次", "累计复习 100 次", total_reviews, 100),
            Achievement("reviews_500", "记忆加固", "累计复习 500 次", total_reviews, 500),
            Achievement("reviews_1000", "千锤百炼", "累计复习 1000 次", total_reviews, 1000),
            Achievement("quiz_1", "初次检验", "完成第 1 次阅读检测", total_quizzes, 1),
            Achievement("quiz_10", "十次校验", "完成 10 次阅读检测", total_quizzes, 10),
        )


__all__ = [
    "ACTIVITY_DAYS",
    "Achievement",
    "ActivityDay",
    "CUMULATIVE_DAYS",
    "CumulativePoint",
    "HEATMAP_WEEKS",
    "HEAT_LEVELS",
    "QuizSeries",
    "StatsOverview",
    "StatsOverviewService",
    "TREND_LEVEL_LIMIT",
    "TREND_POINT_LIMIT",
    "TrendPoint",
    "WordStatusSplit",
    "heat_level",
    "nice_max",
]
