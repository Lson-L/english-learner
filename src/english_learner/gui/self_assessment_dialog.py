"""阅读自评弹窗：首次阅读检测开始前强制选择阅读感受。

产品动机：
- 原来「这篇读起来」放在第二轮题目页，默认「刚好」，用户容易直接跳过；
- 自评是等级后验里的弱证据，也是安排下一篇真实文章的依据，值得单独确认；
- AI 出题在打开阅读检测时就已经在后台跑，这里只等一个选择，不耽误出题。

线程约定：纯本地弹窗，不发起任何网络请求；AI 请求由阅读检测弹窗的 TaskWorker 负责。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .pages.common import divider

# (中文标签, 落库值, 一句话说明)；顺序与阅读检测里的下拉框一致。
SELF_REPORT_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("轻松", "easy", "几乎不用查词，读得很顺"),
    ("刚好", "ok", "有少量生词，但能读懂大意"),
    ("吃力", "hard", "生词和长句较多，读得比较费劲"),
)


class SelfAssessmentDialog(QDialog):
    """模态弹窗：未选择阅读感受前不能开始检测。

    用户点「暂不检测」或直接关窗表示放弃本次检测（不会跳过自评继续答题）。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = ""
        self.setWindowTitle("阅读感受 · Daily English")
        self.setModal(True)
        self.setMinimumWidth(480)
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        badge = QLabel("阅读完成")
        badge.setObjectName("stepBadge")
        root.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)

        title = QLabel("这篇读起来怎么样？")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        subtitle = QLabel(
            "AI 已在后台准备题目；先选择这篇读起来的感受，题目准备好后立即开始。"
            "这个选择会作为弱证据参与等级后验，不会单独决定升降级。"
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        self._group = QButtonGroup(self)
        self._buttons: list[QRadioButton] = []
        for index, (label, _, hint) in enumerate(SELF_REPORT_OPTIONS):
            radio = QRadioButton(label)
            radio.setToolTip(hint)
            self._group.addButton(radio, index)
            self._buttons.append(radio)
            root.addWidget(radio)

            description = QLabel(hint)
            description.setObjectName("muted")
            description.setWordWrap(True)
            description.setContentsMargins(28, 0, 0, 0)
            root.addWidget(description)
        self._group.idToggled.connect(self._on_toggled)

        self._hint = QLabel("请选择一项后再开始检测；不想做可以点「暂不检测」。")
        self._hint.setObjectName("muted")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

        root.addWidget(divider())

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addStretch(1)

        self._cancel_button = QPushButton("暂不检测")
        self._cancel_button.setObjectName("secondaryButton")
        self._cancel_button.setAutoDefault(False)
        self._cancel_button.clicked.connect(self.reject)
        footer.addWidget(self._cancel_button)

        self._start_button = QPushButton("开始检测")
        self._start_button.setObjectName("primaryButton")
        self._start_button.setAutoDefault(False)
        self._start_button.setDefault(True)
        self._start_button.setEnabled(False)
        self._start_button.clicked.connect(self.accept)
        footer.addWidget(self._start_button)
        root.addLayout(footer)

    # ----------------------------------------------------------------- 交互
    def _on_toggled(self, index: int, checked: bool) -> None:
        if not checked or not 0 <= index < len(SELF_REPORT_OPTIONS):
            return
        self._value = SELF_REPORT_OPTIONS[index][1]
        self._hint.setText("已选择阅读感受，可以开始检测。")
        self._start_button.setEnabled(True)

    def self_report(self) -> str:
        """`easy` / `ok` / `hard`；未选择时是空串。"""
        return self._value


__all__ = ["SELF_REPORT_OPTIONS", "SelfAssessmentDialog"]
