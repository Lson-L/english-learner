"""学习统计（M7 / M8）：把本地记录聚合成“看得见的积累”。

设计：
- **只读现有表**：`daily_stats / words / reading_quizzes / articles /
  real_text_history`，不建新表、不发 AI 请求；
- 聚合全部在服务层完成，界面只读 `StatsOverview`，保证 `services/` 可单测；
- 热力图级别、成就阈值、复习负载窗口固定在本文，避免以后统计口径漂移；
- 没有数据时返回全 0 快照，由界面显示引导文案，不用假数据撑场面。

统计口径（与今日页 / 设置页保持一致）：

- 打卡日 = `daily_stats` 当天三项计数任意一项大于 0；
- 连续天数 = 从今天（今天没学则从昨天）往前连续有打卡的天数；
- 最长连续 = 全部历史里最长的连续打卡区间；
- 累计生词 = `words` 中 `in_vocabulary = 1` 的词数（查词缓存不算）；
- 累计复习 / 阅读 = `daily_stats` 对应计数求和（撤销复习的负值按 0 处理）；
- 检测趋势 = 已完成且非练习的阅读检测，按结果等级分组，每组取最近 8 次；
- 阅读剂量 = `articles.read_at` 非空的 `word_count` 之和，另给出“完成过检测”
  的严格口径（M8）；
- 复习负载 = 未来 7 天按 `words.due_date` 分桶的未掌握词数；逾期单独计数；
- 真实文章 = `real_text_history` 的 status / outcome 分布（M8）。

Java 对照：≈ 一个只读的 `StatsOverviewService`，把多个 Mapper 的聚合结果
组装成一个 DTO；它不写库，可以在 UI 线程直接执行。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from ..models.entities import (
    CEFR_LEVELS,
    DailyStats,
    QuizSummary,
    normalize_cefr_level,
    today_str,
)
from ..storage.repositories import (
    ArticleRepo,
    QuizRepo,
    RealTextRepo,
    StatsRepo,
    WordRepo,
)

# 热力图展示半年：26 周 × 7 天。
HEATMAP_WEEKS = 26
# 累计曲线默认窗口；0 表示“全部历史”。
CUMULATIVE_DAYS = 90
# 累计曲线窗口按钮：文案 → 天数（0 = 全部）。
CUMULATIVE_WINDOW_CHOICES: tuple[tuple[str, int], ...] = (
    ("近 30 天", 30),
    ("近 90 天", 90),
    ("近 1 年", 365),
    ("全部", 0),
)
# 每日活动柱状图窗口。
ACTIVITY_DAYS = 30
# 未来复习负载窗口。
DUE_FORECAST_DAYS = 7
# 停滞词：到期后超过这么多天仍未复习。
STALLED_DAYS = 7
# 检测趋势最多画 4 条等级线（分类色只有 4 个槽位）。
TREND_LEVEL_LIMIT = 4
# 每个等级最多取最近 8 次检测，避免旧数据把折线挤成一团。
TREND_POINT_LIMIT = 8

# 热力图分档：当天活动总量 → 颜色级别（0 表示没学习）。
HEAT_LEVELS: tuple[tuple[int, int], ...] = ((2, 1), (5, 2), (10, 3))

# 成就定义：(key, 标题, 说明, 目标值, 指标)。
# 指标决定“当前值”和“解锁日”怎么算：
# - articles / reviews / words：全部历史累计（words 用 daily_stats.words_added）；
# - quizzes：完成检测次数；
# - streak：最长连续打卡。
ACHIEVEMENT_SPECS: tuple[tuple[str, str, str, int, str], ...] = (
    ("first_read", "初次开卷", "完成第 1 篇阅读", 1, "articles"),
    ("streak_7", "一周不断", "连续学习 7 天", 7, "streak"),
    ("streak_30", "月度坚持", "连续学习 30 天", 30, "streak"),
    ("streak_100", "百日筑基", "连续学习 100 天", 100, "streak"),
    ("words_50", "词汇萌芽", "累计生词 50 个", 50, "words"),
    ("words_200", "词汇成林", "累计生词 200 个", 200, "words"),
    ("words_500", "词汇沃土", "累计生词 500 个", 500, "words"),
    ("reviews_100", "复习百次", "累计复习 100 次", 100, "reviews"),
    ("reviews_500", "记忆加固", "累计复习 500 次", 500, "reviews"),
    ("reviews_1000", "千锤百炼", "累计复习 1000 次", 1000, "reviews"),
    ("quiz_1", "初次检验", "完成第 1 次阅读检测", 1, "quizzes"),
    ("quiz_10", "十次校验", "完成 10 次阅读检测", 10, "quizzes"),
)

# 真实文章 outcome 的展示顺序；未知值排在后面。
OUTCOME_ORDER = ("success", "neutral", "struggle", "skip", "abandon")


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
class WordBands:
    """生词本按 CEFR 词档拆分；`unlabeled` 是没标注词档的旧词条。"""

    bands: tuple[tuple[str, int], ...] = ()
    unlabeled: int = 0

    @property
    def labeled(self) -> int:
        return sum(count for _band, count in self.bands)

    @property
    def has_data(self) -> bool:
        return self.labeled > 0


@dataclass(frozen=True)
class DueDay:
    """未来某一天到期的未掌握词数。"""

    day: str
    count: int = 0


@dataclass(frozen=True)
class RealTextStats:
    """真实文章供给闭环（M8）。数据来自 `real_text_history`。"""

    total: int = 0
    served: int = 0
    completed: int = 0
    overridden: int = 0
    abandoned: int = 0
    outcomes: tuple[tuple[str, int], ...] = ()

    @property
    def completion_rate(self) -> float | None:
        return self.completed / self.total if self.total else None

    @property
    def overridden_rate(self) -> float | None:
        return self.overridden / self.total if self.total else None

    @property
    def abandoned_rate(self) -> float | None:
        return self.abandoned / self.total if self.total else None

    @property
    def has_data(self) -> bool:
        return self.total > 0


@dataclass(frozen=True)
class Achievement:
    """一个成就：当前值达到目标即解锁。"""

    key: str
    title: str
    description: str
    current: int
    target: int
    # 首次达标的日期；历史数据推导不出时为空串（仍按 current 判定解锁）。
    unlocked_on: str = ""

    @property
    def unlocked(self) -> bool:
        return self.current >= self.target

    @property
    def progress(self) -> float:
        if self.target <= 0:
            return 1.0
        return min(1.0, self.current / self.target)


@dataclass(frozen=True)
class ReadingVolume:
    """阅读剂量（M7.3 / M8）：按“打开过文章”统计的累计词数。

    口径：
    - `total_words`：`articles.read_at IS NOT NULL` 的 `word_count` 之和；
    - `completed_words`：其中**完成过阅读检测**的文章词数（更严格的口径）；
    - `real_articles / generated_articles`：按 `articles.source` 拆分已读文章；
    - `est_books` 按每本 9 万词粗略换算，只用于给用户一个直觉参照。
    """

    total_words: int = 0
    words_7d: int = 0
    words_30d: int = 0
    read_articles: int = 0
    est_books: float = 0.0
    completed_words: int = 0
    completed_articles: int = 0
    real_articles: int = 0
    generated_articles: int = 0

    @property
    def has_data(self) -> bool:
        return self.read_articles > 0


@dataclass(frozen=True)
class StatsOverview:
    """一次统计快照；界面只读这个对象，不自己拼 SQL。"""

    today: str = ""
    current_streak: int = 0
    longest_streak: int = 0
    active_days: int = 0
    today_active: bool = False
    total_words: int = 0
    total_reviews: int = 0
    total_articles: int = 0
    total_quizzes: int = 0
    heatmap_days: tuple[ActivityDay, ...] = ()
    cumulative: tuple[CumulativePoint, ...] = ()
    # 窗口开始前的累计值；界面用「末点 - 基线」计算窗口内增量。
    cumulative_baseline: CumulativePoint = CumulativePoint(day="")
    cumulative_days: int = CUMULATIVE_DAYS
    daily: tuple[ActivityDay, ...] = ()
    word_status: WordStatusSplit = WordStatusSplit()
    word_bands: WordBands = WordBands()
    stalled_words: int = 0
    due_forecast: tuple[DueDay, ...] = ()
    due_today: int = 0
    overdue_words: int = 0
    quiz_series: tuple[QuizSeries, ...] = ()
    achievements: tuple[Achievement, ...] = ()
    reading_volume: ReadingVolume = ReadingVolume()
    real_text: RealTextStats = RealTextStats()

    @property
    def unlocked_achievements(self) -> int:
        return sum(1 for item in self.achievements if item.unlocked)

    @property
    def heatmap_active_days(self) -> int:
        return sum(1 for item in self.heatmap_days if item.total > 0)

    @property
    def has_data(self) -> bool:
        return bool(
            self.active_days
            or self.total_words
            or self.total_quizzes
            or self.reading_volume.read_articles
        )


class StatsOverviewService:
    """汇总统计页需要的全部数据；只读 SQL、可离线、0 次 AI 调用。"""

    def __init__(
        self,
        stats_repo: StatsRepo,
        word_repo: WordRepo,
        quiz_repo: QuizRepo,
        article_repo: ArticleRepo | None = None,
        real_text_repo: RealTextRepo | None = None,
        *,
        today=today_str,
    ) -> None:
        self._stats = stats_repo
        self._words = word_repo
        self._quizzes = quiz_repo
        self._articles = article_repo
        self._real_text = real_text_repo
        self._today = today

    def build(
        self,
        *,
        on: date | None = None,
        cumulative_days: int = CUMULATIVE_DAYS,
    ) -> StatsOverview:
        """汇总全部统计；空库也能返回结构完整的快照。"""
        day = on or self._parse_today()
        rows = self._stats.list_all()
        by_day = {row.stat_date: row for row in rows}
        active_dates = self._active_dates(rows)
        longest_streak = self._longest_streak(active_dates)

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

        quizzes = self._quizzes.list_formal_completed_light()
        total_articles = self._sum(rows, "article_read")
        total_reviews = self._sum(rows, "reviews_done")
        completed_article_ids = self._quizzes.completed_article_ids()
        quiz_days = Counter(
            (quiz.completed_at or quiz.article_date)[:10] for quiz in quizzes if quiz.completed_at
        )
        cumulative, cumulative_baseline = self._cumulative(day, rows, by_day, cumulative_days)

        return StatsOverview(
            today=day.isoformat(),
            current_streak=self._current_streak(active_dates, day),
            longest_streak=longest_streak,
            active_days=len(active_dates),
            today_active=day in active_dates,
            total_words=total_words,
            total_reviews=total_reviews,
            total_articles=total_articles,
            total_quizzes=len(quizzes),
            heatmap_days=self._heatmap(day, by_day),
            cumulative=cumulative,
            cumulative_baseline=cumulative_baseline,
            cumulative_days=max(0, int(cumulative_days)),
            daily=self._daily(day, by_day),
            word_status=word_status,
            word_bands=self._word_bands(total_words),
            stalled_words=self._words.count_stalled(day.isoformat(), days=STALLED_DAYS),
            due_forecast=self._due_forecast(day),
            due_today=self._words.count_due(day.isoformat()),
            overdue_words=self._words.count_overdue(day.isoformat()),
            quiz_series=self._quiz_series(quizzes),
            achievements=self._achievements(
                total_articles=total_articles,
                total_reviews=total_reviews,
                total_words=total_words,
                total_quizzes=len(quizzes),
                longest_streak=longest_streak,
                rows=rows,
                quiz_days=quiz_days,
                active_dates=active_dates,
            ),
            reading_volume=self._reading_volume(day, completed_article_ids),
            real_text=self._real_text_stats(),
        )

    # ------------------------------------------------------------------ 内部
    def _reading_volume(self, today: date, completed_article_ids: set[int]) -> ReadingVolume:
        """累计打开过的文章词数；7 / 30 天窗口按 `read_at` 的日期归属。"""
        if self._articles is None:
            return ReadingVolume()
        total_words = 0
        read_articles = 0
        words_7d = 0
        words_30d = 0
        completed_words = 0
        completed_articles = 0
        real_articles = 0
        generated_articles = 0
        window_7 = today - timedelta(days=6)
        window_30 = today - timedelta(days=29)
        for article_id, word_count, read_at, source in self._articles.list_reading_volume():
            if not read_at:
                continue
            read_articles += 1
            count = max(0, int(word_count or 0))
            total_words += count
            if source == "real":
                real_articles += 1
            elif source == "generated":
                generated_articles += 1
            if article_id in completed_article_ids:
                completed_articles += 1
                completed_words += count
            read_day = _parse_day(read_at)
            if read_day is None or read_day > today:
                continue
            if read_day >= window_7:
                words_7d += count
            if read_day >= window_30:
                words_30d += count
        return ReadingVolume(
            total_words=total_words,
            words_7d=words_7d,
            words_30d=words_30d,
            read_articles=read_articles,
            est_books=round(total_words / 90000, 1),
            completed_words=completed_words,
            completed_articles=completed_articles,
            real_articles=real_articles,
            generated_articles=generated_articles,
        )

    def _due_forecast(self, today: date) -> tuple[DueDay, ...]:
        start = today.isoformat()
        end = (today + timedelta(days=DUE_FORECAST_DAYS - 1)).isoformat()
        counts = self._words.due_counts(start, end)
        return tuple(
            DueDay(
                day=(today + timedelta(days=offset)).isoformat(),
                count=counts.get((today + timedelta(days=offset)).isoformat(), 0),
            )
            for offset in range(DUE_FORECAST_DAYS)
        )

    def _word_bands(self, total_words: int) -> WordBands:
        counts = self._words.count_by_band()
        ordered = [level for level in CEFR_LEVELS if level in counts]
        ordered += sorted(level for level in counts if level not in CEFR_LEVELS)
        bands = tuple((level, counts[level]) for level in ordered)
        labeled = sum(counts.values())
        return WordBands(bands=bands, unlabeled=max(0, total_words - labeled))

    def _real_text_stats(self) -> RealTextStats:
        if self._real_text is None:
            return RealTextStats()
        history = self._real_text.list_history()
        if not history:
            return RealTextStats()
        statuses = Counter((item.status or "") for item in history)
        outcomes = Counter((item.outcome or "").strip() for item in history)
        ordered = [(key, outcomes.get(key, 0)) for key in OUTCOME_ORDER if outcomes.get(key, 0)]
        ordered += sorted(
            (key, count) for key, count in outcomes.items() if key and key not in OUTCOME_ORDER
        )
        return RealTextStats(
            total=len(history),
            served=statuses.get("served", 0),
            completed=statuses.get("completed", 0),
            overridden=statuses.get("overridden", 0),
            abandoned=statuses.get("abandoned", 0),
            outcomes=tuple(ordered),
        )

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
    def _streak_reach(active: set[date], target: int) -> str:
        """连续打卡首次达到 target 的那一天（区间起始日 + target - 1）。"""
        if target <= 0:
            return ""
        previous: date | None = None
        run_start: date | None = None
        length = 0
        for day in sorted(active):
            if previous is not None and (day - previous).days == 1:
                length += 1
            else:
                length = 1
                run_start = day
            if length >= target and run_start is not None:
                return (run_start + timedelta(days=target - 1)).isoformat()
            previous = day
        return ""

    @staticmethod
    def _cumulative_reach(rows: list[DailyStats], field: str, target: int) -> str:
        """累计值首次达到 target 的日期；历史不足时返回空串。"""
        if target <= 0:
            return ""
        total = 0
        for row in sorted(rows, key=lambda item: item.stat_date):
            total += max(0, int(getattr(row, field, 0)))
            if total >= target:
                return row.stat_date
        return ""

    @staticmethod
    def _quiz_reach(quiz_days: Counter[str], target: int) -> str:
        """完成检测次数首次达到 target 的日期。"""
        if target <= 0:
            return ""
        total = 0
        for day in sorted(quiz_days):
            total += int(quiz_days[day])
            if total >= target:
                return day
        return ""

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
        days: int,
    ) -> tuple[tuple[CumulativePoint, ...], CumulativePoint]:
        """窗口开始前的活动先折成基线，曲线第一点就是真实累计值。

        `days <= 0` 表示全部历史：从最早一条记录开始；完全没有记录时
        退回默认窗口，保证图表仍有一个可画的空区间。

        返回值第二项是基线（窗口开始前一天的真实累计值）。界面用
        「末点 - 基线」计算窗口内增量；如果直接用第一个点做差，会把窗口
        第一天的活动也扣掉，全部历史模式下更是永远少算最早一天。
        """
        if days <= 0:
            dates = sorted(row.stat_date for row in rows if row.stat_date)
            start = today - timedelta(days=CUMULATIVE_DAYS - 1)
            if dates:
                try:
                    start = date.fromisoformat(dates[0])
                except ValueError:
                    start = today - timedelta(days=CUMULATIVE_DAYS - 1)
            days = max(1, (today - start).days + 1)
        else:
            days = max(1, int(days))
            start = today - timedelta(days=days - 1)
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
        for offset in range(days):
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
        baseline_point = CumulativePoint(
            day=(start - timedelta(days=1)).isoformat(),
            words=baseline["words"],
            articles=baseline["articles"],
            reviews=baseline["reviews"],
        )
        return tuple(points), baseline_point

    def _quiz_series(self, quizzes: list[QuizSummary]) -> tuple[QuizSeries, ...]:
        grouped: dict[str, list[TrendPoint]] = {}
        for quiz in quizzes:
            level = normalize_cefr_level(quiz.result_level or quiz.level, quiz.level or "?")
            day = (quiz.completed_at or quiz.article_date)[:10]
            grouped.setdefault(level, []).append(TrendPoint(day=day, percent=quiz.percent))

        order = {level: index for index, level in enumerate(CEFR_LEVELS)}
        levels = sorted(grouped, key=lambda level: order.get(level, len(CEFR_LEVELS)))
        return tuple(
            QuizSeries(level=level, points=tuple(grouped[level][-TREND_POINT_LIMIT:]))
            for level in levels[:TREND_LEVEL_LIMIT]
        )

    def _achievements(
        self,
        *,
        total_articles: int,
        total_reviews: int,
        total_words: int,
        total_quizzes: int,
        longest_streak: int,
        rows: list[DailyStats],
        quiz_days: Counter[str],
        active_dates: set[date],
    ) -> tuple[Achievement, ...]:
        """组装成就。

        注意：`words` 的 current 用当前生词本数量，解锁日却只能用
        `daily_stats.words_added` 反推（words 表没有“状态变更时间”列）。
        两者口径略有差异：删除过的词仍算历史新增，所以极少数情况下
        “当前值已达目标但找不到解锁日”，此时 unlocked_on 留空。
        """
        current = {
            "articles": total_articles,
            "reviews": total_reviews,
            "words": total_words,
            "quizzes": total_quizzes,
            "streak": longest_streak,
        }
        items: list[Achievement] = []
        for key, title, description, target, metric in ACHIEVEMENT_SPECS:
            unlocked_on = ""
            if metric == "streak":
                unlocked_on = self._streak_reach(active_dates, target)
            elif metric == "quizzes":
                unlocked_on = self._quiz_reach(quiz_days, target)
            else:
                fields = {
                    "articles": "article_read",
                    "reviews": "reviews_done",
                    "words": "words_added",
                }
                unlocked_on = self._cumulative_reach(rows, fields[metric], target)
            items.append(
                Achievement(
                    key=key,
                    title=title,
                    description=description,
                    current=current[metric],
                    target=target,
                    unlocked_on=unlocked_on,
                )
            )
        return tuple(items)


__all__ = [
    "ACHIEVEMENT_SPECS",
    "ACTIVITY_DAYS",
    "Achievement",
    "ActivityDay",
    "CUMULATIVE_DAYS",
    "CUMULATIVE_WINDOW_CHOICES",
    "CumulativePoint",
    "DUE_FORECAST_DAYS",
    "DueDay",
    "HEATMAP_WEEKS",
    "HEAT_LEVELS",
    "OUTCOME_ORDER",
    "QuizSeries",
    "RealTextStats",
    "ReadingVolume",
    "STALLED_DAYS",
    "StatsOverview",
    "StatsOverviewService",
    "TREND_LEVEL_LIMIT",
    "TREND_POINT_LIMIT",
    "TrendPoint",
    "WordBands",
    "WordStatusSplit",
    "heat_level",
    "nice_max",
]


def _parse_day(value: str | None) -> date | None:
    """取 ISO 时间的前 10 位解析日期；失败返回 None。"""
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
