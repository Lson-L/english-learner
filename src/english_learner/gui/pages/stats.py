"""统计页：把每日 / 累计 / 词汇 / 检测 / 用量数据画成“看得见的积累”。

页面只读 `AppContext` 返回的快照；不查库、不发 AI 请求。
图表全部是 `gui/widgets/charts.py` 里的自绘控件，颜色跟随两套主题。

M8 起布局按“重要数据靠上”重排：
1. 顶部指标卡（连续 / 累计学习 / 生词 / 阅读 / 复习）；
2. 学习效果 + AI 用量（都从设置页迁入）；
3. 学习足迹（热力图）；
4. 累计成长 + 近 30 天活动；
5. 检测趋势 + 错因薄弱点；
6. 词汇掌握 + 复习负载；
7. 真实文章（有数据才显示）；
8. 成就（始终最后）。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...models.entities import AIUsageSummary, today_str
from ...services.app_context import AppContext
from ...services.metrics_service import MetricsSnapshot, PlanCompletion, RateMetric, RetryMetric
from ...services.quiz_insights import (
    ERROR_TYPE_LABELS,
    ERROR_TYPE_LABELS_EN,
    SKILL_LABELS,
    SKILL_LABELS_EN,
    QuizInsights,
)
from ...services.stats_overview import (
    CUMULATIVE_DAYS,
    CUMULATIVE_WINDOW_CHOICES,
    StatsOverview,
    WordStatusSplit,
)
from ..ai_usage_dialog import AIUsageDialog
from ..widgets.achievements import AchievementWall
from ..widgets.charts import (
    CumulativeChart,
    DailyActivityChart,
    DueForecastChart,
    HBarChart,
    HeatLegend,
    HeatmapCalendar,
    ScoreTrendChart,
    WordStatusBar,
)
from .common import Card, MetricTile, PageHeader, StatCard

logger = logging.getLogger(__name__)

# 累计曲线切换项：key → (中文名, 中文单位, 英文名)。
_METRIC_INFO: dict[str, tuple[str, str, str]] = {
    "words": ("生词", "个", "Words"),
    "articles": ("阅读", "篇", "Articles"),
    "reviews": ("复习", "次", "Reviews"),
}

# 累计曲线窗口按钮的英文文案（QComboBox 不走 i18n 的按钮翻译）。
_WINDOW_EN: dict[str, str] = {
    "近 30 天": "30 days",
    "近 90 天": "90 days",
    "近 1 年": "1 year",
    "全部": "All",
}

# 真实文章 outcome 的中文标签。
_OUTCOME_LABELS = {
    "success": "成功",
    "neutral": "一般",
    "struggle": "吃力",
    "skip": "跳过",
    "abandon": "弃读",
}

_EMPTY_HINT = "统计会随着学习和复习自动累积。先去今日页读一篇文章，或在复习页过几个词吧。"
_TIPS_HINT = "再积累几天，这里会出现趋势曲线。"
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
        self._metrics: MetricsSnapshot | None = None
        self._insights: QuizInsights | None = None
        self._usage_today: AIUsageSummary | None = None
        self._usage_total: AIUsageSummary | None = None
        self._metric = "words"
        self._cumulative_days = CUMULATIVE_DAYS
        self._low_profile = bool(low_profile)
        # 需要随主题切换中英文的普通 QLabel（按钮由 gui.i18n 统一处理）。
        self._themed_labels: list[tuple[QLabel, str, str]] = []

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
                "把每天的一点点，攒成看得见的进步：效果、用量、打卡、累计与检测趋势都在这里。",
            )
        )

        self._empty_hint = QLabel(_EMPTY_HINT)
        self._empty_hint.setObjectName("muted")
        self._empty_hint.setWordWrap(True)
        layout.addWidget(self._empty_hint)

        self._tips_hint = QLabel(_TIPS_HINT)
        self._tips_hint.setObjectName("muted")
        self._tips_hint.setWordWrap(True)
        self._tips_hint.setVisible(False)
        layout.addWidget(self._tips_hint)

        layout.addLayout(self._build_stat_cards())
        layout.addLayout(self._build_effect_row())
        layout.addWidget(self._build_heatmap_card())
        layout.addLayout(self._build_cumulative_row())
        layout.addLayout(self._build_trend_row())
        layout.addLayout(self._build_word_row())
        self._real_text_card = self._build_real_text_card()
        layout.addWidget(self._real_text_card)
        self._achievement_card = self._build_achievements_card()
        layout.addWidget(self._achievement_card)
        layout.addStretch(1)

        self.set_low_profile(self._low_profile)

    # ------------------------------------------------------------------ UI
    def _themed_label(self, zh: str, en: str, object_name: str = "cardTitle") -> QLabel:
        """创建随主题切换中英文的 QLabel，并登记到统一刷新的列表。"""
        label = QLabel(self._t(zh, en))
        label.setObjectName(object_name)
        self._themed_labels.append((label, zh, en))
        return label

    def _build_stat_cards(self) -> QHBoxLayout:
        stats = QHBoxLayout()
        stats.setSpacing(14)
        self._streak_card = StatCard(
            self._t("连续学习", "Streak"),
            "0 天",
            self._t("今天有阅读或复习即算打卡", "Active today or not"),
        )
        self._active_card = StatCard(
            self._t("累计学习", "Active days"),
            "0 天",
            self._t("有学习记录的天数", "Days with activity"),
        )
        self._words_card = StatCard(
            self._t("累计生词", "Words"),
            "0 词",
            self._t("只统计加入生词本的词", "Saved words only"),
        )
        self._reading_card = StatCard(
            self._t("累计阅读", "Reading"),
            "0 词",
            self._t("按打开过的文章计", "Opened articles"),
        )
        self._reviews_card = StatCard(
            self._t("累计复习", "Reviews"),
            "0 次",
            self._t("每次四档反馈都会计数", "Every rating counts"),
        )
        for card in (
            self._streak_card,
            self._active_card,
            self._words_card,
            self._reading_card,
            self._reviews_card,
        ):
            # 5 张卡横排；给最小宽度，窗口变窄时靠布局压缩而不是把数字挤成两行。
            card.setMinimumWidth(136)
            stats.addWidget(card)
        return stats

    def _build_effect_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        self._effect_card = self._build_effect_card()
        self._ai_card = self._build_ai_card()
        row.addWidget(self._effect_card, 1)
        row.addWidget(self._ai_card, 1)
        return row

    def _build_effect_card(self) -> Card:
        card = Card()
        title_row = QHBoxLayout()
        title_row.addWidget(self._themed_label("学习效果（近 30 天）", "Learning effect (30 days)"))
        title_row.addStretch(1)
        card.body.addLayout(title_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        metrics = (
            ("first_pass", "生词首次通过", "First-pass"),
            ("relearn", "词回炉率", "Relearn rate"),
            ("plan", "计划完成率", "Plan done"),
            ("article_retry", "文章生成重试", "Article retry"),
            ("quiz_retry", "阅读检测重试", "Quiz retry"),
        )
        self._effect_tiles: dict[str, MetricTile] = {}
        self._effect_captions: dict[str, tuple[str, str]] = {}
        for index, (key, caption, caption_en) in enumerate(metrics):
            tile = MetricTile(self._t(caption, caption_en))
            self._effect_tiles[key] = tile
            self._effect_captions[key] = (caption, caption_en)
            grid.addWidget(tile, index // 3, index % 3)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        card.body.addLayout(grid)

        self._trend_label = QLabel("")
        self._trend_label.setObjectName("muted")
        self._trend_label.setWordWrap(True)
        card.body.addWidget(self._trend_label)

        note = self._themed_label(
            "首次通过率看每个词的第一次复习评分；回炉率 = 复习过的词里出现过"
            "“忘记 / 模糊”的比例；重试率 = 重试请求占该任务全部请求的比例。",
            "First-pass uses each word's first review; relearn = share of reviewed "
            "words that ever scored Forgot/Vague; retry = retries over all attempts.",
            "muted",
        )
        note.setWordWrap(True)
        card.body.addWidget(note)
        return card

    def _build_ai_card(self) -> Card:
        card = Card()
        title_row = QHBoxLayout()
        title_row.addWidget(self._themed_label("AI 用量", "AI usage"))
        title_row.addStretch(1)
        self._usage_button = QPushButton("查看调用记录")
        self._usage_button.setObjectName("secondaryButton")
        self._usage_button.clicked.connect(self._open_usage)
        title_row.addWidget(self._usage_button)
        card.body.addLayout(title_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        metrics = (
            ("today_calls", "今日调用", "Calls today"),
            ("today_failed", "今日失败", "Failed today"),
            ("today_tokens", "今日 tokens", "Tokens today"),
            ("total_calls", "累计调用", "Calls total"),
            ("total_tokens", "累计 tokens", "Tokens total"),
            ("latency", "平均耗时", "Avg latency"),
        )
        self._usage_tiles: dict[str, MetricTile] = {}
        self._usage_captions: dict[str, tuple[str, str]] = {}
        for index, (key, caption, caption_en) in enumerate(metrics):
            tile = MetricTile(self._t(caption, caption_en))
            self._usage_tiles[key] = tile
            self._usage_captions[key] = (caption, caption_en)
            # tokens 可能到 7 位以上，2 列比 3 列更不容易挤爆（尤其低调主题等宽字体）。
            grid.addWidget(tile, index // 2, index % 2)
        for column in range(2):
            grid.setColumnStretch(column, 1)
        card.body.addLayout(grid)

        note = self._themed_label(
            "只记录任务、模型、token、耗时与错误，不保存提示词和模型回复。",
            "Only task, model, tokens, latency and errors are stored.",
            "muted",
        )
        note.setWordWrap(True)
        card.body.addWidget(note)
        return card

    def _build_heatmap_card(self) -> Card:
        card = Card()
        card.body.addWidget(self._themed_label("学习足迹", "Activity"))
        self._heatmap_card = card

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

    def _build_cumulative_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        self._cumulative_card = self._build_cumulative_card()
        self._daily_card = self._build_daily_card()
        row.addWidget(self._cumulative_card, 1)
        row.addWidget(self._daily_card, 1)
        return row

    def _build_cumulative_card(self) -> Card:
        card = Card()
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(self._themed_label("累计成长", "Cumulative"))
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

        bottom = QHBoxLayout()
        self._cumulative_summary = QLabel("")
        self._cumulative_summary.setObjectName("muted")
        self._cumulative_summary.setWordWrap(True)
        bottom.addWidget(self._cumulative_summary, 1)
        self._window_combo = QComboBox()
        self._window_combo.setToolTip(self._t("累计曲线时间范围", "Cumulative window"))
        self._fill_window_combo()
        self._window_combo.currentIndexChanged.connect(self._on_window_changed)
        bottom.addWidget(self._window_combo, 0)
        card.body.addLayout(bottom)
        return card

    def _build_daily_card(self) -> Card:
        card = Card()
        card.body.addWidget(self._themed_label("近 30 天", "Last 30 days"))

        self._daily_chart = DailyActivityChart()
        card.body.addWidget(self._daily_chart)

        self._daily_summary = QLabel("")
        self._daily_summary.setObjectName("muted")
        self._daily_summary.setWordWrap(True)
        card.body.addWidget(self._daily_summary)
        return card

    def _build_trend_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        self._score_card = self._build_quiz_card()
        self._insight_card = self._build_insight_card()
        row.addWidget(self._score_card, 1)
        row.addWidget(self._insight_card, 1)
        return row

    def _build_quiz_card(self) -> Card:
        card = Card()
        card.body.addWidget(self._themed_label("检测得分趋势", "Quiz scores"))

        self._score_chart = ScoreTrendChart()
        card.body.addWidget(self._score_chart)

        self._quiz_summary = QLabel("")
        self._quiz_summary.setObjectName("muted")
        self._quiz_summary.setWordWrap(True)
        card.body.addWidget(self._quiz_summary)
        return card

    def _build_insight_card(self) -> Card:
        card = Card()
        card.body.addWidget(self._themed_label("错因与薄弱点", "Error patterns"))

        self._error_chart = HBarChart()
        self._error_chart.set_rows((), empty_zh="还没有错因记录", empty_en="No error data yet")
        card.body.addWidget(self._error_chart)

        self._insight_summary = QLabel("")
        self._insight_summary.setObjectName("muted")
        self._insight_summary.setWordWrap(True)
        card.body.addWidget(self._insight_summary)
        return card

    def _build_word_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        self._word_card = self._build_word_card()
        self._due_card = self._build_due_card()
        row.addWidget(self._word_card, 1)
        row.addWidget(self._due_card, 1)
        return row

    def _build_word_card(self) -> Card:
        card = Card()
        card.body.addWidget(self._themed_label("词汇掌握", "Vocabulary"))

        self._word_bar = WordStatusBar()
        card.body.addWidget(self._word_bar)

        self._word_summary = QLabel("")
        self._word_summary.setObjectName("muted")
        self._word_summary.setWordWrap(True)
        card.body.addWidget(self._word_summary)

        self._band_summary = QLabel("")
        self._band_summary.setObjectName("muted")
        self._band_summary.setWordWrap(True)
        card.body.addWidget(self._band_summary)
        return card

    def _build_due_card(self) -> Card:
        card = Card()
        card.body.addWidget(self._themed_label("复习负载", "Review load"))

        self._due_chart = DueForecastChart()
        card.body.addWidget(self._due_chart)

        self._due_summary = QLabel("")
        self._due_summary.setObjectName("muted")
        self._due_summary.setWordWrap(True)
        card.body.addWidget(self._due_summary)
        return card

    def _build_real_text_card(self) -> Card:
        card = Card()
        card.body.addWidget(self._themed_label("真实文章", "Real articles"))

        self._real_text_summary = QLabel("")
        self._real_text_summary.setObjectName("muted")
        self._real_text_summary.setWordWrap(True)
        card.body.addWidget(self._real_text_summary)

        self._real_text_outcome = QLabel("")
        self._real_text_outcome.setObjectName("muted")
        self._real_text_outcome.setWordWrap(True)
        card.body.addWidget(self._real_text_outcome)
        card.setVisible(False)
        return card

    def _build_achievements_card(self) -> Card:
        card = Card()
        title_row = QHBoxLayout()
        title_row.addWidget(self._themed_label("成就", "Achievements"))
        title_row.addStretch(1)
        self._achievement_summary = QLabel("")
        self._achievement_summary.setObjectName("muted")
        title_row.addWidget(self._achievement_summary)
        card.body.addLayout(title_row)

        self._achievement_wall = AchievementWall()
        card.body.addWidget(self._achievement_wall)

        note = self._themed_label(
            "成就由本地记录自动解锁，不消耗 token；进度按当前累计值计算。",
            "Achievements unlock from local records only; progress uses current totals.",
            "muted",
        )
        note.setWordWrap(True)
        card.body.addWidget(note)
        return card

    # -------------------------------------------------------------- 数据刷新
    def on_activated(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        try:
            overview = self._context.stats_overview(cumulative_days=self._cumulative_days)
        except Exception:  # noqa: BLE001 - 只读统计失败不应让页面崩掉
            logger.warning("计算学习统计失败", exc_info=True)
            self._show_error()
            return
        self._overview = overview
        self._metrics = self._load_metrics()
        self._insights = self._load_insights()
        self._usage_today, self._usage_total = self._load_usage()
        self._render(overview)

    def _load_metrics(self) -> MetricsSnapshot | None:
        loader = getattr(self._context, "metrics_service", None)
        if not callable(loader):
            return None
        try:
            return loader().build()
        except Exception:  # noqa: BLE001 - 单个卡片失败不影响整页
            logger.warning("计算学习效果失败", exc_info=True)
            return None

    def _load_insights(self) -> QuizInsights | None:
        loader = getattr(self._context, "quiz_insights", None)
        if not callable(loader):
            return None
        try:
            return loader()
        except Exception:  # noqa: BLE001 - 单个卡片失败不影响整页
            logger.warning("计算错因分布失败", exc_info=True)
            return None

    def _load_usage(self) -> tuple[AIUsageSummary | None, AIUsageSummary | None]:
        repo = getattr(self._context, "ai_call_repo", None)
        if repo is None:
            return None, None
        try:
            return repo.summary(since=today_str()), repo.summary()
        except Exception:  # noqa: BLE001 - 单个卡片失败不影响整页
            logger.warning("读取 AI 用量失败", exc_info=True)
            return None, None

    def _render(self, overview: StatsOverview) -> None:
        self._empty_hint.setText(_EMPTY_HINT)
        self._empty_hint.setVisible(not overview.has_data)

        self._streak_card.set_value(f"{overview.current_streak} 天")
        self._streak_card.set_hint(
            self._t("今天已打卡", "Active today")
            if overview.today_active
            else self._t("今天还没打卡", "Not active today")
        )
        self._active_card.set_value(f"{overview.active_days} 天")
        self._active_card.set_hint(
            self._t(
                f"最长连续 {overview.longest_streak} 天",
                f"Best streak {overview.longest_streak}",
            )
        )
        self._words_card.set_value(f"{overview.total_words:,} 词")
        self._reviews_card.set_value(f"{overview.total_reviews:,} 次")
        volume = overview.reading_volume
        self._reading_card.set_value(f"{volume.total_words:,} 词")
        self._reading_card.set_hint(
            self._t(
                f"近 7 天 {volume.words_7d:,} · 近 30 天 {volume.words_30d:,}"
                f" · 完成检测 {volume.completed_words:,} 词"
                f" · 已读真实 {volume.real_articles} / AI {volume.generated_articles} 篇",
                f"7d {volume.words_7d:,} · 30d {volume.words_30d:,}"
                f" · quizzed {volume.completed_words:,}"
                f" · read real {volume.real_articles} / AI {volume.generated_articles}",
            )
        )
        self._reading_card.set_tip(
            self._t(
                f"阅读词数按打开文章计；完成检测是更严格的口径；"
                f"约合 {volume.est_books} 本 9 万词的书",
                f"Reading counts opened articles; quizzed is stricter; "
                f"≈ {volume.est_books} books of 90k words",
            )
        )

        self._render_effect()
        self._render_usage()
        self._render_real_text(overview)

        self._heatmap.set_days(overview.heatmap_days)
        self._cumulative_chart.set_points(overview.cumulative)
        self._cumulative_chart.set_metric(self._metric)
        self._daily_chart.set_days(overview.daily)
        self._word_bar.set_split(overview.word_status)
        self._score_chart.set_series(overview.quiz_series)
        self._due_chart.set_days(overview.due_forecast)
        self._achievement_wall.set_achievements(overview.achievements)
        self._update_visibility(overview)
        self._render_summaries()

    def _render_effect(self) -> None:
        snapshot = self._metrics
        if snapshot is None:
            for tile in self._effect_tiles.values():
                tile.set_value("—")
            self._trend_label.setText(
                self._t("学习效果暂时不可用。", "Learning effect is unavailable.")
            )
            return
        self._effect_tiles["first_pass"].set_value(self._rate_text(snapshot.first_pass))
        self._effect_tiles["relearn"].set_value(self._rate_text(snapshot.relearn))
        self._effect_tiles["plan"].set_value(self._plan_text(snapshot.plan))
        self._effect_tiles["article_retry"].set_value(self._retry_text(snapshot.article_retry))
        self._effect_tiles["quiz_retry"].set_value(self._retry_text(snapshot.quiz_retry))
        self._trend_label.setText(self._trend_text(snapshot))

    def _render_usage(self) -> None:
        today = self._usage_today
        total = self._usage_total
        if today is None or total is None:
            for tile in self._usage_tiles.values():
                tile.set_value("—")
            return
        self._usage_tiles["today_calls"].set_value(f"{today.calls:,}")
        self._usage_tiles["today_failed"].set_value(f"{today.failed_calls:,}")
        self._usage_tiles["today_tokens"].set_value(f"{today.total_tokens:,}")
        self._usage_tiles["total_calls"].set_value(f"{total.calls:,}")
        self._usage_tiles["total_tokens"].set_value(f"{total.total_tokens:,}")
        self._usage_tiles["latency"].set_value(f"{today.avg_latency_ms:,} ms")

    def _render_real_text(self, overview: StatsOverview) -> None:
        stats = overview.real_text
        self._real_text_card.setVisible(stats.has_data)
        if not stats.has_data:
            self._real_text_summary.setText("")
            self._real_text_outcome.setText("")
            return
        completion = f"{stats.completion_rate:.0%}" if stats.completion_rate is not None else "—"
        overridden = f"{stats.overridden_rate:.0%}" if stats.overridden_rate is not None else "—"
        abandoned = f"{stats.abandoned_rate:.0%}" if stats.abandoned_rate is not None else "—"
        volume = overview.reading_volume
        self._real_text_summary.setText(
            self._t(
                f"上架 {stats.total} 篇 · 完成 {stats.completed}（{completion}）"
                f" · 换回 {stats.overridden}（{overridden}）"
                f" · 弃读 {stats.abandoned}（{abandoned}）"
                f" · 已读真实 {volume.real_articles} 篇 / AI {volume.generated_articles} 篇",
                f"Served {stats.total} · completed {stats.completed} ({completion})"
                f" · overridden {stats.overridden} ({overridden})"
                f" · abandoned {stats.abandoned} ({abandoned})"
                f" · read real {volume.real_articles} / AI {volume.generated_articles}",
            )
        )
        if stats.outcomes:
            parts = []
            for key, count in stats.outcomes:
                label = _OUTCOME_LABELS.get(key, key) if not self._low_profile else key
                parts.append(f"{label} {count}")
            self._real_text_outcome.setText(self._t("反馈：", "Feedback: ") + " · ".join(parts))
        else:
            self._real_text_outcome.setText("")

    def _show_error(self) -> None:
        self._overview = None
        self._metrics = None
        self._insights = None
        self._usage_today = None
        self._usage_total = None
        for card in (
            self._streak_card,
            self._active_card,
            self._words_card,
            self._reading_card,
            self._reviews_card,
        ):
            card.set_value("—")
        for tile in (*self._effect_tiles.values(), *self._usage_tiles.values()):
            tile.set_value("—")
        self._heatmap.set_days(())
        self._cumulative_chart.set_points(())
        self._daily_chart.set_days(())
        self._word_bar.set_split(WordStatusSplit())
        self._score_chart.set_series(())
        self._due_chart.set_days(())
        self._error_chart.set_rows(())
        self._achievement_wall.set_achievements(())
        self._real_text_card.setVisible(False)
        self._empty_hint.setText(_ERROR_HINT)
        self._empty_hint.setVisible(True)
        self._tips_hint.setVisible(False)
        for label in (
            self._heat_summary,
            self._cumulative_summary,
            self._daily_summary,
            self._word_summary,
            self._band_summary,
            self._due_summary,
            self._quiz_summary,
            self._insight_summary,
            self._achievement_summary,
            self._trend_label,
            self._real_text_summary,
            self._real_text_outcome,
        ):
            label.setText("")

    # -------------------------------------------------------------- 可见性
    def _update_visibility(self, overview: StatsOverview) -> None:
        """数据很少时收起趋势卡，避免整页都是“还没有数据”的空图。"""
        has_data = overview.has_data
        enough = has_data and overview.active_days >= 3
        # 两张数字卡始终显示：没有数据时显示 “—”，比把半行留空更整齐。
        self._effect_card.setVisible(True)
        self._ai_card.setVisible(True)
        self._heatmap_card.setVisible(has_data)
        self._cumulative_card.setVisible(enough)
        self._daily_card.setVisible(enough)
        self._score_card.setVisible(enough)
        self._insight_card.setVisible(enough)
        self._word_card.setVisible(has_data)
        self._due_card.setVisible(has_data)
        self._tips_hint.setVisible(has_data and not enough)

    # -------------------------------------------------------------- 文案刷新
    def _select_metric(self, metric: str) -> None:
        if metric not in _METRIC_INFO:
            return
        self._metric = metric
        self._cumulative_chart.set_metric(metric)
        if self._overview is not None:
            self._render_cumulative_summary()

    def _fill_window_combo(self) -> None:
        current = self._cumulative_days
        self._window_combo.blockSignals(True)
        self._window_combo.clear()
        for label, days in CUMULATIVE_WINDOW_CHOICES:
            text = _WINDOW_EN.get(label, label) if self._low_profile else label
            self._window_combo.addItem(text, days)
        index = self._window_combo.findData(current)
        self._window_combo.setCurrentIndex(max(0, index))
        self._window_combo.blockSignals(False)

    def _on_window_changed(self, index: int) -> None:
        days = self._window_combo.itemData(index)
        if days is None:
            return
        days = int(days)
        if days == self._cumulative_days:
            return
        self._cumulative_days = days
        self.refresh()

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

        bands = overview.word_bands
        if bands.has_data:
            parts = [f"{band} {count}" for band, count in bands.bands]
            if bands.unlabeled:
                parts.append(self._t(f"未标注 {bands.unlabeled}", f"unlabeled {bands.unlabeled}"))
            self._band_summary.setText(self._t("词档 ", "Bands ") + " · ".join(parts))
        else:
            self._band_summary.setText("")
        if overview.stalled_words:
            self._word_summary.setText(
                self._word_summary.text()
                + self._t(
                    f" · 停滞 {overview.stalled_words} 个（7 天前到期未复习）",
                    f" · {overview.stalled_words} stalled",
                )
            )

        self._due_summary.setText(
            self._t(
                f"今天到期 {overview.due_today} 个 · 其中逾期 {overview.overdue_words} 个"
                f" · 未来 7 天共 {sum(day.count for day in overview.due_forecast)} 个",
                f"Due today {overview.due_today} · overdue {overview.overdue_words}"
                f" · next 7 days {sum(day.count for day in overview.due_forecast)}",
            )
        )

        self._quiz_summary.setText(self._quiz_summary_text(overview))
        self._render_insight_summary()
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
        delta = max(0, current - overview.cumulative_baseline.value(self._metric))
        window = (
            self._t("全部", "all time")
            if overview.cumulative_days <= 0
            else self._t(f"近 {overview.cumulative_days} 天", f"{overview.cumulative_days} days")
        )
        self._cumulative_summary.setText(
            self._t(
                f"当前累计{label} {current:,} {unit} · {window} +{delta:,}",
                f"{english} {current:,} · +{delta:,} in {window}",
            )
        )

    def _render_insight_summary(self) -> None:
        insights = self._insights
        if insights is None or not insights.has_data:
            self._error_chart.set_rows(
                (),
                empty_zh="还没有错因记录",
                empty_en="No error data yet",
            )
            self._insight_summary.setText(
                self._t(
                    "完成阅读检测后，这里会显示错因分布。",
                    "Finish a reading quiz to see error patterns.",
                )
            )
            return

        labels = ERROR_TYPE_LABELS_EN if self._low_profile else ERROR_TYPE_LABELS
        rows = tuple(
            (labels.get(key, key), count) for key, count in insights.errors[:5] if key != "none"
        )
        self._error_chart.set_rows(
            rows,
            empty_zh="最近没有答错的题",
            empty_en="No wrong answers recently",
            unit="",
        )

        parts = []
        unknown = insights.unknown_rate
        if unknown is not None:
            parts.append(self._t(f"不知道 {unknown:.0%}", f"don't know {unknown:.0%}"))
        skill_labels = SKILL_LABELS_EN if self._low_profile else SKILL_LABELS
        weakest = [item for item in insights.skills if item.total >= 2][:3]
        for item in weakest:
            parts.append(f"{skill_labels.get(item.skill, item.skill)} {item.accuracy:.0%}")
        summary = self._t(
            f"近 {insights.window_days} 天 {insights.quizzes} 次检测",
            f"{insights.quizzes} quizzes in {insights.window_days} days",
        )
        if parts:
            summary += " · " + " · ".join(parts)
        difficulty_parts = [
            f"{item.level} {item.accuracy:.0%}" for item in insights.difficulties if item.total > 0
        ][:4]
        if difficulty_parts:
            summary += self._t(" · 难度 ", " · levels ") + " / ".join(difficulty_parts)
        self._insight_summary.setText(summary)

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

    # -------------------------------------------------------------- 效果文案
    @staticmethod
    def _rate_text(metric: RateMetric) -> str:
        rate = metric.rate
        if rate is None:
            return "—"
        return f"{metric.hit}/{metric.total} · {rate:.0%}"

    @staticmethod
    def _retry_text(metric: RetryMetric) -> str:
        rate = metric.rate
        if rate is None:
            return "—"
        return f"{metric.retries}/{metric.attempts} · {rate:.0%}"

    @staticmethod
    def _plan_text(plan: PlanCompletion | None) -> str:
        if plan is None or plan.planned == 0:
            return "—"
        rate = plan.rate or 0.0
        return f"{plan.completed}/{plan.planned} · {rate:.0%}"

    def _trend_text(self, snapshot: MetricsSnapshot) -> str:
        if not snapshot.quiz_trends:
            return self._t(
                "同等级得分趋势：近 30 天还没有完成的阅读检测。",
                "Score trend: no completed quizzes in 30 days.",
            )
        arrows = {"up": "↑", "down": "↓", "flat": "→"}
        lines = [
            self._t(
                "同等级得分趋势（最近 3 次对比更早 3 次）：",
                "Score trend (last 3 vs earlier 3):",
            )
        ]
        for trend in snapshot.quiz_trends:
            base = self._t(
                f"{trend.level} · {len(trend.points)} 次 · 最近一次 {trend.latest_percent}%",
                f"{trend.level} · {len(trend.points)} quizzes · latest {trend.latest_percent}%",
            )
            delta = trend.delta
            if delta is None or trend.recent_avg is None:
                lines.append(base + self._t(" · 样本不足，暂不判断趋势", " · not enough samples"))
                continue
            arrow = arrows.get(trend.direction, "→")
            zh_delta = (
                f" · 近 3 次均值 {trend.recent_avg:.0f}%（较之前 {delta:+.0f} 个百分点 {arrow}）"
            )
            en_delta = f" · avg {trend.recent_avg:.0f}% ({delta:+.0f} pts {arrow})"
            lines.append(base + self._t(zh_delta, en_delta))
        return "\n".join(lines)

    def _open_usage(self) -> None:
        repo = getattr(self._context, "ai_call_repo", None)
        if repo is None:
            return
        AIUsageDialog(repo, parent=self).exec()
        self._usage_today, self._usage_total = self._load_usage()
        if self._overview is not None:
            self._render_usage()
            self._update_visibility(self._overview)

    def _t(self, zh: str, en: str) -> str:
        return en if self._low_profile else zh

    # ------------------------------------------------------------------ 主题
    def set_low_profile(self, low_profile: bool) -> None:
        """切换主题：图表重取调色板，普通文字与汇总文案一起切英文。"""
        self._low_profile = bool(low_profile)
        for widget in (
            self._heatmap,
            self._heat_legend,
            self._cumulative_chart,
            self._daily_chart,
            self._word_bar,
            self._score_chart,
            self._due_chart,
            self._error_chart,
        ):
            widget.set_low_profile(self._low_profile)
        self._achievement_wall.set_low_profile(self._low_profile)
        for label, zh, en in self._themed_labels:
            label.setText(self._t(zh, en))
        # 指标小卡的说明文字也要切中英文。
        for key, (zh, en) in self._effect_captions.items():
            self._effect_tiles[key].set_caption(self._t(zh, en))
        for key, (zh, en) in self._usage_captions.items():
            self._usage_tiles[key].set_caption(self._t(zh, en))
        # 顶部 StatCard 的说明与标题也要切中英文；标题存在 caption 标签里，
        # 这里最多 5 张卡，直接重建文案比反射找控件更清晰。
        self._apply_stat_card_copy()
        self._fill_window_combo()
        if self._overview is not None:
            self._render(self._overview)

    def _apply_stat_card_copy(self) -> None:
        pairs = (
            (
                self._streak_card,
                "连续学习",
                "Streak",
                "今天有阅读或复习即算打卡",
                "Active today or not",
            ),
            (
                self._active_card,
                "累计学习",
                "Active days",
                "有学习记录的天数",
                "Days with activity",
            ),
            (
                self._words_card,
                "累计生词",
                "Words",
                "只统计加入生词本的词",
                "Saved words only",
            ),
            (
                self._reading_card,
                "累计阅读",
                "Reading",
                "按打开过的文章计",
                "Opened articles",
            ),
            (
                self._reviews_card,
                "累计复习",
                "Reviews",
                "每次四档反馈都会计数",
                "Every rating counts",
            ),
        )
        for card, zh_caption, en_caption, zh_hint, en_hint in pairs:
            card.set_caption(self._t(zh_caption, en_caption))
            card.set_hint(self._t(zh_hint, en_hint))


__all__ = ["StatsPage"]
