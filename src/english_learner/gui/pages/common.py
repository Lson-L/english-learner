"""页面里共用的小组件与样式辅助函数。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

STATUS_COLORS = {
    "hint": "#6B7280",
    "error": "#DC2626",
    "success": "#059669",
    "loading": "#2563EB",
}


class Card(QFrame):
    """白底圆角卡片。内容往 self.body 里加。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 18, 20, 18)
        self.body.setSpacing(10)


class PageHeader(QWidget):
    """页面标题区：大标题 + 一句话说明。"""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        layout.addWidget(title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("pageSubtitle")
            subtitle_label.setWordWrap(True)
            layout.addWidget(subtitle_label)


class StatCard(Card):
    """首页的小指标卡：说明 + 大数字 + 补充文字。"""

    def __init__(self, caption: str, value: str, hint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        caption_label = QLabel(caption)
        caption_label.setObjectName("muted")
        self.body.addWidget(caption_label)

        value_label = QLabel(value)
        value_label.setObjectName("bigNumber")
        self.body.addWidget(value_label)

        hint_label = QLabel(hint)
        hint_label.setObjectName("muted")
        hint_label.setWordWrap(True)
        self.body.addWidget(hint_label)


class PlaceholderPage(QWidget):
    """M1/M2 才实现的页面占位。

    占位页也把产品意图写清楚，避免下个会话只看到一句“待实现”。
    """

    def __init__(
        self,
        title: str,
        subtitle: str,
        items: list[tuple[str, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)

        layout.addWidget(PageHeader(title, subtitle))

        for item_title, item_desc in items:
            card = Card()
            row = QHBoxLayout()
            row.setSpacing(10)

            title_label = QLabel(item_title)
            title_label.setObjectName("cardTitle")
            row.addWidget(title_label)

            badge = QLabel("M1")
            badge.setObjectName("stepBadge")
            row.addWidget(badge)
            row.addStretch(1)
            card.body.addLayout(row)

            desc_label = QLabel(item_desc)
            desc_label.setObjectName("muted")
            desc_label.setWordWrap(True)
            card.body.addWidget(desc_label)

            layout.addWidget(card)

        layout.addStretch(1)


def set_status(label: QLabel, text: str, kind: str = "hint") -> None:
    """更新状态文字颜色。

    直接改 styleSheet 而不是用 QSS 动态属性，省去 unpolish/repolish 的坑。
    """
    color = STATUS_COLORS.get(kind, STATUS_COLORS["hint"])
    label.setText(text)
    label.setStyleSheet(f"color: {color}; font-size: 13px;")
    label.setWordWrap(True)


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFrameShape(QFrame.Shape.HLine)
    return line
