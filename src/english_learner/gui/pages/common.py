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
    "warning": "#B45309",
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
    """页面标题区：大标题 + 一句话说明。

    compact=True 用更小的标题/说明字号和更紧的行距，适合复习页这种
    要把竖向空间尽量让给正文卡片的页面。
    """

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
        *,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2 if compact else 6)

        # 标题可以留空：调用方只想要说明文字时不用自己拼 QLabel。
        # 保留引用供低调模式动态隐藏（例如生词本页的“生词本”三个字）。
        self._title_label: QLabel | None = None
        if title:
            self._title_label = QLabel(title)
            self._title_label.setObjectName("pageTitleCompact" if compact else "pageTitle")
            layout.addWidget(self._title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("pageSubtitleCompact" if compact else "pageSubtitle")
            subtitle_label.setWordWrap(True)
            layout.addWidget(subtitle_label)

    def set_title_visible(self, visible: bool) -> None:
        """显示/隐藏大标题（低调模式下用来去掉显眼的页面名）。"""
        if self._title_label is not None:
            self._title_label.setVisible(bool(visible))


class StatCard(Card):
    """首页的小指标卡：说明 + 大数字 + 补充文字。

    数字和补充文字支持后续刷新（M1 起首页接真实数据）。
    """

    def __init__(self, caption: str, value: str, hint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._caption_label = QLabel(caption)
        self._caption_label.setObjectName("muted")
        self.body.addWidget(self._caption_label)

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

    def set_caption(self, caption: str) -> None:
        self._caption_label.setText(caption)

    def set_tip(self, text: str) -> None:
        """把提示同时挂到卡片与标签上：子控件会遮挡父控件的 tooltip。"""
        self.setToolTip(text)
        self._value_label.setToolTip(text)
        self._hint_label.setToolTip(text)


class MetricTile(QWidget):
    """学习效果 / AI 用量的小指标：说明 + 数字。

    统计页和设置页共用；数字使用 `usageValue` 样式（比首页大数字小一档），
    说明使用 `muted`。两套主题都由全局 QSS 接管，这里不写死颜色。
    """

    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(52)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._caption_label = QLabel(caption)
        self._caption_label.setObjectName("muted")
        layout.addWidget(self._caption_label)

        self._value_label = QLabel("—")
        self._value_label.setObjectName("usageValue")
        layout.addWidget(self._value_label)

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)

    def set_caption(self, caption: str) -> None:
        self._caption_label.setText(caption)

    @property
    def value(self) -> str:
        return self._value_label.text()


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
