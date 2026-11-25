"""换一篇弹窗：写一句想读的内容，留空则由 AI 自行选题。

产品动机：
- 固定日期轮换的主题很宽泛，用户可能今天就想读点别的（深海、创业、足球……）；
- 让用户一句话说清方向，比在 prompt 里猜主题更准；
- 读得吃力或太简单时，不必回阅读页找按钮，直接点弹窗里的「调整难度」，
  继续走原来的阅读反馈调级流程。

线程约定：弹窗只负责收集文本 / 选择模式，真正的 AI 调用仍由阅读页放进 TaskWorker。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .pages.common import divider

# 与 prompts.build_article_messages 里的截断长度保持一致。
REQUEST_MAX_LEN = 300


class RegenerateDialog(QDialog):
    """模态弹窗；确认后用 `request_text()` 取方向，空字符串表示让 AI 决定。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = "regenerate"
        self.setWindowTitle("换一篇 · Daily English")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        badge = QLabel("换一篇")
        badge.setObjectName("stepBadge")
        root.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)

        title = QLabel("想读点什么？")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        subtitle = QLabel("可以写一个主题、场景或关键词；留空则由 AI 自己挑一个全新的选题。")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        self._request_edit = QPlainTextEdit()
        self._request_edit.setPlaceholderText(
            "例如：写一篇关于深海生物的文章；或者：一个普通人的创业故事，句子简单一点"
        )
        self._request_edit.setFixedHeight(96)
        root.addWidget(self._request_edit)

        hint = QLabel("文章等级和词数仍按当前 CEFR 设置执行。")
        hint.setObjectName("muted")
        root.addWidget(hint)

        adjust_row = QHBoxLayout()
        adjust_row.setSpacing(8)
        adjust_hint = QLabel("读得吃力或太简单？")
        adjust_hint.setObjectName("muted")
        adjust_row.addWidget(adjust_hint)
        self._adjust_button = QPushButton("调整难度…")
        self._adjust_button.setObjectName("linkButton")
        self._adjust_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._adjust_button.clicked.connect(self._on_adjust_clicked)
        adjust_row.addWidget(self._adjust_button)
        adjust_row.addStretch(1)
        root.addLayout(adjust_row)

        warning = QLabel("换一篇会删除今天的文章及正文高亮，生词本里的词不会丢失。")
        warning.setObjectName("muted")
        warning.setWordWrap(True)
        root.addWidget(warning)

        root.addWidget(divider())

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addStretch(1)

        self._cancel_button = QPushButton("取消")
        self._cancel_button.setObjectName("secondaryButton")
        self._cancel_button.clicked.connect(self.reject)
        footer.addWidget(self._cancel_button)

        self._confirm_button = QPushButton("生成新文章")
        self._confirm_button.setObjectName("primaryButton")
        self._confirm_button.clicked.connect(self.accept)
        footer.addWidget(self._confirm_button)
        root.addLayout(footer)

        self._request_edit.setFocus()

    def _on_adjust_clicked(self) -> None:
        """选择“调整难度”：由阅读页打开原来的阅读反馈弹窗。"""
        self._mode = "adjust"
        self.accept()

    def mode(self) -> str:
        """`regenerate`（换选题）或 `adjust`（调整难度）。"""
        return self._mode

    def request_text(self) -> str:
        """用户填写的主题/方向；空字符串表示由 AI 自行决定。"""
        return self._request_edit.toPlainText().strip()[:REQUEST_MAX_LEN]


__all__ = ["REQUEST_MAX_LEN", "RegenerateDialog"]
