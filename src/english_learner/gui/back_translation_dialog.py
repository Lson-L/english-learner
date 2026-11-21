"""回译练习弹窗：看中文，用英文写回原句，AI 给最小修改与原因。

交互流程（AI_TASKS.md M5.4）：
1. 打开时先查缓存：一篇文章只出一次题，重复打开不重复请求 AI；
2. 没有缓存 → 本地抽 3 句 + 一次 AI 翻译出题（TaskWorker）；
3. 用户逐句写英文 → 一次批量批改，逐句展示得分、最小修改、原因与错误类型；
4. 已完成的练习再次打开直接展示结果，不重复判分。

边界：只作练习反馈，不影响等级与 SRS；失败 / 离线可以直接关闭跳过。

线程约定：出题与批改都是网络请求，必须走 TaskWorker；
请求进行中拒绝关闭窗口，避免 QThread 被销毁。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..models.entities import Article, WritingAttempt
from ..services.app_context import AppContext
from ..services.back_translation import SCORE_PER_SENTENCE, WRITING_ERROR_LABELS
from ..services.back_translation_service import BackTranslationService
from .pages.common import Card, divider, set_status
from .widgets import busy_progress_bar
from .workers import TaskWorker, retire_worker


class AttemptCard(Card):
    """一句练习：中文参考 + 英文输入 + 批改结果。"""

    def __init__(
        self,
        attempt: WritingAttempt,
        index: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.attempt = attempt

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel(f"第 {index + 1} 句")
        title.setObjectName("fieldLabel")
        header.addWidget(title)
        header.addStretch(1)
        self._badge = QLabel("")
        header.addWidget(self._badge)
        self.body.addLayout(header)

        self._cn_label = QLabel(attempt.reference_cn or "（还没有中文译文）")
        self._cn_label.setWordWrap(True)
        self._cn_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._cn_label.setStyleSheet("color: #111827; font-size: 15px;")
        self.body.addWidget(self._cn_label)

        hint = QLabel("用英文写出这句话：")
        hint.setObjectName("muted")
        self.body.addWidget(hint)

        self._edit = QPlainTextEdit()
        self._edit.setPlaceholderText("Write the sentence in English…")
        self._edit.setFixedHeight(78)
        self._edit.setPlainText(attempt.answer_en)
        self.body.addWidget(self._edit)

        self._edited_label = QLabel("")
        self._edited_label.setWordWrap(True)
        self._edited_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(self._edited_label)

        self._reason_label = QLabel("")
        self._reason_label.setWordWrap(True)
        self.body.addWidget(self._reason_label)

        self._tags_label = QLabel("")
        self._tags_label.setObjectName("muted")
        self._tags_label.setWordWrap(True)
        self.body.addWidget(self._tags_label)

        self._edited_label.hide()
        self._reason_label.hide()
        self._tags_label.hide()

        if attempt.graded:
            self.apply(attempt)

    # ------------------------------------------------------------------ 数据
    def answer(self) -> str:
        return self._edit.toPlainText().strip()

    def set_busy(self, busy: bool) -> None:
        # 已批改的卡片保持可选中/复制（readOnly 已禁止编辑），不要再置灰。
        if self.attempt.graded:
            return
        self._edit.setEnabled(not busy)

    def focus_editor(self) -> None:
        self._edit.setFocus()

    def apply(self, attempt: WritingAttempt) -> None:
        """把批改结果渲染到卡片；已批改的输入框锁定为最终答案。"""
        self.attempt = attempt
        if attempt.graded:
            self._edit.setPlainText(attempt.answer_en)
            self._edit.setReadOnly(True)
            self._edit.setEnabled(True)

            score = int(attempt.score or 0)
            if score >= SCORE_PER_SENTENCE:
                self._badge.setText(f"{SCORE_PER_SENTENCE}/{SCORE_PER_SENTENCE} · 正确")
                self._badge.setStyleSheet("color: #059669;")
            else:
                self._badge.setText(f"0/{SCORE_PER_SENTENCE} · 需要修改")
                self._badge.setStyleSheet("color: #DC2626;")

            verdict = attempt.verdict or {}
            edited = str(verdict.get("edited") or "").strip()
            if edited:
                self._edited_label.setText(f"最小修改：{edited}")
            elif score >= SCORE_PER_SENTENCE:
                self._edited_label.setText("最小修改：无需修改。")
            if self._edited_label.text():
                self._edited_label.show()

            reason = str(verdict.get("reason") or "").strip()
            if reason:
                self._reason_label.setText(f"原因：{reason}")
                self._reason_label.show()

            types = verdict.get("error_types")
            labels = [
                WRITING_ERROR_LABELS.get(str(item), str(item))
                for item in (types if isinstance(types, (list, tuple)) else ())
                if str(item or "").strip()
            ]
            if labels:
                self._tags_label.setText("问题类型：" + "、".join(labels))
                self._tags_label.show()


class BackTranslationDialog(QDialog):
    """模态回译练习弹窗。完成后把结果放在 `result_attempts`。"""

    def __init__(
        self,
        context: AppContext,
        article: Article,
        parent: QWidget | None = None,
        service: BackTranslationService | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._article = article
        self._service = service or context.back_translation_service()
        self._worker: TaskWorker | None = None
        self._attempts: tuple[WritingAttempt, ...] = ()
        self._cards: list[AttemptCard] = []
        self._prepared = False
        self._graded = False
        self._grade_failed = False

        self.result_attempts: tuple[WritingAttempt, ...] = ()

        self.setWindowTitle("回译练习 · Daily English")
        self.setModal(True)
        self.setMinimumSize(860, 680)
        self.resize(900, 720)
        self._build_ui()
        self._bootstrap()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        badge = QLabel("回译练习")
        badge.setObjectName("stepBadge")
        root.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)

        title = QLabel("看中文，把英文写回来")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        subtitle = QLabel(
            "从今天的文章里抽了 3 句。先读中文，再用自己的英文写出来；"
            "提交后 AI 会对照原句给出最小修改和原因。只作练习反馈，"
            "不影响等级与复习安排。"
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self._cards_layout = QVBoxLayout(body)
        self._cards_layout.setContentsMargins(0, 0, 8, 0)
        self._cards_layout.setSpacing(12)

        self._loading_card = Card()
        loading_title = QLabel("正在准备题目")
        loading_title.setObjectName("cardTitle")
        self._loading_card.body.addWidget(loading_title)
        self._loading_label = QLabel("正在从今天的文章里抽句并翻译成中文，通常需要 5~15 秒…")
        self._loading_label.setObjectName("muted")
        self._loading_label.setWordWrap(True)
        self._loading_card.body.addWidget(self._loading_label)
        self._cards_layout.addWidget(self._loading_card)
        self._cards_layout.addStretch(1)

        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self._status = QLabel("")
        root.addWidget(self._status)
        root.addWidget(divider())

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addStretch(1)
        self._busy_progress = busy_progress_bar(width=110)
        footer.addWidget(self._busy_progress)
        self._close_button = QPushButton("关闭")
        self._close_button.setObjectName("secondaryButton")
        self._close_button.setToolTip("AI 处理中可取消；已写的内容不会丢失")
        self._close_button.clicked.connect(self._on_close_clicked)
        footer.addWidget(self._close_button)

        self._submit_button = QPushButton("请稍候")
        self._submit_button.setObjectName("primaryButton")
        self._submit_button.setEnabled(False)
        self._submit_button.clicked.connect(self._submit)
        footer.addWidget(self._submit_button)
        root.addLayout(footer)

    # -------------------------------------------------------------- 生命周期
    def _bootstrap(self) -> None:
        if self._article.id is None:
            set_status(self._status, "文章还没有入库，无法开始回译练习。", "error")
            return
        try:
            cached = self._service.get_cached(self._article.id)
        except Exception as exc:  # noqa: BLE001 - 本地读库失败也要给出可读提示
            set_status(self._status, f"读取练习缓存失败：{exc}", "error")
            return
        # 与 service.load_or_create() 的“完整缓存”判定保持一致：
        # 只有题目和中文译文都在才直接展示，残缺行（手工改库/异常中断）重新出题。
        complete = cached and all(item.sentence_en and item.reference_cn for item in cached)
        if complete:
            self._show_attempts(cached)
            return
        self._start_prepare()

    def _start_prepare(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._loading_card.show()
        self._prepared = False
        self._submit_button.setEnabled(False)
        self._submit_button.setText("请稍候")
        set_status(self._status, "正在准备题目…", "loading")

        worker = TaskWorker(lambda: self._service.load_or_create(self._article))
        worker.succeeded.connect(self._on_prepared)
        worker.failed.connect(self._on_prepare_failed)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_prepared(self, result: object) -> None:
        if not isinstance(result, tuple) or not result:
            set_status(self._status, "出题完成，但返回了意外数据。", "error")
            self._submit_button.setText("重试")
            self._submit_button.setEnabled(True)
            return
        self._show_attempts(result)

    def _on_prepare_failed(self, message: str) -> None:
        self._loading_card.hide()
        set_status(self._status, message, "error")
        self._submit_button.setText("重试")
        self._submit_button.setEnabled(True)

    def _show_attempts(self, attempts: tuple[WritingAttempt, ...] | list[WritingAttempt]) -> None:
        self._attempts = tuple(attempts)
        self._prepared = True
        self._graded = all(item.graded for item in self._attempts)
        self._clear_cards()
        self._loading_card.hide()

        for index, attempt in enumerate(self._attempts):
            card = AttemptCard(attempt, index)
            self._cards.append(card)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)

        if self._graded:
            self.result_attempts = self._attempts
            self._submit_button.setText("已完成")
            self._submit_button.setEnabled(False)
            set_status(self._status, self._summary_text(), "success")
        else:
            self._submit_button.setText("提交批改")
            self._submit_button.setEnabled(True)
            set_status(self._status, "写完后点「提交批改」，AI 会逐句给最小修改。", "hint")

    def _clear_cards(self) -> None:
        for card in self._cards:
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

    # ------------------------------------------------------------------ 交互
    def _submit(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        if not self._prepared:
            self._start_prepare()
            return

        answers = [card.answer() for card in self._cards]
        for card, text in zip(self._cards, answers, strict=False):
            if not text:
                set_status(self._status, "请先用英文写出所有句子，再提交批改。", "error")
                card.focus_editor()
                return

        self._grade_failed = False
        self._set_busy(True)
        set_status(self._status, "AI 正在对照原句批改，通常需要 5~15 秒…", "loading")
        worker = TaskWorker(lambda: self._service.grade(self._article, answers))
        worker.succeeded.connect(self._on_graded)
        worker.failed.connect(self._on_grade_failed)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_graded(self, result: object) -> None:
        if not isinstance(result, tuple) or not result:
            set_status(self._status, "批改完成，但返回了意外数据。", "error")
            return
        attempts = tuple(result)
        if len(attempts) != len(self._cards):
            set_status(self._status, "批改结果数量与题目不一致。", "error")
            return

        self._attempts = attempts
        self._graded = True
        for card, attempt in zip(self._cards, attempts, strict=True):
            card.apply(attempt)
        self.result_attempts = attempts
        self._submit_button.setText("已完成")
        self._submit_button.setEnabled(False)
        set_status(self._status, self._summary_text(), "success")

    def _on_grade_failed(self, message: str) -> None:
        self._grade_failed = True
        set_status(self._status, message, "error")
        self._submit_button.setText("重新提交")
        self._submit_button.setEnabled(True)

    def _summary_text(self) -> str:
        total = sum(item.score for item in self._attempts)
        maximum = len(self._attempts) * SCORE_PER_SENTENCE
        correct = sum(1 for item in self._attempts if item.score >= SCORE_PER_SENTENCE)
        return f"本次 {total}/{maximum} 分，{correct} 句完全正确。修改与原因见每句下方。"

    def _set_busy(self, busy: bool) -> None:
        for card in self._cards:
            card.set_busy(busy)
        # 忙碌时关闭按钮变成取消请求，避免只能干等。
        self._close_button.setEnabled(True)
        self._close_button.setText("取消请求" if busy else "关闭")
        self._busy_progress.setVisible(busy)
        self._submit_button.setEnabled(not busy)
        if busy:
            self._submit_button.setText("正在批改…")

    def _on_close_clicked(self) -> None:
        worker = self._worker
        if worker is not None and worker.isRunning():
            if retire_worker(worker):
                self._worker = None
                # 出题阶段取消时要把“正在准备题目”卡片收掉，否则会一直挡在上面。
                if not self._prepared:
                    self._loading_card.hide()
                self._on_worker_finished()
                set_status(self._status, "已取消 AI 请求，可以重新提交或直接关闭。", "hint")
            return
        self.reject()

    def _on_worker_finished(self) -> None:
        self._worker = None
        self._set_busy(False)
        if not self._prepared:
            self._submit_button.setText("重试")
            self._submit_button.setEnabled(True)
        elif self._graded:
            self._submit_button.setText("已完成")
            self._submit_button.setEnabled(False)
        else:
            self._submit_button.setText("重新提交" if self._grade_failed else "提交批改")
            self._submit_button.setEnabled(True)

    # ------------------------------------------------------------ 关闭保护
    def reject(self) -> None:  # noqa: D401 - Qt 约定
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "AI 正在处理，请等待结果后再关闭。")
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 约定
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "AI 正在处理，请等待结果后再关闭。")
            event.ignore()
            return
        super().closeEvent(event)


__all__ = ["AttemptCard", "BackTranslationDialog"]
