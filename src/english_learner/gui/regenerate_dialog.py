"""换一篇弹窗：写主题/方向或阅读感受，留空则由 AI 自行选题。

产品动机：
- 固定日期轮换的主题很宽泛，用户可能今天就想读点别的（深海、创业、足球……）；
- 读得吃力或太简单时，也不必再找“调整难度”按钮，直接在同一个输入框里说；
- 有输入时先按这句话重新评估等级，再生成新文章；留空则保持当前等级随机换一篇。

线程约定：弹窗只负责收集文本，真正的 AI 调用仍由阅读页放进 TaskWorker。
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

        subtitle = QLabel(
            "可以写一个主题/方向；如果写「太难了」「太简单」之类的阅读感受，"
            "会先重新评估等级，再生成新文章。留空则由 AI 自己挑一个新选题。"
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        self._request_edit = QPlainTextEdit()
        self._request_edit.setPlaceholderText(
            "例如：写一篇关于深海生物的文章；或者：生词太多，读得吃力"
        )
        self._request_edit.setFixedHeight(96)
        root.addWidget(self._request_edit)

        hint = QLabel("有输入时会先按这句话重新评估等级；留空则保持当前等级随机换一篇。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

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

    def request_text(self) -> str:
        """用户填写的主题或阅读感受；空字符串表示只换选题、不改等级。"""
        return self._request_edit.toPlainText().strip()[:REQUEST_MAX_LEN]


__all__ = ["REQUEST_MAX_LEN", "RegenerateDialog"]
