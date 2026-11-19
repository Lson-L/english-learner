"""首次启动的摸底测评对话框。

流程（对应 HISTORY.md M1-3）：
1. 展示固定题库（默认 B1 套）：短文 + 5 道理解题 + 5 道词汇题 + 1 道翻译题；
2. 用户作答后，后台线程调用 AI 只做评分；
3. 成功 → 结果页展示 CEFR / 词汇量 / 优势 / 薄弱点，写 profile；
4. AI 失败 → 结果页切换成“手动选级”，保证流程不卡死。

线程约定：评分必须走 TaskWorker；关闭窗口时若任务仍在跑，拒绝关闭。
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models.entities import CEFR_LEVELS, Profile
from ..services.app_context import AppContext
from ..services.assessment_bank import (
    UNKNOWN_ANSWER,
    UNKNOWN_LABEL,
    AssessmentSet,
    Question,
    get_set,
)
from ..services.assessment_service import AnswerSheet
from .pages.common import Card, divider, set_status
from .workers import TaskWorker

MODE_TEST = "test"
MODE_SUCCESS = "success"
MODE_FALLBACK = "fallback"


def _passage_html(text: str) -> str:
    """把纯文本短文转成带行距的富文本。

    QSS 不支持 line-height，QLabel 只能靠富文本控制行距；Qt 的富文本引擎
    支持 `line-height`（实测百分比和倍数都生效）。段落按空行切分，段内换行
    合并成空格，避免源码折行被当成多个自然段。
    """
    paragraphs = [p.strip().replace("\n", " ") for p in text.split("\n\n") if p.strip()]
    body = "".join(
        f'<p style="line-height:180%; margin:0 0 12px 0;">{html.escape(p)}</p>' for p in paragraphs
    )
    return f"<div>{body}</div>"


class AssessmentDialog(QDialog):
    """模态测评。成功后把 profile 放在 `result_profile`。"""

    def __init__(
        self,
        context: AppContext,
        assessment_set: AssessmentSet | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._set = assessment_set or get_set()
        self._worker: TaskWorker | None = None
        self._mode = MODE_TEST
        self._comp_groups: list[QButtonGroup] = []
        self._vocab_groups: list[QButtonGroup] = []
        # 与题目一一对应的“哪里看不懂”输入框，提交时作为 AI 评分参考。
        self._comp_notes: list[QLineEdit] = []
        self._vocab_notes: list[QLineEdit] = []
        self.result_profile: Profile | None = None

        self.setWindowTitle("水平测评 · Daily English")
        # QDialog 在 Windows 标题栏默认只有关闭按钮；补上最大化/最小化按钮，
        # 方便用户把窗口铺满屏幕读短文。注意：setWindowFlags 会重建原生窗口，
        # 必须在 setModal 之前调用，否则模态状态会丢失。
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setModal(True)
        self.setMinimumSize(900, 720)
        self.resize(920, 760)
        self._build_ui()
        self._show_test()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        self._step_label = QLabel("摸底测评")
        self._step_label.setObjectName("stepBadge")
        root.addWidget(self._step_label, 0, Qt.AlignmentFlag.AlignLeft)

        self._title_label = QLabel("")
        self._title_label.setObjectName("pageTitle")
        root.addWidget(self._title_label)

        self._subtitle_label = QLabel("")
        self._subtitle_label.setObjectName("pageSubtitle")
        self._subtitle_label.setWordWrap(True)
        root.addWidget(self._subtitle_label)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_test_page())
        self._stack.addWidget(self._build_result_page())
        root.addWidget(self._stack, 1)

        root.addWidget(divider())

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._skip_button = QPushButton("跳过，手动选级")
        self._skip_button.setObjectName("secondaryButton")
        self._skip_button.clicked.connect(self._show_manual_page)
        footer.addWidget(self._skip_button)
        footer.addStretch(1)

        self._back_button = QPushButton("返回修改")
        self._back_button.setObjectName("secondaryButton")
        self._back_button.clicked.connect(self._show_test)
        footer.addWidget(self._back_button)

        self._primary_button = QPushButton("提交并评分")
        self._primary_button.setObjectName("primaryButton")
        self._primary_button.clicked.connect(self._primary_clicked)
        footer.addWidget(self._primary_button)
        root.addLayout(footer)

    def _build_test_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        passage_card = Card()
        passage_title = QLabel("阅读下面的短文，然后完成后面的题目")
        passage_title.setObjectName("cardTitle")
        passage_card.body.addWidget(passage_title)
        passage_label = QLabel(_passage_html(self._set.passage))
        passage_label.setObjectName("passageText")
        passage_label.setTextFormat(Qt.TextFormat.RichText)
        passage_label.setWordWrap(True)
        passage_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        passage_card.body.addWidget(passage_label)
        layout.addWidget(passage_card)

        layout.addWidget(
            self._build_question_card(
                "一、阅读理解",
                self._set.comprehension,
                self._comp_groups,
                self._comp_notes,
            )
        )
        layout.addWidget(
            self._build_question_card(
                "二、词汇",
                self._set.vocabulary,
                self._vocab_groups,
                self._vocab_notes,
            )
        )

        translation_card = Card()
        translation_title = QLabel("三、翻译")
        translation_title.setObjectName("cardTitle")
        translation_card.body.addWidget(translation_title)
        prompt = QLabel(self._set.translation_prompt)
        prompt.setObjectName("muted")
        prompt.setWordWrap(True)
        translation_card.body.addWidget(prompt)
        self._translation_edit = QPlainTextEdit()
        self._translation_edit.setPlaceholderText("在这里写下你的翻译…")
        self._translation_edit.setFixedHeight(110)
        translation_card.body.addWidget(self._translation_edit)
        layout.addWidget(translation_card)

        self._test_status = QLabel("")
        layout.addWidget(self._test_status)
        layout.addStretch(1)

        scroll.setWidget(content)
        return scroll

    def _build_question_card(
        self,
        title_text: str,
        questions: tuple[Question, ...],
        groups: list[QButtonGroup],
        notes: list[QLineEdit],
    ) -> Card:
        card = Card()
        title = QLabel(title_text)
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        for index, question in enumerate(questions, start=1):
            block = QWidget()
            block_layout = QVBoxLayout(block)
            block_layout.setContentsMargins(0, 4, 0, 4)
            block_layout.setSpacing(2)

            prompt = QLabel(f"{index}. {question.prompt}")
            prompt.setObjectName("questionPrompt")
            prompt.setWordWrap(True)
            block_layout.addWidget(prompt)

            group = QButtonGroup(self)
            for option_index, option in enumerate(question.options):
                letter = "ABCD"[option_index]
                button = QRadioButton(f"{letter}. {option}")
                group.addButton(button, option_index)
                block_layout.addWidget(button)
            # 主动放弃作答：用户不用靠瞎猜，AI 也能知道“这题是真不会”。
            unknown_letter = chr(ord("A") + len(question.options))
            unknown_button = QRadioButton(f"{unknown_letter}. {UNKNOWN_LABEL}")
            unknown_button.setToolTip("不会就选这个；下面的补充说明会一并交给 AI 参考")
            group.addButton(unknown_button, UNKNOWN_ANSWER)
            block_layout.addWidget(unknown_button)
            groups.append(group)

            note = QLineEdit()
            note.setPlaceholderText(
                "选填：这题哪里看不懂？可以写给 AI 参考，例如“题目里的单词不认识”"
            )
            note.setClearButtonEnabled(True)
            block_layout.addWidget(note)
            notes.append(note)

            card.body.addWidget(block)
        return card

    def _build_result_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._success_card = Card()
        result_title = QLabel("你的当前水平")
        result_title.setObjectName("cardTitle")
        self._success_card.body.addWidget(result_title)

        self._result_level = QLabel("")
        self._result_level.setObjectName("bigNumber")
        self._success_card.body.addWidget(self._result_level)

        self._result_vocab = QLabel("")
        self._result_vocab.setObjectName("muted")
        self._success_card.body.addWidget(self._result_vocab)

        self._result_strengths = QLabel("")
        self._result_strengths.setObjectName("muted")
        self._result_strengths.setWordWrap(True)
        self._success_card.body.addWidget(self._result_strengths)

        self._result_weaknesses = QLabel("")
        self._result_weaknesses.setObjectName("muted")
        self._result_weaknesses.setWordWrap(True)
        self._success_card.body.addWidget(self._result_weaknesses)

        self._result_comment = QLabel("")
        self._result_comment.setObjectName("muted")
        self._result_comment.setWordWrap(True)
        self._success_card.body.addWidget(self._result_comment)
        layout.addWidget(self._success_card)

        self._fallback_card = Card()
        fallback_title = QLabel("选择你的等级")
        fallback_title.setObjectName("cardTitle")
        self._fallback_card.body.addWidget(fallback_title)

        self._fallback_message = QLabel("")
        self._fallback_message.setObjectName("muted")
        self._fallback_message.setWordWrap(True)
        self._fallback_card.body.addWidget(self._fallback_message)

        combo_row = QHBoxLayout()
        combo_row.setSpacing(10)
        self._manual_combo = QComboBox()
        for level in CEFR_LEVELS:
            self._manual_combo.addItem(level)
        self._manual_combo.setCurrentText("B1")
        combo_row.addWidget(self._manual_combo)

        manual_button = QPushButton("使用该等级开始")
        manual_button.setObjectName("primaryButton")
        manual_button.clicked.connect(self._manual_accept)
        combo_row.addWidget(manual_button)
        combo_row.addStretch(1)
        self._fallback_card.body.addLayout(combo_row)
        layout.addWidget(self._fallback_card)

        self._result_status = QLabel("")
        self._result_status.setWordWrap(True)
        layout.addWidget(self._result_status)
        layout.addStretch(1)
        return page

    # ---------------------------------------------------------------- 状态机
    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._stack.setCurrentIndex(0 if mode == MODE_TEST else 1)
        is_test = mode == MODE_TEST
        self._skip_button.setVisible(is_test)
        self._back_button.setVisible(not is_test)
        self._success_card.setVisible(mode == MODE_SUCCESS)
        self._fallback_card.setVisible(mode == MODE_FALLBACK)

        if mode == MODE_TEST:
            self._step_label.setText("摸底测评")
            self._title_label.setText("先做一次 5 分钟的摸底测评")
            self._subtitle_label.setText(
                f"这套题按 {self._set.level_hint} 难度设计；"
                "选择题本地计分，翻译题交给 AI 评分。"
                "每道选择题都要作答，不会就选「不知道」；"
                "选完可以在题目下写一句哪里看不懂，AI 评分时会参考。"
            )
            self._primary_button.setText("提交并评分")
            self._primary_button.setEnabled(True)
        elif mode == MODE_SUCCESS:
            self._step_label.setText("测评结果")
            self._title_label.setText("测评完成，结果已保存")
            self._subtitle_label.setText(
                "之后每天的文章会按这个等级生成；想调整可以随时在首页重新测评。"
            )
            self._primary_button.setText("完成")
            self._primary_button.setEnabled(True)
        else:
            self._step_label.setText("手动选级")
            self._title_label.setText("自动评分没有成功")
            self._subtitle_label.setText(
                "可以先重试；也可以直接选一个等级开始，之后在首页还能重新测评。"
            )
            self._primary_button.setText("重试评分")
            self._primary_button.setEnabled(True)

    def _show_test(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._set_mode(MODE_TEST)

    def _show_manual_page(self) -> None:
        self._set_mode(MODE_FALLBACK)
        self._fallback_message.setText(
            "已跳过自动评分。请选择一个最接近你当前水平的等级（拿不准就选 B1）。"
        )

    def _show_fallback(self, message: str) -> None:
        self._set_mode(MODE_FALLBACK)
        self._fallback_message.setText(
            f"AI 评分失败：{message}\n你可以重试，或直接选择一个等级继续。"
        )

    def _show_success(self, profile: Profile) -> None:
        self._set_mode(MODE_SUCCESS)
        self._result_level.setText(profile.cefr_level)
        if profile.vocab_estimate:
            self._result_vocab.setText(f"估算词汇量：约 {profile.vocab_estimate:,} 词")
        else:
            self._result_vocab.setText("估算词汇量：未提供")
        self._result_strengths.setText(
            "优势：" + "、".join(profile.strengths) if profile.strengths else ""
        )
        self._result_weaknesses.setText(
            "薄弱：" + "、".join(profile.weaknesses) if profile.weaknesses else ""
        )
        self._result_comment.setText(profile.comment)
        set_status(self._result_status, "结果已保存到本机。", "success")

    # ------------------------------------------------------------------ 交互
    def _primary_clicked(self) -> None:
        if self._mode == MODE_SUCCESS:
            self.accept()
        else:
            self._submit()

    def _collect_sheet(self) -> AnswerSheet:
        return AnswerSheet(
            comprehension=tuple(group.checkedId() for group in self._comp_groups),
            vocabulary=tuple(group.checkedId() for group in self._vocab_groups),
            translation=self._translation_edit.toPlainText(),
            comprehension_notes=tuple(note.text().strip() for note in self._comp_notes),
            vocabulary_notes=tuple(note.text().strip() for note in self._vocab_notes),
        )

    def _unanswered_questions(self) -> int:
        """统计完全没选的选择题数量。

        注意 `checkedId() == -1` 才是未作答；`UNKNOWN_ANSWER`（-2）是用户
        主动选的“不知道”，属于有效作答。
        """
        groups = (*self._comp_groups, *self._vocab_groups)
        return sum(1 for group in groups if group.checkedId() == -1)

    def _submit(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        unanswered = self._unanswered_questions()
        if unanswered:
            set_status(
                self._test_status,
                f"还有 {unanswered} 道选择题没有作答；不会就选「{UNKNOWN_LABEL}」。",
                "error",
            )
            return

        sheet = self._collect_sheet()
        service = self._context.assessment_service()
        assessment_set = self._set

        self._set_busy(True)
        set_status(self._test_status, "正在评分，请稍候…", "loading")
        set_status(self._result_status, "正在评分，请稍候…", "loading")

        worker = TaskWorker(lambda: service.submit(assessment_set, sheet))
        worker.succeeded.connect(self._on_submit_succeeded)
        worker.failed.connect(self._on_submit_failed)
        worker.finished.connect(self._on_submit_finished)
        self._worker = worker
        worker.start()

    def _on_submit_succeeded(self, result: object) -> None:
        if not isinstance(result, Profile):
            set_status(self._result_status, "评分完成，但返回了意外数据。", "error")
            return
        self.result_profile = result
        self._show_success(result)

    def _on_submit_failed(self, message: str) -> None:
        self._show_fallback(message)

    def _on_submit_finished(self) -> None:
        self._set_busy(False)
        self._worker = None

    def _manual_accept(self) -> None:
        cefr = self._manual_combo.currentText().upper()
        try:
            profile = self._context.assessment_service().save_manual(cefr)
        except ValueError as exc:
            set_status(self._result_status, f"保存失败：{exc}", "error")
            return
        self.result_profile = profile
        self.accept()

    def _set_busy(self, busy: bool) -> None:
        self._stack.setEnabled(not busy)
        self._skip_button.setEnabled(not busy)
        self._back_button.setEnabled(not busy)
        self._primary_button.setEnabled(not busy)

    # ------------------------------------------------------------ 关闭保护
    def reject(self) -> None:  # noqa: D401 - Qt 约定
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "评分正在进行，请等待结果后再关闭。")
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 约定
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "评分正在进行，请等待结果后再关闭。")
            event.ignore()
            return
        super().closeEvent(event)


__all__ = ["AssessmentDialog"]
