"""阅读检测弹窗：自适应两阶段出题 + 本地后验 + AI 讲评。

交互流程（Phase 3）：
1. 打开时先查缓存：同一篇文章每一阶段只出一次题；
2. 没有缓存 → 第一轮 3 道当前等级题；
3. 用户答完第一轮 → 按正确率生成 2 道边界题 + 2 道英译中题；
4. 用户补完第二轮（第一轮答案锁定展示）→ 盲评 + 讲评 + 本地后验；
5. 结果页展示得分、置信度、逐题解析与等级变化；
6. 已完成的检测再次打开时直接展示结果，不重复判分。

线程约定：出题、第二轮、判分都是网络请求，必须走 TaskWorker；
请求进行中拒绝关闭窗口，避免 QThread 被销毁。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
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

from ..models.entities import CEFR_LEVELS, Article, QuizQuestion, ReadingQuiz
from ..services.app_context import AppContext
from ..services.level_progress import summarize_quiz
from ..services.reading_quiz_service import (
    UNKNOWN_ANSWER,
    UNKNOWN_LABEL,
    UPGRADE_EVIDENCE_REQUIRED,
    QuizOutcome,
    ReadingQuizService,
)
from .pages.common import Card, divider, set_status
from .widgets import busy_progress_bar
from .workers import TaskWorker, retire_worker

MODE_LOADING = "loading"
MODE_QUIZ = "quiz"
MODE_RESULT = "result"

_MODE_INDEX = {MODE_LOADING: 0, MODE_QUIZ: 1, MODE_RESULT: 2}

_ERROR_LABELS = {
    "vocabulary": "词汇不理解",
    "inference": "推断偏差",
    "detail": "细节没抓住",
    "main_idea": "主旨把握不准",
    "grammar": "语法 / 句子结构",
    "translation": "英译中理解偏差",
    "short_answer": "表达不够准确",
    "rewrite": "改写不准确",
    "careless": "粗心",
    "unknown": "原因不明",
}
_SELF_REPORT_TEXT = {"easy": "轻松", "ok": "刚好", "hard": "吃力"}


def _production_label(question: QuizQuestion) -> str:
    """产出题的中文标签；兼容旧缓存里的英文短答 / 改写。"""
    if question.is_translation:
        return "英译中"
    if (question.skill or "").strip().lower() in {
        "rewrite",
        "sentence_rewrite",
        "translation",
    }:
        return "句子改写 / 翻译"
    return "短答"


class ReadingQuizDialog(QDialog):
    """模态阅读检测。完成后把结果放在 `result_quiz`。"""

    def __init__(
        self,
        context: AppContext,
        article: Article,
        parent: QWidget | None = None,
        service: ReadingQuizService | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._article = article
        self._service = service or context.reading_quiz_service()
        self._worker: TaskWorker | None = None
        # 取消后据此把界面恢复到对应的可重试状态。
        self._busy_phase = ""
        self._mode = MODE_LOADING
        self._primary_enabled = False
        self._quiz: ReadingQuiz | None = None
        self._outcome: QuizOutcome | None = None
        self._groups: list[QButtonGroup] = []
        # 与 _groups 一一对应：选「不知道」时才显示 / 提交的补充说明输入框。
        self._choice_notes: list[QLineEdit] = []
        self._production_edits: list[QPlainTextEdit] = []
        self._stage_one_answers: tuple[int, ...] = ()
        self._practice_hint = False

        self.result_quiz: ReadingQuiz | None = None

        self.setWindowTitle("阅读检测 · Daily English")
        # 让窗口带最大化按钮；setWindowFlags 会重建原生窗口，必须在 setModal 之前。
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setModal(True)
        self.setMinimumSize(860, 680)
        self.resize(920, 760)
        self._build_ui()
        self._bootstrap()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        badge = QLabel("阅读检测")
        badge.setObjectName("stepBadge")
        top_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)
        top_row.addStretch(1)

        self._fullscreen_button = QPushButton("全屏")
        self._fullscreen_button.setObjectName("linkButton")
        self._fullscreen_button.setToolTip("全屏显示题目（F11；再按 Esc 退出全屏）")
        self._fullscreen_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fullscreen_button.clicked.connect(self._toggle_fullscreen)
        top_row.addWidget(self._fullscreen_button)
        root.addLayout(top_row)

        self._subtitle = QLabel(
            "检测分两轮：先答 3 道当前等级题，再根据表现出 2 道边界题和 2 道英译中题；"
            "最后按本地后验与 AI 讲评调整等级。"
        )
        self._subtitle.setObjectName("pageSubtitle")
        self._subtitle.setWordWrap(True)
        root.addWidget(self._subtitle)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_loading_page())
        self._stack.addWidget(self._build_quiz_page())
        self._stack.addWidget(self._build_result_page())
        root.addWidget(self._stack, 1)

        root.addWidget(divider())

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._busy_progress = busy_progress_bar(width=110)
        footer.addWidget(self._busy_progress)
        self._cancel_button = QPushButton("取消")
        self._cancel_button.setObjectName("secondaryButton")
        self._cancel_button.setToolTip("AI 处理中可取消；已答的题和已生成的内容不会丢失")
        self._cancel_button.clicked.connect(self._on_cancel_clicked)
        footer.addWidget(self._cancel_button)
        footer.addStretch(1)

        self._primary_button = QPushButton("请稍候")
        self._primary_button.setObjectName("primaryButton")
        self._primary_button.setEnabled(False)
        self._primary_button.clicked.connect(self._primary_clicked)
        footer.addWidget(self._primary_button)
        root.addLayout(footer)

        self._fullscreen_shortcut = QShortcut(QKeySequence("F11"), self)
        self._fullscreen_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._fullscreen_shortcut.activated.connect(self._toggle_fullscreen)

    def _build_loading_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        card = Card()
        title = QLabel("正在准备题目")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)
        self._loading_label = QLabel("AI 正在阅读今天的文章并出题，通常需要 10~30 秒…")
        self._loading_label.setObjectName("muted")
        self._loading_label.setWordWrap(True)
        card.body.addWidget(self._loading_label)
        layout.addWidget(card)

        self._loading_status = QLabel("")
        layout.addWidget(self._loading_status)
        layout.addStretch(1)
        return page

    def _build_quiz_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        self._quiz_layout = QVBoxLayout(body)
        self._quiz_layout.setContentsMargins(0, 0, 8, 0)
        self._quiz_layout.setSpacing(12)
        scroll.setWidget(body)
        return scroll

    def _build_result_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        self._result_layout = QVBoxLayout(body)
        self._result_layout.setContentsMargins(0, 0, 8, 0)
        self._result_layout.setSpacing(12)
        scroll.setWidget(body)
        return scroll

    # ---------------------------------------------------------------- 启动
    def _bootstrap(self) -> None:
        article_id = self._article.id
        cached = self._service.get_cached(article_id) if article_id is not None else None
        if cached is not None:
            if cached.completed:
                self._show_result(cached)
            else:
                # stage 1 也可能已经保存过答案（第二轮出题失败 / 中途关窗），
                # 重开时把已选中的选项回显出来。
                self._stage_one_answers = tuple(cached.answers[: cached.choice_total])
                self._show_quiz(cached)
            return
        self._start_generation()

    def _detect_practice_hint(self, quiz: ReadingQuiz) -> bool:
        """在答题前预判今天是否已有正式检测；桩服务没有该方法时按 false 处理。"""
        checker = getattr(self._service, "is_practice_day", None)
        if not callable(checker):
            return False
        try:
            return bool(checker(self._article, quiz.id))
        except (AttributeError, TypeError):
            return False

    # ---------------------------------------------------------------- 出题
    def _start_generation(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        article = self._article
        level = self._context.level()
        service = self._service

        self._busy_phase = "loading"
        self._set_mode(MODE_LOADING, "请稍候", primary_enabled=False)
        set_status(self._loading_status, "正在根据文章出题，请稍候…", "loading")
        self._set_busy(True)

        worker = TaskWorker(lambda: service.get_or_create(article, level))
        worker.succeeded.connect(self._on_quiz_ready)
        worker.failed.connect(self._on_quiz_failed)
        worker.cancelled.connect(self._on_worker_finished)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_quiz_ready(self, result: object) -> None:
        if not isinstance(result, ReadingQuiz):
            set_status(self._loading_status, "出题完成，但返回了意外数据，可以重试。", "error")
            self._set_mode(MODE_LOADING, "重试出题", primary_enabled=True)
            return
        if result.completed:
            self._show_result(result)
        else:
            self._show_quiz(result)

    def _on_quiz_failed(self, message: str) -> None:
        set_status(self._loading_status, message, "error")
        self._set_mode(MODE_LOADING, "重试出题", primary_enabled=True)

    # ---------------------------------------------------------------- 答题
    def _show_quiz(self, quiz: ReadingQuiz) -> None:
        self._quiz = quiz
        self._clear_layout(self._quiz_layout)
        self._groups = []
        self._choice_notes = []
        self._production_edits = []

        stage = 1 if int(quiz.stage or 1) < 2 else 2
        locked = quiz.stage >= 2
        if locked and not self._stage_one_answers:
            self._stage_one_answers = tuple(quiz.answers[: quiz.choice_total])
        self._practice_hint = self._detect_practice_hint(quiz)

        productions = [question for question in quiz.questions if question.is_short]
        all_translation = bool(productions) and all(
            question.is_translation for question in productions
        )

        intro = Card()
        if stage == 1:
            title = QLabel(f"第 1 轮 · {quiz.choice_total} 道当前等级题")
            hint = (
                "先做 3 道当前等级的选择题。提交后系统会按正确率决定第二轮"
                "往上一档还是往下一档探测，并补 2 道英译中题。"
                f"不会的题可以选「{UNKNOWN_LABEL}」，并写明哪里没看懂。"
            )
        else:
            production_label = "英译中" if all_translation else "产出题"
            title = QLabel(
                f"第 2 轮 · {quiz.choice_total} 道选择题 + {quiz.short_total} 道{production_label}"
            )
            if all_translation:
                hint = (
                    "第一轮的答案已锁定；请完成边界选择题，并把英文句子翻译成中文。"
                    "不会的选择题可以选「不知道」并写明原因；"
                    "AI 会逐题讲评，等级由本地后验综合决定。"
                )
            else:
                hint = (
                    "第一轮的答案已锁定；请完成边界选择题和产出题。"
                    "不会的选择题可以选「不知道」并写明原因；"
                    "AI 会逐题讲评，等级由本地后验综合决定。"
                )
        title.setObjectName("cardTitle")
        intro.body.addWidget(title)
        hint_label = QLabel(hint)
        hint_label.setObjectName("muted")
        hint_label.setWordWrap(True)
        intro.body.addWidget(hint_label)
        if self._practice_hint:
            practice = QLabel("今天已经完成过一次正式检测；本次为练习模式，完成后不会调整等级。")
            practice.setObjectName("muted")
            practice.setWordWrap(True)
            intro.body.addWidget(practice)

        self._self_report_combo = None
        if stage == 2:
            report_row = QHBoxLayout()
            report_row.setSpacing(8)
            report_label = QLabel("这篇读起来：")
            report_label.setObjectName("fieldLabel")
            report_row.addWidget(report_label)
            self._self_report_combo = QComboBox()
            self._self_report_combo.addItem("轻松", "easy")
            self._self_report_combo.addItem("刚好", "ok")
            self._self_report_combo.addItem("吃力", "hard")
            self._self_report_combo.setCurrentIndex(1)
            self._self_report_combo.setToolTip("作为弱证据参与后验，不会单独决定升降级")
            report_row.addWidget(self._self_report_combo)
            report_row.addStretch(1)
            intro.body.addLayout(report_row)
        self._quiz_layout.addWidget(intro)

        choice_position = 0
        chosen_stage_one = self._stage_one_answers
        for index, question in enumerate(quiz.questions, start=1):
            if question.is_short:
                self._quiz_layout.addWidget(self._build_production_input_card(index, question))
                continue

            position = choice_position
            choice_position += 1
            is_locked = stage == 2 and position < len(chosen_stage_one)
            saved = chosen_stage_one[position] if position < len(chosen_stage_one) else -1
            saved_note = quiz.choice_notes[position] if position < len(quiz.choice_notes) else ""
            self._quiz_layout.addWidget(
                self._build_choice_card(
                    index,
                    question,
                    quiz.level,
                    position=position,
                    initial_value=saved,
                    initial_note=saved_note,
                    locked=is_locked,
                )
            )

        self._quiz_status = QLabel("")
        self._quiz_layout.addWidget(self._quiz_status)
        self._quiz_layout.addStretch(1)

        if stage == 1:
            self._subtitle.setText(
                f"第 1 轮共 {quiz.choice_total} 道题；提交后会按正确率生成第二轮题目。"
            )
            self._set_mode(MODE_QUIZ, "提交第一轮", primary_enabled=True)
        else:
            production_label = "英译中" if all_translation else "产出题"
            self._subtitle.setText(
                f"共 {quiz.choice_total} 道选择题 + {quiz.short_total} 道{production_label}；"
                "提交后 AI 会讲评，等级由本地后验与盲评综合决定。"
            )
            self._set_mode(MODE_QUIZ, "提交答案", primary_enabled=True)
        self._cancel_button.setVisible(True)

    def _build_choice_card(
        self,
        index: int,
        question: QuizQuestion,
        default_level: str,
        *,
        position: int,
        initial_value: int = -1,
        initial_note: str = "",
        locked: bool = False,
    ) -> QWidget:
        card = Card()
        prompt = QLabel(f"{index}. {question.prompt}")
        prompt.setObjectName("questionPrompt")
        prompt.setWordWrap(True)
        card.body.addWidget(prompt)

        meta_text = (
            f"题型：{question.skill or 'unknown'} · 难度：{question.difficulty or default_level}"
        )
        if locked:
            meta_text += " · 第一轮已作答"
        meta = QLabel(meta_text)
        meta.setObjectName("muted")
        meta.setWordWrap(True)
        card.body.addWidget(meta)

        group = QButtonGroup(self)
        for option_index, option in enumerate(question.options):
            letter = chr(ord("A") + option_index)
            radio = QRadioButton(f"{letter}. {option}")
            group.addButton(radio, option_index)
            if option_index == initial_value:
                radio.setChecked(True)
            if locked:
                radio.setEnabled(False)
            card.body.addWidget(radio)

        # 与测评一致：选项之后的字母作为「不知道」，-1 仍保留给未作答。
        unknown_letter = chr(ord("A") + len(question.options))
        unknown = QRadioButton(f"{unknown_letter}. {UNKNOWN_LABEL}")
        unknown.setToolTip("不会就选这个；下面的补充说明会交给 AI 参考")
        group.addButton(unknown, UNKNOWN_ANSWER)
        if initial_value == UNKNOWN_ANSWER:
            unknown.setChecked(True)
        if locked:
            unknown.setEnabled(False)
        card.body.addWidget(unknown)

        note = QLineEdit()
        note.setPlaceholderText("选填：这题哪里没看懂？会交给 AI 参考，例如“单词不认识 / 句子太长”")
        note.setText(initial_note)
        note.setClearButtonEnabled(True)
        note.setVisible(initial_value == UNKNOWN_ANSWER)
        note.setEnabled(not locked)
        # 只在选中「不知道」时显示，避免每道题都多一行输入框。
        group.idToggled.connect(
            lambda option_id, checked, edit=note: edit.setVisible(
                bool(checked) and option_id == UNKNOWN_ANSWER
            )
        )
        card.body.addWidget(note)

        self._groups.append(group)
        self._choice_notes.append(note)
        return card

    def _build_production_input_card(self, index: int, question: QuizQuestion) -> QWidget:
        card = Card()
        label = _production_label(question)
        prompt = QLabel(f"{index}. [{label}] {question.prompt}")
        prompt.setObjectName("questionPrompt")
        prompt.setWordWrap(True)
        card.body.addWidget(prompt)

        difficulty = question.difficulty or "B1"
        if question.is_translation:
            meta_text = (
                f"题型：英译中 · 难度：{difficulty} · "
                "用中文翻译这句话，AI 按「理解准确 / 中文通顺」给 0/1 分。"
            )
            placeholder = "用中文写出这句话的意思。例如：这句话的意思是……"
        else:
            meta_text = (
                f"题型：{question.skill or 'writing'} · 难度：{difficulty} · "
                "用英文完成，AI 按「内容 / 语法 / 词汇」给 0/1 分。"
            )
            is_rewrite = (question.skill or "").lower() in {
                "rewrite",
                "sentence_rewrite",
                "translation",
            }
            placeholder = (
                "Rewrite the sentence in your own words."
                if is_rewrite
                else "Write 1-2 sentences in English. 例如：The passage mainly argues that ..."
            )
        meta = QLabel(meta_text)
        meta.setObjectName("muted")
        meta.setWordWrap(True)
        card.body.addWidget(meta)

        edit = QPlainTextEdit()
        edit.setPlaceholderText(placeholder)
        edit.setFixedHeight(88)
        card.body.addWidget(edit)
        self._production_edits.append(edit)
        return card

    def _collect_answers(self) -> tuple[int, ...]:
        return tuple(group.checkedId() for group in self._groups)

    def _collect_choice_notes(self) -> tuple[str, ...]:
        """只收集「不知道」题的补充说明；其它题的输入框内容不提交。"""
        return tuple(
            edit.text().strip() if group.checkedId() == UNKNOWN_ANSWER else ""
            for group, edit in zip(self._groups, self._choice_notes, strict=False)
        )

    def _collect_productions(self) -> tuple[str, ...]:
        return tuple(edit.toPlainText().strip() for edit in self._production_edits)

    def _advance_stage(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        quiz = self._quiz
        if quiz is None:
            return
        answers = self._collect_answers()
        if any(index == -1 for index in answers):
            set_status(
                self._quiz_status,
                f"还有选择题没有作答；不会的题可以选「{UNKNOWN_LABEL}」。",
                "error",
            )
            return

        notes = self._collect_choice_notes()
        self._stage_one_answers = answers
        article = self._article
        service = self._service
        self._busy_phase = "stage"
        set_status(self._quiz_status, "正在根据第一轮表现生成边界题，请稍候…", "loading")
        self._set_busy(True)
        worker = TaskWorker(
            lambda: service.advance_stage(article, quiz, answers, choice_notes=notes)
        )
        worker.succeeded.connect(self._on_stage_two_ready)
        worker.failed.connect(self._on_stage_failed)
        worker.cancelled.connect(self._on_worker_finished)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_stage_two_ready(self, result: object) -> None:
        if not isinstance(result, ReadingQuiz):
            set_status(self._quiz_status, "第二轮出题返回了意外数据，可以重试。", "error")
            self._set_mode(MODE_QUIZ, "重新生成第二轮", primary_enabled=True)
            return
        if result.completed:
            self._show_result(result)
            return
        self._show_quiz(result)

    def _on_stage_failed(self, message: str) -> None:
        set_status(self._quiz_status, message, "error")
        self._set_mode(MODE_QUIZ, "重新生成第二轮", primary_enabled=True)

    def _submit_answers(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        quiz = self._quiz
        if quiz is None:
            return
        if quiz.stage < 2:
            self._advance_stage()
            return

        answers = self._collect_answers()
        if any(index == -1 for index in answers):
            set_status(
                self._quiz_status,
                f"还有选择题没有作答；不会的题可以选「{UNKNOWN_LABEL}」。",
                "error",
            )
            return
        notes = self._collect_choice_notes()

        productions = self._collect_productions()
        if quiz.short_total and any(not text for text in productions):
            if all(question.is_translation for question in quiz.production_questions):
                message = "请完成每道英译中题；写不出也可以写出你理解到的关键词。"
            else:
                message = "请完成每道产出题；写不出也可以写关键词。"
            set_status(self._quiz_status, message, "error")
            if self._production_edits:
                for edit, text in zip(self._production_edits, productions, strict=False):
                    if not text:
                        edit.setFocus()
                        break
            return

        profile = self._context.profile()
        if profile is None:
            set_status(self._quiz_status, "请先在「今日」页完成水平测评，再提交答案。", "error")
            return

        article = self._article
        self_report = (
            self._self_report_combo.currentData() if self._self_report_combo is not None else "ok"
        ) or "ok"
        service = self._service

        self._busy_phase = "grading"
        set_status(self._quiz_status, "AI 正在批改并生成讲评，请稍候…", "loading")
        self._set_busy(True)
        worker = TaskWorker(
            lambda: service.submit(
                article,
                quiz,
                answers,
                profile,
                self_report=self_report,
                production_texts=productions,
                choice_notes=notes,
            )
        )
        worker.succeeded.connect(self._on_graded)
        worker.failed.connect(self._on_grading_failed)
        worker.cancelled.connect(self._on_worker_finished)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_graded(self, result: object) -> None:
        if not isinstance(result, QuizOutcome):
            set_status(self._quiz_status, "批改完成，但返回了意外数据，可以重试。", "error")
            self._set_mode(MODE_QUIZ, "重新提交", primary_enabled=True)
            return
        self._outcome = result
        self.result_quiz = result.quiz
        self._show_result(result.quiz)

    def _on_grading_failed(self, message: str) -> None:
        set_status(self._quiz_status, message, "error")
        self._set_mode(MODE_QUIZ, "重新提交", primary_enabled=True)

    # ---------------------------------------------------------------- 结果
    def _show_result(self, quiz: ReadingQuiz) -> None:
        self.result_quiz = quiz
        self._clear_layout(self._result_layout)

        self._result_layout.addWidget(self._build_score_card(quiz))
        self._result_layout.addWidget(self._build_level_card(quiz))

        choice_position = 0
        production_position = 0
        productions = quiz.production_answers
        for index, question in enumerate(quiz.questions, start=1):
            if question.is_short:
                production = (
                    productions[production_position]
                    if production_position < len(productions)
                    else None
                )
                production_position += 1
                self._result_layout.addWidget(
                    self._build_production_result_card(index, question, quiz, production)
                )
                continue
            chosen = quiz.answers[choice_position] if choice_position < len(quiz.answers) else -1
            position = choice_position
            choice_position += 1
            self._result_layout.addWidget(
                self._build_choice_result_card(index, question, quiz, chosen, position)
            )

        self._result_layout.addStretch(1)
        self._subtitle.setText(self._result_subtitle(quiz))
        self._cancel_button.setVisible(False)
        self._set_mode(MODE_RESULT, "完成", primary_enabled=True)

    def _build_score_card(self, quiz: ReadingQuiz) -> Card:
        card = Card()
        title = QLabel("本次得分")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        score_label = QLabel(f"{quiz.score} / {quiz.total}")
        score_label.setObjectName("bigNumber")
        card.body.addWidget(score_label)

        parts: list[str] = []
        if quiz.choice_total:
            parts.append(f"选择题 {quiz.choice_correct}/{quiz.choice_total}")
        if quiz.short_total:
            parts.append(f"产出题 {quiz.production_score}/{quiz.short_total}")
            if quiz.unscored_production_count:
                parts.append(f"{quiz.unscored_production_count} 道 AI 未评分（不计入等级）")
        if quiz.unscored_production_count:
            # 今日页的“答对 X/Y”会排除未评分题，这里标明口径，避免两个数字打架。
            parts.append(f"正确率 {quiz.percent}%（含未评分题）")
        else:
            parts.append(f"正确率 {quiz.percent}%")
        parts.append(f"文章等级 {quiz.level}")
        meta = QLabel(" · ".join(parts))
        meta.setObjectName("muted")
        meta.setWordWrap(True)
        card.body.addWidget(meta)

        # M6.4：把“这张卷子考了什么难度”说清楚，避免只看正确率。
        summary = summarize_quiz(quiz, quiz.previous_level or quiz.level)
        if summary.difficulty_counts:
            distribution = "本卷难度：" + " · ".join(
                f"{level}×{count}" for level, count in summary.difficulty_counts
            )
            distribution_label = QLabel(distribution)
            distribution_label.setObjectName("muted")
            distribution_label.setWordWrap(True)
            card.body.addWidget(distribution_label)
        if summary.high_correct:
            high_label = QLabel(
                f"本次答对 {summary.high_correct} 道高两档题（比当前等级难两档及以上）。"
            )
            high_label.setObjectName("muted")
            high_label.setWordWrap(True)
            card.body.addWidget(high_label)

        if quiz.self_report:
            report = QLabel(
                "本次自评：" + _SELF_REPORT_TEXT.get(quiz.self_report, quiz.self_report)
            )
            report.setObjectName("muted")
            report.setWordWrap(True)
            card.body.addWidget(report)

        if quiz.practice_mode:
            practice = QLabel("今天已经完成过一次正式检测；本次为练习模式，不调整等级。")
            practice.setObjectName("muted")
            practice.setWordWrap(True)
            card.body.addWidget(practice)

        evidence = QLabel(
            f"证据强度：{quiz.evidence_ratio:+.2f}（正数说明整体表现强于当前等级，负数偏弱）"
        )
        evidence.setObjectName("muted")
        evidence.setWordWrap(True)
        card.body.addWidget(evidence)
        return card

    def _build_level_card(self, quiz: ReadingQuiz) -> Card:
        card = Card()
        title = QLabel("等级调整")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        confidence = float(quiz.level_confidence or 0.0)
        if quiz.previous_level and quiz.result_level:
            if quiz.practice_mode:
                change = f"保持 {quiz.result_level} · 练习模式"
            elif quiz.level_changed:
                change = f"{quiz.previous_level} → {quiz.result_level}"
            elif quiz.waiting_for_upgrade:
                change = f"保持 {quiz.result_level} · 建议升到 {quiz.pending_level}"
            else:
                change = f"保持 {quiz.result_level}"
            change_label = QLabel(change)
            change_label.setObjectName("bigNumber")
            card.body.addWidget(change_label)

            if confidence:
                confidence_label = QLabel(f"等级置信度 {confidence:.2f}")
                confidence_label.setObjectName("muted")
                confidence_label.setWordWrap(True)
                card.body.addWidget(confidence_label)

            if quiz.waiting_for_upgrade:
                waiting = QLabel(
                    f"已积累 {quiz.pending_count}/{UPGRADE_EVIDENCE_REQUIRED} 次向上证据；"
                    "再完成一次同等级检测即可升级。"
                )
                waiting.setObjectName("muted")
                waiting.setWordWrap(True)
                card.body.addWidget(waiting)
            elif (
                quiz.recommended_level
                and quiz.recommended_level != quiz.result_level
                and not quiz.practice_mode
            ):
                suggested = QLabel(
                    f"后验建议：{quiz.recommended_level}；本次保持 {quiz.result_level}。"
                )
                suggested.setObjectName("muted")
                suggested.setWordWrap(True)
                card.body.addWidget(suggested)

        posterior_text = self._posterior_text(quiz)
        if posterior_text:
            posterior = QLabel(posterior_text)
            posterior.setObjectName("muted")
            posterior.setWordWrap(True)
            card.body.addWidget(posterior)

        if quiz.feedback:
            feedback = QLabel(quiz.feedback)
            feedback.setObjectName("muted")
            feedback.setWordWrap(True)
            card.body.addWidget(feedback)
        return card

    @staticmethod
    def _posterior_text(quiz: ReadingQuiz) -> str:
        mapping = quiz.posterior_map
        if not mapping:
            return ""
        parts = [f"{level} {mapping.get(level, 0.0) * 100:.0f}%" for level in CEFR_LEVELS]
        return "本地后验：" + " · ".join(parts)

    def _build_choice_result_card(
        self,
        index: int,
        question: QuizQuestion,
        quiz: ReadingQuiz,
        chosen: int,
        position: int,
    ) -> Card:
        correct = chosen == question.answer_index
        if chosen == UNKNOWN_ANSWER:
            heading_text = "主动表示不会"
        else:
            heading_text = "回答正确" if correct else "回答错误"
        card = Card()
        heading = QLabel(f"第 {index} 题 · {heading_text}")
        heading.setObjectName("cardTitle")
        card.body.addWidget(heading)

        meta = QLabel(
            f"题型：{question.skill or 'unknown'} · 难度：{question.difficulty or quiz.level}"
        )
        meta.setObjectName("muted")
        meta.setWordWrap(True)
        card.body.addWidget(meta)

        prompt = QLabel(question.prompt)
        prompt.setObjectName("questionPrompt")
        prompt.setWordWrap(True)
        card.body.addWidget(prompt)

        if chosen == UNKNOWN_ANSWER:
            chosen_text = UNKNOWN_LABEL
        elif 0 <= chosen < len(question.options):
            chosen_text = f"{question.letter(chosen)}. {question.options[chosen]}"
        else:
            chosen_text = "未作答"
        answer_label = QLabel(f"你的答案：{chosen_text}")
        answer_label.setObjectName("muted")
        answer_label.setWordWrap(True)
        card.body.addWidget(answer_label)

        note = quiz.choice_notes[position] if position < len(quiz.choice_notes) else ""
        if note:
            note_label = QLabel(f"补充说明：{note}")
            note_label.setObjectName("muted")
            note_label.setWordWrap(True)
            card.body.addWidget(note_label)

        if not correct:
            correct_text = (
                f"{question.letter(question.answer_index)}. "
                f"{question.options[question.answer_index]}"
            )
            correct_label = QLabel(f"正确答案：{correct_text}")
            correct_label.setObjectName("muted")
            correct_label.setWordWrap(True)
            card.body.addWidget(correct_label)

            error_type = quiz.error_types[index - 1] if index - 1 < len(quiz.error_types) else ""
            error_label = _ERROR_LABELS.get(error_type)
            if error_label:
                reason = QLabel(f"错因：{error_label}")
                reason.setObjectName("muted")
                reason.setWordWrap(True)
                card.body.addWidget(reason)

        explanation = quiz.explanations[index - 1] if index - 1 < len(quiz.explanations) else ""
        if not explanation:
            explanation = question.explanation
        if explanation:
            detail = QLabel(f"解析：{explanation}")
            detail.setObjectName("muted")
            detail.setWordWrap(True)
            detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            card.body.addWidget(detail)
        return card

    def _build_production_result_card(
        self,
        index: int,
        question: QuizQuestion,
        quiz: ReadingQuiz,
        production,
    ) -> Card:
        card = Card()
        label = _production_label(question)
        score = production.score if production is not None else 0
        scored = production.scored if production is not None else True
        if scored:
            heading_text = f"第 {index} 题 · {label} · 得分 {score}/1"
        else:
            heading_text = f"第 {index} 题 · {label} · AI 未评分（不计入等级）"
        heading = QLabel(heading_text)
        heading.setObjectName("cardTitle")
        card.body.addWidget(heading)

        meta = QLabel(f"题型：{label} · 难度：{question.difficulty or quiz.level}")
        meta.setObjectName("muted")
        meta.setWordWrap(True)
        card.body.addWidget(meta)

        prompt = QLabel(question.prompt)
        prompt.setObjectName("questionPrompt")
        prompt.setWordWrap(True)
        card.body.addWidget(prompt)

        text = production.text if production is not None else ""
        answer_title = "你的译文" if question.is_translation else "你的回答"
        answer = QLabel(f"{answer_title}：{text or '未作答'}")
        answer.setObjectName("muted")
        answer.setWordWrap(True)
        answer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.body.addWidget(answer)

        if question.reference_answer:
            reference_title = "参考翻译" if question.is_translation else "参考思路"
            reference = QLabel(f"{reference_title}：{question.reference_answer}")
            reference.setObjectName("muted")
            reference.setWordWrap(True)
            reference.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            card.body.addWidget(reference)

        feedback = production.feedback if production is not None else ""
        explanation = quiz.explanations[index - 1] if index - 1 < len(quiz.explanations) else ""
        detail_text = feedback or explanation or question.explanation
        if detail_text:
            detail = QLabel(f"讲评：{detail_text}")
            detail.setObjectName("muted")
            detail.setWordWrap(True)
            detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            card.body.addWidget(detail)
        return card

    def _result_subtitle(self, quiz: ReadingQuiz) -> str:
        score = f"得分 {quiz.score}/{quiz.total}"
        if quiz.practice_mode:
            return (
                f"检测完成：{score}；今天已完成过一次正式检测，"
                f"本次为练习模式，等级保持 {quiz.result_level}。"
            )
        if quiz.waiting_for_upgrade:
            return (
                f"检测完成：{score}，后验建议升到 {quiz.pending_level}"
                f"（置信度 {quiz.level_confidence:.2f}）；"
                f"已记录 {quiz.pending_count}/{UPGRADE_EVIDENCE_REQUIRED} 次向上证据，"
                f"等级暂时保持 {quiz.result_level}。"
            )
        if quiz.level_changed:
            return (
                f"检测完成：{score}，等级从 {quiz.previous_level} 调整为 "
                f"{quiz.result_level}（置信度 {quiz.level_confidence:.2f}）。"
            )
        if quiz.recommended_level and quiz.result_level:
            return (
                f"检测完成：{score}，后验建议 {quiz.recommended_level}，"
                f"本次等级保持 {quiz.result_level}。"
            )
        return "检测完成，结果已保存；等级会根据多次检测的表现动态调整。"

    # ---------------------------------------------------------------- 状态机
    def _set_mode(self, mode: str, primary_text: str, *, primary_enabled: bool) -> None:
        self._mode = mode
        self._stack.setCurrentIndex(_MODE_INDEX[mode])
        self._primary_button.setText(primary_text)
        self._primary_enabled = primary_enabled
        if self._worker is None or self._worker.isFinished():
            self._primary_button.setEnabled(primary_enabled)

    def _primary_clicked(self) -> None:
        if self._mode == MODE_LOADING:
            self._start_generation()
        elif self._mode == MODE_QUIZ:
            if self._quiz is not None and self._quiz.stage < 2:
                self._advance_stage()
            else:
                self._submit_answers()
        else:
            self.accept()

    def _set_busy(self, busy: bool) -> None:
        self._stack.setEnabled(not busy)
        # 取消按钮在忙碌时反而最重要：点击后放弃当前请求，界面立即恢复。
        self._cancel_button.setEnabled(True)
        self._cancel_button.setText("取消请求" if busy else "取消")
        self._busy_progress.setVisible(busy)
        if busy:
            self._primary_button.setEnabled(False)
        else:
            self._primary_button.setEnabled(self._primary_enabled)

    def _on_cancel_clicked(self) -> None:
        worker = self._worker
        if worker is None or worker.isFinished():
            self.reject()
            return

        phase = self._busy_phase
        if not retire_worker(worker):
            return
        self._worker = None
        self._busy_phase = ""
        self._set_busy(False)

        if phase == "loading":
            set_status(self._loading_status, "已取消出题，可以重试。", "hint")
            self._set_mode(MODE_LOADING, "重试出题", primary_enabled=True)
        elif phase == "stage":
            set_status(self._quiz_status, "已取消第二轮出题，可以重新生成。", "hint")
            self._set_mode(MODE_QUIZ, "重新生成第二轮", primary_enabled=True)
        else:
            set_status(self._quiz_status, "已取消批改，可以重新提交。", "hint")
            self._set_mode(MODE_QUIZ, "重新提交", primary_enabled=True)

    def _on_worker_finished(self) -> None:
        self._worker = None
        self._busy_phase = ""
        self._set_busy(False)

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                ReadingQuizDialog._clear_layout(item.layout())

    # ---------------------------------------------------------------- 全屏
    def _toggle_fullscreen(self) -> None:
        """在普通窗口与全屏之间切换；全屏时题目区自动铺满屏幕。"""
        if self.isFullScreen():
            self.showNormal()
            self._fullscreen_button.setText("全屏")
        else:
            self.showFullScreen()
            self._fullscreen_button.setText("退出全屏")

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 约定
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self._toggle_fullscreen()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------ 关闭保护
    def reject(self) -> None:  # noqa: D401 - Qt 约定
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "AI 正在处理，请等结果出来后再关闭。")
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 约定
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "AI 正在处理，请等结果出来后再关闭。")
            event.ignore()
            return
        super().closeEvent(event)

    def shutdown(self, timeout_ms: int = 3000) -> TaskWorker | None:
        """供主窗口兜底收尾：取消并等待后台线程。"""
        worker = self._worker
        if worker is None or worker.isFinished():
            return None
        if worker.stop(timeout_ms):
            return None
        return worker


__all__ = ["ReadingQuizDialog"]
