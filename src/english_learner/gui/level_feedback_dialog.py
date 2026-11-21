"""阅读反馈弹窗：写下“这篇读起来怎么样”，AI 重新定级并换一篇。

产品动机：
- 测评定的等级可能偏高，第一篇生词密集会打击积极性；
- 用户读完可以直接说“太难了 / 刚好 / 太简单”，AI 结合今日生词重新定级；
- 新文章尽量复现今天标注的生词，但流畅自然优先。

线程约定：调级 + 重写文章是两次 AI 调用，必须走 TaskWorker；
生成失败时旧文章和旧画像保持原样（服务层先生成、后替换）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models.entities import Article, today_str
from ..services.app_context import AppContext
from ..services.level_feedback_service import LevelAdjustment
from .pages.common import Card, divider, set_status
from .widgets import busy_progress_bar
from .workers import TaskWorker, retire_worker

QUICK_FEEDBACKS: tuple[str, ...] = (
    "太难了，生词太多",
    "有点难，读得吃力",
    "难度刚好",
    "有点简单，想要难一点",
)


class LevelFeedbackDialog(QDialog):
    """模态弹窗。成功后把结果放在 `result_outcome`。"""

    def __init__(
        self,
        context: AppContext,
        article: Article,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._article = article
        self._worker: TaskWorker | None = None
        self.result_outcome: LevelAdjustment | None = None

        self.setWindowTitle("调整阅读难度 · Daily English")
        self.setModal(True)
        self.setMinimumSize(760, 560)
        self.resize(780, 580)
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        badge = QLabel("阅读反馈")
        badge.setObjectName("stepBadge")
        root.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)

        title = QLabel("这篇读起来怎么样？")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        subtitle = QLabel(
            "写下你的真实感受，AI 会结合今天标注的生词重新评定等级，并换一篇更适合你的文章。"
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        root.addWidget(self._build_status_card())
        root.addWidget(self._build_feedback_card())

        warning = QLabel("提交后会重新生成今日文章并清除正文高亮；生词本里的词会保留。")
        warning.setObjectName("muted")
        warning.setWordWrap(True)
        root.addWidget(warning)

        self._status = QLabel("")
        root.addWidget(self._status)
        root.addWidget(divider())

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addStretch(1)
        self._busy_progress = busy_progress_bar(width=110)
        footer.addWidget(self._busy_progress)
        self._cancel_button = QPushButton("取消")
        self._cancel_button.setObjectName("secondaryButton")
        self._cancel_button.setToolTip("重新评估中可以取消；当前等级与文章保持不变")
        self._cancel_button.clicked.connect(self._on_cancel_clicked)
        footer.addWidget(self._cancel_button)

        self._submit_button = QPushButton("重新评估并换一篇")
        self._submit_button.setObjectName("primaryButton")
        self._submit_button.clicked.connect(self._submit)
        footer.addWidget(self._submit_button)
        root.addLayout(footer)

    def _build_status_card(self) -> Card:
        card = Card()
        title = QLabel("当前状态")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        profile = self._context.profile()
        level_text = profile.cefr_level if profile is not None else "尚未测评"
        if profile is not None and profile.vocab_estimate:
            level_text += f"（估算词汇量 {profile.vocab_estimate:,}）"

        words = self._context.level_feedback_service().collect_today_words(
            self._article.id, today_str()
        )
        word_text = (
            "、".join(word.display_form or word.lemma for word in words)
            if words
            else "（今天还没有标注生词）"
        )

        for label_text, value_text in (
            ("当前等级", level_text),
            (
                "今日文章",
                f"《{self._article.title}》（{self._article.level} · "
                f"{self._article.word_count} 词）",
            ),
            ("今日生词", word_text),
        ):
            row = QHBoxLayout()
            row.setSpacing(12)
            key = QLabel(label_text)
            key.setObjectName("fieldLabel")
            row.addWidget(key, 0, Qt.AlignmentFlag.AlignTop)
            value = QLabel(value_text)
            value.setObjectName("muted")
            value.setWordWrap(True)
            row.addWidget(value, 1)
            card.body.addLayout(row)
        return card

    def _build_feedback_card(self) -> Card:
        card = Card()
        title = QLabel("你的阅读感受")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        hint = QLabel("可以直接选一种，也可以自己写，比如“生词太多，一句话要查三次”。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        card.body.addWidget(hint)

        self._feedback_edit = QPlainTextEdit()
        self._feedback_edit.setPlaceholderText("这篇读起来……")
        self._feedback_edit.setFixedHeight(100)
        card.body.addWidget(self._feedback_edit)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        self._quick_buttons: list[QPushButton] = []
        for text in QUICK_FEEDBACKS:
            button = QPushButton(text)
            button.setObjectName("linkButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            # 点击时读按钮当前显示文本：低调模式下填入英文，默认模式仍是中文。
            button.clicked.connect(lambda _checked=False, b=button: self._fill_feedback(b.text()))
            quick_row.addWidget(button)
            self._quick_buttons.append(button)
        quick_row.addStretch(1)
        card.body.addLayout(quick_row)
        return card

    # ------------------------------------------------------------------ 交互
    def _fill_feedback(self, text: str) -> None:
        current = self._feedback_edit.toPlainText().strip()
        self._feedback_edit.setPlainText(text if not current else f"{current}；{text}")
        self._feedback_edit.setFocus()

    def _submit(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        feedback = self._feedback_edit.toPlainText().strip()
        if not feedback:
            set_status(self._status, "先写一句阅读感受，再提交。", "error")
            self._feedback_edit.setFocus()
            return

        service = self._context.level_feedback_service()
        self._set_busy(True)
        set_status(
            self._status,
            "正在重新评估等级并生成新文章，可能需要 20~40 秒…",
            "loading",
        )

        worker = TaskWorker(lambda: service.adjust(feedback))
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _on_succeeded(self, result: object) -> None:
        if not isinstance(result, LevelAdjustment):
            set_status(self._status, "调整完成，但返回了意外数据。", "error")
            return
        self.result_outcome = result
        self.accept()

    def _on_failed(self, message: str) -> None:
        set_status(self._status, message, "error")

    def _on_finished(self) -> None:
        self._worker = None
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._feedback_edit.setEnabled(not busy)
        for button in self._quick_buttons:
            button.setEnabled(not busy)
        self._submit_button.setEnabled(not busy)
        # 忙碌时取消按钮用于放弃请求，空闲时用于退出弹窗。
        self._cancel_button.setEnabled(True)
        self._cancel_button.setText("取消请求" if busy else "取消")
        self._busy_progress.setVisible(busy)

    def _on_cancel_clicked(self) -> None:
        worker = self._worker
        if worker is not None and worker.isRunning():
            if retire_worker(worker):
                self._worker = None
                self._set_busy(False)
                set_status(
                    self._status,
                    "已取消重新评估，当前等级与文章保持不变。",
                    "hint",
                )
            return
        self.reject()

    # ------------------------------------------------------------ 关闭保护
    def reject(self) -> None:  # noqa: D401 - Qt 约定
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "正在重新评估，请等待结果后再关闭。")
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 约定
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "正在重新评估，请等待结果后再关闭。")
            event.ignore()
            return
        super().closeEvent(event)


__all__ = ["QUICK_FEEDBACKS", "LevelFeedbackDialog"]
