"""每周复盘弹窗：展示 AI 总结与建议，用户确认后才改等级。

流程：
1. 打开时调用 `WeeklyReviewService.get_or_generate()`（有本周缓存就不调 AI）；
2. 后台线程等待期间禁止关闭，避免 QThread 被销毁；
3. 展示本周数据、总结、建议等级与下周重点；
4. 用户点「应用建议等级」才写回画像；点「保持当前」只关闭。

线程约定：AI 调用在 TaskWorker 里，写库操作（apply）在 UI 线程，只碰本地 SQLite。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..models.entities import Profile, WeeklyReview
from ..services.app_context import AppContext
from ..services.weekly_review_service import WeeklyReviewService
from .pages.common import Card, divider
from .widgets import busy_progress_bar
from .workers import TaskWorker, retire_worker


class WeeklyReviewDialog(QDialog):
    """模态每周复盘。应用建议后 `result_profile` 为新的画像。"""

    def __init__(
        self,
        context: AppContext,
        parent: QWidget | None = None,
        service: WeeklyReviewService | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._service = service or context.weekly_review_service()
        self._worker: TaskWorker | None = None
        self._review: WeeklyReview | None = None
        self._profile: Profile | None = context.profile()

        self.review: WeeklyReview | None = None
        self.result_profile: Profile | None = None
        self.applied = False

        self.setWindowTitle("本周复盘 · Daily English")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setModal(True)
        self.setMinimumSize(720, 520)
        self.resize(780, 600)
        self._build_ui()
        self._start_loading()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        badge = QLabel("每周复盘")
        badge.setObjectName("stepBadge")
        root.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)

        title = QLabel("看看这一周的进步与下一步")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        self._subtitle = QLabel("AI 会汇总本周的检测、生词和复习数据；等级只在你确认后变化。")
        self._subtitle.setObjectName("pageSubtitle")
        self._subtitle.setWordWrap(True)
        root.addWidget(self._subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(0, 0, 8, 0)
        self._body_layout.setSpacing(12)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        root.addWidget(divider())

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._busy_progress = busy_progress_bar(width=110)
        footer.addWidget(self._busy_progress)
        self._cancel_button = QPushButton("取消")
        self._cancel_button.setObjectName("secondaryButton")
        self._cancel_button.setToolTip("生成中可以取消；本次不会修改任何数据")
        self._cancel_button.clicked.connect(self._on_cancel_clicked)
        footer.addWidget(self._cancel_button)
        self._retry_button = QPushButton("重试")
        self._retry_button.setObjectName("secondaryButton")
        self._retry_button.setVisible(False)
        self._retry_button.clicked.connect(self._start_loading)
        footer.addWidget(self._retry_button)
        self._keep_button = QPushButton("保持当前")
        self._keep_button.setObjectName("secondaryButton")
        self._keep_button.clicked.connect(self.accept)
        footer.addWidget(self._keep_button)
        footer.addStretch(1)

        self._apply_button = QPushButton("应用建议等级")
        self._apply_button.setObjectName("primaryButton")
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._apply)
        footer.addWidget(self._apply_button)
        root.addLayout(footer)

    # ---------------------------------------------------------------- 生成
    def _start_loading(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        profile = self._profile
        if profile is None:
            self._show_error("请先在「今日」页完成水平测评，再做每周复盘。")
            return

        self._clear_body()
        self._retry_button.setVisible(False)
        card = Card()
        label = QLabel("正在汇总本周数据并生成复盘，通常需要 10~30 秒…")
        label.setObjectName("muted")
        label.setWordWrap(True)
        card.body.addWidget(label)
        self._body_layout.addWidget(card)
        self._status = QLabel("")
        self._status.setObjectName("muted")
        self._body_layout.addWidget(self._status)
        self._body_layout.addStretch(1)
        self._keep_button.setEnabled(False)
        self._apply_button.setEnabled(False)
        self._cancel_button.setText("取消请求")
        self._busy_progress.setVisible(True)

        service = self._service
        worker = TaskWorker(lambda: service.get_or_generate(profile))
        worker.succeeded.connect(self._on_loaded)
        worker.failed.connect(self._show_error)
        worker.cancelled.connect(self._on_worker_finished)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_loaded(self, result: object) -> None:
        if not isinstance(result, WeeklyReview):
            self._show_error("复盘返回了意外数据，可以重试。")
            return
        self._review = result
        self.review = result
        self._render(result)

    def _render(self, review: WeeklyReview) -> None:
        self._clear_body()

        summary_card = Card()
        heading = QLabel("本周总结")
        heading.setObjectName("cardTitle")
        summary_card.body.addWidget(heading)
        self._suggestion_label = QLabel(self._suggestion_text(review))
        self._suggestion_label.setObjectName("bigNumber")
        self._suggestion_label.setWordWrap(True)
        summary_card.body.addWidget(self._suggestion_label)
        stats = QLabel(
            f"检测 {review.quiz_count} 次 · 复习 {review.review_count} 次 · "
            f"新增生词 {review.words_added} 个 · 置信度 {review.confidence:.2f}"
        )
        stats.setObjectName("muted")
        stats.setWordWrap(True)
        summary_card.body.addWidget(stats)
        self._body_layout.addWidget(summary_card)

        text_card = Card()
        text_title = QLabel("AI 复盘")
        text_title.setObjectName("cardTitle")
        text_card.body.addWidget(text_title)
        summary = QLabel(review.summary)
        summary.setObjectName("muted")
        summary.setWordWrap(True)
        summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_card.body.addWidget(summary)
        self._body_layout.addWidget(text_card)

        if review.focus:
            focus_card = Card()
            focus_title = QLabel("下周重点")
            focus_title.setObjectName("cardTitle")
            focus_card.body.addWidget(focus_title)
            for index, item in enumerate(review.focus, start=1):
                focus_card.body.addWidget(QLabel(f"{index}. {item}"))
            self._body_layout.addWidget(focus_card)

        if review.applied:
            applied = QLabel(f"本周复盘已应用：{review.applied_level}。")
            applied.setObjectName("muted")
            applied.setWordWrap(True)
            self._body_layout.addWidget(applied)

        self._body_layout.addStretch(1)
        self._retry_button.setVisible(False)
        self._keep_button.setEnabled(True)
        self._apply_button.setEnabled(
            bool(review.suggested_level)
            and review.suggested_level != (self._profile.cefr_level if self._profile else "")
            and not review.applied
        )
        self._subtitle.setText("复盘已生成；只有点「应用建议等级」才会修改画像。")

    @staticmethod
    def _suggestion_text(review: WeeklyReview) -> str:
        if review.applied:
            return f"已应用：{review.applied_level}"
        if review.suggested_level:
            return f"建议等级：{review.suggested_level}"
        return "建议等级：保持当前"

    # ---------------------------------------------------------------- 应用
    def _apply(self) -> None:
        review = self._review
        profile = self._profile
        if review is None or profile is None:
            return
        try:
            updated = self._service.apply_suggestion(review, profile)
        except Exception as exc:  # pragma: no cover - 写库失败只影响本次操作
            QMessageBox.warning(self, "应用失败", f"写回等级时出错：{exc}")
            return
        self.result_profile = updated
        self.applied = True
        self._profile = updated
        self._review = self._service.latest() or review
        self.review = self._review
        if self._review is not None:
            self._render(self._review)

    # ---------------------------------------------------------------- 辅助
    def _show_error(self, message: str) -> None:
        self._clear_body()
        card = Card()
        title = QLabel("暂时无法生成复盘")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)
        detail = QLabel(message)
        detail.setObjectName("muted")
        detail.setWordWrap(True)
        card.body.addWidget(detail)
        self._body_layout.addWidget(card)
        self._status = QLabel("")
        self._body_layout.addWidget(self._status)
        self._body_layout.addStretch(1)
        self._retry_button.setVisible(self._profile is not None)
        self._keep_button.setEnabled(True)
        self._apply_button.setEnabled(False)
        self._subtitle.setText("可以检查网络后重试；本次没有修改任何数据。")

    def _clear_body(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _on_worker_finished(self) -> None:
        self._worker = None
        self._keep_button.setEnabled(True)
        self._cancel_button.setText("取消")
        self._busy_progress.setVisible(False)

    def _on_cancel_clicked(self) -> None:
        worker = self._worker
        if worker is not None and worker.isRunning():
            if retire_worker(worker):
                self._worker = None
                self._on_worker_finished()
                self._show_error("已取消生成复盘；本次没有修改任何数据。")
            return
        self.reject()

    # ------------------------------------------------------------ 关闭保护
    def reject(self) -> None:  # noqa: D401 - Qt 约定
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "AI 正在生成复盘，请等结果出来后再关闭。")
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 约定
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "AI 正在生成复盘，请等结果出来后再关闭。")
            event.ignore()
            return
        super().closeEvent(event)

    def shutdown(self, timeout_ms: int = 3000) -> TaskWorker | None:
        worker = self._worker
        if worker is None or worker.isFinished():
            return None
        if worker.stop(timeout_ms):
            return None
        return worker


__all__ = ["WeeklyReviewDialog"]
