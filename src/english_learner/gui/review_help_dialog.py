"""复习求助弹窗：点“忘记 / 模糊”后，先问原因，再让 AI 给针对性记忆帮助。

交互：
- 快捷原因按钮可以组合，也可以自己写；
- 「请 AI 帮我记」在 TaskWorker 里请求，成功后就地展示建议；
- 「跳过，直接记录」不做 AI 请求，评分照常记录（离线可用）；
- 关闭 / Esc 表示取消本次评分，留在当前卡片。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..models.entities import Word
from ..services.app_context import AppContext
from ..services.review_help_service import ReviewHelpResult, ReviewHelpService
from ..services.srs import RATING_VAGUE
from .pages.common import set_status
from .workers import TaskWorker

# 弹窗与屏幕边缘的留白：窗口边框 + 任务栏，避免贴边。
_SCREEN_MARGIN = 48

_QUICK_REASONS: tuple[str, ...] = (
    "和近义词混淆",
    "只记得大概意思",
    "发音或拼写记不住",
    "不会在句子里用",
    "完全没印象",
    "换个语境就不认识",
)


class ReviewHelpDialog(QDialog):
    def __init__(
        self,
        context: AppContext,
        word: Word,
        rating: int,
        parent: QWidget | None = None,
        service: ReviewHelpService | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._word = word
        self._rating = rating
        self._service = service or context.review_help_service()
        self._worker: TaskWorker | None = None
        self._advice = ""
        self._result_ready = False
        self._reason_buttons: list[QPushButton] = []

        self.setWindowTitle("记忆帮助")
        self.setMinimumWidth(580)
        self._build_ui()
        self._fit_to_screen()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        title = "有点模糊？" if self._rating == RATING_VAGUE else "为什么没记住？"
        self._title_label = QLabel(title)
        self._title_label.setObjectName("pageTitle")
        layout.addWidget(self._title_label)

        # 正文放进滚动区：AI 建议可能很长，内容超高时滚动，而不是把窗口撑出屏幕。
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_body = QWidget()
        body = QVBoxLayout(self._scroll_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        hint_label = QLabel("写下卡住的地方，AI 会针对这个词给你具体的记忆方法。")
        hint_label.setObjectName("muted")
        hint_label.setWordWrap(True)
        body.addWidget(hint_label)

        body.addWidget(self._build_word_card())
        body.addWidget(self._field_label("哪里卡住了？（可点快捷原因，也可以自己写）"))
        body.addLayout(self._build_quick_reasons())

        self._reason_edit = QPlainTextEdit()
        self._reason_edit.setPlaceholderText(
            "例如：总是和 affect / effect 搞混；只记得“有韧性的”，说不出英文……"
        )
        self._reason_edit.setFixedHeight(76)
        body.addWidget(self._reason_edit)

        self._advice_label = QLabel("")
        self._advice_label.setObjectName("reviewHelpAdvice")
        self._advice_label.setWordWrap(True)
        self._advice_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._advice_label.setVisible(False)
        body.addWidget(self._advice_label)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        body.addWidget(self._status)

        self._scroll.setWidget(self._scroll_body)
        layout.addWidget(self._scroll, 1)

        # 按钮行固定在滚动区外，窗口再矮也点得到。
        self._button_row = QWidget()
        buttons = QHBoxLayout(self._button_row)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(10)
        buttons.addStretch(1)

        self._skip_button = QPushButton("跳过，直接记录")
        self._skip_button.setObjectName("secondaryButton")
        self._skip_button.setToolTip("不请求 AI，按当前评分继续复习")
        self._skip_button.clicked.connect(self._skip)
        buttons.addWidget(self._skip_button)

        self._submit_button = QPushButton("请 AI 帮我记")
        self._submit_button.setObjectName("primaryButton")
        self._submit_button.clicked.connect(self._submit)
        buttons.addWidget(self._submit_button)
        layout.addWidget(self._button_row)

    def _fit_to_screen(self) -> None:
        """按内容高度定窗口大小，并封顶在屏幕可用区域内。

        背景：带布局的 QDialog 不做显式 resize 时，Qt 会在每次移动窗口后
        按 sizeHint 重新撑高；窗口一旦高过屏幕，Windows 会按最大跟踪尺寸
        把高度截断，布局又把它撑回去，拖动时高度在两者之间反复跳变（看起来
        就是“闪烁移动”）。这里显式 resize 并给最大高度封顶，之后 Qt 不再
        自动撑高，超出的内容由滚动区承接。
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        margins = self.layout().contentsMargins()

        max_width = max(self.minimumWidth(), avail.width() - _SCREEN_MARGIN)
        max_height = max(320, avail.height() - _SCREEN_MARGIN)
        self.setMaximumWidth(max_width)
        self.setMaximumHeight(max_height)

        width = min(max(self.minimumWidth(), self.sizeHint().width()), max_width)
        # 预留垂直滚动条宽度，保证正文按“会出现滚动条”时的宽度换行。
        scrollbar_width = self._scroll.verticalScrollBar().sizeHint().width()
        viewport_width = max(
            width
            - margins.left()
            - margins.right()
            - 2 * self._scroll.frameWidth()
            - scrollbar_width,
            1,
        )
        body_height = self._scroll_body.heightForWidth(viewport_width)
        if body_height <= 0:
            body_height = self._scroll_body.sizeHint().height()

        chrome = (
            margins.top()
            + margins.bottom()
            + self._title_label.sizeHint().height()
            + self._button_row.sizeHint().height()
            + 2 * self.layout().spacing()
        )
        self.resize(width, min(chrome + body_height, max_height))

    def _build_word_card(self) -> QWidget:
        word = self._word
        card = QWidget()
        box = QVBoxLayout(card)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        title = QLabel(word.display_form or word.lemma)
        title.setObjectName("wordTitle")
        title.setWordWrap(True)
        box.addWidget(title)

        if word.display_form and word.display_form.lower() != word.lemma.lower():
            form = QLabel(f"原形：{word.lemma}")
            form.setObjectName("wordPhonetic")
            box.addWidget(form)

        meaning = QLabel("；".join(part for part in (word.meaning_cn, word.meaning_en) if part))
        meaning.setObjectName("wordMeaningCn")
        meaning.setWordWrap(True)
        box.addWidget(meaning)

        if word.context_sentence:
            context = QLabel(f"原文：{word.context_sentence}")
            context.setObjectName("wordContext")
            context.setWordWrap(True)
            box.addWidget(context)

        if word.mnemonic:
            mnemonic = QLabel(f"已有记忆方法：{word.mnemonic}")
            mnemonic.setObjectName("wordMnemonic")
            mnemonic.setWordWrap(True)
            box.addWidget(mnemonic)
        return card

    def _build_quick_reasons(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for index, reason in enumerate(_QUICK_REASONS):
            button = QPushButton(reason)
            button.setObjectName("secondaryButton")
            button.clicked.connect(lambda _checked=False, text=reason: self._append_reason(text))
            self._reason_buttons.append(button)
            grid.addWidget(button, index // 3, index % 3)
        return grid

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    # ------------------------------------------------------------------ 状态
    @property
    def result_reason(self) -> str:
        return self._reason_edit.toPlainText().strip()[:300]

    @property
    def result_advice(self) -> str:
        return self._advice

    def _append_reason(self, text: str) -> None:
        current = self._reason_edit.toPlainText().strip()
        if text in current:
            return
        combined = f"{current}；{text}" if current else text
        self._reason_edit.setPlainText(combined)

    # ------------------------------------------------------------------ AI
    def _submit(self) -> None:
        if self._result_ready:
            self.accept()
            return
        if self._worker is not None and self._worker.isRunning():
            return

        reason = self.result_reason
        word = self._word
        rating = self._rating
        level = self._context.level()
        service = self._service

        self._set_busy(True)
        set_status(self._status, "AI 正在分析你的困难，请稍候…", "loading")
        worker = TaskWorker(lambda: service.help(word, rating, reason, level))
        worker.succeeded.connect(self._on_help_ready)
        worker.failed.connect(self._on_help_failed)
        worker.cancelled.connect(self._on_worker_finished)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_help_ready(self, result: object) -> None:
        if not isinstance(result, ReviewHelpResult):
            set_status(self._status, "AI 返回了意外数据，可以重试或跳过。", "error")
            return

        self._advice = result.advice
        text = result.advice
        if result.mnemonic:
            text = f"{text}\n\n记忆方法：{result.mnemonic}"
        self._advice_label.setText(text)
        self._advice_label.setVisible(True)
        self._result_ready = True
        self._submit_button.setText("完成")
        self._skip_button.setText("关闭")
        set_status(self._status, "建议已生成：点「完成」记录本次复习。", "success")
        # 建议可能很长：窗口最多长到屏幕高度，超出的部分滚动，并滚到建议处。
        self._fit_to_screen()
        self._scroll.ensureWidgetVisible(self._advice_label)

    def _on_help_failed(self, message: str) -> None:
        self._result_ready = False
        self._submit_button.setText("重试")
        set_status(self._status, message, "error")

    def _on_worker_finished(self) -> None:
        self._worker = None
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._submit_button.setEnabled(not busy)
        self._skip_button.setEnabled(not busy)
        self._reason_edit.setEnabled(not busy)
        for button in self._reason_buttons:
            button.setEnabled(not busy)

    # ------------------------------------------------------------------ 关闭
    def _skip(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        # 已有 AI 建议时保留（按钮此时显示“关闭”），没有则按“跳过”处理。
        self.accept()

    def reject(self) -> None:  # noqa: D401 - Qt 约定的方法名
        # 请求进行中不允许关闭，避免 QThread 被销毁。
        if self._worker is not None and self._worker.isRunning():
            return
        super().reject()

    def shutdown(self, timeout_ms: int = 3000) -> TaskWorker | None:
        worker = self._worker
        if worker is None or worker.isFinished():
            return None
        if worker.stop(timeout_ms):
            return None
        return worker
