"""页面里共用的小组件与样式辅助函数。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
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
    """首页的小指标卡：说明 + 大数字 + 补充文字。

    数字和补充文字支持后续刷新（M1 起首页接真实数据）。
    """

    def __init__(self, caption: str, value: str, hint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        caption_label = QLabel(caption)
        caption_label.setObjectName("muted")
        self.body.addWidget(caption_label)

        self._value_label = QLabel(value)
        self._value_label.setObjectName("bigNumber")
        self.body.addWidget(self._value_label)

        self._hint_label = QLabel(hint)
        self._hint_label.setObjectName("muted")
        self._hint_label.setWordWrap(True)
        self.body.addWidget(self._hint_label)

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)

    def set_hint(self, hint: str) -> None:
        self._hint_label.setText(hint)


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
