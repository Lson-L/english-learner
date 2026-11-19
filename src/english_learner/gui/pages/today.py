"""今日页：学习闭环的入口，数字与文章状态都接真实数据库。

M2 的数据口径：
- 连续学习：`daily_stats` 里连续有活动的天数；
- 今日待复习：`due_date <= 今天` 且未掌握的词数；
- 累计生词：`words` 表总数；
- 今日文章：当天有缓存就显示标题，没有就引导去阅读页生成。

本页不发网络请求：生成文章由阅读页负责，测评由 MainWindow 弹窗负责。
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...models.entities import CEFR_LEVELS, today_str
from ...services.app_context import AppContext
from .common import Card, PageHeader, StatCard


class TodayPage(QWidget):
    """`read_requested`：用户要读/生成今日文章；`assessment_requested`：开始测评。"""

    read_requested = Signal()
    assessment_requested = Signal()
    review_requested = Signal()
    weekly_review_requested = Signal()

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context

        # 首页卡片较多，放进滚动区：窗口较矮时也能看到「本周复盘」，不会把
        # 底部卡片挤到不可见。
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
                "今日",
                "每天读一篇刚好比你水平难一点的文章，标几个生词，再把到期的词复习一遍。",
            )
        )

        stats = QHBoxLayout()
        stats.setSpacing(14)
        self._streak_card = StatCard("连续学习", "0 天", "完成一次阅读或复习即算打卡")
        self._due_card = StatCard("今日待复习", "0 词", "到期生词会在这里提醒你")
        self._words_card = StatCard("累计生词", "0 词", "阅读时标注的单词会自动进入生词本")
        stats.addWidget(self._streak_card)
        stats.addWidget(self._due_card)
        stats.addWidget(self._words_card)
        layout.addLayout(stats)

        layout.addWidget(self._build_profile_card())
        layout.addWidget(self._build_article_card())
        layout.addWidget(self._build_review_card())
        layout.addWidget(self._build_weekly_card())
        layout.addStretch(1)

    # ------------------------------------------------------------------ UI
    def _build_profile_card(self) -> Card:
        card = Card()
        row = QHBoxLayout()
        row.setSpacing(14)

        info = QVBoxLayout()
        info.setSpacing(6)
        self._level_label = QLabel("尚未测评")
        self._level_label.setObjectName("cardTitle")
        info.addWidget(self._level_label)

        self._profile_desc = QLabel(
            "完成一次 5 分钟的摸底测评，AI 会给出 CEFR 等级、词汇量估算和薄弱点。"
        )
        self._profile_desc.setObjectName("muted")
        self._profile_desc.setWordWrap(True)
        info.addWidget(self._profile_desc)

        self._trend_label = QLabel("")
        self._trend_label.setObjectName("muted")
        self._trend_label.setWordWrap(True)
        info.addWidget(self._trend_label)

        row.addLayout(info, 1)
        self._assessment_button = QPushButton("开始测评")
        self._assessment_button.setObjectName("secondaryButton")
        self._assessment_button.clicked.connect(self.assessment_requested.emit)
        row.addWidget(self._assessment_button, 0, Qt.AlignmentFlag.AlignTop)

        card.body.addLayout(row)
        return card

    def _build_article_card(self) -> Card:
        card = Card()
        self._article_title = QLabel("今日文章 · 尚未生成")
        self._article_title.setObjectName("cardTitle")
        card.body.addWidget(self._article_title)

        self._article_desc = QLabel(
            "按你的 CEFR 等级生成 250~350 词短文，并优先复现生词本里到期的词。"
            "生成文章需要联网；断网时仍可复习生词、查看已缓存的文章。"
        )
        self._article_desc.setObjectName("muted")
        self._article_desc.setWordWrap(True)
        card.body.addWidget(self._article_desc)

        self._article_button = QPushButton("生成今日文章")
        self._article_button.setObjectName("primaryButton")
        self._article_button.clicked.connect(self.read_requested.emit)
        button_row = QHBoxLayout()
        button_row.addWidget(self._article_button)
        button_row.addStretch(1)
        card.body.addLayout(button_row)
        return card

    def _build_review_card(self) -> Card:
        card = Card()
        title = QLabel("今日复习")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        self._review_desc = QLabel("")
        self._review_desc.setObjectName("muted")
        self._review_desc.setWordWrap(True)
        card.body.addWidget(self._review_desc)

        self._review_button = QPushButton("开始复习")
        self._review_button.setObjectName("primaryButton")
        self._review_button.clicked.connect(self.review_requested.emit)
        row = QHBoxLayout()
        row.addWidget(self._review_button)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    def _build_weekly_card(self) -> Card:
        card = Card()
        title = QLabel("本周复盘")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        self._weekly_desc = QLabel(
            "汇总本周的阅读检测、生词与复习数据，AI 给出水平趋势和下周建议；"
            "只有你点「应用建议」才会改等级。"
        )
        self._weekly_desc.setObjectName("muted")
        self._weekly_desc.setWordWrap(True)
        card.body.addWidget(self._weekly_desc)

        self._weekly_button = QPushButton("生成本周复盘")
        self._weekly_button.setObjectName("secondaryButton")
        self._weekly_button.clicked.connect(self.weekly_review_requested.emit)
        row = QHBoxLayout()
        row.addWidget(self._weekly_button)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    # -------------------------------------------------------------- 数据刷新
    def on_activated(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        today = today_str()
        stats = self._context.stats_repo
        words = self._context.word_repo

        due_count = stats.count_due(today)

        self._streak_card.set_value(f"{stats.streak()} 天")
        self._due_card.set_value(f"{due_count} 词")
        self._due_card.set_hint("按到期日统计，复习完会自动排到以后")
        self._words_card.set_value(f"{words.count()} 词")

        if due_count:
            self._review_desc.setText(
                f"今天有 {due_count} 个词到期。先回忆再看答案，按四档反馈即可，"
                "3~5 分钟走完，全程离线可用。"
            )
            self._review_button.setText("开始复习")
        else:
            self._review_desc.setText(
                "今天没有到期的词。读一篇文章、标几个新词，明天它们就会出现在复习队列里。"
            )
            self._review_button.setText("查看复习页")

        self._refresh_profile()
        self._refresh_article(today)
        self._refresh_weekly()

    def _refresh_profile(self) -> None:
        profile = self._context.profile()
        if profile is None:
            self._level_label.setText("尚未测评")
            self._profile_desc.setText(
                "完成一次 5 分钟的摸底测评，AI 会给出 CEFR 等级、词汇量估算和薄弱点。"
            )
            self._trend_label.setText("")
            self._assessment_button.setText("开始测评")
            return

        confidence = ""
        if profile.level_confidence:
            confidence = f" · 置信度 {profile.level_confidence:.2f}"
        if profile.vocab_estimate:
            self._level_label.setText(
                f"{profile.cefr_level} · 估算词汇量 {profile.vocab_estimate:,}{confidence}"
            )
        else:
            self._level_label.setText(f"{profile.cefr_level}{confidence}")
        details: list[str] = []
        if profile.strengths:
            details.append("优势：" + "、".join(profile.strengths))
        if profile.weaknesses:
            details.append("薄弱：" + "、".join(profile.weaknesses))
        if profile.comment:
            details.append(profile.comment)
        if profile.level_updated_reason:
            details.append(profile.level_updated_reason)
        self._profile_desc.setText("；".join(details) or "已完成测评。")
        self._trend_label.setText(self._trend_text())
        self._assessment_button.setText("重新测评")

    def _trend_text(self) -> str:
        """近 4 周等级记录，先用文字表达，不做复杂图表。"""
        today = date.today()
        start = (today - timedelta(days=27)).isoformat()
        quizzes = [
            quiz
            for quiz in self._context.quiz_repo.list_completed_between(start, today.isoformat())
            if not quiz.practice_mode
        ]
        if not quizzes:
            return "近 4 周：还没有完成阅读检测"
        counts = {level: 0 for level in CEFR_LEVELS}
        for quiz in quizzes:
            level = quiz.result_level or quiz.level
            if level in counts:
                counts[level] += 1
        parts = [f"{level} ×{counts[level]}" for level in CEFR_LEVELS if counts[level]]
        return f"近 4 周检测 {len(quizzes)} 次：" + " · ".join(parts)

    def _refresh_weekly(self) -> None:
        review = self._context.weekly_review_repo.list_recent(limit=1)
        if not review:
            self._weekly_desc.setText(
                "汇总本周的阅读检测、生词与复习数据，AI 给出水平趋势和下周建议；"
                "只有你点「应用建议」才会改等级。"
            )
            self._weekly_button.setText("生成本周复盘")
            return
        latest = review[0]
        today = date.today()
        current_week = (today - timedelta(days=today.weekday())).isoformat()
        state = "已应用" if latest.applied else "待确认"
        suggestion = latest.suggested_level or "保持当前等级"
        if latest.week_start == current_week:
            prefix = "本周复盘"
            button_text = "查看本周复盘"
        else:
            prefix = f"最近复盘（{latest.week_start} 那一周）"
            button_text = "查看最近复盘"
        self._weekly_desc.setText(f"{prefix}（{state}）：建议 {suggestion}。\n{latest.summary}")
        self._weekly_button.setText(button_text)

    def _refresh_article(self, today: str) -> None:
        article = self._context.article_repo.get_by_date(today)
        if article is None:
            self._article_title.setText("今日文章 · 尚未生成")
            self._article_desc.setText(
                "点击下面的按钮，AI 会按你的等级生成一篇 250~350 词的短文，"
                "并优先复现生词本里到期的词。生成需要联网；断网时可先去复习。"
            )
            self._article_button.setText("生成今日文章")
            return

        self._article_title.setText(f"今日文章 · {article.title}")
        meta = [article.level, f"{article.word_count} 词"]
        if article.topic:
            meta.append(article.topic)
        quiz = (
            self._context.quiz_repo.get_by_article(article.id) if article.id is not None else None
        )
        if quiz is not None and quiz.completed:
            state = f"已完成检测 · 答对 {quiz.score}/{quiz.total}"
        elif article.read_at:
            state = "已读完"
        else:
            state = "未开始"
        self._article_desc.setText(" · ".join(meta) + f" · {state}")
        self._article_button.setText("继续阅读" if article.read_at else "开始阅读")
