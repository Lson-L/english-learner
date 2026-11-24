"""统计页：把每日 / 累计 / 词汇 / 检测数据画成“看得见的积累”。

页面只读 `AppContext.stats_overview()` 返回的快照；不查库、不发 AI 请求。
图表全部是 `gui/widgets/charts.py` 里的自绘控件，颜色跟随两套主题。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...services.app_context import AppContext
from ...services.stats_overview import StatsOverview, WordStatusSplit
from ..widgets.achievements import AchievementWall
from ..widgets.charts import (
    CumulativeChart,
    DailyActivityChart,
    HeatLegend,
    HeatmapCalendar,
    ScoreTrendChart,
    WordStatusBar,
)
from .common import Card, PageHeader, StatCard

logger = logging.getLogger(__name__)

# 累计曲线切换项：key → (中文名, 中文单位, 英文名)。
_METRIC_INFO: dict[str, tuple[str, str, str]] = {
    "words": ("生词", "个", "Words"),
    "articles": ("阅读", "篇", "Articles"),
    "reviews": ("复习", "次", "Reviews"),
}

_EMPTY_HINT = "统计会随着学习和复习自动累积。先去今日页读一篇文章，或在复习页过几个词吧。"
_ERROR_HINT = "统计计算失败，下次进入本页时会自动重试。"


class StatsPage(QWidget):
    """学习统计页；`on_activated()` 时从本地数据库重新聚合。"""

    def __init__(
        self,
        context: AppContext,
        parent: QWidget | None = None,
        *,
        low_profile: bool = False,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._overview: StatsOverview | None = None
        self._metric = "words"
        self._low_profile = bool(low_profile)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(18)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        layout.addWidget(
            PageHeader(
                "统计",
                "把每天的一点点，攒成看得见的进步：打卡、累计、词汇与检测趋势都在这里。",
            )
        )

        self._empty_hint = QLabel(_EMPTY_HINT)
        self._empty_hint.setObjectName("muted")
        self._empty_hint.setWordWrap(True)
        layout.addWidget(self._empty_hint)

        stats = QHBoxLayout()
        stats.setSpacing(14)
        self._streak_card = StatCard("连续学习", "0 天", "今天有阅读或复习即算打卡")
        self._active_card = StatCard("累计学习", "0 天", "有学习记录的天数")
        self._words_card = StatCard("累计生词", "0 词", "只统计加入生词本的词")
        self._reviews_card = StatCard("累计复习", "0 次", "每次四档反馈都会计数")
        stats.addWidget(self._streak_card)
        stats.addWidget(self._active_card)
        stats.addWidget(self._words_card)
        stats.addWidget(self._reviews_card)
        layout.addLayout(stats)

        layout.addWidget(self._build_heatmap_card())
        layout.addWidget(self._build_cumulative_card())
        layout.addWidget(self._build_daily_card())

        columns = QHBoxLayout()
        columns.setSpacing(14)
        columns.addWidget(self._build_word_card(), 1)
        columns.addWidget(self._build_quiz_card(), 1)
        layout.addLayout(columns)

        layout.addWidget(self._build_achievements_card())
        layout.addStretch(1)

        self.set_low_profile(self._low_profile)

    # ------------------------------------------------------------------ UI
    def _build_heatmap_card(self) -> Card:
        card = Card()
        title = QLabel("学习足迹")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        self._heatmap = HeatmapCalendar()
        # 热力图只有 26 列宽，居中放比拉满整张卡片更好看。
        card.body.addWidget(self._heatmap, 0, Qt.AlignmentFlag.AlignHCenter)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._heat_summary = QLabel("")
        self._heat_summary.setObjectName("muted")
        self._heat_summary.setWordWrap(True)
        row.addWidget(self._heat_summary, 1)
        self._heat_legend = HeatLegend()
        row.addWidget(self._heat_legend, 0, Qt.AlignmentFlag.AlignRight)
        card.body.addLayout(row)
        return card

    def _build_cumulative_card(self) -> Card:
        card = Card()
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel("累计成长")
        title.setObjectName("cardTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)

        self._metric_group = QButtonGroup(self)
        self._metric_group.setExclusive(True)
        for key, (label, _unit, _english) in _METRIC_INFO.items():
            button = QPushButton(label)
            button.setObjectName("segmentButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, value=key: self._select_metric(value))
            self._metric_group.addButton(button)
            title_row.addWidget(button)
            if key == self._metric:
                button.setChecked(True)

        card.body.addLayout(title_row)
        self._cumulative_chart = CumulativeChart()
        self._cumulative_chart.set_metric(self._metric)
        card.body.addWidget(self._cumulative_chart)

        self._cumulative_summary = QLabel("")
        self._cumulative_summary.setObjectName("muted")
        self._cumulative_summary.setWordWrap(True)
        card.body.addWidget(self._cumulative_summary)
        return card

    def _build_daily_card(self) -> Card:
        card = Card()
        title = QLabel("近 30 天")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        self._daily_chart = DailyActivityChart()
        card.body.addWidget(self._daily_chart)

        self._daily_summary = QLabel("")
        self._daily_summary.setObjectName("muted")
        self._daily_summary.setWordWrap(True)
        card.body.addWidget(self._daily_summary)
        return card

    def _build_word_card(self) -> Card:
        card = Card()
        title = QLabel("词汇掌握")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        self._word_bar = WordStatusBar()
        card.body.addWidget(self._word_bar)

        self._word_summary = QLabel("")
        self._word_summary.setObjectName("muted")
        self._word_summary.setWordWrap(True)
        card.body.addWidget(self._word_summary)
        return card

    def _build_quiz_card(self) -> Card:
        card = Card()
        title = QLabel("检测得分趋势")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        self._score_chart = ScoreTrendChart()
        card.body.addWidget(self._score_chart)

        self._quiz_summary = QLabel("")
        self._quiz_summary.setObjectName("muted")
        self._quiz_summary.setWordWrap(True)
        card.body.addWidget(self._quiz_summary)
        return card

    def _build_achievements_card(self) -> Card:
        card = Card()
        title_row = QHBoxLayout()
        title = QLabel("成就")
        title.setObjectName("cardTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self._achievement_summary = QLabel("")
        self._achievement_summary.setObjectName("muted")
        title_row.addWidget(self._achievement_summary)
        card.body.addLayout(title_row)

        self._achievement_wall = AchievementWall()
        card.body.addWidget(self._achievement_wall)

        note = QLabel("成就由本地记录自动解锁，不消耗 token；进度按当前累计值计算。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        card.body.addWidget(note)
        return card

    # -------------------------------------------------------------- 数据刷新
    def on_activated(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        try:
            overview = self._context.stats_overview()
        except Exception:  # noqa: BLE001 - 只读统计失败不应让页面崩掉
            logger.warning("计算学习统计失败", exc_info=True)
            self._show_error()
            return
        self._overview = overview
        self._render(overview)

    def _render(self, overview: StatsOverview) -> None:
        self._empty_hint.setText(_EMPTY_HINT)
        self._empty_hint.setVisible(not overview.has_data)

        self._streak_card.set_value(f"{overview.current_streak} 天")
        self._active_card.set_value(f"{overview.active_days} 天")
        self._active_card.set_hint(f"最长连续 {overview.longest_streak} 天")
        self._words_card.set_value(f"{overview.total_words:,} 词")
        self._reviews_card.set_value(f"{overview.total_reviews:,} 次")

        self._heatmap.set_days(overview.heatmap_days)
        self._cumulative_chart.set_points(overview.cumulative)
        self._cumulative_chart.set_metric(self._metric)
        self._daily_chart.set_days(overview.daily)
        self._word_bar.set_split(overview.word_status)
        self._score_chart.set_series(overview.quiz_series)
        self._achievement_wall.set_achievements(overview.achievements)
        self._render_summaries()

    def _show_error(self) -> None:
        self._overview = None
        for card in (
            self._streak_card,
            self._active_card,
            self._words_card,
            self._reviews_card,
        ):
            card.set_value("—")
        self._heatmap.set_days(())
        self._cumulative_chart.set_points(())
        self._daily_chart.set_days(())
        self._word_bar.set_split(WordStatusSplit())
        self._score_chart.set_series(())
        self._achievement_wall.set_achievements(())
        self._empty_hint.setText(_ERROR_HINT)
        self._empty_hint.setVisible(True)
        for label in (
            self._heat_summary,
            self._cumulative_summary,
            self._daily_summary,
            self._word_summary,
            self._quiz_summary,
            self._achievement_summary,
        ):
            label.setText("")

    # -------------------------------------------------------------- 文案刷新
    def _select_metric(self, metric: str) -> None:
        if metric not in _METRIC_INFO:
            return
        self._metric = metric
        self._cumulative_chart.set_metric(metric)
        if self._overview is not None:
            self._render_cumulative_summary()

    def _render_summaries(self) -> None:
        overview = self._overview
        if overview is None:
            return

        active = overview.heatmap_active_days
        self._heat_summary.setText(
            self._t(
                f"近 26 周打卡 {active} 天 · 最长连续 {overview.longest_streak} 天",
                f"{active} active days in 26 weeks · best streak {overview.longest_streak}",
            )
        )
        self._render_cumulative_summary()

        articles = sum(day.articles for day in overview.daily)
        words = sum(day.words for day in overview.daily)
        reviews = sum(day.reviews for day in overview.daily)
        self._daily_summary.setText(
            self._t(
                f"阅读 {articles} 篇 · 新增生词 {words} 个 · 复习 {reviews} 次",
                f"Read {articles} · words {words} · reviews {reviews}",
            )
        )

        split = overview.word_status
        if split.total:
            rate = split.mastered_rate or 0.0
            self._word_summary.setText(
                self._t(
                    f"共 {split.total:,} 个 · 已掌握 {split.mastered:,} 个（{rate:.0%}）",
                    f"{split.total:,} saved · {split.mastered:,} mastered ({rate:.0%})",
                )
            )
        else:
            self._word_summary.setText(self._t("还没有加入生词本的词。", "No saved words yet."))

        self._quiz_summary.setText(self._quiz_summary_text(overview))
        total_achievements = len(overview.achievements)
        self._achievement_summary.setText(
            self._t(
                f"已解锁 {overview.unlocked_achievements}/{total_achievements}",
                f"{overview.unlocked_achievements}/{total_achievements} unlocked",
            )
        )

    def _render_cumulative_summary(self) -> None:
        overview = self._overview
        if overview is None or not overview.cumulative:
            return
        label, unit, english = _METRIC_INFO[self._metric]
        current = overview.cumulative[-1].value(self._metric)
        delta = max(0, current - overview.cumulative[0].value(self._metric))
        self._cumulative_summary.setText(
            self._t(
                f"当前累计{label} {current:,} {unit} · 近 90 天 +{delta:,}",
                f"{english} {current:,} · +{delta:,} in 90 days",
            )
        )

    def _quiz_summary_text(self, overview: StatsOverview) -> str:
        latest: tuple[str, str, int] | None = None
        for series in overview.quiz_series:
            for point in series.points:
                if latest is None or point.day > latest[0]:
                    latest = (point.day, series.level, point.percent)
        if latest is None:
            return self._t(
                "完成阅读检测后，这里会显示得分趋势。",
                "Finish a reading quiz to see score trends.",
            )
        _day, level, percent = latest
        return self._t(
            f"共 {overview.total_quizzes} 次 · 最近一次 {level} {percent}%",
            f"{overview.total_quizzes} quizzes · latest {level} {percent}%",
        )

    def _t(self, zh: str, en: str) -> str:
        return en if self._low_profile else zh

    # ------------------------------------------------------------------ 主题
    def set_low_profile(self, low_profile: bool) -> None:
        """切换主题：图表重取调色板，内部文字与汇总文案一起切英文。"""
        self._low_profile = bool(low_profile)
        for widget in (
            self._heatmap,
            self._heat_legend,
            self._cumulative_chart,
            self._daily_chart,
            self._word_bar,
            self._score_chart,
        ):
            widget.set_low_profile(self._low_profile)
        self._achievement_wall.set_low_profile(self._low_profile)
        if self._overview is not None:
            self._render_summaries()


__all__ = ["StatsPage"]
