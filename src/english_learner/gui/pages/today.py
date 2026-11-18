"""今日页：学习闭环的入口。

M0 只有静态占位；M1/M2 接入数据库后，这里的数字和卡片要从真实状态读取。
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .common import Card, PageHeader, StatCard


class TodayPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(18)

        layout.addWidget(
            PageHeader(
                "今日",
                "每天读一篇刚好比你水平难一点的文章，标几个生词，再把到期的词复习一遍。",
            )
        )

        stats = QHBoxLayout()
        stats.setSpacing(14)
        stats.addWidget(StatCard("连续学习", "0 天", "完成一次阅读或复习即算打卡"))
        stats.addWidget(StatCard("今日待复习", "0 词", "到期生词会在这里提醒你"))
        stats.addWidget(StatCard("累计生词", "0 词", "阅读时标注的单词会自动进入生词本"))
        layout.addLayout(stats)

        article_card = Card()
        title = QLabel("今日文章 · 尚未生成")
        title.setObjectName("cardTitle")
        article_card.body.addWidget(title)
        desc = QLabel(
            "M1 将接入「按你的 CEFR 等级生成每日文章」：首次进入时先做一次摸底测评，"
            "之后每天打开应用会自动准备一篇 250~350 词的短文，并优先复现你生词本里到期的词。"
        )
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        article_card.body.addWidget(desc)
        layout.addWidget(article_card)

        review_card = Card()
        review_title = QLabel("今日复习 · 队列为空")
        review_title.setObjectName("cardTitle")
        review_card.body.addWidget(review_title)
        review_desc = QLabel(
            "M2 将实现简化版 SM-2 间隔重复：每个词按「忘记 / 模糊 / 认识 / 太简单」"
            "四档反馈计算下次出现时间，每天只复习到期的词。"
        )
        review_desc.setObjectName("muted")
        review_desc.setWordWrap(True)
        review_card.body.addWidget(review_desc)
        layout.addWidget(review_card)

        layout.addStretch(1)
